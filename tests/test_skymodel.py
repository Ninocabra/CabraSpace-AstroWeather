"""Tests for the clear-sky model and the cloud residual (SPEC v8 2.2/3.1)."""

from datetime import datetime, timedelta, timezone

import pytest

from astroweather.skybackground import NATURAL_FLOOR_MAG
from astroweather.skymodel import (
    TWILIGHT_FLOOR, cloud_score, predict, residuals, twilight_magnitude,
)

DARK = 21.9
FULL_MOON_NIGHT = datetime(2026, 8, 27, 23, tzinfo=timezone.utc)
NEW_MOON_NIGHT = datetime(2026, 7, 15, 23, tzinfo=timezone.utc)


def test_the_model_follows_the_lunar_cycle():
    """A full Moon must predict a far brighter sky than a new Moon.

    The moonless value is checked against the site's MEASURED floor plus its
    zodiacal term, not against the 21.9 that was assumed before seven years of
    photometry said otherwise (skybackground.py).
    """
    full = predict([FULL_MOON_NIGHT])[0]
    new = predict([NEW_MOON_NIGHT])[0]
    assert full.predicted_mag < new.predicted_mag - 2.0
    assert new.predicted_mag == pytest.approx(NATURAL_FLOOR_MAG, abs=0.35)


def test_twilight_was_fitted_not_guessed():
    """The first version guessed and was 2.5-3 magnitudes too bright.

    These are the site's own measured values: essentially dark at -18, and
    clearly bright at -10.
    """
    assert twilight_magnitude(-18.0, DARK) > 21.0     # negligible next to the sky
    assert twilight_magnitude(-10.0, DARK) < 19.0     # dominant
    assert twilight_magnitude(TWILIGHT_FLOOR - 1, DARK) == float("inf")


def test_twilight_darkens_monotonically_as_the_sun_sets():
    values = [twilight_magnitude(alt, DARK) for alt in (-10, -12, -14, -16, -18)]
    assert values == sorted(values)      # fainter and fainter contribution


def test_a_sky_matching_the_model_scores_as_clear():
    """A clear night sits on the prediction whatever the Moon is doing."""
    times = [FULL_MOON_NIGHT + timedelta(minutes=10 * i) for i in range(20)]
    predicted = [p.predicted_mag for p in predict(times)]
    perfect = residuals(list(zip(times, predicted)))
    assert cloud_score([r for *_, r in perfect]) == pytest.approx(0.0, abs=1e-6)


def test_a_wandering_sky_scores_as_cloudy():
    """Clouds move, so the signal is scatter around the model, not level."""
    times = [NEW_MOON_NIGHT + timedelta(minutes=10 * i) for i in range(20)]
    predicted = [p.predicted_mag for p in predict(times)]
    wandering = [m + (0.8 if i % 2 else -0.8) for i, m in enumerate(predicted)]
    assert cloud_score([r for *_, r in residuals(list(zip(times, wandering)))]) > 0.5


def test_a_constant_offset_is_not_read_as_cloud():
    """A miscalibrated photometer shifts the level; it does not add scatter."""
    times = [NEW_MOON_NIGHT + timedelta(minutes=10 * i) for i in range(20)]
    shifted = [p.predicted_mag - 0.4 for p in predict(times)]
    assert cloud_score([r for *_, r in residuals(list(zip(times, shifted)))]) < 0.05


def test_too_few_samples_give_no_verdict():
    """Three readings are not a night."""
    assert cloud_score([0.1, 0.2, 0.1]) is None
