"""What is worth photographing tonight, and how much it will yield.

The product this whole system was built for (SPEC v8 section 1.1). Almost every
astronomy weather service answers "how will the sky be". This one answers "what
should I point at, and what will I actually get", which is a different question
and a much more useful one.

Two numbers per target, and both are published because they mean different
things:

    Q_cond  hours of reference-quality integration IF the sky cooperates
    E[Q]    the same, multiplied by the odds that it does

A target with a magnificent 3 hours behind a 20 % chance of clear sky is not
the same proposition as a modest 2 hours behind 90 %, and collapsing them into
one figure hides exactly the trade-off the observer is making.

The per-hour yield weighs three penalties against a reference night:

    extinction   10^(-0.8 k (X-1))          light lost on the way in
    background   B_ref / B_sky              faint targets drown in bright sky
    seeing       (FWHM_ref / FWHM)^(2 alpha) only compact targets care

alpha is what stops the engine treating everything alike: 0 for extended
nebulosity, whose surface brightness does not care about blur; 1 for compact
sources, where the noise in the aperture grows as FWHM^2 and a 1.5 vs 3 arcsec
night is a factor four in speed.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
from astropy.coordinates import AltAz, SkyCoord, get_body
from astropy.time import Time
import astropy.units as u

from .ephem import location, moon_phase_angle, moon_positions, night_for
from .extinction import evaluate as evaluate_transparency
from .moonsky import mag_to_nl, moon_brightness, nl_to_mag
from .seeing import Turbulence
from .site import NERPIO, Site
from .skybackground import NATURAL_FLOOR_MAG
from .skymodel import predict

# Reference night: the yield of one hour under these conditions is 1.0.
FWHM_REFERENCE = 2.0
K_REFERENCE = 0.18
# Below this altitude nothing is worth starting; extinction and differential
# refraction grow faster than the target rises.
MIN_ALTITUDE = 25.0
# Width of the transition around each class gate, and the most a good seeing
# night is allowed to be worth. Both exist because an uncalibrated model must
# not be able to produce a spectacular number (see hourly_yield).
GATE_SHOULDER_MAG = 0.4
MAX_SHARPNESS_GAIN = 2.0


def _shoulder(value: float, centre: float, width: float) -> float:
    """Smooth 0->1 ramp across `width`, reaching 0.5 exactly at `centre`."""
    if width <= 0:
        return 1.0 if value >= centre else 0.0
    t = (value - (centre - width / 2)) / width
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)          # smoothstep: flat at both ends

# Sky brightness each class of target NEEDS, in mag/arcsec^2 at its position.
# The continuous yield alone is not enough: it would happily offer four tenths
# of an hour of IFN under a full Moon, and there is no such thing. These are
# the sanity cuts of SPEC v8 section 1.2 -- an hour brighter than the class
# threshold counts as zero for that target, not as a little bit of progress.
# Fraction of the moonlight that survives each filter. A 3 nm narrowband
# passes a sliver of the lunar continuum, which is exactly why a full Moon
# night is a narrowband night. These are the fallback values of SPEC v8
# section 1.1, flagged as approximation_quality=low until the SkyCalc LUT
# replaces them: the real factor depends on phase, filter shape and QE.
MOON_FILTER_FACTOR = {
    "narrowband": 0.12,        # a 3-5 nm Ha/OIII/SII filter
    "compacto_resolucion": 1.0,
    "extremo_contraste": 1.0,
    "bajo_contraste": 1.0,
    "normal": 1.0,
}

SKY_GATE = {
    "extremo_contraste": 21.3,     # IFN, tidal tails, haloes
    "bajo_contraste": 20.9,        # faint broadband structure
    "normal": 20.0,
    "compacto_resolucion": 19.5,   # small and bright: tolerates sky, not blur
    "narrowband": 19.5,            # applied AFTER the filter factor above
}


@dataclass(frozen=True)
class Target:
    """A target with the two things that decide how it behaves tonight."""

    name: str
    ra_deg: float
    dec_deg: float
    kind: str
    # Morphology exponent: 0 extended, 0.5 intermediate, 1 compact.
    alpha: float
    # Curated difficulty, because a catalogue surface brightness is not
    # comparable across object classes (SPEC v8 section 1.1).
    difficulty: str
    note: str = ""

    def positions(self, times: list[dt.datetime], site: Site = NERPIO):
        when = Time([t.astimezone(dt.timezone.utc) for t in times])
        coordinates = SkyCoord(ra=self.ra_deg * u.deg, dec=self.dec_deg * u.deg)
        altaz = coordinates.transform_to(AltAz(obstime=when, location=location(site)))
        separations = coordinates.separation(get_body("moon", when)).deg
        return (np.asarray(altaz.alt.deg, dtype=float),
                np.asarray(altaz.az.deg, dtype=float),
                np.asarray(separations, dtype=float))


# A small curated list. Fifty well-understood objects beat twenty thousand with
# incomparable photometry, which is why this is hand-written.
CATALOGUE = [
    Target("IFN en M81/M82", 148.9, 69.1, "ifn", 0.0, "extremo_contraste",
           "el target mas exigente en contraste que existe: sin Luna o nada"),
    Target("M51 (halo y puente de marea)", 202.47, 47.19, "galaxy", 0.5,
           "bajo_contraste", "el nucleo sale casi siempre; el puente, no"),
    Target("M31", 10.68, 41.27, "galaxy", 0.5, "normal", ""),
    Target("M81", 148.89, 69.07, "galaxy", 0.5, "normal", ""),
    Target("NGC 7000 (Norteamerica)", 314.75, 44.52, "nebula", 0.0, "narrowband",
           "banda estrecha: aprovecha noches de Luna"),
    Target("Velo (NGC 6960)", 311.66, 30.72, "nebula", 0.0, "narrowband", ""),
    Target("M57 (Anillo)", 283.40, 33.03, "planetary", 1.0, "compacto_resolucion",
           "pequena: el seeing manda"),
    Target("Cruz de Einstein (Q2237+0305)", 340.13, 3.36, "lens", 1.0,
           "compacto_resolucion",
           "componentes a 1.8 arcsec: solo con seeing excepcional"),
    Target("M13", 250.42, 36.46, "cluster", 0.5, "normal",
           "poco exigente: buen plan de noche mediocre"),
    Target("M27 (Dumbbell)", 299.90, 22.72, "planetary", 0.5, "normal", ""),
]


@dataclass
class TargetNight:
    """What one target yields over one night."""

    target: Target
    q_conditional: float
    q_expected: float
    hours_above_minimum: float
    max_altitude: float
    min_moon_separation: float
    best_hour: dt.datetime | None
    reason: str
    # Hour by hour, what the engine actually saw. Exposed so the charts read
    # the SAME numbers the score came from: a view that recomputes them is a
    # view that will one day disagree with the recommendation it illustrates.
    trace: list[dict] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.hours_above_minimum == 0:
            return "no observable"
        if self.q_expected >= 1.5:
            return "recomendado"
        if self.q_expected >= 0.5:
            return "posible"
        return "poco rentable"


def hourly_yield(*, airmass: float, sky_mag: float, seeing_arcsec: float,
                 k_total: float, alpha: float, difficulty: str = "normal") -> float:
    """Fraction of a reference hour that this hour is worth for this target.

    Zero when the sky is brighter than the class can survive: partial credit
    for an impossible hour is worse than no credit, because it accumulates.
    """
    if airmass is None or airmass <= 0:
        return 0.0
    gate = SKY_GATE.get(difficulty, 20.0)
    # The gate used to be a step, and a step is a lie: an hour at 21.28 scored
    # zero while 21.30 scored 0.685 (external audit, 2026-08-27). Signal-to-
    # noise degrades smoothly, so the gate becomes a shoulder and keeps its
    # meaning at the 50 % point. The width is the scale of the yield function
    # itself: 0.4 mag is a factor 1.45 in background flux.
    admissible = _shoulder(sky_mag, gate, GATE_SHOULDER_MAG)
    if admissible <= 0.0:
        return 0.0
    extinction = 10 ** (-0.8 * k_total * (airmass - 1.0))
    background = min(1.0, 10 ** (0.4 * (sky_mag - NATURAL_FLOOR_MAG)))
    if alpha > 0:
        sharpness = (FWHM_REFERENCE / max(seeing_arcsec, 0.3)) ** (2 * alpha)
    else:
        sharpness = 1.0                 # extended targets do not care about blur
    return float(extinction * background * admissible
                 * min(sharpness, MAX_SHARPNESS_GAIN))


def evaluate_night(targets: list[Target], times: list[dt.datetime],
                   *, usable_probability: list[float],
                   turbulence: Turbulence | None = None,
                   aod_total: float | None = None, aod_dust: float | None = None,
                   site: Site = NERPIO) -> list[TargetNight]:
    """Score every target over the night, using the whole physics stack."""
    transparency = evaluate_transparency(
        aod_total=aod_total, aod_dust=aod_dust,
        elevation_m=site.site_elevation, delta_h_m=site.delta_h)
    # The floor must carry twilight, not just the natural background: the
    # product window opens an hour before sunset, and without the twilight term
    # those daylight hours look like pristine dark sky and score as usable.
    floors = {c.t_utc: c.predicted_mag for c in predict(times, site)}
    moon = {p.t_utc: p for p in moon_positions(times, site)}
    phase = moon_phase_angle(times[len(times) // 2])

    results = []
    for target in targets:
        altitudes, azimuths, separations = target.positions(times, site)
        conditional, expected, hours, best, best_value = 0.0, 0.0, 0.0, None, 0.0

        trace: list[dict] = []
        for index, when in enumerate(times):
            altitude = float(altitudes[index])
            if altitude < MIN_ALTITUDE:
                trace.append({"alt": round(altitude, 1),
                              "az": round(float(azimuths[index]), 1),
                              "yield": 0.0, "sky_mag": None,
                              "moon_sep": round(float(separations[index]), 1),
                              "gate_ok": False, "too_low": True})
                continue
            airmass = 1.0 / np.sin(np.radians(altitude))
            hours += 1.0 / len(times) * (
                (times[-1] - times[0]).total_seconds() / 3600)

            # predict() already carries the Moon AT THE ZENITH; here we need it
            # at the TARGET's real separation, which is the whole point of the
            # directional model. So the zenith moonlight is removed and the
            # target's own contribution added in its place.
            zenith_moon = moon_brightness(
                phase_angle_deg=phase,
                separation_deg=max(1.0, 90.0 - moon[when].altitude),
                moon_altitude_deg=moon[when].altitude,
                target_altitude_deg=90.0,
                extinction_k=transparency.k_total,
            )
            target_moon = moon_brightness(
                phase_angle_deg=phase,
                separation_deg=float(separations[index]),
                moon_altitude_deg=moon[when].altitude,
                target_altitude_deg=altitude,
                extinction_k=transparency.k_total,
            )
            # The filter cuts EVERYTHING outside its passband, not just the
            # Moon: twilight, airglow and zodiacal light are broadband too.
            # Attenuating only the lunar term (as this did until the audit of
            # 2026-08-27) left narrowband facing an unfiltered sky and zeroed
            # it through both twilights -- backwards, since twilight narrowband
            # is precisely what the technique is good at.
            filter_factor = MOON_FILTER_FACTOR.get(target.difficulty, 1.0)
            flux = max(mag_to_nl(floors[when]) - zenith_moon.physical_nl, 1e-6)
            flux += target_moon.physical_nl
            sky_mag = nl_to_mag(flux * filter_factor)

            # The seeing model is uncalibrated and known to run OPTIMISTIC
            # (1.21 predicted where 3.16 was measured), so the yield is taken
            # from the pessimistic edge of the published band, never from the
            # point value. Using the loose number contradicted our own rule of
            # publishing terciles, and swung compact targets by a factor 3.7.
            seeing = ((turbulence.range_arcsec[1] * airmass ** 0.6) if turbulence
                      else FWHM_REFERENCE * airmass ** 0.6)
            value = hourly_yield(airmass=airmass, sky_mag=sky_mag,
                                 seeing_arcsec=seeing,
                                 k_total=transparency.k_total, alpha=target.alpha,
                                 difficulty=target.difficulty)
            step = (times[1] - times[0]).total_seconds() / 3600 if len(times) > 1 else 1
            conditional += value * step
            expected += value * step * usable_probability[index]
            if value > best_value:
                best_value, best = value, when
            trace.append({"alt": round(altitude, 1),
                          "az": round(float(azimuths[index]), 1),
                          "yield": round(value, 3),
                          "sky_mag": round(sky_mag, 2),
                          "moon_sep": round(float(separations[index]), 1),
                          "gate_ok": value > 0.0, "too_low": False})

        results.append(TargetNight(
            target=target,
            q_conditional=conditional,
            q_expected=expected,
            hours_above_minimum=hours,
            max_altitude=float(altitudes.max()),
            min_moon_separation=float(separations.min()),
            best_hour=best,
            reason=_reason(target, conditional, hours, float(separations.min()),
                           transparency, turbulence),
            trace=trace,
        ))
    return sorted(results, key=lambda r: r.q_expected, reverse=True)


def _reason(target: Target, conditional: float, hours: float,
            moon_separation: float, transparency, turbulence) -> str:
    """Every verdict carries its why, in one sentence."""
    if hours == 0:
        return f"no sube de {MIN_ALTITUDE:.0f} grados esta noche"
    if moon_separation < 30 and target.difficulty == "extremo_contraste":
        return f"la Luna pasa a {moon_separation:.0f} grados: imposible para SB extremo"
    if transparency.dust_alert and target.alpha < 0.5:
        return "calima: los objetos extensos y debiles no cuajan"
    if target.alpha >= 1.0 and turbulence and turbulence.tercile == "desfavorable":
        return f"seeing {turbulence.tercile}: los detalles finos se pierden"
    if conditional <= 0.01:
        gate = SKY_GATE.get(target.difficulty, 20.0)
        return (f"el cielo no baja de {gate:.1f} mag/arcsec2 esta noche: "
                f"esta clase no cuaja")
    if conditional < 0.3:
        return "poca altura o cielo demasiado brillante para rendir"
    return f"{conditional:.1f} h equivalentes si despeja"
