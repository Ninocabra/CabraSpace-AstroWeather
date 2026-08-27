"""Predicted clear-sky brightness, and the cloud signal hiding under it.

The photometer measures how bright the sky is, not whether it is cloudy. Taken
raw, its variability is dominated by the Moon: nights that looked "unstable"
in a first pass turned out to be nights at 83-96 % illumination, while the
"stable" ones were new Moon. Any cloud detector built on the raw series is a
Moon detector.

Subtracting the modelled clear sky removes that. What is left -- measured minus
predicted -- is dominated by clouds, and it can be run over the whole archive
back to 2019, which is the only way this project gets historical cloud truth
for a site whose IR sensors do not report (SPEC v8 section 3.1, note 6).

Sign convention, worth stating because it is counter-intuitive: magnitudes run
backwards, so a POSITIVE residual means the sky was DARKER than predicted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from astropy.coordinates import AltAz, get_body, get_sun
from astropy.time import Time

from .ephem import location
from .moonsky import mag_to_nl, moon_brightness, nl_to_mag
from .site import NERPIO, Site

# Twilight, FITTED to this site rather than assumed: 646 moonless samples from
# the TESS archive between -20 and -8 degrees of Sun altitude give a residual
# flux falling by a factor e every 1.22 degrees. The first version of this
# function was a guessed exponential and it overestimated twilight brightness
# by 2.5-3 magnitudes, which showed up immediately as a huge positive residual
# in exactly that band.
TWILIGHT_LN_SLOPE = 0.820
TWILIGHT_LN_OFFSET = -9.02
# Below this the fitted flux is far under the natural sky and adds nothing.
TWILIGHT_FLOOR = -20.0


def twilight_magnitude(sun_altitude_deg: float, dark_sky_mag: float) -> float:
    """Sky brightness contributed by residual twilight, in mag/arcsec^2.

    Measured shape, not a spectral model: a proper treatment (Patat 2006, or a
    SkyCalc grid) is still an open objective in SPEC 2.2, and would also carry
    the azimuth dependence this ignores.
    """
    if sun_altitude_deg <= TWILIGHT_FLOOR:
        return float("inf")           # no measurable contribution
    flux = math.exp(TWILIGHT_LN_SLOPE * sun_altitude_deg + TWILIGHT_LN_OFFSET)
    if flux <= 0:
        return float("inf")
    return -2.5 * math.log10(flux)


@dataclass(frozen=True)
class ClearSky:
    t_utc: datetime
    sun_altitude: float
    moon_altitude: float
    moon_phase_angle: float
    predicted_mag: float

    @property
    def is_night(self) -> bool:
        return self.sun_altitude < -18.0


def predict(times: list[datetime], site: Site = NERPIO,
            *, dark_sky_mag: float | None = None,
            extinction_k: float = 0.18) -> list[ClearSky]:
    """Clear-sky brightness at the zenith for each time.

    Vectorised over the whole list: the ephemerides dominate the cost, and the
    archive is measured in hundreds of thousands of samples.
    """
    dark_sky_mag = dark_sky_mag if dark_sky_mag is not None else site.b_ref_mag
    when = Time([t.astimezone(timezone.utc) for t in times])
    frame = AltAz(obstime=when, location=location(site))

    sun = get_sun(when)
    moon = get_body("moon", when)
    sun_altaz = sun.transform_to(frame)
    moon_altaz = moon.transform_to(frame)
    # Phase angle: zero at full Moon, NOT the elongation (see ephem.py).
    phase_angles = 180.0 - moon.separation(sun).deg

    results = []
    for index, t in enumerate(times):
        sun_alt = float(sun_altaz.alt.deg[index])
        moon_alt = float(moon_altaz.alt.deg[index])
        alpha = float(phase_angles[index])

        components = [dark_sky_mag]
        # The photometer looks up, so the separation is the Moon's zenith angle.
        moon_light = moon_brightness(
            phase_angle_deg=alpha,
            separation_deg=max(1.0, 90.0 - moon_alt),
            moon_altitude_deg=moon_alt,
            target_altitude_deg=90.0,
            extinction_k=extinction_k,
        )
        if moon_light.physical_nl > 0:
            components.append(moon_light.physical_mag)
        twilight = twilight_magnitude(sun_alt, dark_sky_mag)
        if twilight != float("inf"):
            components.append(twilight)

        total_nl = sum(mag_to_nl(m) for m in components if m != float("inf"))
        results.append(ClearSky(t, sun_alt, moon_alt, alpha, nl_to_mag(total_nl)))
    return results


def residuals(measurements: list[tuple[datetime, float]], site: Site = NERPIO,
              **kwargs) -> list[tuple[datetime, float, float, float]]:
    """(time, measured, predicted, residual) with residual = measured - predicted.

    Positive means darker than a clear sky should be; negative means brighter,
    which under a dark site with distant towns is the signature of cloud
    reflecting light pollution back down.
    """
    times = [t for t, _ in measurements]
    predictions = predict(times, site, **kwargs)
    return [
        (t, value, p.predicted_mag, value - p.predicted_mag)
        for (t, value), p in zip(measurements, predictions)
    ]


def cloud_score(residual_values: list[float]) -> float | None:
    """How cloudy a night was, from the scatter of its residuals.

    Scatter rather than level: clouds move. A clear night sits on the model
    with a few hundredths of a magnitude of noise, whatever the Moon is doing.
    """
    clean = [r for r in residual_values if r is not None and np.isfinite(r)]
    if len(clean) < 10:
        return None
    return float(np.std(clean))
