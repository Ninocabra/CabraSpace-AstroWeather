"""The scoreboard's arithmetic, on data whose answer is known in advance.

The archive-wide run is deliberately not tested here: it takes minutes and its
numbers move with every night collected. What is pinned is the machinery those
numbers rest on, and one claim from the module docstring that is easy to assert
and easy to get wrong -- that AUC survives a recalibration and Brier does not.
That difference is the whole reason both are reported.
"""

import numpy as np
import pytest

from astroweather.scoreboard import _auc, _brier, _lead_days
from astroweather.verification import MIN_CLOUD_PAIRS


def test_auc_reads_perfect_and_reversed_orderings():
    outcomes = np.array([0.0, 0.0, 1.0, 1.0])
    assert _auc(np.array([0.1, 0.2, 0.8, 0.9]), outcomes) == 1.0
    assert _auc(np.array([0.9, 0.8, 0.2, 0.1]), outcomes) == 0.0


def test_a_constant_forecast_scores_half_and_not_one():
    """Ties must be averaged. Without that, a model that always says the same
    thing gets ranked by whatever order the rows happened to arrive in, and
    scores a perfect 1.0 or a perfect 0.0 for saying nothing at all."""
    outcomes = np.array([0.0, 1.0, 0.0, 1.0])
    assert _auc(np.full(4, 0.5), outcomes) == pytest.approx(0.5)


def test_auc_needs_both_outcomes_present():
    """A stretch of nights that were all clear cannot rank anything."""
    assert _auc(np.array([0.1, 0.9]), np.array([1.0, 1.0])) is None
    assert _auc(np.array([0.1, 0.9]), np.array([0.0, 0.0])) is None


def test_recalibration_moves_brier_and_leaves_auc_alone():
    """The claim that justifies reporting both numbers side by side."""
    generator = np.random.default_rng(3)
    outcomes = generator.integers(0, 2, 500).astype(float)
    raw = np.clip(0.5 + 0.25 * (outcomes - 0.5) + generator.normal(0, 0.2, 500), 0.01, 0.99)
    recalibrated = 1.0 / (1.0 + np.exp(-(3.0 * (raw - 0.5))))   # monotonic

    assert _auc(recalibrated, outcomes) == pytest.approx(_auc(raw, outcomes), abs=1e-9)
    assert _brier(recalibrated, outcomes) != pytest.approx(_brier(raw, outcomes), abs=1e-6)


def test_brier_rewards_being_right_and_sure():
    outcomes = np.array([1.0, 0.0])
    assert _brier(np.array([1.0, 0.0]), outcomes) == 0.0
    assert _brier(np.array([0.5, 0.5]), outcomes) == pytest.approx(0.25)
    assert _brier(np.array([0.0, 1.0]), outcomes) == 1.0


def test_lead_days_reads_both_ways_of_writing_a_lead():
    """The backfill carries its lead in the model name, a live run in lead_h.
    Mixing the two up would silently compare a 5-day forecast against a 1-day
    one in the same row of the table."""
    assert _lead_days("ecmwf_ifs025_prev3d", 0) == 3
    assert _lead_days("ecmwf_ifs025_prev5d", 999) == 5      # the name wins
    assert _lead_days("icon_seamless", 47) == 1
    assert _lead_days("icon_seamless", 0) == 0


def test_the_panel_cannot_announce_skill_from_a_handful_of_hours():
    """It printed Brier=0.000 from 51 rows of a two-day clear spell. The guard
    is the only thing standing between that and a repeat."""
    assert MIN_CLOUD_PAIRS >= 200
