"""Tests for the "how is Nerpio right now" summary (astroweather/site_now.py).

Offline by design: the fixture below is the real markup of
com1.astrocamp.es:81/cloud.html, trimmed to the tables that matter and with the
live timestamps rewritten to "a couple of minutes ago" so freshness can be
exercised without a clock hack. The frozen sensors and the 2025 station are
kept verbatim, because they are the trap the module exists to avoid.

Each test corresponds to something the page actually did, not to something it
could hypothetically do.
"""

from datetime import datetime, timedelta, timezone

from astroweather.site_now import (
    DEW_MARGIN_CRITICAL,
    DEW_MARGIN_WATCH,
    SiteNow,
    extract_safety_sensors,
    summarise,
)
from astroweather.workers.scrape_astrocamp import parse

NOW = datetime.now(timezone.utc)
FRESH = (NOW - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
JUST_BEFORE = (NOW - timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")
STALE = (NOW - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")

# The safety strip, verbatim from the live page (attributes included: the
# extractor must not depend on them).
SENSOR_STRIP = (
    "<table class='myTable'><tr>"
    "<th>Humidity (1)</th><td bgcolor=green>Dry</td>"
    "<th>Internet (1)</th><td bgcolor=green>Online</td>"
    "<th>Sky (4)</th><td bgcolor=green>Clear</td>"
    "<th>Sky (3)</th><td bgcolor=green>Clear</td>"
    "<th>SunAlt (1)</th><td bgcolor=green>Night</td>"
    "<th>Temperature (1)</th><td bgcolor=green>Warm</td>"
    "</tr></table>"
)

CLOUD_TABLE = """
<table><tr><td></td></tr>
<tr><th>Cloud<th>Date time<th>SkyTemp<th>Temp<th>S.I.</tr>
<tr><td>3<td>2026-08-22 16:07:29<td>-9.62<td>41.93<td>3.1</tr>
<tr><td>4<td>{fresh}<td>-15.11<td>13.13<td>0.5</tr>
<tr><td>5<td>2026-07-21 21:12:45<td>-6.21<td>26.99<td>2.1</tr>
</table>
""".format(fresh=FRESH)

METEO_TABLE = """
<table><tr><th>Meteo<th>Date time<th>Temp Out<th>Hum.Out<th>Dew<th>Wind(km/h)
<th>Dir(deg)<th>Temp In<th>Hum.In<th>Pressure<th>Rain</tr>
<tr><td>1<td>{before}<td>13.8<td>71<td>8.63<td>23<td>289<td>20.5<td>52<td>845.8<td></tr>
<tr><td>2<td>{fresh}<td>13.0<td>67<td>7.01<td>23<td>289<td>20.5<td>52<td>845.8<td></tr>
<tr><td>3<td>2025-04-06 07:44:12<td>18<td>32.4<td>1.23<td>4<td>300<td>9.2<td>70<td>840.4<td>0</tr>
</table>
""".format(fresh=FRESH, before=JUST_BEFORE)

ROOF_TABLE = (
    "<table class='myTable'><tr>"
    "<th>S1R1</th><td>Open</td><th>S1R2</th><td>Open</td>"
    "<th>S1R3</th><td>Closed</td><th>S2R1</th><td>Open</td>"
    "</tr></table>"
)

SAFETY_LOG = """
<table><tr><td></td></tr>
<tr><th>Date time<th>AstroCamp Safety log</tr>
<tr><td>{fresh}<td>Status: Safe.  SunAlt(1) is Night..</tr>
<tr><td>2026-08-27 17:51:43<td>Status: Unsafe.  Sky1(4) is Clear..</tr>
<tr><td>2026-08-27 17:50:36<td>Status: Unsafe.  Sky1(4) is Cloudy..</tr>
</table>
""".format(fresh=FRESH)

OPEN_HOURS = """
<table><tr><th>Observatory Open Dark Time Statistics</th></tr>
<tr><th>Night Open Hours</th><td>Jan</td><td>Feb</td><td>Mar</td><td>Abr</td><td>May</td>
<td>Jun</td><td>Jul</td><td>Aug</td><td>Sep</td><td>Oct</td><td>Nov</td><td>Dec</td>
<th>Total Hours</th><th>Open Days</th></tr>
<tr><th>2019</th><td>221</td><td>277</td><td>230</td><td>98</td><td>164</td><td>201</td>
<td>197</td><td>235</td><td>155</td><td>230</td><td>80</td><td>192</td>
<td>2278</td><td>279</td></tr>
</table>
"""

FULL_PAGE = SENSOR_STRIP + CLOUD_TABLE + METEO_TABLE + ROOF_TABLE + SAFETY_LOG + OPEN_HOURS
# The failure actually observed in the wild: the server answered with only the
# CloudWatcher table and nothing else.
PARTIAL_PAGE = CLOUD_TABLE


def snapshot(page: str = FULL_PAGE) -> SiteNow:
    return summarise(
        parse(page),
        sensors=extract_safety_sensors(page),
        retrieved_at=NOW,
    )


def test_readings_come_from_the_newest_row_not_the_first_one():
    """meteo1 is printed above meteo2 but meteo2 is one minute newer."""
    now = snapshot()
    assert now.t_out == 13.0            # meteo2, not the 13.8 of meteo1
    assert now.dew_point == 7.01
    assert now.sky_temp == -15.11       # cloudwatcher4, not the frozen 3 or 5
    assert now.sky_index == 0.5


def test_wind_is_reported_in_metres_per_second():
    """The page publishes km/h; a panel showing 23 m/s would be a hurricane."""
    assert abs(snapshot().wind_ms - 23 / 3.6) < 0.01


def test_dew_margin_is_the_subtraction_the_user_actually_needs():
    now = snapshot()
    assert abs(now.dew_margin - (13.0 - 7.01)) < 1e-9
    assert now.dew_risk == "bajo"
    assert now.dew_warning is None


def test_dew_warning_fires_below_the_practice_thresholds():
    """Thresholds are astrophotography practice, not site calibration."""
    watch = SiteNow(site_id="nerpio", retrieved_at=NOW, t_out=10.0,
                    dew_point=10.0 - (DEW_MARGIN_WATCH - 0.5))
    critical = SiteNow(site_id="nerpio", retrieved_at=NOW, t_out=10.0,
                       dew_point=10.0 - (DEW_MARGIN_CRITICAL - 0.5))
    blind = SiteNow(site_id="nerpio", retrieved_at=NOW, t_out=10.0)

    assert watch.dew_risk == "vigilar"
    assert "Riesgo" in watch.dew_warning
    assert critical.dew_risk == "crítico"
    assert "casi seguro" in critical.dew_warning
    # Both must say where the threshold comes from: it is not measured here.
    assert "no medido aquí" in watch.dew_warning
    assert "no medido aquí" in critical.dew_warning
    # No data must never look like good news.
    assert blind.dew_margin is None
    assert blind.dew_risk == "desconocido"
    assert blind.dew_warning is None


def test_roof_count_is_open_over_total():
    now = snapshot()
    assert (now.roofs_open, now.roofs_total) == (3, 4)


def test_safety_status_and_sky_verdict_are_read_separately():
    """'Status: Unsafe.  Sky1(4) is Clear..' is a real line from the page.

    The sensor named in a log line is the one that changed, not the reason for
    the status, so inferring one from the other flips both.
    """
    now = snapshot()
    assert now.safety_status == "Safe"       # newest line, by timestamp
    assert now.sky_clear is True             # from the live sensor strip


def test_live_sensor_strip_beats_the_history_log():
    """The strip is 'now'; the log is 'the last time something changed'."""
    cloudy_strip = SENSOR_STRIP.replace(">Clear<", ">Cloudy<")
    page = cloudy_strip + CLOUD_TABLE + METEO_TABLE + ROOF_TABLE + SAFETY_LOG + OPEN_HOURS
    assert snapshot(page).sky_clear is False


def test_safety_sensors_do_not_swallow_the_roofs():
    """Roofs use the same name/state pair layout as the safety strip."""
    sensors = extract_safety_sensors(FULL_PAGE)
    assert sensors["Sky (4)"] == "Clear"
    assert sensors["SunAlt (1)"] == "Night"
    assert sensors["Humidity (1)"] == "Dry"
    assert not any(name.startswith("S1R") for name in sensors)
    assert "Open" not in sensors.values()


def test_a_partial_download_is_declared_never_smoothed_over():
    """Observed in the wild: only the CloudWatcher table came back."""
    now = snapshot(PARTIAL_PAGE)
    assert now.complete is False
    assert now.missing == ["meteo", "open_hours", "roofs", "safety_log"]
    assert "Descarga incompleta" in now.missing_note
    assert "Descarga incompleta" in now.headline
    # Nothing may be invented to fill the hole.
    assert now.t_out is None and now.dew_margin is None
    assert now.safety_status is None
    assert (now.roofs_open, now.roofs_total) == (0, 0)
    # ...but what did arrive is still shown.
    assert now.sky_temp == -15.11


def test_a_complete_download_says_so():
    now = snapshot()
    assert now.complete is True
    assert now.missing == []
    assert now.missing_note is None


def test_stale_readings_are_labelled_not_hidden():
    """The site goes quiet for hours; an unlabelled old number is a lie."""
    stale_meteo = METEO_TABLE.replace(FRESH, STALE).replace(JUST_BEFORE, STALE)
    page = SENSOR_STRIP + CLOUD_TABLE + stale_meteo + ROOF_TABLE + SAFETY_LOG + OPEN_HOURS
    now = snapshot(page)

    assert now.meteo_fresh is False
    assert now.sky_fresh is True              # the CloudWatcher is still live
    assert abs(now.age_minutes - 360) < 1
    assert "CADUCADA" in now.headline
    assert now.t_out == 13.8                  # still shown, just flagged


def test_headline_is_one_spanish_sentence_with_the_key_numbers():
    text = snapshot().headline
    assert text.startswith("Nerpio ahora:")
    assert text.endswith(".")
    assert "\n" not in text
    for fragment in ("13.0 °C", "67% HR", "margen de rocío", "cielo IR",
                     "despejado", "3/4 techos abiertos", "seguro"):
        assert fragment in text, fragment


def test_headline_degrades_instead_of_crashing_on_an_empty_page():
    """An empty answer must not be dressed up as a report about Nerpio."""
    now = summarise(parse("<html></html>"), retrieved_at=NOW)
    assert now.headline.startswith(
        "Sin datos utilizables de la página del observatorio."
    )
    assert "Nerpio ahora" not in now.headline
    assert "Descarga incompleta" in now.headline
    assert now.age is None and now.age_minutes is None
    assert now.meteo_fresh is False and now.sky_fresh is False
