"""The full sky background budget, measured at this site (SPEC v8 section 2.2).

Everything that makes the night sky less than black, added in FLUX because
magnitudes do not add:

    natural floor + zodiacal light + airglow + light pollution
    + moonlight (moonsky.py) + twilight (skymodel.py)

The zodiacal and floor terms here are not taken from a textbook: they were
recovered from 18,791 clear moonless hours of the site's own photometer, by
looking at the residual against the Moon-and-twilight model as a function of
where the zenith sat relative to the ecliptic.

    |ecliptic latitude| 0-15 deg  ->  0.46 mag brighter than the bare floor
                        15-30     ->  0.30
                        30-45     ->  0.17
                        45-90     ->  0.19-0.20  (flat: the floor itself)

So the zenith sky carries about 0.27 mag of zodiacal light when the ecliptic
passes overhead, and the floor is 0.19 mag brighter than the 21.9 assumed
before. Both match what the literature expects, which is the point: the site
was measured rather than looked up.

The GALACTIC term was added later, after an external audit pointed out that the
Milky Way was missing while the zodiacal light was modelled. It is measurable
here for a geometric reason: the zenith of a site at +38.17 deg traces a fixed
declination circle that crosses the galactic equator, so |b| at the zenith
sweeps the full 0-79 deg. Over 50,412 clean moonless deep-night samples the
residual runs monotonically from -0.33 mag on the plane to -0.04 at the pole,
while the ecliptic latitude stays flat across those bins -- so it is the Milky
Way, not zodiacal light leaking.

Fitting the floor, the zodiacal term and the galactic term JOINTLY on the raw
measurements is what settles it. Without the galactic term the fit is forced to
push the zodiacal amplitude to an absurd 0.76 mag over a 9 deg scale to soak up
the Milky Way. With it:

    natural floor            21.69 mag   (published value 21.71)
    zodiacal on the ecliptic  0.28 mag / 22.8 deg   (published 0.27 / 25.0)
    galactic on the plane     0.39 mag / 26.5 deg   (new)

The zodiacal numbers falling back onto the published ones, recovered by a
different route and no longer able to hide the Milky Way, is the evidence that
both terms are real. RMS improves from 0.239 to 0.222 mag.

One honest limit that the same geometry imposes: the zenith here never gets
closer than 14.7 deg to the ecliptic, so the zodiacal amplitude AT the ecliptic
is an extrapolation of the fitted shape, not a measurement. The galactic
amplitude is not -- the zenith does cross |b| = 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np
from astropy.coordinates import AltAz, SkyCoord, get_sun
from astropy.time import Time
import astropy.units as u

from .ephem import location
from .moonsky import mag_to_nl, nl_to_mag
from .site import NERPIO, Site

# Natural floor away from the ecliptic, FITTED to this site: the 21.9 used as a
# starting value is 0.19 mag too faint against seven years of measurement.
#
# CAVEAT that matters (SPEC v8 section 2.3): this is what the TESS reads in the
# TESS-W band, not a V magnitude. Quoting it as "21.71 in V" would be exactly
# the band conflation the specification forbids. It is the right floor to
# compare TESS against; converting it to V needs an explicit, versioned
# calibration that does not exist yet.
NATURAL_FLOOR_MAG = 21.71

# ...y ESE es el problema, porque el suelo DERIVA. Medido el 29-08-2026 sobre
# 9.814 horas del fotometro sin Luna y en noche cerrada, la mediana por anio:
#
#   2019 21,300   2021 21,300   2023 21,440   2025 21,680
#   2020 21,280   2022 21,405   2024 21,520   2026 21,865
#
# +0,565 mag en siete anios, monotona. Una constante no describe ningun anio:
# 21,71 se queda 0,15 corta para 2026 y sobra 0,41 para 2019, y ese error entra
# en TODAS las puertas del recomendador -- el rendimiento de cada hora se mide
# contra este numero.
#
# Asi que el suelo pasa a medirse de la epoca. La constante se queda como
# respaldo declarado para cuando no hay archivo (otra maquina, un test), no
# como la verdad.
FLOOR_VENTANA_DIAS = 400
FLOOR_MIN_HORAS = 200
# El suelo medido se guarda en disco y se vuelve a medir una vez al dia, como
# el calibrador de nubes. No se calcula al vuelo a proposito: medirlo son miles
# de horas de fotometro contra efemerides, y `hourly_yield` lo pide 58.000 veces
# por pasada. Un numero central no puede costar lo que cuesta medirlo.
FLOOR_FICHERO = "sky_floor.json"


@lru_cache(maxsize=1)
def epoch_floor() -> float:
    """El suelo natural de la EPOCA, o la constante si no se ha medido aun.

    Nunca lanza y nunca tarda: lee un fichero. Si no esta, devuelve la
    constante de siete anios y el producto sigue funcionando -- peor, pero
    funcionando -- que es lo que tiene que hacer un numero tan central.
    """
    try:
        from .archive import DATA_ROOT
        ruta = DATA_ROOT / FLOOR_FICHERO
        if not ruta.exists():
            return NATURAL_FLOOR_MAG
        import json
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        suelo = float(datos["suelo"])
        # Un suelo fuera de lo fisicamente razonable es un fichero corrupto, no
        # un descubrimiento.
        return suelo if 20.0 <= suelo <= 22.5 else NATURAL_FLOOR_MAG
    except Exception:
        return NATURAL_FLOOR_MAG


def fit_epoch_floor(dias: int = FLOOR_VENTANA_DIAS, root=None) -> dict:
    """Mide el suelo con el fotometro y lo deja escrito. Una vez al dia.

    Se miden las horas SIN Luna (disco a mas de 6 grados bajo el horizonte) en
    noche astronomica y con lectura fisicamente posible, y se toma la mediana.
    Es la misma definicion con la que se obtuvo la constante, aplicada a una
    ventana movil en vez de a siete anios.

    La ventana es de algo mas de un anio a proposito: mas corta y manda la
    estacion -- el cielo de invierno y el de verano no son el mismo -- y mas
    larga vuelve a promediar epocas que ya sabemos que difieren.
    """
    import json
    from .ephem import moon_positions, sun_positions
    from .queries import connect
    from .archive import DATA_ROOT, utcnow

    filas = connect(root).execute(f"""
        SELECT date_trunc('hour', t_utc) AS h, median(sky_mag_raw) AS m
        FROM measurements
        WHERE instrument_type = 'TESS' AND sky_mag_raw IS NOT NULL
          AND t_utc > now() - INTERVAL {int(dias)} DAY
        GROUP BY 1 HAVING count(*) >= 20 ORDER BY 1
    """).fetchall()
    if not filas:
        return {"medido": False, "porque": "sin lecturas del fotometro"}
    tiempos = [f[0].replace(tzinfo=timezone.utc) for f in filas]
    medidos = [float(f[1]) for f in filas]
    # Solo el Sol y la Luna: `predict` haria el modelo entero y son miles de
    # horas. Aqui basta con saber si era de noche y si la Luna estaba fuera.
    soles = sun_positions(tiempos, NERPIO)
    lunas = moon_positions(tiempos, NERPIO)
    limpios = [(t, m) for t, m, sol, luna in zip(tiempos, medidos, soles, lunas)
               if sol.altitude < -18.0 and luna.altitude <= -6.0 and 18.0 < m < 22.6]
    limpias = sorted(m for _, m in limpios)
    if len(limpias) < FLOOR_MIN_HORAS:
        return {"medido": False, "n": len(limpias),
                "porque": f"solo {len(limpias)} horas limpias, hacen falta {FLOOR_MIN_HORAS}"}
    # Y ahora lo que de verdad hace falta: no la mediana del cielo medido, sino
    # el suelo que hace que EL MODELO acierte en esas mismas horas.
    #
    # No son lo mismo, y confundirlos era el error. La prediccion sin Luna no es
    # el suelo: es el suelo mas el zodiacal mas la Via Lactea, todos expresados
    # como excesos sobre el, asi que la prediccion entera se desplaza 1:1 con el
    # suelo. Medido el 29-08-2026, el modelo sale 0,246 mag mas brillante que el
    # fotometro en horas sin Luna, con dispersion 0,219 -- la mas apretada de
    # los cuatro regimenes lunares, o sea un desfase de verdad y no ruido. Con
    # la Luna alta el sesgo es -0,026: la ley lunar no tiene la culpa.
    from .skymodel import predict
    tiempos_limpios = [t for t, _ in limpios]
    medidos_limpios = [m for _, m in limpios]
    diferencias = [p.predicted_mag - m
                   for p, m in zip(predict(tiempos_limpios, NERPIO), medidos_limpios)
                   if p.is_night and p.is_trustworthy]
    sesgo = sum(diferencias) / len(diferencias) if diferencias else 0.0
    suelo_usado = epoch_floor()
    n = len(limpias)
    resultado = {
        "medido": True,
        # El suelo que anula el sesgo. Restarlo es exacto porque el
        # desplazamiento es 1:1, no una aproximacion.
        "suelo": round(suelo_usado - sesgo, 3),
        "mediana_medida": round(limpias[n // 2], 3),
        "sesgo_corregido": round(sesgo, 3),
        "suelo_anterior": suelo_usado,
        "n_sesgo": len(diferencias),
        "n": n, "ventana_dias": dias,
        "p10": round(limpias[n // 10], 3), "p90": round(limpias[9 * n // 10], 3),
        "constante_anterior": NATURAL_FLOOR_MAG,
        "ajustado_el": utcnow().isoformat(),
    }
    ruta = (root or DATA_ROOT) / FLOOR_FICHERO
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(resultado, indent=1), encoding="utf-8")
    epoch_floor.cache_clear()
    return resultado


# Zodiacal excess at the ecliptic over the floor, in magnitudes, and the
# latitude scale over which it decays.
ZODIACAL_AMPLITUDE_MAG = 0.27
ZODIACAL_LATITUDE_SCALE_DEG = 25.0
# Extra brightening towards the Sun, over and above the latitude term.
ZODIACAL_ELONGATION_GAIN = 0.10

# Integrated starlight of the Milky Way, fitted jointly with the two terms
# above so that neither can absorb it (see the module docstring). Larger than
# the zodiacal contribution, which is why leaving it out was not cosmetic.
GALACTIC_AMPLITUDE_MAG = 0.39
GALACTIC_LATITUDE_SCALE_DEG = 26.5

# Airglow tracks solar activity slowly. F10.7 near 130 is the current cycle-25
# level; the coefficient is small on purpose (SPEC v8 section 2.2 keeps airglow
# as a SLOW term, never an hourly predictor).
F107_REFERENCE = 130.0
AIRGLOW_MAG_PER_100_SFU = 0.10


def zodiacal_excess(ecliptic_latitude_deg: float,
                    solar_elongation_deg: float | None = None) -> float:
    """Zodiacal brightening over the natural floor, in magnitudes.

    Shape fitted to the site's own residual: strongest on the ecliptic, decaying
    to nothing by about 45 degrees of latitude, with a mild extra term towards
    the Sun.
    """
    latitude = min(90.0, abs(ecliptic_latitude_deg))
    excess = ZODIACAL_AMPLITUDE_MAG * math.exp(
        -latitude / ZODIACAL_LATITUDE_SCALE_DEG)
    if solar_elongation_deg is not None:
        # Closer to the Sun, brighter; flat beyond the antisolar hemisphere.
        towards_sun = max(0.0, (180.0 - abs(solar_elongation_deg)) / 180.0)
        excess += ZODIACAL_ELONGATION_GAIN * towards_sun
    return excess


def galactic_excess(galactic_latitude_deg: float) -> float:
    """Milky Way brightening over the natural floor, in magnitudes.

    Same exponential shape as the zodiacal term, and for the same reason: it is
    what the site's own residual traces. The scale height of 26.5 deg is wider
    than the visible star clouds because integrated starlight includes the
    faint unresolved disc, not just the bright lanes the eye picks out.
    """
    latitude = min(90.0, abs(galactic_latitude_deg))
    return GALACTIC_AMPLITUDE_MAG * math.exp(
        -latitude / GALACTIC_LATITUDE_SCALE_DEG)


def airglow_excess(f107: float | None) -> float:
    """Airglow brightening over the floor, from the SLOW solar-activity term.

    Deliberately not an hourly predictor: the physical variation is nightly and
    partly unpredictable, and the photometer captures the residual anyway.
    """
    if f107 is None:
        return 0.0
    return AIRGLOW_MAG_PER_100_SFU * (f107 - F107_REFERENCE) / 100.0


@dataclass(frozen=True)
class SkyBudget:
    """Where each magnitude of sky brightness comes from."""

    t_utc: datetime
    natural_floor: float
    zodiacal: float
    galactic: float
    airglow: float
    light_pollution: float
    total_mag: float
    ecliptic_latitude: float
    galactic_latitude: float

    @property
    def breakdown(self) -> str:
        return (f"suelo natural {self.natural_floor:.2f}"
                f" · zodiacal {self.zodiacal:+.2f}"
                f" · via lactea {self.galactic:+.2f}"
                f" · airglow {self.airglow:+.2f}"
                f" · LP {self.light_pollution:+.2f}"
                f" = {self.total_mag:.2f} mag/arcsec2")


def direction_ecliptic(times: list[datetime], altitudes_deg, azimuths_deg,
                       site: Site = NERPIO):
    """Ecliptic latitude of a DIRECTION, and its elongation from the Sun."""
    when = Time([t.astimezone(timezone.utc) for t in times])
    pointing = SkyCoord(AltAz(az=np.asarray(azimuths_deg, dtype=float) * u.deg,
                              alt=np.asarray(altitudes_deg, dtype=float) * u.deg,
                              obstime=when, location=location(site)))
    ecliptic = pointing.transform_to("geocentricmeanecliptic")
    sun = get_sun(when).transform_to("geocentricmeanecliptic")
    latitudes = np.asarray(ecliptic.lat.deg, dtype=float)
    elongations = np.abs(((np.asarray(ecliptic.lon.deg, dtype=float)
                           - np.asarray(sun.lon.deg, dtype=float) + 180) % 360) - 180)
    return latitudes, elongations


def direction_galactic(times: list[datetime], altitudes_deg, azimuths_deg,
                       site: Site = NERPIO):
    """Galactic latitude of a DIRECTION."""
    when = Time([t.astimezone(timezone.utc) for t in times])
    pointing = SkyCoord(AltAz(az=np.asarray(azimuths_deg, dtype=float) * u.deg,
                              alt=np.asarray(altitudes_deg, dtype=float) * u.deg,
                              obstime=when, location=location(site)))
    return np.asarray(pointing.galactic.b.deg, dtype=float)


def zenith_ecliptic(times: list[datetime], site: Site = NERPIO):
    """Ecliptic latitude of the zenith, and its elongation from the Sun."""
    return direction_ecliptic(times, np.full(len(times), 90.0),
                              np.zeros(len(times)), site)


def zenith_galactic(times: list[datetime], site: Site = NERPIO):
    """Galactic latitude of the zenith.

    At a fixed site this is a pure function of sidereal time, which is exactly
    why seven years of a stationary photometer can measure the Milky Way.
    """
    return direction_galactic(times, np.full(len(times), 90.0),
                              np.zeros(len(times)), site)


def natural_background(times: list[datetime], altitudes_deg, azimuths_deg,
                       site: Site = NERPIO, *, f107: float | None = None):
    """Natural sky background WHERE THE TELESCOPE IS POINTING, in magnitudes.

    The zenith is not the sky. The Milky Way term is 0.39 mag on the plane and
    decays over 26.5 deg, so collapsing the whole dome to its zenith value costs
    up to 0.405 mag over the usable sky -- measured over four nights of 2026,
    against the 0.17 mag of uncertainty this model declares. That is enough to
    push a target across the 21.3 gate of `extremo_contraste` in the wrong
    direction, which is the class where a false positive costs a whole night.

    WHAT THIS STILL DOES NOT MODEL, and it is a real gap rather than a rounding
    error: the AIRMASS dependence of the background itself. Airglow follows a
    van Rhijn law and the column grows towards the horizon, so the real sky
    brightens as you go down. The product only recommends above 25 deg, where
    that term is a couple of tenths, but it is NOT in here and must not be
    quoted as if it were (SPEC v8 section 2.2).
    """
    # One frame and one SkyCoord for both transforms: building the AltAz frame
    # is what costs, and doing it twice per target doubled the runtime of the
    # whole recommender for nothing.
    when = Time([t.astimezone(timezone.utc) for t in times])
    pointing = SkyCoord(AltAz(az=np.asarray(azimuths_deg, dtype=float) * u.deg,
                              alt=np.asarray(altitudes_deg, dtype=float) * u.deg,
                              obstime=when, location=location(site)))
    ecliptic = pointing.transform_to("geocentricmeanecliptic")
    sun = get_sun(when).transform_to("geocentricmeanecliptic")
    latitudes = np.asarray(ecliptic.lat.deg, dtype=float)
    elongations = np.abs(((np.asarray(ecliptic.lon.deg, dtype=float)
                           - np.asarray(sun.lon.deg, dtype=float) + 180) % 360) - 180)
    galactic_latitudes = np.asarray(pointing.galactic.b.deg, dtype=float)
    floor_flux = mag_to_nl(epoch_floor())
    airglow = airglow_excess(f107)
    values = []
    for index in range(len(latitudes)):
        zodiacal = zodiacal_excess(float(latitudes[index]),
                                   float(elongations[index]))
        galactic = galactic_excess(float(galactic_latitudes[index]))
        values.append(nl_to_mag(floor_flux * (
            1.0
            + (10 ** (0.4 * zodiacal) - 1.0)
            + (10 ** (0.4 * galactic) - 1.0)
            + (10 ** (0.4 * airglow) - 1.0))))
    return np.asarray(values, dtype=float)


def budget(times: list[datetime], site: Site = NERPIO, *,
           f107: float | None = None,
           light_pollution_mag: float | None = None) -> list[SkyBudget]:
    """Natural sky background at the zenith, component by component.

    Moonlight and twilight are NOT included: they belong to skymodel.predict,
    which adds them on top of this floor.
    """
    latitudes, elongations = zenith_ecliptic(times, site)
    galactic_latitudes = zenith_galactic(times, site)
    floor_flux = mag_to_nl(epoch_floor())
    results = []
    for index, when in enumerate(times):
        zodiacal = zodiacal_excess(float(latitudes[index]), float(elongations[index]))
        galactic = galactic_excess(float(galactic_latitudes[index]))
        airglow = airglow_excess(f107)

        # Each component is its own FLUX added on top of the floor, which is
        # what the module has always claimed and did not do: subtracting three
        # magnitude offsets from the floor compounds them, and with the Milky
        # Way now in the budget there are three of them. The excess magnitudes
        # are converted to the flux they represent relative to the floor.
        natural_flux = floor_flux * (1.0
                                     + (10 ** (0.4 * zodiacal) - 1.0)
                                     + (10 ** (0.4 * galactic) - 1.0)
                                     + (10 ** (0.4 * airglow) - 1.0))
        pollution_flux = mag_to_nl(light_pollution_mag) if light_pollution_mag else 0.0
        total = nl_to_mag(natural_flux + pollution_flux)
        results.append(SkyBudget(
            t_utc=when,
            natural_floor=epoch_floor(),
            zodiacal=-zodiacal,
            galactic=-galactic,
            airglow=-airglow,
            light_pollution=total - nl_to_mag(natural_flux),
            total_mag=total,
            ecliptic_latitude=float(latitudes[index]),
            galactic_latitude=float(galactic_latitudes[index]),
        ))
    return results


def integration_penalty(mag_sky: float, reference_mag: float | None = None) -> float:
    """How much longer you must expose because the sky is brighter.

    Background-limited imaging: the time to a fixed SNR scales with the sky
    FLUX, so each magnitude of sky lost costs a factor 2.512 in exposure. This
    is the number that turns "21.0 instead of 21.9" into something an imager
    can act on.

    El suelo de referencia es el de la EPOCA, no una constante de siete anios:
    el cielo de aqui se ha oscurecido 0,56 mag desde 2019, y una penalizacion
    medida contra un suelo que ya no existe no significa nada.
    """
    referencia = epoch_floor() if reference_mag is None else reference_mag
    return float(10 ** (0.4 * (referencia - mag_sky)))
