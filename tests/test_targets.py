"""Tests for the SNR-hours engine (SPEC v8 section 1.1)."""

import datetime as dt

import pytest

from astroweather.ephem import night_for
from astroweather.seeing import integrate
from astroweather.targets import (
    CATALOGUE, MOON_FILTER_FACTOR, SKY_GATE, Target, evaluate_night, hourly_yield,
)

FULL_MOON = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)
NEW_MOON = dt.datetime(2026, 9, 11, tzinfo=dt.timezone.utc)


def _run(day, probability=0.8):
    night = night_for(day)
    times = night.hours(60)
    return evaluate_night(CATALOGUE, times,
                          usable_probability=[probability] * len(times),
                          turbulence=integrate(20.0), aod_total=0.06, aod_dust=0.01)


def test_a_full_moon_night_is_a_narrowband_night():
    """The advice a good astrophotographer would give, derived from physics.

    Estuvo en xfail unas horas el 2026-08-28. Con la ley de dispersion lunar
    medida, el cielo con Luna llena alta es hasta 1,8 mag mas brillante de lo
    que decia el modelo, y el 0,12 por clase que el codigo usaba -- que no esta
    en SPEC -- dejaba de colar: el producto pasaba a decir que con Luna llena no
    se hace banda estrecha. Se arreglo adoptando los factores POR FILTRO de
    SPEC 2.3, que es el documento que manda.
    """
    results = {r.target.difficulty: r.q_conditional for r in _run(FULL_MOON)}
    assert results["narrowband"] > 1.0
    assert results["extremo_contraste"] == 0.0     # IFN under a full Moon: no
    assert results["bajo_contraste"] == 0.0


def test_a_new_moon_night_favours_everything_else():
    """And the advice must invert, or the engine is just saying one thing."""
    results = _run(NEW_MOON)
    best = results[0]
    assert best.target.difficulty in ("compacto_resolucion", "normal")
    assert best.q_conditional > 5.0


def test_the_moon_separation_is_what_kills_a_target_not_just_its_phase():
    """The directional model is the whole point: 14 degrees away is fatal."""
    results = {r.target.name: r for r in _run(FULL_MOON)}
    einstein = results["Cruz de Einstein (Q2237+0305)"]
    assert einstein.min_moon_separation < 20
    assert einstein.q_conditional == 0.0


def test_expected_hours_never_exceed_conditional_ones():
    """E[Q] is Q_cond weighted by the odds; it cannot be more optimistic."""
    for result in _run(NEW_MOON, probability=0.5):
        assert result.q_expected <= result.q_conditional + 1e-9


def test_certainty_is_worth_something():
    confident = _run(NEW_MOON, probability=0.95)[0]
    doubtful = _run(NEW_MOON, probability=0.20)[0]
    assert confident.q_expected > doubtful.q_expected
    assert confident.q_conditional == pytest.approx(doubtful.q_conditional)


def test_only_compact_targets_care_about_seeing():
    """alpha is what stops the engine treating a nebula like a lens."""
    sharp = dict(airmass=1.0, sky_mag=21.7, k_total=0.18, difficulty="normal")
    extended_good = hourly_yield(seeing_arcsec=1.0, alpha=0.0, **sharp)
    extended_bad = hourly_yield(seeing_arcsec=4.0, alpha=0.0, **sharp)
    compact_good = hourly_yield(seeing_arcsec=1.0, alpha=1.0, **sharp)
    compact_bad = hourly_yield(seeing_arcsec=4.0, alpha=1.0, **sharp)

    assert extended_good == extended_bad          # blur does not touch surface brightness
    assert compact_good > compact_bad * 3         # but it is everything for a point


def test_an_impossible_hour_scores_zero_not_a_little():
    """Partial credit for an impossible hour is worse than none: it accumulates."""
    assert hourly_yield(airmass=1.0, sky_mag=19.0, seeing_arcsec=2.0,
                        k_total=0.18, alpha=0.0, difficulty="extremo_contraste") == 0.0
    assert hourly_yield(airmass=1.0, sky_mag=21.6, seeing_arcsec=2.0,
                        k_total=0.18, alpha=0.0, difficulty="extremo_contraste") > 0.0


def test_narrowband_survives_moonlight_by_construction():
    assert MOON_FILTER_FACTOR["narrowband"] < 0.2
    assert MOON_FILTER_FACTOR["normal"] == 1.0
    assert SKY_GATE["extremo_contraste"] > SKY_GATE["normal"]


def test_every_verdict_carries_its_reason():
    for result in _run(FULL_MOON):
        assert result.reason
        assert result.verdict in ("recomendado", "posible", "poco rentable",
                                  "no observable")
