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
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

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
# Zodiacal excess at the ecliptic over the floor, in magnitudes, and the
# latitude scale over which it decays.
ZODIACAL_AMPLITUDE_MAG = 0.27
ZODIACAL_LATITUDE_SCALE_DEG = 25.0
# Extra brightening towards the Sun, over and above the latitude term.
ZODIACAL_ELONGATION_GAIN = 0.10

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
    airglow: float
    light_pollution: float
    total_mag: float
    ecliptic_latitude: float

    @property
    def breakdown(self) -> str:
        return (f"suelo natural {self.natural_floor:.2f}"
                f" · zodiacal {self.zodiacal:+.2f}"
                f" · airglow {self.airglow:+.2f}"
                f" · LP {self.light_pollution:+.2f}"
                f" = {self.total_mag:.2f} mag/arcsec2")


def zenith_ecliptic(times: list[datetime], site: Site = NERPIO):
    """Ecliptic latitude of the zenith, and its elongation from the Sun."""
    when = Time([t.astimezone(timezone.utc) for t in times])
    zenith = SkyCoord(AltAz(az=0 * u.deg, alt=90 * u.deg,
                            obstime=when, location=location(site)))
    ecliptic = zenith.transform_to("geocentricmeanecliptic")
    sun = get_sun(when).transform_to("geocentricmeanecliptic")
    latitudes = np.asarray(ecliptic.lat.deg, dtype=float)
    elongations = np.abs(((np.asarray(ecliptic.lon.deg, dtype=float)
                           - np.asarray(sun.lon.deg, dtype=float) + 180) % 360) - 180)
    return latitudes, elongations


def budget(times: list[datetime], site: Site = NERPIO, *,
           f107: float | None = None,
           light_pollution_mag: float | None = None) -> list[SkyBudget]:
    """Natural sky background at the zenith, component by component.

    Moonlight and twilight are NOT included: they belong to skymodel.predict,
    which adds them on top of this floor.
    """
    latitudes, elongations = zenith_ecliptic(times, site)
    results = []
    for index, when in enumerate(times):
        zodiacal = zodiacal_excess(float(latitudes[index]), float(elongations[index]))
        airglow = airglow_excess(f107)
        # Light pollution enters as its own flux, not as a magnitude offset.
        floor_flux = mag_to_nl(NATURAL_FLOOR_MAG - zodiacal - airglow)
        pollution_flux = mag_to_nl(light_pollution_mag) if light_pollution_mag else 0.0
        total = nl_to_mag(floor_flux + pollution_flux)
        results.append(SkyBudget(
            t_utc=when,
            natural_floor=NATURAL_FLOOR_MAG,
            zodiacal=-zodiacal,
            airglow=-airglow,
            light_pollution=total - nl_to_mag(floor_flux),
            total_mag=total,
            ecliptic_latitude=float(latitudes[index]),
        ))
    return results


def integration_penalty(mag_sky: float, reference_mag: float = NATURAL_FLOOR_MAG) -> float:
    """How much longer you must expose because the sky is brighter.

    Background-limited imaging: the time to a fixed SNR scales with the sky
    FLUX, so each magnitude of sky lost costs a factor 2.512 in exposure. This
    is the number that turns "21.0 instead of 21.9" into something an imager
    can act on.
    """
    return float(10 ** (0.4 * (reference_mag - mag_sky)))
