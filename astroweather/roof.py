"""Will the roof be open tonight, and when.

The question the observer actually asks, and the one this project had been
carefully avoiding. gates.py predicts CONDITIONS on purpose -- "the roof is
opened by people, so a closed roof does not mean bad weather" -- which is
correct and unhelpful when you want to know whether to drive out.

So the prediction keeps its two halves apart, because they have completely
different standing:

    P(open) = what this month usually manages  x  how tonight compares

The first half is theirs: AstroCamp's own published record of open dark hours
per month, 2013 onwards -- fourteen years of what the observatory actually
did. The second is ours: the calibrated cloud probability from calibration.py,
expressed relative to an average night.

It is written that way round on purpose. The obvious formulation -- divide the
open fraction by our clear-hour rate to extract a "human factor" -- saturates:
August opens 82 % of the night against a 55 % rate of photometrically clear
hours. That is not a contradiction and capping it at 1.0 would have buried it.
Our criterion is PHOTOMETRIC, theirs is SAFETY, and they open on plenty of
nights we would not call clear.

What that record is measured against was determined rather than assumed. Of
the four plausible denominators, only "hours with the Sun below the horizon"
never produces an impossible month:

    Sun below horizon   mean ratio 0.530, max 0.965, 0 of 156 months over 1
    civil   (-6 deg)    mean 0.588, max 1.099,  4 months over 1
    nautical (-12 deg)  mean 0.664, max 1.288, 25 months over 1
    astronomical (-18)  mean 0.772, max 1.609, 43 months over 1

So the site is open for about 53 % of the available night across the record.

Validated on years the model never saw: fitting 2013-2023 and predicting the
24 months of 2024-2025, the monthly climatology is 14.4 points of mean error
against 18.7 for a single annual number -- a 23 % improvement. An earlier note
claimed 26 %, which came from comparing against the MEDIAN of the monthly
medians (0.487) instead of their mean (0.528). Both baselines are defensible
and the one that flatters us is not the one to quote.

The monthly spread is not noise to be averaged away, and it is not weather
either. April runs from 22 hours (2020, lockdown) to 243 (2023). Whatever
closed the dome in April 2020 will not show up in any forecast, which is
exactly why the human factor is reported with its own range instead of folded
into a single number.
"""

from __future__ import annotations

import calendar
import datetime as dt
import functools
import json
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

from .ephem import night_for, sun_positions
from .site import NERPIO, Site

MONTHS_ES = {1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo",
             6: "junio", 7: "julio", 8: "agosto", 9: "septiembre",
             10: "octubre", 11: "noviembre", 12: "diciembre"}

CLIMATOLOGY_PATH = Path(__file__).resolve().parent.parent / "data" / "roof_climatology.json"
# Years with a full twelve months of record. 2012 and the current year are
# partial and would bias any month they happen to cover.
FIRST_FULL_YEAR, LAST_FULL_YEAR = 2013, 2025


@functools.lru_cache(maxsize=256)
def dark_hours(year: int, month: int, site: Site = NERPIO,
               step_minutes: int = 20) -> float:
    """Hours in the month with the Sun below the horizon.

    Cached: it costs a third of a second of ephemerides and depends only on the
    year and the month, and outlook() was paying it on every single call.
    """
    days = calendar.monthrange(year, month)[1]
    start = dt.datetime(year, month, 1, tzinfo=dt.timezone.utc)
    steps = days * 24 * 60 // step_minutes
    times = [start + dt.timedelta(minutes=step_minutes * i) for i in range(steps)]
    altitudes = np.array([p.altitude for p in sun_positions(times, site)])
    return float((altitudes < 0.0).sum()) * step_minutes / 60.0


@dataclass
class MonthlyRoof:
    """What the observatory usually manages in one month of the year."""

    month: int
    median_fraction: float
    low_fraction: float          # 10th percentile across years
    high_fraction: float         # 90th percentile
    years: int

    @property
    def spread(self) -> float:
        return self.high_fraction - self.low_fraction


def build_climatology(open_hours: dict, site: Site = NERPIO) -> dict:
    """Turn the published table into a fraction of the night, month by month."""
    per_month: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    for year, record in open_hours.items():
        y = int(year)
        if not (FIRST_FULL_YEAR <= y <= LAST_FULL_YEAR):
            continue
        for month, hours in enumerate(record["months"], start=1):
            if hours is None:
                continue
            available = dark_hours(y, month, site)
            if available > 0:
                fraction = hours / available
                if fraction > 1.0:
                    # Clamping quietly is how a wrong denominator survives: the
                    # whole point of choosing "Sun below the horizon" was that
                    # nothing exceeds one. If something does, the denominator
                    # changed and somebody must hear about it.
                    raise ValueError(
                        f"{year}-{month:02d}: {hours} h open against {available:.1f} h "
                        f"of night is impossible; the denominator is wrong")
                per_month[month].append(fraction)

    months = {}
    for month, values in per_month.items():
        if not values:
            continue
        array = np.array(values)
        months[month] = asdict(MonthlyRoof(
            month=month,
            median_fraction=round(float(np.median(array)), 4),
            low_fraction=round(float(np.percentile(array, 10)), 4),
            high_fraction=round(float(np.percentile(array, 90)), 4),
            years=len(values),
        ))
    return {"months": months, "denominator": "sun_below_horizon",
            "source": "astrocamp night open hours table",
            "years": f"{FIRST_FULL_YEAR}-{LAST_FULL_YEAR}"}


def load_climatology(path: Path | None = None) -> dict | None:
    path = path or CLIMATOLOGY_PATH
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _default_clear_rate() -> float:
    """The base rate of clear hours, from the fitted model if there is one."""
    try:
        from .calibration import Calibrator
        model = Calibrator.load()
        if model is not None:
            return float(model.base_rate)
    except Exception:
        pass
    return 0.548


@dataclass
class RoofOutlook:
    """The answer, with its two halves kept apart."""

    night: dt.date
    conditions_probability: float     # ours: will the sky allow it
    human_factor: float               # theirs: do they open when it allows
    clear_rate: float                 # the yardstick "a normal night" means
    open_probability: float
    expected_open_hours: float
    climatological_hours: float
    low_hours: float
    high_hours: float
    hourly: list[dict]

    @property
    def verdict(self) -> str:
        if self.open_probability >= 0.70:
            return "muy probable"
        if self.open_probability >= 0.45:
            return "probable"
        if self.open_probability >= 0.20:
            return "dudoso"
        return "poco probable"

    @property
    def sentence(self) -> str:
        month = MONTHS_ES[self.night.month]
        # Compared against the SAME yardstick the number was computed with. It
        # used to compare against a hardcoded 0.548, so passing a different
        # clear rate could make the sentence contradict the percentage it sat
        # next to.
        return (f"techo {self.verdict} ({self.open_probability*100:.0f} %): "
                f"en {month} aqui se abre el {self.human_factor*100:.0f} % de la "
                f"noche por costumbre, y esta prevision esta "
                f"{'por encima' if self.conditions_probability >= self.clear_rate else 'por debajo'}"
                f" de una noche normal")


def month_hours(day: dt.datetime, climatology: dict,
                site: Site = NERPIO) -> tuple[float, float, float]:
    """Horas abiertas de una noche TIPICA de ese mes: mediana, p10 y p90.

    Es solo costumbre del observatorio (13 anios de horas publicadas), sin
    prevision dentro: por eso puede ir al lado de cualquier curva sin contar
    dos veces nada.
    """
    night = night_for(day, site)
    month = night.night.month
    record = climatology["months"].get(str(month)) or climatology["months"].get(month)
    if record is None:
        raise ValueError(f"no climatology for month {month}")
    available = dark_hours(night.night.year, month, site) / calendar.monthrange(
        night.night.year, month)[1]
    return (round(record["median_fraction"] * available, 1),
            round(record["low_fraction"] * available, 1),
            round(record["high_fraction"] * available, 1))


def outlook(day: dt.datetime, clear_probability: list[float],
            times: list[dt.datetime], climatology: dict,
            site: Site = NERPIO,
            measured_clear_fraction: float | None = None) -> RoofOutlook:
    """Fold tonight's cloud forecast into the site's own opening behaviour.

    `measured_clear_fraction` is the climatological rate of clear hours from
    our own photometer truth, and it is used only as the yardstick that turns
    tonight's probability into "better or worse than usual". It is NOT a
    denominator for the open fraction (see the module docstring).
    """
    night = night_for(day, site)
    month = night.night.month
    record = climatology["months"].get(str(month)) or climatology["months"].get(month)
    if record is None:
        raise ValueError(f"no climatology for month {month}")

    # Dividing the open fraction by our clear-hour rate to extract a "human
    # factor" saturates: August opens 82 % of the night against a 55 % rate of
    # photometrically clear hours. That is not a contradiction and must not be
    # hidden behind a cap -- our criterion is PHOTOMETRIC (the sky tracks the
    # clear-sky model to within three sigma) and theirs is SAFETY. They open on
    # nights we would not call clear.
    #
    # So the climatology leads and the forecast modulates it: what August
    # normally manages, scaled by how much better or worse tonight looks than
    # an average August night.
    # `or` treats a legitimate 0.0 as missing. And the fallback is read from the
    # fitted calibrator rather than typed here: the number written in this file
    # had already drifted from the one in data/cloud_calibration.json.
    if measured_clear_fraction is None:
        measured_clear_fraction = _default_clear_rate()
    clear_rate = float(measured_clear_fraction)
    mean_probability = float(np.mean(clear_probability)) if len(clear_probability) else 0.0
    relative = mean_probability / max(clear_rate, 0.05)
    human = float(record["median_fraction"])

    hourly = []
    for when, probability in zip(times, clear_probability):
        hourly.append({"t": when.isoformat(), "label": when.strftime("%H:%M"),
                       "p_open": round(min(1.0, human * float(probability)
                                           / max(clear_rate, 0.05)), 3)})
    available = dark_hours(night.night.year, month, site) / calendar.monthrange(
        night.night.year, month)[1]

    probability = float(min(1.0, human * relative))
    return RoofOutlook(
        night=night.night.date(),
        conditions_probability=round(mean_probability, 3),
        human_factor=round(human, 3),
        clear_rate=round(clear_rate, 4),
        open_probability=round(probability, 3),
        expected_open_hours=round(probability * available, 1),
        climatological_hours=round(record["median_fraction"] * available, 1),
        low_hours=round(record["low_fraction"] * available, 1),
        high_hours=round(record["high_fraction"] * available, 1),
        hourly=hourly,
    )
