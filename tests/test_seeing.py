"""Tests for the turbulence model (SPEC v8 section 2.5)."""

import numpy as np
import pytest

from astroweather.seeing import (
    TERCILE_GOOD, cn2_profile, integrate, jet_wind, richardson_features,
    wind_components,
)


def test_the_profile_reproduces_the_canonical_hv57_values():
    """HV5/7 is the textbook case: r0 near 5 cm and seeing near 2 arcsec."""
    canonical = integrate(27.0, boundary_amplitude=1.7e-14)
    assert canonical.fried_r0_cm == pytest.approx(4.9, abs=0.4)
    assert canonical.seeing_arcsec == pytest.approx(2.07, abs=0.15)


def test_a_stronger_jet_degrades_every_parameter():
    calm, windy = integrate(8.0), integrate(45.0)
    assert windy.seeing_arcsec > calm.seeing_arcsec
    assert windy.isoplanatic_arcsec < calm.isoplanatic_arcsec
    assert windy.coherence_time_ms < calm.coherence_time_ms   # lucky imaging dies


def test_heights_are_above_the_site_not_above_the_sea():
    """With h^10 in the jet term, the two conventions are kilometres apart.

    Feeding heights above sea level to a site at 1650 m shifts the whole jet
    layer, and the term is steep enough that it changes the answer.
    """
    heights_agl = np.array([8000.0, 10000.0])
    correct = cn2_profile(heights_agl, 40.0)
    as_if_asl = cn2_profile(heights_agl + 1650.0, 40.0)
    # atol must be zero: Cn^2 is around 1e-17, and numpy's default absolute
    # tolerance of 1e-8 would call every profile identical.
    assert not np.allclose(correct, as_if_asl, rtol=0.05, atol=0.0)
    assert abs(as_if_asl[1] / correct[1] - 1) > 0.10


def test_the_boundary_layer_dominates_near_the_ground():
    profile = cn2_profile(np.array([50.0, 500.0, 3000.0]), 20.0)
    assert profile[0] > profile[1] > profile[2]


def test_the_jet_term_peaks_around_ten_kilometres():
    """W^2 h^10 exp(-h/1000) puts the free-atmosphere turbulence at 9-12 km."""
    heights = np.linspace(1000.0, 20000.0, 400)
    jet_only = cn2_profile(heights, 60.0) - cn2_profile(heights, 0.0)
    peak = heights[int(np.argmax(jet_only))]
    assert 9000 < peak < 12000


def test_jet_wind_weights_layers_by_thickness():
    """A flat mean over pressure levels over-weights the low stratosphere."""
    winds = {500: 10.0, 300: 40.0, 250: 40.0, 200: 40.0}
    heights = {500: 5500.0, 300: 9000.0, 250: 10400.0, 200: 11800.0}
    weighted = jet_wind(winds, heights)
    assert weighted is not None
    assert 10 < weighted < 40


def test_jet_wind_needs_at_least_two_levels():
    assert jet_wind({250: 30.0}) is None


def test_seeing_is_published_as_a_tercile_with_a_band():
    """The specification forbids selling an arcsecond that is not verified."""
    quiet = integrate(10.0)
    assert quiet.tercile == "favorable"
    assert "experimental" in quiet.verdict
    low, high = quiet.range_arcsec
    assert low < quiet.seeing_arcsec < high
    assert quiet.seeing_arcsec <= TERCILE_GOOD


def test_seeing_degrades_away_from_the_zenith():
    """X^0.6, so a target at airmass 2 is a fifth worse."""
    turbulence = integrate(20.0)
    assert turbulence.at_airmass(2.0) == pytest.approx(
        turbulence.seeing_arcsec * 2.0 ** 0.6, rel=1e-6)
    assert turbulence.at_airmass(1.0) == pytest.approx(turbulence.seeing_arcsec)


def test_wind_components_follow_the_meteorological_convention():
    """A northerly (0 deg) blows FROM the north, so v is negative."""
    u, v = wind_components(10.0, 0.0)
    assert u == pytest.approx(0.0, abs=1e-9)
    assert v == pytest.approx(-10.0)
    assert wind_components(None, 0.0) == (None, None)


def test_richardson_flags_shear_as_risk_not_as_a_multiplier():
    """Ri goes out as a feature and a flag; the multiplier competes in shadow."""
    sheared = [
        {"height": 3000.0, "theta": 300.0, "u": 5.0, "v": 0.0},
        {"height": 5000.0, "theta": 302.0, "u": 45.0, "v": 0.0},
    ]
    stable = [
        {"height": 3000.0, "theta": 300.0, "u": 5.0, "v": 0.0},
        {"height": 5000.0, "theta": 320.0, "u": 6.0, "v": 0.0},
    ]
    assert richardson_features(sheared)["orographic_risk"] == "high"
    assert richardson_features(stable)["orographic_risk"] == "low"
    assert "ri_min" in richardson_features(sheared)
    # No multiplier anywhere in the output: that was the point of the change.
    assert "beta" not in richardson_features(sheared)


def test_richardson_admits_when_it_cannot_tell():
    assert richardson_features([])["orographic_risk"] == "unknown"
