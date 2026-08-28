"""Moonlit sky brightness: the structure of Krisciunas & Schaefer 1991, with
this site's own scattering law (SPEC v8 section 2.2).

The model ESO and Gemini planners use, implemented with the traps the
specification calls out, each of which is silent if you get it wrong:

* The published scattering function only holds for separations of 10 degrees or
  more; closer in, the paper gives a different expression.
* The extinction coefficient enters the model, so haze makes the Moon WORSE,
  not better. That is what couples this module to the aerosol one.

Declared accuracy is 8-23 %, fitted at Mauna Kea with k around 0.11. At 1650 m
with Saharan dust overhead that extrapolation FAILS, and by a lot: measured
here the published function is short by a factor running from 1.2 with the Moon
low to 5.5 with the Moon high. So `scattering` below is kept as the published
Mauna Kea reference -- it is what the tests compare against -- while what the
model actually uses is `moonscatter.scattering`, measured on this site's own
seven years of photometry.

The third trap in the original list of three was wrong, and the archive is what
said so. It read: "below the horizon the physical value is zero, and the smooth
roll-off some tools apply is presentation only". It is not zero. With the Moon
geometrically invisible the sky here is still measurably brighter, decaying
with the shadow height of the atmosphere rather than switching off, and the
roll-off that was quarantined as decoration was standing in for real physics
with an invented scale. It is now measured, physical, and in
`moonscatter.beam_transmission`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .moonscatter import beam_transmission
from .moonscatter import scattering as site_scattering

# Conversion between nanolamberts and V magnitudes per square arcsecond.
NL_SCALE = 34.08
NL_A, NL_B = 20.7233, 0.92104
# Separation below which the paper switches to its small-angle expression.
SMALL_SEPARATION_DEG = 10.0


def nl_to_mag(brightness_nl: float) -> float:
    """Nanolamberts to V mag/arcsec^2."""
    if brightness_nl <= 0:
        return float("inf")
    return (NL_A - math.log(brightness_nl / NL_SCALE)) / NL_B


def mag_to_nl(magnitude: float) -> float:
    """V mag/arcsec^2 to nanolamberts. Sky background adds in FLUX, never in mag."""
    return NL_SCALE * math.exp(NL_A - NL_B * magnitude)


def airmass_scattering(altitude_deg: float) -> float:
    """X(Z) as K&S define it for scattering: bounded at 5.0 on the horizon.

    This is not Kasten-Young. The bound is deliberate in the paper, so it is
    kept rather than "fixed" with the photometric airmass, which plays another
    role entirely (SPEC v8 section 2.2).
    """
    zenith = math.radians(90.0 - altitude_deg)
    value = 1.0 - 0.96 * math.sin(zenith) ** 2
    return 5.0 if value <= 0.04 else value ** -0.5


def moon_magnitude(phase_angle_deg: float) -> float:
    """Apparent V magnitude of the Moon. Alpha is 0 at FULL Moon."""
    alpha = abs(phase_angle_deg)
    return -12.73 + 0.026 * alpha + 4.0e-9 * alpha ** 4


def scattering(separation_deg: float) -> float:
    """Rayleigh plus Mie scattering, as PUBLISHED for Mauna Kea.

    Kept as the reference the site's own law is measured against; the model
    itself uses `moonscatter.scattering`.
    """
    if separation_deg < SMALL_SEPARATION_DEG:
        # The paper's small-angle form; without it a target a few degrees from
        # the Moon gets a brightness that is simply wrong.
        return 6.2e7 / max(separation_deg, 0.25) ** 2
    rho = math.radians(separation_deg)
    return (10 ** 5.36) * (1.06 + math.cos(rho) ** 2) + 10 ** (6.15 - separation_deg / 40.0)


@dataclass(frozen=True)
class MoonBrightness:
    """What the Moon adds at one point of the sky."""

    physical_nl: float        # what enters mag_sky, gates and SNR hours
    display_nl: float         # kept equal: there is no longer anything to smooth
    moon_altitude: float
    separation: float

    @property
    def physical_mag(self) -> float:
        return nl_to_mag(self.physical_nl) if self.physical_nl > 0 else float("inf")


def moon_brightness(
    *,
    phase_angle_deg: float,
    separation_deg: float,
    moon_altitude_deg: float,
    target_altitude_deg: float,
    extinction_k: float = 0.18,
) -> MoonBrightness:
    """Scattered moonlight at the target, in nanolamberts.

    `extinction_k` is the total coefficient from the aerosol module: raising it
    for haze makes the Moon brighter, which is the physically correct and
    frequently forgotten coupling. The site's scattering law was fitted at
    k = 0.18, so a different k moves the answer along a direction the fit never
    saw -- the coupling is kept because it is real, not because it is validated.
    """
    if target_altitude_deg <= 0:
        return MoonBrightness(0.0, 0.0, moon_altitude_deg, separation_deg)

    # Carries the horizon: 1 with the Moon high, falling continuously through
    # moonset to zero once the whole air column above the site is in shadow.
    transmission = beam_transmission(moon_altitude_deg)
    if transmission <= 0.0:
        return MoonBrightness(0.0, 0.0, moon_altitude_deg, separation_deg)

    illuminance = 10 ** (-0.4 * (moon_magnitude(phase_angle_deg) + 16.57))
    # Clamped at the horizon: X(Z) keeps returning finite values past 90 degrees
    # and they mean nothing. Below it the physics lives in `transmission`.
    moon_extinction = 10 ** (-0.4 * extinction_k
                             * airmass_scattering(max(moon_altitude_deg, 0.0)))
    target_term = 1.0 - 10 ** (-0.4 * extinction_k
                               * airmass_scattering(target_altitude_deg))
    value = (site_scattering(separation_deg) * illuminance * moon_extinction
             * target_term * transmission)
    return MoonBrightness(value, value, moon_altitude_deg, separation_deg)


def sky_magnitude(components_mag: list[float]) -> float:
    """Combine sky background components. They add in flux, never in magnitudes."""
    total = sum(mag_to_nl(m) for m in components_mag if m != float("inf"))
    return nl_to_mag(total) if total > 0 else float("inf")
