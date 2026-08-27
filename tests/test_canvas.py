"""Tests for the night canvas (SPEC v8 section 1.3)."""

import datetime as dt

from astroweather.canvas import CanvasHour, render

BASE = dt.datetime(2026, 9, 11, 19, tzinfo=dt.timezone.utc)


def _hours(**overrides):
    defaults = dict(sky_mag=21.6, cloud_low=0.0, cloud_mid=0.0, cloud_high=0.0,
                    certainty=0.9, usable_probability=0.85, moon_altitude=-10.0,
                    safe=True, limiting="")
    defaults.update(overrides)
    return [CanvasHour(t_utc=BASE + dt.timedelta(hours=i), **defaults)
            for i in range(10)]


def test_the_canvas_is_valid_svg_with_the_night_on_it():
    svg = render(_hours(), title="prueba", subtitle="sub")
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "prueba" in svg and "sub" in svg


def test_a_blocked_hour_is_unmistakable():
    """Safety is never ambiguous, whatever else the column is doing."""
    safe = render(_hours(safe=True), title="t", subtitle="s")
    blocked = render(_hours(safe=False), title="t", subtitle="s")
    assert "url(#blocked)" not in safe
    assert blocked.count("url(#blocked)") >= 10


def test_uncertainty_is_painted_not_hidden():
    """A 40 % nobody agrees on must not look like a confident 40 %."""
    confident = render(_hours(cloud_mid=40.0, certainty=0.95), title="t", subtitle="s")
    doubtful = render(_hours(cloud_mid=40.0, certainty=0.35), title="t", subtitle="s")
    assert "url(#uncertain)" not in confident
    assert "url(#uncertain)" in doubtful


def test_a_brighter_sky_is_painted_lighter():
    dark = render(_hours(sky_mag=21.9), title="t", subtitle="s")
    moonlit = render(_hours(sky_mag=18.5), title="t", subtitle="s")
    assert dark != moonlit


def test_the_moon_is_drawn_only_while_it_is_up():
    down = render(_hours(moon_altitude=-20.0), title="t", subtitle="s")
    up = render(_hours(moon_altitude=45.0), title="t", subtitle="s")
    assert "#f2e6b8" not in down          # no arc when the Moon has set
    assert "#f2e6b8" in up


def test_the_provisional_ridge_says_so_on_the_canvas():
    """Drawing a fake ridge that looks real is the same sin as a fake cloud."""
    assert "PROVISIONAL" in render(_hours(), title="t", subtitle="s")


def test_targets_come_with_their_reason():
    svg = render(_hours(), title="t", subtitle="s",
                 targets=[("M51", 2.9, "el halo pide cielo sin Luna")])
    assert "M51" in svg and "2.9 h eq" in svg and "halo" in svg
