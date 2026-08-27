"""Tests for the cloud probability calibrator (SPEC v8 sections 1.1 and 4.3)."""

import numpy as np
import pytest

from astroweather.calibration import Calibrator, fit, reliability


def _synthetic(n=4000, seed=7):
    """A forecast with real skill: clearer sky, higher chance of a clear hour."""
    rng = np.random.default_rng(seed)
    clouds = rng.uniform(0, 100, n)
    leads = rng.integers(1, 6, n).astype(float)
    truth_probability = 1 / (1 + np.exp(-(2.5 - 4.0 * clouds / 100)))
    observed = rng.random(n) < truth_probability
    return list(zip(clouds, leads, observed))


def test_more_cloud_means_less_chance_of_a_clear_hour():
    model = fit(_synthetic())
    assert model.cloud_weight < 0
    assert model.probability(10) > model.probability(90)


def test_a_skilful_forecast_beats_climatology():
    model = fit(_synthetic())
    assert model.beats_climatology
    assert model.brier < model.brier_climatology


def test_a_useless_forecast_does_not_beat_climatology():
    """The panel has to be able to say 'this adds nothing'."""
    rng = np.random.default_rng(3)
    noise = [(rng.uniform(0, 100), 1.0, bool(rng.random() < 0.64)) for _ in range(4000)]
    model = fit(noise)
    # With no signal the fit collapses onto the base rate; it cannot do better.
    assert model.brier >= model.brier_climatology - 0.002
    assert model.probability(10) == pytest.approx(model.probability(90), abs=0.05)


def test_probabilities_stay_within_range():
    model = fit(_synthetic())
    for cloud in (-10, 0, 50, 100, 150):
        assert 0.0 <= model.probability(cloud) <= 1.0


def test_reliability_curve_tracks_the_diagonal_for_a_good_model():
    """When it says 70 %, it should happen about 70 % of the time."""
    pairs = _synthetic(n=8000)
    curve = reliability(pairs, fit(pairs), bins=6)
    assert curve
    for predicted, observed, count in curve:
        assert abs(predicted - observed) < 0.08, (predicted, observed, count)


def test_calibration_refuses_to_fit_on_a_handful_of_nights():
    with pytest.raises(ValueError, match="insufficient data"):
        fit([(50.0, 1.0, True)] * 10)


def test_the_model_survives_a_round_trip(tmp_path):
    model = fit(_synthetic())
    path = model.save(tmp_path / "cal.json")
    restored = Calibrator.load(path)
    assert restored.probability(30) == pytest.approx(model.probability(30))
    assert Calibrator.load(tmp_path / "missing.json") is None
