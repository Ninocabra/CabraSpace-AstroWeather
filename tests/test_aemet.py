"""Tests for the AEMET daily observations worker (SPEC v8 section 3.1, note 15)."""

from datetime import date, datetime, timezone

from astroweather.site import NERPIO
from astroweather.workers.fetch_aemet import _number, _to_rows, _chunks

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def test_spanish_decimal_comma_is_parsed():
    """AEMET writes 23,2 -- read as a plain float that is 232 or a crash."""
    assert _number("23,2") == 23.2
    assert _number("-0,8") == -0.8


def test_trace_rainfall_is_zero_not_null():
    """'Ip' means measurable-but-inappreciable rain: it rained, just barely."""
    assert _number("Ip") == 0.0


def test_missing_values_stay_missing():
    # 7067Y has no anemometer: velmedia comes back null and must not become 0.
    assert _number(None) is None
    assert _number("") is None


def test_rows_are_labelled_as_external_ground_truth():
    """A station 11.5 km away and 165 m lower is never site data."""
    payload = [{"fecha": "2026-07-14", "tmed": "23,2", "tmin": "12,2",
                "tmax": "34,2", "hrMedia": "21", "prec": "0,0"}]
    row = _to_rows(payload, NERPIO, "7067Y", NOW)[0]

    assert row["date"] == date(2026, 7, 14)
    assert row["station_id"] == "7067Y"
    assert row["station_elevation"] == 1485.0        # not the site's 1650
    assert row["station_distance_km"] == 11.5
    assert "external_station" in row["quality_flags"]
    assert "daily_aggregate" in row["quality_flags"]
    assert row["t_mean"] == 23.2


def test_history_is_walked_in_bounded_windows():
    """AEMET caps the span per request, so a seven-year pull must be chunked."""
    windows = list(_chunks(date(2019, 1, 1), date(2020, 1, 1)))
    assert len(windows) == 2
    assert windows[0][0] == date(2019, 1, 1)
    assert all(start <= end for start, end in windows)
