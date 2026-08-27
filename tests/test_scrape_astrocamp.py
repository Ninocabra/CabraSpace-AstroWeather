"""Tests for the AstroCamp page scraper (SPEC v8 section 3.1, note 7).

Every case here is a mistake the page actually invites.
"""

from datetime import datetime, timedelta, timezone

from astroweather.site import NERPIO
from astroweather.workers.scrape_astrocamp import parse, to_rows, _sky_verdict

NOW = datetime.now(timezone.utc)
FRESH = (NOW - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")

PAGE = """
<table><tr><td></td></tr>
<tr><th>Cloud<th>Date time<th>SkyTemp<th>Temp<th>S.I.</tr>
<tr><td>3<td>2025-07-21 21:12:45<td>-6.21<td>26.99<td>2.1</tr>
<tr><td>4<td>{fresh}<td>-15.77<td>17.55<td>0.1</tr>
</table>
<table><tr><th>Meteo<th>Date time<th>Temp Out<th>Hum.Out<th>Dew<th>Wind(km/h)
<th>Dir(deg)<th>Temp In<th>Hum.In<th>Pressure<th>Rain</tr>
<tr><td>1<td>{fresh}<td>18.6<td>48<td>7.37<td>18<td>303<td>22.6<td>39<td>843.3<td></tr>
<tr><td>3<td>2025-04-06 07:44:12<td>18<td>32.4<td>1.23<td>4<td>300<td>9.2<td>70<td>840.4<td>0</tr>
</table>
<table><tr><th>S1R1</th><td>Closed</td><th>S1R2</th><td>Open</td></tr></table>
<table><tr><td></td></tr>
<tr><th>Date time<th>AstroCamp Safety log</tr>
<tr><td>{fresh}<td>Status: Safe.  Sky1(4) is Clear..</tr>
</table>
<table><tr><th>Observatory Open Dark Time Statistics</th></tr>
<tr><th>Night Open Hours</th><td>Jan</td><td>Feb</td><td>Mar</td><td>Abr</td><td>May</td>
<td>Jun</td><td>Jul</td><td>Aug</td><td>Sep</td><td>Oct</td><td>Nov</td><td>Dec</td>
<th>Total Hours</th><th>Open Days</th></tr>
<tr><th>2019</th><td>221</td><td>277</td><td>230</td><td>98</td><td>164</td><td>201</td>
<td>197</td><td>235</td><td>155</td><td>230</td><td>80</td><td>192</td>
<td>2278</td><td>279</td></tr>
</table>
""".format(fresh=FRESH)


def test_timestamps_are_utc_not_local():
    """The page publishes UTC; its own UT field matches to the second.

    Reading them as Europe/Madrid pushed every sensor two hours into the past
    and made the live units look stale.
    """
    parsed = parse(PAGE)
    live = [c for c in parsed["cloudwatchers"] if c["id"] == "cloudwatcher4"][0]
    age_minutes = (NOW - live["t_utc"]).total_seconds() / 60
    assert 0 <= age_minutes < 10


def test_frozen_sensors_are_dropped_and_live_ones_kept():
    """A CloudWatcher stuck since 2025 sits in the table next to a live one."""
    rows = to_rows(parse(PAGE), NERPIO, NOW)
    watchers = [r for r in rows if r["instrument_type"] == "CloudWatcher"]
    stations = [r for r in rows if r["instrument_type"] == "WeatherStation"]

    assert [w["instrument_id"] for w in watchers] == ["cloudwatcher4"]
    assert [s["instrument_id"] for s in stations] == ["meteo1"]
    assert watchers[0]["t_sky_ir"] == -15.77
    assert watchers[0]["t_sky_ir_semantics"] == "aag_cloudwatcher_skytemp"


def test_wind_is_converted_to_metres_per_second():
    """The page prints km/h; every formula in the spec expects m/s."""
    station = parse(PAGE)["meteo"][0]
    assert abs(station["wind_ms"] - 5.0) < 0.01      # 18 km/h


def test_header_is_found_below_title_and_empty_rows():
    """Some tables open with a blank row, the statistics one with a title."""
    parsed = parse(PAGE)
    assert parsed["cloudwatchers"], "blank first row hid the cloud table"
    assert parsed["safety_log"], "blank first row hid the safety log"
    assert parsed["open_hours"]["2019"]["total"] == 2278


def test_observatory_sky_verdict_is_read_from_the_safety_log():
    assert _sky_verdict(parse(PAGE)["safety_log"]) is True


def test_roof_state_drives_the_censoring_flag():
    """Roofs shut means nobody observed -- not that the weather was bad."""
    rows = to_rows(parse(PAGE), NERPIO, NOW)
    roof = [r for r in rows if r["instrument_type"] == "RoofStatus"][0]
    assert roof["roof_open"] is True          # S1R2 is open in the fixture
    assert roof["censored"] is False
    assert roof["quality_flags"] == "open=1/2"
