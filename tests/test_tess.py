"""Tests for the TESS photometer ingestion (SPEC v8 section 3.1, note 6)."""

from datetime import datetime, timezone

from astroweather.site import NERPIO
from astroweather.workers.fetch_tess import _to_rows


def _series(mag, tamb, tsky, epoch=1787836394):
    return [
        {"name": "mqtt_consumer.mag", "points": [[mag, epoch]]},
        {"name": "mqtt_consumer.tamb", "points": [[tamb, epoch]]},
        {"name": "mqtt_consumer.tsky", "points": [[tsky, epoch]]},
    ]


NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def test_dead_temperature_sensors_do_not_fabricate_a_cloud_index():
    """stars289 publishes tamb=0 and tsky=0: its T sensors do not report.

    Taken at face value, 100 - 3*(tamb - tsky) would be a constant 100, i.e.
    "overcast" on every single sample of a seven-year archive.
    """
    rows = _to_rows(_series(21.9, 0, 0), NERPIO, "stars289", NOW)
    assert rows[0]["sky_mag_raw"] == 21.9      # the brightness IS good
    assert rows[0]["t_sky_ir"] is None         # the temperature is not
    assert rows[0]["clear_flag"] is None       # and no verdict is invented
    assert rows[0]["cloud_frac"] is None


def test_working_temperature_sensors_yield_a_cloud_index():
    # A clear sky: ambient 20 C, IR sky -15 C -> index 100 - 3*35 = -5 < 40.
    rows = _to_rows(_series(21.5, 20.0, -15.0), NERPIO, "stars1", NOW)
    assert rows[0]["clear_flag"] is True
    assert rows[0]["cloud_frac"] == -5.0


def test_daylight_zero_magnitude_is_nulled_but_row_survives():
    rows = _to_rows(_series(0.0, 25.0, 10.0), NERPIO, "stars1", NOW)
    assert rows[0]["sky_mag_raw"] is None      # 0 mag is not a sky reading
    assert rows[0]["cloud_frac"] is not None   # the IR signal still counts


def test_instrument_identity_is_recorded_not_assumed_to_be_sqm():
    rows = _to_rows(_series(21.9, 0, 0), NERPIO, "stars289", NOW)
    assert rows[0]["instrument_type"] == "TESS"
    assert rows[0]["instrument_id"] == "stars289"
    assert rows[0]["bandpass_id"] == "TESS-W"
    assert rows[0]["t_sky_ir_semantics"] == "tess_mqtt_tsky_raw"
    assert "sky_mag_calibrated" not in rows[0]  # any SQM scale is explicit work
