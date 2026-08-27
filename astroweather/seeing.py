"""Optical turbulence from NWP winds (SPEC v8 section 2.5).

The most uncertain thing this system predicts, and the one where the
specification is most insistent about not pretending otherwise. Even ALTA and
FATE, running mesoscale models dedicated to the task, live with 20-30 % error.
A 25 km global model over a mountain site will get the REGIME right -- a night
of jet against a quiet one -- and the exact arcsecond wrong.

So the physics is implemented in full, archived in full, and PUBLISHED AS
TERCILES until the local FWHM archive can show a measured MAE. The number is
computed; it is just not sold.

Three contracts that are easy to get wrong and silent when you do:

* h is height ABOVE THE SITE, not above sea level. With h^10 in the jet term,
  confusing them moves the turbulence by kilometres.
* W is weighted by the THICKNESS of each layer. Pressure levels are not
  equally spaced in height, and a flat mean over-weights the low stratosphere.
* v_eff for the coherence time is weighted by Cn^2 itself, not by any plain
  average of the wind.

WHERE THIS STANDS AGAINST REALITY, as of the first night with both numbers.
On 2026-08-26 the model saw a 36 m/s jet and predicted 1.21 arcsec at the
zenith; the subframes measured 3.16. The gap has two possible owners and the
archive cannot yet separate them:

* A, the boundary-layer amplitude, is a guess (1.7e-15, "good mountain site").
  Reaching 3.16 arcsec would need roughly 4e-14, which is HV5/7 territory and
  implausible for this site.
* The measured FWHM is image quality, not seeing: it carries focus, optics,
  guiding and mount. Subtracting a 2.5 arcsec instrumental floor in quadrature
  leaves 1.93 arcsec of atmosphere, which is a far more believable place to
  land.

The second explanation is very likely most of it, which is exactly why A is NOT
being tuned to close the gap: fitting it now would calibrate the telescope, not
the sky. A needs a low percentile per night per setup_id over months, or a DIMM.
Until then the seeing goes out as a tercile with a band, never as a number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

REFERENCE_WAVELENGTH_M = 500e-9
RADIANS_TO_ARCSEC = 206264.806

# Hufnagel-Valley constants. A is the boundary-layer amplitude and THE
# parameter to calibrate per site: 1.7e-14 is HV5/7 (strong conditions), a good
# mountain site sits one to two orders below.
HV_A_DEFAULT = 1.7e-15
HV_JET_COEFFICIENT = 5.94e-53
HV_MID_COEFFICIENT = 2.7e-16

# Pressure levels used for the jet wind, and their typical heights ASL.
JET_LEVELS_HPA = (500, 300, 250, 200)
# Terciles of predicted seeing, from the model's own distribution over the
# archive rather than from round numbers.
TERCILE_GOOD = 1.35
TERCILE_POOR = 1.90


def jet_wind(winds_by_level: dict[int, float],
             heights_by_level: dict[int, float] | None = None) -> float | None:
    """RMS wind of the 5-20 km layer, weighted by each layer's THICKNESS.

    A flat RMS over pressure levels over-weights the low stratosphere, where
    the levels crowd together in height.
    """
    levels = [p for p in JET_LEVELS_HPA if winds_by_level.get(p) is not None]
    if len(levels) < 2:
        return None
    if heights_by_level:
        heights = [heights_by_level.get(p) for p in levels]
        if any(h is None for h in heights):
            heights = None
    else:
        heights = None

    speeds = np.array([winds_by_level[p] for p in levels], dtype=float)
    if heights is None:
        # Without geopotential, weight by the log-pressure spacing, which is
        # the honest proxy for thickness.
        weights = np.abs(np.gradient(np.log([float(p) for p in levels])))
    else:
        weights = np.abs(np.gradient(np.array(heights, dtype=float)))
    weights = np.where(weights > 0, weights, 1.0)
    return float(np.sqrt(np.sum(speeds ** 2 * weights) / np.sum(weights)))


def cn2_profile(heights_agl: np.ndarray, jet_speed: float,
                boundary_amplitude: float = HV_A_DEFAULT) -> np.ndarray:
    """Hufnagel-Valley Cn^2, with heights ABOVE THE SITE."""
    h = np.maximum(heights_agl, 1.0)
    return (HV_JET_COEFFICIENT * (jet_speed / 27.0) ** 2 * h ** 10 * np.exp(-h / 1000.0)
            + HV_MID_COEFFICIENT * np.exp(-h / 1500.0)
            + boundary_amplitude * np.exp(-h / 100.0))


@dataclass(frozen=True)
class Turbulence:
    """Integrated turbulence parameters, all at 500 nm and at the zenith."""

    fried_r0_cm: float
    seeing_arcsec: float
    isoplanatic_arcsec: float
    coherence_time_ms: float
    jet_speed: float
    boundary_amplitude: float

    @property
    def tercile(self) -> str:
        """What gets published: the regime, not the decimal."""
        if self.seeing_arcsec <= TERCILE_GOOD:
            return "favorable"
        if self.seeing_arcsec >= TERCILE_POOR:
            return "desfavorable"
        return "normal"

    @property
    def range_arcsec(self) -> tuple[float, float]:
        """Honest uncertainty band: professional systems live with 20-30 %."""
        return (self.seeing_arcsec * 0.75, self.seeing_arcsec * 1.30)

    @property
    def verdict(self) -> str:
        low, high = self.range_arcsec
        return (f"seeing {self.tercile} (estimacion experimental "
                f"{low:.1f}-{high:.1f}\", jet {self.jet_speed:.0f} m/s)")

    def at_airmass(self, airmass: float) -> float:
        """Seeing degrades as X^0.6 away from the zenith."""
        return float(self.seeing_arcsec * airmass ** 0.6)


def integrate(jet_speed: float, *, boundary_amplitude: float = HV_A_DEFAULT,
              wind_profile=None, top_m: float = 25000.0,
              samples: int = 4000) -> Turbulence:
    """Turn a jet wind into r0, seeing, isoplanatic angle and coherence time."""
    heights = np.linspace(1.0, top_m, samples)
    cn2 = cn2_profile(heights, jet_speed, boundary_amplitude)
    wavenumber = (2 * math.pi / REFERENCE_WAVELENGTH_M) ** 2

    integral = np.trapezoid(cn2, heights)
    r0 = (0.423 * wavenumber * integral) ** -0.6
    seeing = 0.98 * REFERENCE_WAVELENGTH_M / r0 * RADIANS_TO_ARCSEC

    moment = np.trapezoid(cn2 * heights ** (5 / 3), heights)
    isoplanatic = (2.914 * wavenumber * moment) ** -0.6 * RADIANS_TO_ARCSEC

    # v_eff weighted by Cn^2, as the definition requires. Without a full wind
    # profile the jet speed above 5 km is the honest stand-in.
    if wind_profile is None:
        winds = np.where(heights > 5000, jet_speed, max(jet_speed * 0.2, 2.0))
    else:
        winds = wind_profile(heights)
    v_eff = (np.trapezoid(cn2 * winds ** (5 / 3), heights) / integral) ** 0.6
    tau0 = 0.314 * r0 / max(v_eff, 0.5)

    return Turbulence(
        fried_r0_cm=float(r0 * 100),
        seeing_arcsec=float(seeing),
        isoplanatic_arcsec=float(isoplanatic),
        coherence_time_ms=float(tau0 * 1000),
        jet_speed=float(jet_speed),
        boundary_amplitude=boundary_amplitude,
    )


def richardson_features(levels: list[dict]) -> dict:
    """Stability and shear features, PUBLISHED AS RISK, never as a multiplier.

    The classic Ri < 0.25 threshold belongs to the gradient Richardson number.
    Over layers 1.5-3 km thick any bulk threshold is a heuristic, so these go
    out as features and a risk flag while the multiplicative variant competes
    in the shadow (SPEC v8 section 2.5).
    """
    if len(levels) < 2:
        return {"orographic_risk": "unknown"}

    ordered = sorted(levels, key=lambda x: x["height"])
    numbers, shears = [], []
    for lower, upper in zip(ordered[:-1], ordered[1:]):
        dz = upper["height"] - lower["height"]
        if dz <= 0:
            continue
        # Potential temperature difference drives the stability term.
        d_theta = upper["theta"] - lower["theta"]
        mean_theta = 0.5 * (upper["theta"] + lower["theta"])
        n_squared = 9.81 / mean_theta * d_theta / dz
        du = (upper["u"] - lower["u"]) / dz
        dv = (upper["v"] - lower["v"]) / dz
        shear_squared = du ** 2 + dv ** 2
        shears.append(math.sqrt(shear_squared) * 1000)      # per km
        if shear_squared > 1e-10:
            numbers.append(n_squared / shear_squared)

    if not numbers:
        return {"orographic_risk": "unknown"}

    ri_min = float(min(numbers))
    unstable = sum(1 for r in numbers if r < 0.5) / len(numbers)
    risk = "high" if ri_min < 0.5 else ("medium" if ri_min < 1.0 else "low")
    return {
        "ri_min": ri_min,
        "fraction_layers_unstable": float(unstable),
        "max_shear_per_km": float(max(shears)) if shears else None,
        "orographic_risk": risk,
    }


def wind_components(speed: float | None, direction_deg: float | None):
    """Meteorological direction to u, v. Shear is VECTORIAL: turning counts."""
    if speed is None or direction_deg is None:
        return None, None
    radians = math.radians(direction_deg)
    return -speed * math.sin(radians), -speed * math.cos(radians)
