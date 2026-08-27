"""Moonlit sky brightness, Krisciunas & Schaefer 1991 (SPEC v8 section 2.2).

The model ESO and Gemini planners use, implemented with the three traps the
specification calls out, each of which is silent if you get it wrong:

* Below the horizon the formulae keep returning positive brightness, because
  X(Z) stays defined past 90 degrees. The physical value is zero there, and the
  smooth roll-off some tools apply is presentation only -- it must never reach
  mag_sky, the target gates or the SNR hours.
* The published scattering function only holds for separations of 10 degrees or
  more; closer in, the paper gives a different expression.
* The extinction coefficient enters the model, so haze makes the Moon WORSE,
  not better. That is what couples this module to the aerosol one.

Declared accuracy is 8-23 %, fitted at Mauna Kea with k around 0.11: with dust
overhead this extrapolates, which is why the local photometer has the last word.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Conversion between nanolamberts and V magnitudes per square arcsecond.
NL_SCALE = 34.08
NL_A, NL_B = 20.7233, 0.92104
# Below this altitude the Moon contributes nothing at all.
HORIZON_DEG = 0.0
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
    """Rayleigh plus Mie scattering as a function of Moon-target separation."""
    if separation_deg < SMALL_SEPARATION_DEG:
        # The paper's small-angle form; without it a target a few degrees from
        # the Moon gets a brightness that is simply wrong.
        return 6.2e7 / max(separation_deg, 0.25) ** 2
    rho = math.radians(separation_deg)
    return (10 ** 5.36) * (1.06 + math.cos(rho) ** 2) + 10 ** (6.15 - separation_deg / 40.0)


@dataclass(frozen=True)
class MoonBrightness:
    """What the Moon adds at one point of the sky, physics and display apart."""

    physical_nl: float        # what enters mag_sky, gates and SNR hours
    display_nl: float         # the same, smoothed for plotting only
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
    smooth_below_horizon: bool = True,
) -> MoonBrightness:
    """Scattered moonlight at the target, in nanolamberts.

    `extinction_k` is the total coefficient from the aerosol module: raising it
    for haze makes the Moon brighter, which is the physically correct and
    frequently forgotten coupling.
    """
    if target_altitude_deg <= 0:
        return MoonBrightness(0.0, 0.0, moon_altitude_deg, separation_deg)

    def brightness_at(moon_alt: float) -> float:
        illuminance = 10 ** (-0.4 * (moon_magnitude(phase_angle_deg) + 16.57))
        moon_extinction = 10 ** (-0.4 * extinction_k * airmass_scattering(moon_alt))
        target_term = 1.0 - 10 ** (-0.4 * extinction_k
                                   * airmass_scattering(target_altitude_deg))
        return scattering(separation_deg) * illuminance * moon_extinction * target_term

    if moon_altitude_deg > HORIZON_DEG:
        value = brightness_at(moon_altitude_deg)
        return MoonBrightness(value, value, moon_altitude_deg, separation_deg)

    # Below the horizon the physical contribution is zero, full stop.
    display = 0.0
    if smooth_below_horizon and moon_altitude_deg >= -6.0:
        # Presentation only: keeps charts from stepping at moonset. It is
        # returned in its own field so it can never leak into the physics.
        display = brightness_at(HORIZON_DEG) * math.exp(moon_altitude_deg / 1.5)
    return MoonBrightness(0.0, display, moon_altitude_deg, separation_deg)


def sky_magnitude(components_mag: list[float]) -> float:
    """Combine sky background components. They add in flux, never in magnitudes."""
    total = sum(mag_to_nl(m) for m in components_mag if m != float("inf"))
    return nl_to_mag(total) if total > 0 else float("inf")
