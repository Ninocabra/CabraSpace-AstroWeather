"""The whole night as one payload: all-sky chart plus a lane per target.

Two views of the same numbers, and that is the point of this module existing.
The all-sky answers WHERE -- the full dome in one circle, zenith at the centre,
horizon at the rim, trajectories as arcs. The lanes answer WHEN AND HOW MUCH --
elevation against time, with the night's events drawn on top of the curve.

They share a time cursor and, more importantly, they share their SOURCE: every
number here comes from TargetNight.trace, which is the same trace the score was
computed from. A chart that recomputes what it illustrates is a chart that will
eventually contradict the recommendation beside it.

Events are grouped by what the observer can do about them:

    astro     geometry, and therefore certain: rise, transit, moonrise
    meteo     forecast, and therefore uncertain: cloud, dust
    decision  where the engine changes its mind: a gate opening or closing

That split is not cosmetic. A rise time is a fact; a cloud arrival is a guess
with a probability attached, and the chart must never let them look alike.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass

from zoneinfo import ZoneInfo

from .dome import fetch_cloud_grid, project_clouds
from .ephem import moon_illumination, moon_phase_angle, moon_positions, night_for, sun_positions
from .extinction import evaluate as evaluate_transparency
from .seeing import integrate
from .site import NERPIO, Site
from .queries import night_inputs, night_series
from .skymodel import predict
from .skyobjects import (milky_way_field, planet_positions, sidereal_degrees,
                         visible_stars)
from .skybackground import epoch_floor
from .targets import CATALOGUE, MIN_ALTITUDE, SKY_GATE, evaluate_night

# Cloud within this many degrees of a target's azimuth is cloud that matters
# for that target. Wider than it looks: the grid resolves 25 km, so pretending
# to know which side of a target a patch sits on would be false precision.
AZIMUTH_SECTOR_DEG = 35.0
CLOUD_EVENT_THRESHOLD = 40.0

# The archive keeps UTC and always will. The VIEW is read by someone standing
# in Nerpio, for whom "the window opens at 17:19" is nonsense and "19:19" is
# obvious. ZoneInfo carries the summer-time change, so this stays right across
# the last Sunday of October without anyone remembering it.
LOCAL = ZoneInfo("Europe/Madrid")

# Terciles measured on this site's own archive, so "good" means good HERE.
# Aerosol optical depth over 22,625 hours of CAMS, and sky background over
# 65,844 clean photometer hours. Quoting a number without saying whether it is
# good is most of the way to quoting it uselessly, which is what the seeing
# tercile already fixed for turbulence.
AOD_TERCILES = (0.060, 0.110)
SKY_TERCILES = (21.25, 21.52)
# Below this margin between air temperature and dew point the corrector plate
# fogs. Standard astrophotography practice, NOT measured here.
DEW_MARGIN_WARN, DEW_MARGIN_CRITICAL = 3.0, 1.0


def _tercile(value: float, low: float, high: float, higher_is_better: bool) -> str:
    if higher_is_better:
        return "favorable" if value >= high else "normal" if value >= low else "desfavorable"
    return "favorable" if value <= low else "normal" if value <= high else "desfavorable"


def _advice(*, moon_illumination: float, moon_up: bool, dust: bool,
            seeing_tercile: str, sky_tercile: str, dew_margin: float | None,
            humidity: float | None) -> list[dict]:
    """What tonight is FOR, in the terms an imager actually plans in.

    A panel of numbers tells you the state of the sky. This tells you what to
    do about it, which is the whole reason the numbers were gathered.
    """
    notes = []
    if moon_up and moon_illumination > 0.55:
        notes.append({"kind": "moon", "level": "warn", "text":
            f"Luna al {moon_illumination*100:.0f} %: noche de BANDA ESTRECHA. "
            f"Un filtro de 3-5 nm deja pasar una rendija del continuo lunar, "
            f"asi que Ha y OIII cunden mientras el banda ancha no cuaja."})
    elif moon_illumination < 0.25 or not moon_up:
        notes.append({"kind": "moon", "level": "good", "text":
            "Sin Luna: es la noche para BANDA ANCHA y para lo de bajo "
            "contraste (IFN, halos, colas de marea) que el resto del mes es "
            "imposible. Gastarla en banda estrecha es tirar oscuridad."})
    if dust:
        notes.append({"kind": "dust", "level": "warn", "text":
            "Calima: el polvo dispersa hacia delante y aplasta el contraste de "
            "lo extenso y debil. Los objetos pequenos y brillantes lo notan "
            "mucho menos, y el rojo atraviesa mejor que el azul."})
    if seeing_tercile == "favorable":
        notes.append({"kind": "seeing", "level": "good", "text":
            "Seeing en el tercil bueno: aprovechalo en lo COMPACTO — "
            "planetarias, cumulos globulares, nucleos de galaxia. En "
            "nebulosidad extensa el seeing casi no se nota."})
    elif seeing_tercile == "desfavorable":
        notes.append({"kind": "seeing", "level": "warn", "text":
            "Seeing en el tercil malo: renuncia al detalle fino y ve a lo "
            "extenso. Una noche mediocre de seeing sigue siendo una buena "
            "noche para nebulosas grandes."})
    if sky_tercile == "favorable":
        notes.append({"kind": "sky", "level": "good", "text":
            "Fondo de cielo en el mejor tercio del archivo de este sitio."})
    if dew_margin is not None and dew_margin <= DEW_MARGIN_CRITICAL:
        notes.append({"kind": "dew", "level": "crit", "text":
            f"Margen de rocio {dew_margin:.1f} °C: el corrector se empanara "
            f"casi seguro. Resistencia anti-rocio puesta antes de empezar, no "
            f"cuando ya se note."})
    elif dew_margin is not None and dew_margin <= DEW_MARGIN_WARN:
        notes.append({"kind": "dew", "level": "warn", "text":
            f"Margen de rocio {dew_margin:.1f} °C: riesgo de empanamiento en "
            f"las horas frias de madrugada."})
    if humidity is not None and humidity >= 90:
        notes.append({"kind": "humidity", "level": "warn", "text":
            f"Humedad {humidity:.0f} %: cerca del punto en que muchos "
            f"observatorios cierran por norma, al margen de que haya nubes."})
    return notes


@dataclass(frozen=True)
class Event:
    """Something that happens at a known frame of the night."""

    frame: int
    category: str          # astro | meteo | decision
    kind: str
    label: str

    def as_dict(self) -> dict:
        return {"frame": self.frame, "category": self.category,
                "kind": self.kind, "label": self.label}


def _crossings(values: list[float | None], threshold: float):
    """Frames where a series crosses a threshold, and in which direction."""
    for index in range(1, len(values)):
        before, after = values[index - 1], values[index]
        if before is None or after is None:
            continue
        if before < threshold <= after:
            yield index, "up"
        elif before >= threshold > after:
            yield index, "down"


def _cloud_in_sector(clouds: list[dict], azimuth: float) -> dict[str, float]:
    """Worst cover per layer within the target's slice of sky."""
    worst = {"low": 0.0, "mid": 0.0, "high": 0.0}
    for patch in clouds:
        offset = abs((patch["az"] - azimuth + 180.0) % 360.0 - 180.0)
        if offset <= AZIMUTH_SECTOR_DEG:
            worst[patch["layer"]] = max(worst[patch["layer"]], float(patch["cover"]))
    return worst


def _target_events(result, frames: list[dict], labels: list[str],
                   moon_altitudes: list[float], transparency) -> list[Event]:
    """Everything worth marking on one target's lane."""
    events: list[Event] = []
    trace = result.trace
    if not trace:
        return events

    altitudes = [point["alt"] for point in trace]

    # --- astro: geometry, and therefore certain --------------------------
    for frame, direction in _crossings(altitudes, MIN_ALTITUDE):
        events.append(Event(
            frame, "astro", "min_altitude",
            f"{'sube de' if direction == 'up' else 'baja de'} "
            f"{MIN_ALTITUDE:.0f}° a las {labels[frame]}"))

    peak = max(range(len(altitudes)), key=lambda i: altitudes[i])
    if altitudes[peak] > MIN_ALTITUDE:
        events.append(Event(peak, "astro", "transit",
                            f"punto más alto, {altitudes[peak]:.0f}°, "
                            f"a las {labels[peak]}"))

    closest = min(range(len(trace)), key=lambda i: trace[i]["moon_sep"])
    if moon_altitudes[closest] > 0:
        events.append(Event(closest, "astro", "moon_closest",
                            f"la Luna pasa a {trace[closest]['moon_sep']:.0f}° "
                            f"a las {labels[closest]}"))

    for frame, direction in _crossings(moon_altitudes, 0.0):
        events.append(Event(
            frame, "astro", "moonrise" if direction == "up" else "moonset",
            f"{'sale' if direction == 'up' else 'se pone'} la Luna "
            f"a las {labels[frame]}"))

    # --- meteo: forecast, and therefore uncertain ------------------------
    names = {"low": "nube baja", "mid": "nube media", "high": "nube alta"}
    for layer in ("low", "mid", "high"):
        series = [_cloud_in_sector(frame["clouds"], point["az"])[layer]
                  for frame, point in zip(frames, trace)]
        for frame, direction in _crossings(series, CLOUD_EVENT_THRESHOLD):
            events.append(Event(
                frame, "meteo", f"cloud_{layer}",
                f"{'llega' if direction == 'up' else 'se va la'} "
                f"{names[layer]} por su lado del cielo ({labels[frame]})"))

    if transparency.dust_alert:
        events.append(Event(0, "meteo", "dust",
                            "calima en el aire toda la noche: "
                            "los objetos extensos pierden contraste"))

    # --- decision: where the engine changes its mind ---------------------
    yields = [point["yield"] for point in trace]
    for frame, direction in _crossings(yields, 0.001):
        gate = SKY_GATE.get(result.target.difficulty, 20.0)
        events.append(Event(
            frame, "decision", "gate",
            f"{'empieza a cuajar' if direction == 'up' else 'deja de cuajar'} "
            f"a las {labels[frame]} (corte de clase {gate:.1f} mag)"))

    if max(yields, default=0.0) > 0.0:
        best = max(range(len(yields)), key=lambda i: yields[i])
        events.append(Event(best, "decision", "best",
                            f"su mejor hora: {labels[best]}"))
    return events


def _site_weather(payload, index) -> dict:
    """Humidity and its consequences, from the site's own grid cell.

    Humidity was being collected and never shown, which is a strange thing to
    do with the number that decides whether the corrector fogs and whether the
    observatory closes on principle.
    """
    if not payload or index is None:
        return {}
    hourly = payload[0].get("hourly", {})
    def at(key):
        series = hourly.get(key) or []
        return series[index] if index < len(series) else None
    humidity, dew, temperature = at("relative_humidity_2m"), at("dew_point_2m"), at("temperature_2m")
    margin = (round(temperature - dew, 1)
              if temperature is not None and dew is not None else None)
    return {"humidity": humidity, "dew_point": dew, "temperature": temperature,
            "dew_margin": margin, "wind_ms": at("wind_speed_10m")}


def build(day: dt.datetime | None = None, site: Site = NERPIO, *,
          aod_total: float | None = None, aod_dust: float | None = None,
          jet_speed: float | None = None, serie: dict | None = None,
          with_clouds: bool = True, usable: float = 0.8) -> dict:
    """Everything both views need, in one JSON-ready dictionary.

    El aerosol y el jet salen de la PREVISION de esta noche, no de una
    constante. Lo fueron hasta el 2026-08-28: CAMS pronostica el AOD y el ECMWF
    los niveles de presion, las dos cosas llevaban meses archivadas, y el
    producto usaba en su lugar 0,07 y 25 m/s. Los supuestos estaban elegidos
    para ser plausibles, que es justo por lo que no se veia el fallo.
    """
    day = day or dt.datetime.now(dt.timezone.utc)
    night = night_for(day, site)
    # Ten minutes through astronomical darkness, thirty either side: the part
    # being planned against deserves the resolution, twilight does not.
    times = night.adaptive_hours(10, 30, site)
    labels = [t.astimezone(LOCAL).strftime("%H:%M") for t in times]
    forecast = night_inputs(times[0], times[-1], site.site_id)
    aod_total = forecast["aod_total"] if aod_total is None else aod_total
    aod_dust = forecast["aod_dust"] if aod_dust is None else aod_dust
    jet_speed = forecast["jet_speed"] if jet_speed is None else jet_speed
    # Y la MISMA prevision sin colapsar a la mediana, que es de donde sale la
    # extincion de cada hora. La mediana se queda para el veredicto de la noche.
    serie = (night_series(times[0], times[-1], site.site_id)
             if serie is None else serie)

    skies = predict(times, site)
    moons = moon_positions(times, site)
    suns = sun_positions(times, site)
    phase = moon_phase_angle(times[len(times) // 2])
    transparency = evaluate_transparency(
        aod_total=aod_total, aod_dust=aod_dust,
        elevation_m=site.site_elevation, delta_h_m=site.delta_h)
    turbulence = integrate(jet_speed)

    points, payload = ([], [])
    clouds_error = None
    if with_clouds:
        try:
            points, payload = fetch_cloud_grid(site)
        except Exception as exc:                      # noqa: BLE001
            # NO SE TRAGA EN SILENCIO. Hasta el 31-08-2026 este `except` dejaba
            # una lista vacia y aguas abajo eso es INDISTINGUIBLE de "no hay
            # nubes": la noche del 31 se publico con los 59 marcos a cero
            # mientras la prevision horaria daba 100 % de cobertura al
            # amanecer, y la cupula pinto un cielo limpio toda la noche. El
            # fallo era transitorio -- la rejilla son 49 peticiones seguidas --
            # pero lo que lo hizo invisible fue esta linea.
            points, payload = [], []
            clouds_error = "%s: %s" % (type(exc).__name__, exc)
            print("[nightview] rejilla de nubes NO disponible -- %s" % clouds_error)
    grid_times = ([dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
                   for t in payload[0]["hourly"]["time"]] if payload else [])

    frames = []
    for index, when in enumerate(times):
        cloud_index = (min(range(len(grid_times)),
                           key=lambda i: abs((grid_times[i] - when).total_seconds()))
                       if grid_times else None)
        frames.append({
            "t": when.isoformat(),
            "label": labels[index],
            "sun_alt": round(suns[index].altitude, 1),
            "moon_alt": round(moons[index].altitude, 1),
            "moon_az": round(moons[index].azimuth, 1),
            "sky_mag": round(skies[index].predicted_mag, 2),
            # False before sunset: the twilight fit stops at -8 deg, so those
            # frames are extrapolation and the view must not print a number.
            "sky_mag_trustworthy": skies[index].is_trustworthy,
            "dark": suns[index].altitude < -18.0,
            "clouds": (project_clouds(points, payload, cloud_index)
                       if cloud_index is not None else []),
            **_site_weather(payload, cloud_index),
        })

    results = evaluate_night(CATALOGUE, times,
                             usable_probability=[usable] * len(times),
                             turbulence=turbulence, aod_total=aod_total,
                             aod_dust=aod_dust, serie=serie, site=site)
    moon_altitudes = [f["moon_alt"] for f in frames]
    lanes = []
    for result in results:
        lanes.append({
            "name": result.target.name,
            "kind": result.target.kind,
            "difficulty": result.target.difficulty,
            "ra": result.target.ra_deg, "dec": result.target.dec_deg,
            "q_cond": round(result.q_conditional, 1),
            "q_expected": round(result.q_expected, 1),
            "verdict": result.verdict,
            "reason": result.reason,
            "note": result.target.note,
            "gate": SKY_GATE.get(result.target.difficulty, 20.0),
            "trace": result.trace,
            "events": [e.as_dict() for e in
                       _target_events(result, frames, labels, moon_altitudes,
                                      transparency)],
        })

    # --- the question the observer actually asks -------------------------
    # SIN REJILLA NO HAY PROBABILIDAD. El calculo de abajo hace
    # `max(..., default=0.0)` sobre las nubes de cada marco, asi que con la
    # rejilla caida contaria la noche como DESPEJADA y publicaria una
    # probabilidad de abrir alta construida sobre ceros inventados. Mejor no
    # decir nada que decir eso.
    outlook = None
    try:
        if not grid_times:
            raise RuntimeError("sin rejilla de nubes: no se estima apertura")
        from .calibration import Calibrator
        from .roof import load_climatology, outlook as roof_outlook
        climatology, calibrator = load_climatology(), Calibrator.load()
        if climatology and calibrator:
            clear = [calibrator.probability(
                        max((c["cover"] for c in f["clouds"]), default=0.0), 24.0)
                     for f in frames]
            result = roof_outlook(day, clear, times, climatology, site)
            outlook = {"probability": result.open_probability,
                       "verdict": result.verdict,
                       "sentence": result.sentence,
                       "expected_hours": result.expected_open_hours,
                       "typical_hours": result.climatological_hours,
                       "range_hours": [result.low_hours, result.high_hours],
                       "month_fraction": result.human_factor,
                       "hourly": result.hourly}
    except Exception:
        outlook = None

    darkest = max((f["sky_mag"] for f in frames if f["sky_mag_trustworthy"]),
                  default=epoch_floor())
    middle = frames[len(frames) // 2]
    sky_tercile = _tercile(darkest, *SKY_TERCILES, higher_is_better=True)
    aod_tercile = _tercile(transparency.aod_site, *AOD_TERCILES, higher_is_better=False)
    margins = [f.get("dew_margin") for f in frames if f.get("dew_margin") is not None]
    humidities = [f.get("humidity") for f in frames if f.get("humidity") is not None]

    mid = times[len(times) // 2]
    return {
        "roof": outlook,
        # Que la ausencia VIAJE. Quien pinte esto tiene que poder distinguir
        # "no hay nubes" de "no sabemos si las hay", que es justo lo que no se
        # podia hacer hasta hoy.
        "clouds_available": bool(grid_times),
        "clouds_error": clouds_error,
        "advice": _advice(
            moon_illumination=moon_illumination(night.dusk_astronomical or night.night),
            moon_up=any(f["moon_alt"] > 0 and f["dark"] for f in frames),
            dust=transparency.dust_alert,
            seeing_tercile=turbulence.tercile,
            sky_tercile=sky_tercile,
            dew_margin=min(margins) if margins else None,
            humidity=max(humidities) if humidities else None),
        "sky_tercile": sky_tercile,
        "aod_tercile": aod_tercile,
        "timezone": "Europe/Madrid",
        "site": {"name": site.name, "lat": site.latitude, "lon": site.longitude,
                 "elevation": site.site_elevation},
        "night": night.night.date().isoformat(),
        "dark_hours": round(night.dark_hours, 1),
        "labels": labels,
        "lst": sidereal_degrees(times, site),
        "moon": {"illumination": round(moon_illumination(
                     night.dusk_astronomical or night.night), 3),
                 "phase_angle": round(phase, 1)},
        "transparency": {"k": round(transparency.k_total, 3),
                         "aod_source": forecast["aod_source"],
                         "aod_site": round(transparency.aod_site, 3),
                         "dust_alert": transparency.dust_alert,
                         "verdict": transparency.verdict,
                         "color_note": transparency.color_note},
        "seeing": {"source": forecast["jet_source"],
                   "tercile": turbulence.tercile,
                   "range": [round(turbulence.range_arcsec[0], 1),
                             round(turbulence.range_arcsec[1], 1)],
                   "jet": round(turbulence.jet_speed, 0),
                   # ESTA NOTA DECIA "un solo valor por noche, no se modela
                   # variacion intranoche" y desde el 03-09-2026 es FALSA: el
                   # modelo puntua con el jet de CADA HORA. Lo que se publica
                   # aqui sigue siendo el resumen de la noche -- el tercil es
                   # una frase sobre la noche, como el de la calima -- asi que
                   # la nota tiene que decir las dos cosas y no una.
                   "note": "estimacion experimental sin calibrar; el tercil y "
                           "la banda resumen la noche, el modelo puntua con el "
                           "jet de cada hora"},
        "galaxy": milky_way_field(),
        "stars": visible_stars(site),
        "planets": planet_positions(mid, site),
        "min_altitude": MIN_ALTITUDE,
        "frames": frames,
        "lanes": lanes,
    }


if __name__ == "__main__":
    data = build()
    print(json.dumps({k: v for k, v in data.items()
                      if k not in ("frames", "lanes", "milky_way", "stars")},
                     indent=1)[:1200])
