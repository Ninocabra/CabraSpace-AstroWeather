"""Atmospheric extinction and what it costs the observer (SPEC v8 section 2.1).

Transparency is the quiet factor: unlike clouds it does not stop the night, it
just taxes every photon, and it does so without looking like anything is wrong.
A calima episode turns a "clear" night into one where faint targets simply
never build signal.

Three couplings this module exists to make explicit:

* Altitude. At 1650 m the Rayleigh term is already 15 % below sea level, and
  the non-dust aerosol below the site does not count at all.
* Wavelength. Dust extinguishes red and blue almost alike (a neutral, milky
  sky); smoke punishes the blue, which is where OIII and the B channel live.
* The Moon. k enters the moonlight model, so haze makes the Moon WORSE. That
  coupling lives in moonsky.py and is fed from here.

Measured on this site, 2451 moonless clear hours cross-matched with CAMS:

    AOD 0.00-0.05  ->  sky 21.593 mag/arcsec2
    AOD 0.15-0.25  ->  sky 21.720
    AOD 0.25+      ->  sky 21.738

The sky gets DARKER with more aerosol, which refutes the urban intuition that
haze brightens the night by bouncing distant town lights back down. At a site
this dark the aerosol extinguishes the natural sky -- airglow, zodiacal light,
integrated starlight, all of which arrive from above -- faster than it scatters
light pollution from below. The size checks out: 1.086 x dAOD predicts 0.25 mag
of extinction across that range and 0.145 was measured.

Two consequences. The photometer cannot be used as a haze detector by level
alone, since dust and a genuinely darker sky look the same. And an urban site
would show the opposite sign, so this is a site-specific result, not a general
one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Sea-level Rayleigh coefficient in V, and the atmospheric scale height.
K_RAYLEIGH_SEA_LEVEL = 0.1451
SCALE_HEIGHT_M = 7996.0
# Ozone is near enough constant for this purpose.
K_OZONE = 0.016
# Extinction in magnitudes per unit optical depth.
MAG_PER_TAU = 1.086
REFERENCE_WAVELENGTH_NM = 550.0

# Angstrom exponents: coarse dust scatters neutrally, fine smoke does not.
ALPHA_DUST = 0.4
ALPHA_SMOKE = 1.6
ALPHA_DEFAULT = 1.0

# Starting reference for a photometric night here. It is a STARTING value: the
# real one is the 20th percentile of k measured by the allsky, which does not
# exist yet (SPEC v8 section 1.1).
K_REFERENCE_START = 0.18


def rayleigh(elevation_m: float) -> float:
    """Molecular extinction in V at the site's altitude."""
    return K_RAYLEIGH_SEA_LEVEL * math.exp(-elevation_m / SCALE_HEIGHT_M)


def total_k(aod_site: float, elevation_m: float) -> float:
    """Total extinction coefficient in V, in magnitudes per airmass."""
    return rayleigh(elevation_m) + K_OZONE + MAG_PER_TAU * max(0.0, aod_site)


def k_at_wavelength(k_total_v: float, wavelength_nm: float, *,
                    aod_site: float, elevation_m: float,
                    angstrom: float = ALPHA_DEFAULT) -> float:
    """Extinction at another wavelength, scaling only the aerosol part.

    Rayleigh has its own steep colour dependence; lumping everything under one
    Angstrom exponent would make the molecular term wander.
    """
    aerosol_v = MAG_PER_TAU * max(0.0, aod_site)
    aerosol = aerosol_v * (wavelength_nm / REFERENCE_WAVELENGTH_NM) ** (-angstrom)
    molecular = rayleigh(elevation_m) * (wavelength_nm / REFERENCE_WAVELENGTH_NM) ** -4
    return molecular + K_OZONE + aerosol


def angstrom_from_species(aod_total: float, aod_dust: float | None) -> float:
    """Blend the dust and fine-mode exponents by how much dust there is.

    With no species split we fall back to a neutral value and the caller can
    see that in the flag rather than guessing.
    """
    if not aod_total or aod_dust is None:
        return ALPHA_DEFAULT
    dust_fraction = min(1.0, max(0.0, aod_dust / aod_total))
    return ALPHA_DUST * dust_fraction + ALPHA_SMOKE * (1 - dust_fraction)


def transmission(k: float, airmass: float) -> float:
    """Fraction of the target's light that survives the atmosphere."""
    return float(10 ** (-0.4 * k * airmass))


def quality(k: float, k_reference: float = K_REFERENCE_START) -> float:
    """q_transp in 0..1: how this night compares with a photometric one."""
    return float(min(1.0, 10 ** (-0.4 * (k - k_reference))))


def exposure_penalty(k: float, airmass: float,
                     k_reference: float = K_REFERENCE_START) -> float:
    """How much longer you must integrate, versus a reference night at zenith.

    The number an imager actually cares about: extinction costs signal, and
    signal-to-noise grows as the square root of time, so a 0.3 mag loss is not
    30 % more exposure but roughly 75 % more.
    """
    lost_mag = (k * airmass) - (k_reference * 1.0)
    return float(10 ** (0.8 * max(0.0, lost_mag)))


@dataclass(frozen=True)
class Transparency:
    """The transparency verdict for one hour, with its reasons."""

    aod_total: float | None
    aod_dust: float | None
    aod_site: float
    k_total: float
    angstrom: float
    quality: float
    dust_alert: bool
    species_resolved: bool

    @property
    def verdict(self) -> str:
        if self.dust_alert:
            return (f"calima (AOD polvo {self.aod_dust:.2f}): cielo lechoso, "
                    "la Luna molesta más y los objetos débiles no cuajan")
        if self.quality > 0.9:
            return "noche transparente"
        if self.quality > 0.7:
            return "transparencia media: banda estrecha bien, banda ancha profunda no"
        return f"transparencia pobre (k={self.k_total:.2f})"

    @property
    def color_note(self) -> str:
        """What the aerosol does to colour, which decides the filter."""
        if self.angstrom < 0.7:
            return "extincion neutra (polvo): afecta igual a R y a B"
        if self.angstrom > 1.3:
            return "extincion selectiva (humo/fina): castiga el azul y el OIII"
        return "extincion mixta"


def evaluate(*, aod_total: float | None, aod_dust: float | None,
             elevation_m: float, delta_h_m: float,
             scale_height_m: float = 1700.0,
             dust_alert_threshold: float = 0.15,
             k_reference: float = K_REFERENCE_START) -> Transparency:
    """Full transparency assessment for one hour.

    The site correction is by species: dust rides above the observatory and is
    not scaled, everything else is reduced by the height of the site over the
    model topography.
    """
    if aod_total is None:
        aod_site = 0.0
    elif aod_dust is None:
        aod_site = aod_total * math.exp(-delta_h_m / scale_height_m)
    else:
        aod_site = aod_dust + max(0.0, aod_total - aod_dust) * math.exp(
            -delta_h_m / scale_height_m)

    k = total_k(aod_site, elevation_m)
    return Transparency(
        aod_total=aod_total,
        aod_dust=aod_dust,
        aod_site=aod_site,
        k_total=k,
        angstrom=angstrom_from_species(aod_total or 0.0, aod_dust),
        quality=quality(k, k_reference),
        dust_alert=bool(aod_dust is not None and aod_dust > dust_alert_threshold),
        species_resolved=aod_dust is not None,
    )
