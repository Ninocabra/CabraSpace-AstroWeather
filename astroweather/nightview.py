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

from .dome import fetch_cloud_grid, project_clouds
from .ephem import moon_illumination, moon_phase_angle, moon_positions, night_for, sun_positions
from .extinction import evaluate as evaluate_transparency
from .seeing import integrate
from .site import NERPIO, Site
from .skymodel import predict
from .skyobjects import (milky_way_field, planet_positions, sidereal_degrees,
                         visible_stars)
from .targets import CATALOGUE, MIN_ALTITUDE, SKY_GATE, evaluate_night

# Cloud within this many degrees of a target's azimuth is cloud that matters
# for that target. Wider than it looks: the grid resolves 25 km, so pretending
# to know which side of a target a patch sits on would be false precision.
AZIMUTH_SECTOR_DEG = 35.0
CLOUD_EVENT_THRESHOLD = 40.0


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


def build(day: dt.datetime | None = None, site: Site = NERPIO, *,
          aod_total: float = 0.07, aod_dust: float = 0.01,
          jet_speed: float = 25.0, with_clouds: bool = True,
          usable: float = 0.8) -> dict:
    """Everything both views need, in one JSON-ready dictionary."""
    day = day or dt.datetime.now(dt.timezone.utc)
    night = night_for(day, site)
    times = night.hours(30)
    labels = [t.strftime("%H:%M") for t in times]

    skies = predict(times, site)
    moons = moon_positions(times, site)
    suns = sun_positions(times, site)
    phase = moon_phase_angle(times[len(times) // 2])
    transparency = evaluate_transparency(
        aod_total=aod_total, aod_dust=aod_dust,
        elevation_m=site.site_elevation, delta_h_m=site.delta_h)
    turbulence = integrate(jet_speed)

    points, payload = ([], [])
    if with_clouds:
        try:
            points, payload = fetch_cloud_grid(site)
        except Exception:
            points, payload = [], []
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
        })

    results = evaluate_night(CATALOGUE, times,
                             usable_probability=[usable] * len(times),
                             turbulence=turbulence, aod_total=aod_total,
                             aod_dust=aod_dust, site=site)
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

    mid = times[len(times) // 2]
    return {
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
                         "aod_site": round(transparency.aod_site, 3),
                         "dust_alert": transparency.dust_alert,
                         "verdict": transparency.verdict,
                         "color_note": transparency.color_note},
        "seeing": {"tercile": turbulence.tercile,
                   "range": [round(turbulence.range_arcsec[0], 1),
                             round(turbulence.range_arcsec[1], 1)],
                   "jet": round(turbulence.jet_speed, 0),
                   # Said plainly because the lanes would otherwise imply it:
                   # one jet speed per night means no intra-night variation is
                   # modelled, so there are no seeing events to draw.
                   "note": "estimacion experimental sin calibrar; un solo valor "
                           "por noche, no se modela variacion intranoche"},
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
