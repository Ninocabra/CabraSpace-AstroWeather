"""Tests for the verification panel (SPEC v8 section 4.3)."""

from datetime import datetime, timedelta, timezone

import pytest

from astroweather.archive import write_canonical
from astroweather.verification import cloud_scores, report


def _forecast(model, valid, cloud, lead=24, member=None):
    return {
        "site_id": "test", "model": model,
        "run_utc": valid - timedelta(hours=lead), "valid_utc": valid,
        "lead_h": lead, "member": member, "cloud_total": cloud,
        "retrieved_at": valid,
    }


def _truth(t_utc, clear):
    return {
        "site_id": "test", "t_utc": t_utc, "clear_flag": clear,
        "instrument_id": "cw", "source": "test", "retrieved_at": t_utc,
    }


@pytest.fixture
def archive(tmp_path):
    base = datetime(2026, 8, 1, 22, tzinfo=timezone.utc)
    hours = [base + timedelta(hours=i) for i in range(10)]

    # A model that nails it, and one that is confidently wrong.
    write_canonical([_forecast("good", h, 5.0) for h in hours],
                    dataset="forecasts", site_id="test", root=tmp_path)
    write_canonical([_forecast("bad", h, 95.0) for h in hours],
                    dataset="forecasts", site_id="test", root=tmp_path)
    write_canonical([_truth(h, True) for h in hours],
                    dataset="measurements", site_id="test", root=tmp_path)
    return tmp_path


def test_a_correct_forecast_scores_better_than_a_wrong_one(archive):
    scores = {s["model"]: s["brier"] for s in cloud_scores(root=archive)}
    assert scores["good"] == pytest.approx(0.0)
    assert scores["bad"] == pytest.approx(1.0)
    assert scores["good"] < scores["bad"]


def test_scores_need_a_minimum_sample(archive):
    """One lucky hit is not a skill score."""
    base = datetime(2026, 9, 1, 22, tzinfo=timezone.utc)
    write_canonical([_forecast("anecdote", base, 5.0)],
                    dataset="forecasts", site_id="test", root=archive)
    write_canonical([_truth(base, True)],
                    dataset="measurements", site_id="test", root=archive)
    assert "anecdote" not in {s["model"] for s in cloud_scores(root=archive)}


def test_report_admits_what_it_cannot_verify(archive):
    """Seeing has no ground truth yet, and the panel has to say so."""
    text = report(root=archive)
    assert "seeing no se verifica" in text
    assert "BASELINES QUE HAY QUE BATIR" in text
