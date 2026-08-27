"""Tests for the roof forecast (astroweather/roof.py).

The module answers the one question the rest of the project refuses to answer
-- will the dome be open tonight -- by multiplying a record that belongs to
AstroCamp (published open dark hours per month, 2013-2025) by a probability
that belongs to us (the calibrated clear-sky forecast). Almost every way of
getting this wrong is silent: a denominator one twilight step too deep, a
percentile band collapsed into a mean, an expectation measured against the
product window instead of against the night. None of those raise; they just
print a plausible number that is not true. These tests exist to make those
failures loud.

Everything runs off the snapshot of the AstroCamp page committed at the repo
root, so there is no network here and the fixtures are the real published
table, not invented numbers.
"""

from __future__ import annotations

import calendar
import datetime as dt
import functools
import json
from pathlib import Path

import numpy as np
import pytest

from astroweather import roof
from astroweather.ephem import sun_positions
from astroweather.roof import (
    FIRST_FULL_YEAR,
    LAST_FULL_YEAR,
    build_climatology,
    load_climatology,
    outlook,
)
from astroweather.site import NERPIO
from astroweather.workers.scrape_astrocamp import parse

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO_ROOT / "cloud_snapshot.html"

# The four months whose published total is closest to the whole night. They are
# the ones that decide which denominator is defensible: under "Sun below the
# horizon" they all fit inside the night, under civil twilight none of them do.
TIGHTEST_MONTHS = [(2013, 7), (2014, 8), (2018, 7), (2023, 8)]


@pytest.fixture(scope="session", autouse=True)
def _cached_dark_hours():
    """Memoise dark_hours for the session; it is pure and it is the slow part.

    Every test in this file leans on the same 156 (year, month) pairs, and each
    one costs an astropy transform of a month of sun positions. The wrapper
    changes no value -- the real implementation still computes each pair once,
    and test_dark_hours_counts_only_the_sun_below_the_horizon checks its output
    against an independent count -- it only stops the file recomputing the
    record five times over.
    """
    original = roof.dark_hours
    roof.dark_hours = functools.lru_cache(maxsize=None)(original)
    yield
    roof.dark_hours = original


@pytest.fixture(scope="session")
def published_hours() -> dict:
    """The observatory's own open-hours table, straight off the page snapshot."""
    page = SNAPSHOT.read_text(encoding="utf-8", errors="replace")
    open_hours = parse(page)["open_hours"]
    assert open_hours, "the snapshot no longer contains the open-hours table"
    return open_hours


@pytest.fixture(scope="session")
def built(published_hours) -> dict:
    """The climatology rebuilt from the snapshot, as the worker would build it."""
    return build_climatology(published_hours)


@pytest.fixture(scope="session")
def record_fractions(published_hours) -> dict:
    """Open hours as a fraction of the available night, for all 156 months."""
    fractions = {}
    for year in range(FIRST_FULL_YEAR, LAST_FULL_YEAR + 1):
        months = published_hours[str(year)]["months"]
        for month, hours in enumerate(months, start=1):
            if hours is not None:
                fractions[(year, month)] = hours / roof.dark_hours(year, month)
    return fractions


def _sun_hours_below(year: int, month: int, threshold: float,
                     step_minutes: int = 20) -> float:
    """Independent count of hours with the Sun below `threshold` degrees."""
    days = calendar.monthrange(year, month)[1]
    start = dt.datetime(year, month, 1, tzinfo=dt.timezone.utc)
    steps = days * 24 * 60 // step_minutes
    times = [start + dt.timedelta(minutes=step_minutes * i) for i in range(steps)]
    altitudes = np.array([p.altitude for p in sun_positions(times, NERPIO)])
    return float((altitudes < threshold).sum()) * step_minutes / 60.0


# --------------------------------------------------------------------------
# The denominator
# --------------------------------------------------------------------------

def test_dark_hours_counts_only_the_sun_below_the_horizon():
    """The threshold is 0 degrees, and nothing deeper, by measurement.

    Four denominators were plausible and only one survived contact with the
    data. Deepening the threshold shortens the night, so the same published
    hours are divided by less and the fractions grow: civil twilight already
    puts four months of the record above 100 % of the night, nautical 25 and
    astronomical 43. Since dark_hours is a one-line function it is exactly the
    kind of thing a later reader "tidies up" to -18 because that is what
    astronomers usually mean by dark, which would quietly turn the whole model
    into impossible fractions.
    """
    year, month = 2018, 7
    horizon = _sun_hours_below(year, month, 0.0)

    assert roof.dark_hours(year, month) == pytest.approx(horizon, abs=0.01)
    for twilight in (-6.0, -12.0, -18.0):
        assert _sun_hours_below(year, month, twilight) < horizon


def test_the_busiest_months_still_fit_inside_the_night(published_hours):
    """No published month may claim more open hours than the night had.

    These four are the tightest in the record -- July 2018 alone reaches 96.5 %
    of its available night -- so they are the first to break. Against the Sun
    below the horizon each of them fits; against civil twilight each of them
    reports more open hours than there was night to open in, which is the
    signature of the wrong denominator and is impossible rather than merely
    surprising.
    """
    for year, month in TIGHTEST_MONTHS:
        hours = published_hours[str(year)]["months"][month - 1]
        night = roof.dark_hours(year, month)
        civil = _sun_hours_below(year, month, -6.0)

        assert hours / night <= 1.0
        assert hours / civil > 1.0


def test_no_month_of_the_whole_record_is_impossible(record_fractions):
    """156 months, none over 100 %, and the site open just over half the night.

    build_climatology clamps each ratio with min(1.0, ...), so a broken
    denominator would not show up in its output at all -- it would be absorbed
    silently. This checks the raw ratios before any clamp, which is the only
    place the error can still be seen.
    """
    values = np.array(list(record_fractions.values()))

    assert len(values) == 156
    assert values.max() <= 1.0
    assert values.max() == pytest.approx(0.965, abs=0.005)
    assert values.mean() == pytest.approx(0.530, abs=0.005)


# --------------------------------------------------------------------------
# The climatology
# --------------------------------------------------------------------------

def test_the_shipped_file_is_what_the_snapshot_produces(built):
    """data/roof_climatology.json must stay reproducible from the raw page.

    The file is generated, committed and then read by everything downstream, so
    it is the natural place for a hand edit or a stale regeneration to hide. If
    rebuilding it from the snapshot no longer gives the same numbers, either the
    file or the code moved without the other.
    """
    shipped = load_climatology()
    assert shipped is not None

    assert built["denominator"] == "sun_below_horizon" == shipped["denominator"]
    assert built["years"] == shipped["years"] == f"{FIRST_FULL_YEAR}-{LAST_FULL_YEAR}"
    assert sorted(built["months"]) == list(range(1, 13))

    for month in range(1, 13):
        rebuilt = built["months"][month]
        stored = shipped["months"][str(month)]
        for key in ("median_fraction", "low_fraction", "high_fraction", "years"):
            assert rebuilt[key] == pytest.approx(stored[key], abs=1e-4), month


def test_partial_years_are_left_out_of_the_climatology(built, published_hours):
    """2012 has one month and the current year is still running; both would bias.

    A single December in 2012 and an unfinished 2026 sit in the same table as
    the complete years. Counting them would tilt whichever months they happen to
    cover, so every month must come from exactly the thirteen full years.
    """
    assert "2012" in published_hours and "2026" in published_hours
    assert all(built["months"][m]["years"] == 13 for m in range(1, 13))

    padded = dict(published_hours)
    padded["2027"] = {"months": [720] * 12, "total": 8640}
    assert build_climatology(padded)["months"][7] == built["months"][7]


def test_summer_opens_more_than_twice_as_often_as_winter(built):
    """July and August against January to April: the whole point of going monthly.

    A single site-wide "open about half the night" number would be wrong by
    thirty points in both directions at once. This pins the actual shape of the
    year, so that a change to the percentile logic or to the denominator that
    flattened the seasonal contrast would fail here rather than reach the user
    as a confident February forecast built on August behaviour.
    """
    fraction = {m: built["months"][m]["median_fraction"] for m in range(1, 13)}

    assert fraction[7] == pytest.approx(0.835, abs=0.005)
    assert fraction[8] == pytest.approx(0.823, abs=0.005)
    assert fraction[1] == pytest.approx(0.383, abs=0.005)
    assert fraction[2] == pytest.approx(0.356, abs=0.005)
    assert fraction[3] == pytest.approx(0.376, abs=0.005)
    assert fraction[4] == pytest.approx(0.418, abs=0.005)

    for winter in (1, 2, 3, 4):
        for summer in (7, 8):
            assert fraction[summer] > 2 * fraction[winter], (summer, winter)


def test_april_is_uncertain_and_july_is_not(built):
    """The band is the message: April 2020 was lockdown, not weather.

    April runs from 22 open hours in 2020 to 243 in 2023 and no forecast will
    ever anticipate that, whereas July does the same thing every year. Reporting
    a bare median would present both with equal confidence. If a refactor ever
    replaced the p10-p90 band with something symmetric or narrower, April would
    start looking predictable, and that is precisely the claim this module
    exists to avoid making.
    """
    april = built["months"][4]
    july = built["months"][7]
    spread = lambda m: m["high_fraction"] - m["low_fraction"]

    assert spread(april) > 2 * spread(july)
    assert spread(april) > 0.40
    assert spread(july) < 0.20
    assert july["low_fraction"] > 0.70          # July's bad years are still good
    assert april["low_fraction"] < 0.20         # April's bad years are hopeless

    for month in range(1, 13):
        record = built["months"][month]
        assert record["low_fraction"] <= record["median_fraction"] <= record["high_fraction"]


def test_monthly_climatology_beats_one_annual_number_on_unseen_years(record_fractions):
    """Fit on 2013-2023, predict 2024-2025: 24 months nobody looked at.

    The seasonal shape could be overfitting -- thirteen samples per month is not
    many -- so it has to earn its place against the simplest alternative, a
    single number for the whole site. It does: about 14.4 points of mean
    absolute error against roughly 19, a fifth of the error gone. If a future
    change to the percentiles or the denominator eroded that margin, the monthly
    breakdown would be costing complexity for nothing.
    """
    train = {k: v for k, v in record_fractions.items() if k[0] <= 2023}
    held_out = {k: v for k, v in record_fractions.items() if k[0] >= 2024}
    assert len(held_out) == 24

    by_month = {m: float(np.median([v for (_, mm), v in train.items() if mm == m]))
                for m in range(1, 13)}
    single = float(np.median(list(train.values())))

    monthly_error = float(np.mean([abs(v - by_month[m]) for (_, m), v in held_out.items()])) * 100
    annual_error = float(np.mean([abs(v - single) for _, v in held_out.items()])) * 100

    assert monthly_error == pytest.approx(14.4, abs=0.3)
    assert annual_error > 18.0
    assert (annual_error - monthly_error) / annual_error > 0.20


# --------------------------------------------------------------------------
# Tonight's outlook
# --------------------------------------------------------------------------

def _night_inputs(day: dt.datetime, probability: float):
    night = roof.night_for(day, NERPIO)
    times = night.hours(30)
    return times, [probability] * len(times)


@pytest.fixture(scope="session")
def climatology() -> dict:
    stored = load_climatology()
    assert stored is not None, "data/roof_climatology.json is missing"
    return stored


@pytest.mark.parametrize("day", [
    dt.datetime(2026, 1, 15, tzinfo=dt.timezone.utc),
    dt.datetime(2026, 4, 15, tzinfo=dt.timezone.utc),
    dt.datetime(2026, 7, 15, tzinfo=dt.timezone.utc),
    dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc),
])
@pytest.mark.parametrize("probability", [0.0, 0.548, 1.0])
def test_expected_hours_never_exceed_the_night_itself(day, probability, climatology):
    """This was a real bug, and an expensive one to spot by eye.

    The expectation was once scaled by the product window -- an hour before
    sunset to an hour after sunrise -- which is not night, it is night plus two
    hours of daylight. July then promised 10.2 open hours against a typical 8.7
    and a historical maximum of 9.6: a forecast that beat the record every time
    and looked merely optimistic. The expectation must live inside the night,
    even on a night the forecast calls perfect.
    """
    times, clear = _night_inputs(day, probability)
    result = outlook(day, clear, times, climatology)

    days = calendar.monthrange(day.year, day.month)[1]
    available = roof.dark_hours(day.year, day.month) / days
    window = (times[-1] - times[0]).total_seconds() / 3600

    assert window > available                      # the trap that was fallen into
    assert 0.0 <= result.expected_open_hours <= available + 0.05
    assert 0.0 <= result.open_probability <= 1.0


@pytest.mark.parametrize("day", [
    dt.datetime(2026, 1, 15, tzinfo=dt.timezone.utc),
    dt.datetime(2026, 7, 15, tzinfo=dt.timezone.utc),
])
def test_the_reported_hours_agree_with_each_other(day, climatology):
    """The four hour figures are one band and must be read as one.

    They are rounded independently from the same nightly length, so nothing in
    the code forces low <= climatological <= high, and a reader shown a median
    of 8.0 inside a band of 8.5 to 7.3 would rightly stop trusting the panel. On
    an average night the expectation should also land on the climatological
    figure, since that is what "average" is defined to mean here.
    """
    times, clear = _night_inputs(day, 0.548)
    result = outlook(day, clear, times, climatology)

    assert result.low_hours <= result.climatological_hours <= result.high_hours
    assert result.expected_open_hours == pytest.approx(result.climatological_hours, abs=0.15)
    assert result.night == day.date()


def test_a_better_forecast_can_only_help(climatology):
    """Monotonic in the cloud forecast, and honest at the extremes.

    The two halves are combined by multiplication, so an inverted sign or a
    misplaced clamp would still return numbers in [0, 1] and still read as a
    forecast -- it would simply recommend the worst nights. Anchoring both ends
    (a hopeless sky closes the roof, a perfect sky cannot exceed certainty)
    catches that.
    """
    day = dt.datetime(2026, 7, 15, tzinfo=dt.timezone.utc)
    times, _ = _night_inputs(day, 0.0)

    probabilities = [outlook(day, [p] * len(times), times, climatology).open_probability
                     for p in (0.0, 0.2, 0.5, 0.8, 1.0)]

    assert probabilities == sorted(probabilities)
    assert probabilities[0] == 0.0
    assert probabilities[-1] <= 1.0
    assert outlook(day, [0.0] * len(times), times, climatology).verdict == "poco probable"


def test_the_hourly_track_matches_the_grid_it_was_given(climatology):
    """One entry per sample, bounded, and labelled from the same timestamps.

    The night panel draws this list directly. A zip against a mismatched grid
    silently truncates rather than raising, which would shorten the curve by an
    hour or two without anyone noticing.
    """
    day = dt.datetime(2026, 4, 15, tzinfo=dt.timezone.utc)
    times, clear = _night_inputs(day, 0.7)
    result = outlook(day, clear, times, climatology)

    assert len(result.hourly) == len(times)
    assert [h["label"] for h in result.hourly] == [t.strftime("%H:%M") for t in times]
    assert all(0.0 <= h["p_open"] <= 1.0 for h in result.hourly)


def test_the_two_halves_are_reported_separately(climatology):
    """The human factor is theirs and the conditions are ours; never merge them.

    The module's whole argument is that August opening 82 % of the night while
    only 55 % of hours are photometrically clear is not a contradiction -- their
    criterion is safety, ours is photometry. Whoever divides one by the other to
    "correct" the human factor will get a number above 1 and cap it, and the
    distinction disappears. Keeping both fields on the result, unmixed, is what
    makes that visible.
    """
    day = dt.datetime(2026, 8, 15, tzinfo=dt.timezone.utc)
    times, clear = _night_inputs(day, 0.42)
    result = outlook(day, clear, times, climatology)

    assert result.human_factor == pytest.approx(0.823, abs=0.005)
    assert result.conditions_probability == pytest.approx(0.42, abs=0.005)
    assert result.human_factor != result.open_probability
    assert "agosto" in result.sentence and "%" in result.sentence


def test_a_month_without_history_refuses_to_guess(climatology):
    """Silence is better than borrowing another month's behaviour.

    If the published table ever loses a month, the honest answer is an error the
    caller has to handle, not a quiet fallback to the annual average dressed up
    as a February figure.
    """
    day = dt.datetime(2026, 2, 15, tzinfo=dt.timezone.utc)
    times, clear = _night_inputs(day, 0.5)
    crippled = {**climatology, "months": {k: v for k, v in climatology["months"].items()
                                          if k != "2"}}

    with pytest.raises(ValueError):
        outlook(day, clear, times, crippled)


def test_a_missing_climatology_file_is_not_an_exception(tmp_path):
    """The file is generated by a worker, so the reader must survive its absence.

    A fresh checkout that has not run the scraper yet should degrade to "no roof
    forecast", not crash the whole night view on import.
    """
    assert load_climatology(tmp_path / "nope.json") is None

    copy = tmp_path / "roof_climatology.json"
    copy.write_text(json.dumps(load_climatology()), encoding="utf-8")
    assert load_climatology(copy)["months"]["7"]["median_fraction"] == pytest.approx(0.835, abs=0.005)
