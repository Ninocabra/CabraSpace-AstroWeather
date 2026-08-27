"""Tests for the safety gates and the night report (SPEC v8 1.1 and 1.4)."""

import datetime as dt

import pytest

from astroweather.gates import (
    GUST_CLOSE, evaluate, opening_probability, probability_usable,
)

NOW = dt.datetime(2026, 8, 27, 23, tzinfo=dt.timezone.utc)


def test_rain_on_the_sensor_closes_the_roof_whatever_the_forecast_says():
    verdict = evaluate(t_utc=NOW, hours_ahead=0.5, observed_rain=True,
                       gust=1.0, precipitation=0.0, precipitation_probability=0.0)
    assert not verdict.safe_to_open
    assert "sensor local" in verdict.summary


def test_a_gust_above_the_closure_limit_blocks():
    assert not evaluate(t_utc=NOW, hours_ahead=3, gust=GUST_CLOSE + 1).safe_to_open
    assert evaluate(t_utc=NOW, hours_ahead=3, gust=2.0).safe_to_open


def test_moderate_wind_warns_without_closing():
    verdict = evaluate(t_utc=NOW, hours_ahead=3, gust=9.0)
    assert verdict.safe_to_open
    assert any(r.level == "watch" for r in verdict.risks)
    assert "guiado" in verdict.summary


def test_cape_alone_never_closes_the_dome():
    """CAPE says the atmosphere could convect, not that anything is falling.

    Gating on it shuts the roof on plenty of perfectly usable nights.
    """
    verdict = evaluate(t_utc=NOW, hours_ahead=12, cape=2500.0,
                       precipitation_probability=5.0, gust=2.0)
    assert verdict.safe_to_open
    assert any(r.name == "tormenta" and r.level == "watch" for r in verdict.risks)


def test_cape_with_precipitation_does_close():
    verdict = evaluate(t_utc=NOW, hours_ahead=12, cape=2500.0,
                       precipitation_probability=60.0, gust=2.0)
    assert not verdict.safe_to_open


def test_rain_probability_decides_close_in_and_only_warns_far_out():
    """The same 60 % means different things at 3 hours and at 4 days."""
    soon = evaluate(t_utc=NOW, hours_ahead=3, precipitation_probability=60.0, gust=2.0)
    later = evaluate(t_utc=NOW, hours_ahead=90, precipitation_probability=60.0, gust=2.0)
    assert not soon.safe_to_open
    assert later.safe_to_open


def test_every_verdict_carries_its_reason():
    """A number without its why is not published anywhere in this system."""
    verdict = evaluate(t_utc=NOW, hours_ahead=2, gust=GUST_CLOSE + 2)
    assert verdict.summary.startswith("NO abrir:")
    assert all(risk.reason for risk in verdict.risks)


def test_a_blocked_hour_is_unusable_however_clear_it_is():
    blocked = evaluate(t_utc=NOW, hours_ahead=1, observed_rain=True)
    assert probability_usable(0.99, blocked) == 0.0


def test_watched_risks_eat_into_the_odds_without_zeroing_them():
    windy = evaluate(t_utc=NOW, hours_ahead=3, gust=9.0)
    assert 0 < probability_usable(0.8, windy) < 0.8


def test_opening_probability_falls_back_to_climatology_without_data():
    assert opening_probability([], climatology=0.57) == pytest.approx(0.57)


def test_a_night_of_good_hours_is_worth_opening_for():
    assert opening_probability([0.8] * 8) > 0.7
    assert opening_probability([0.1, 0.1]) < 0.2
