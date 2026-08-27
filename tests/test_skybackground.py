"""Tests for the sky background budget (SPEC v8 section 2.2)."""

import datetime as dt

import pytest

from astroweather.skybackground import (
    NATURAL_FLOOR_MAG, airglow_excess, budget, integration_penalty,
    zodiacal_excess,
)

NIGHT = dt.datetime(2026, 7, 15, 23, tzinfo=dt.timezone.utc)


def test_the_ecliptic_is_brighter_than_the_ecliptic_pole():
    """Measured on site: 0.27 mag of zodiacal light overhead at the ecliptic."""
    assert zodiacal_excess(0) > zodiacal_excess(45) > zodiacal_excess(89)
    assert zodiacal_excess(0) == pytest.approx(0.27, abs=0.02)


def test_the_zodiacal_term_decays_and_does_not_go_negative():
    for latitude in (0, 10, 30, 60, 90, -30):
        assert 0 <= zodiacal_excess(latitude) <= 0.4


def test_airglow_follows_solar_activity_slowly():
    """A slow term, never an hourly predictor."""
    assert airglow_excess(230) > airglow_excess(130) > airglow_excess(70)
    assert airglow_excess(130) == pytest.approx(0.0)
    assert airglow_excess(None) == 0.0
    # A whole solar cycle moves the sky by about a tenth of a magnitude.
    assert abs(airglow_excess(230) - airglow_excess(70)) < 0.25


def test_the_budget_says_where_every_magnitude_came_from():
    entry = budget([NIGHT])[0]
    assert entry.natural_floor == NATURAL_FLOOR_MAG
    assert entry.zodiacal <= 0            # brightening, so it lowers the number
    assert "suelo natural" in entry.breakdown
    assert 20.5 < entry.total_mag < 22.2


def test_light_pollution_adds_in_flux_not_in_magnitudes():
    clean = budget([NIGHT])[0]
    polluted = budget([NIGHT], light_pollution_mag=22.0)[0]
    assert polluted.total_mag < clean.total_mag
    # Two comparable sources brighten by less than a full magnitude.
    assert clean.total_mag - polluted.total_mag < 1.0


def test_each_magnitude_of_sky_costs_a_factor_in_exposure():
    """The number that turns 21.0 instead of 21.9 into something actionable."""
    assert integration_penalty(NATURAL_FLOOR_MAG) == pytest.approx(1.0)
    assert integration_penalty(NATURAL_FLOOR_MAG - 1) == pytest.approx(2.512, rel=1e-3)
    assert integration_penalty(NATURAL_FLOOR_MAG - 2) == pytest.approx(6.31, rel=1e-2)
