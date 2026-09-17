"""Tests for the raw + canonical archive (SPEC v8 sections 4.1 and 5)."""

from datetime import datetime, timezone

import duckdb
import pytest

from astroweather.archive import store_raw, write_canonical


def _row(model: str, hour: int) -> dict:
    return {
        "site_id": "test",
        "model": model,
        "run_utc": datetime(2026, 8, 27, tzinfo=timezone.utc),
        "valid_utc": datetime(2026, 8, 27, hour, tzinfo=timezone.utc),
        "lead_h": hour,
        "member": None,
        "cloud_low": 10.0,
    }


def test_two_writes_in_the_same_second_do_not_overwrite(tmp_path):
    """Regression: the surface and profile workers run back to back.

    With a filename built only from the timestamp, the second write silently
    replaced the first and one whole model vanished from the archive.
    """
    first = write_canonical([_row("ecmwf_ifs", 1)], dataset="forecasts",
                            site_id="test", root=tmp_path)
    second = write_canonical([_row("icon_seamless", 1)], dataset="forecasts",
                             site_id="test", root=tmp_path)

    assert first != second
    models = duckdb.sql(
        f"SELECT DISTINCT model FROM read_parquet('{tmp_path.as_posix()}/forecasts/**/*.parquet')"
    ).fetchall()
    assert {m[0] for m in models} == {"ecmwf_ifs", "icon_seamless"}


def test_unknown_column_is_rejected(tmp_path):
    """A typo must fail on write, not three months later during training."""
    row = _row("ecmwf_ifs", 1) | {"cluod_low": 10.0}
    with pytest.raises(ValueError, match="unknown columns"):
        write_canonical([row], dataset="forecasts", site_id="test", root=tmp_path)


def test_raw_is_content_addressed_and_never_rewritten(tmp_path):
    payload = {"hourly": {"time": ["2026-08-27T00:00"]}}
    when = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

    first = store_raw(payload, source="test", site_id="test",
                      retrieved_at=when, root=tmp_path)
    mtime = first.stat().st_mtime_ns
    second = store_raw(payload, source="test", site_id="test",
                       retrieved_at=when, root=tmp_path)

    assert first == second
    assert first.stat().st_mtime_ns == mtime  # untouched, not rewritten


def test_empty_rows_write_nothing(tmp_path):
    assert write_canonical([], dataset="forecasts", site_id="test", root=tmp_path) is None


def test_tag_goes_into_the_file_name_and_is_checked(tmp_path):
    """The archive push sorts WeatherNext files by name, not by opening them."""
    path = write_canonical([_row("weathernext3", 1)], dataset="forecasts",
                           site_id="test", root=tmp_path, tag="weathernext3")
    assert path.name.endswith("_weathernext3.parquet")
    untagged = write_canonical([_row("ecmwf_ifs", 1)], dataset="forecasts",
                               site_id="test", root=tmp_path)
    assert untagged.name.count("_") == 1
    with pytest.raises(ValueError, match="tag"):
        write_canonical([_row("x", 1)], dataset="forecasts", site_id="test",
                        root=tmp_path, tag="../escape")
