"""Turning a cloud forecast into a probability that has been measured.

A model saying "20 % cloud" is not a probability of anything: it is a model
output. What the observer needs is "of the nights this model said 20 %, how
many were actually usable here?". That is what this module fits, from the
archive, per lead time.

Two deliberate limits:

* It calibrates the DETERMINISTIC forecast, because that is what we have 2.6
  years of. The ensemble fraction (P_raw over 51 members) is the better input,
  but its members are only kept upstream for four days, so its calibration can
  only grow from our own archive going forward (SPEC v8 section 1.1).
* One night is one Bernoulli trial, not 51. The fit is therefore a single
  logistic with lead time as a covariate, not one curve per lead-and-season
  cell, which would starve on this much data.

Ground truth comes from the photometer residual, not from a cloud sensor: the
site's IR channels do not report, and the residual against the clear-sky model
is what recovers cloudiness back to 2019.

What the archive actually says, measured over 33,924 hours (this is the shape
the fit reproduces, not an artefact of it):

    forecast 0-10 % cloud   ->  75.3 % of hours were clear
    forecast 50-60 %        ->  59.5 %
    forecast exactly 100 %  ->  40.0 %

Monotonic and real, but shallow, and ASYMMETRIC in a way worth surfacing in the
product: a fully overcast forecast is genuinely informative, while a clear
forecast still only buys three nights in four. This model is much better at
saying "do not bother" than at promising a good night.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from .queries import DATA_ROOT, connect
from .skymodel import predict

# Residual scatter within an hour below which the sky tracked the clear-sky
# model. Chosen against the site's own climatology: it yields 64 % clear hours
# where the dome-open record gives ~57 %, and dome-open is a lower bound
# because nobody observes every clear night.
CLEAR_SCATTER_THRESHOLD = 0.10
MIN_SAMPLES_PER_HOUR = 4
MODEL_PATH = DATA_ROOT / "cloud_calibration.json"


@dataclass
class Calibrator:
    """P(clear) = sigmoid(a + b*cloud_fraction + c*lead_days)."""

    intercept: float
    cloud_weight: float
    lead_weight: float
    trained_on: int
    brier: float
    brier_climatology: float
    base_rate: float
    fitted_at: str

    def probability(self, cloud_percent: float, lead_hours: float = 24.0) -> float:
        z = (self.intercept
             + self.cloud_weight * (cloud_percent / 100.0)
             + self.lead_weight * (lead_hours / 24.0))
        return float(1.0 / (1.0 + np.exp(-z)))

    @property
    def beats_climatology(self) -> bool:
        """The only claim that matters: does it add information at all?"""
        return self.brier < self.brier_climatology

    def save(self, path: Path | None = None) -> Path:
        path = path or MODEL_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path | None = None) -> "Calibrator | None":
        path = path or MODEL_PATH
        if not path.exists():
            return None
        return cls(**json.loads(path.read_text(encoding="utf-8")))


def observed_clear_hours(since: str = "2024-02-01", root=None) -> dict[dt.datetime, bool]:
    """Hourly truth: did the sky track the clear-sky model during that hour?"""
    connection = connect(root)
    rows = connection.execute(f"""
        SELECT date_trunc('hour', t_utc)
               + INTERVAL (floor(minute(t_utc) / 10) * 10) MINUTE AS t,
               median(sky_mag_raw) AS mag
        FROM measurements
        WHERE sky_mag_raw IS NOT NULL AND t_utc >= '{since}'
        GROUP BY t ORDER BY t
    """).fetchall()
    if not rows:
        return {}

    predictions = predict([t for t, _ in rows])
    by_hour: dict[dt.datetime, list[float]] = defaultdict(list)
    for (time, measured), clear_sky in zip(rows, predictions):
        if clear_sky.sun_altitude < -18.0:       # deep night only
            key = time.astimezone(dt.timezone.utc).replace(
                minute=0, second=0, microsecond=0, tzinfo=None)
            by_hour[key].append(measured - clear_sky.predicted_mag)

    return {
        hour: float(np.std(values)) < CLEAR_SCATTER_THRESHOLD
        for hour, values in by_hour.items()
        if len(values) >= MIN_SAMPLES_PER_HOUR
    }


def training_pairs(truth: dict[dt.datetime, bool], root=None):
    """(cloud %, lead in days, was clear) from the deterministic backfill."""
    connection = connect(root)
    rows = connection.execute("""
        SELECT valid_utc, cloud_total, model
        FROM forecasts
        WHERE cloud_total IS NOT NULL AND member IS NULL AND model LIKE '%prev%'
    """).fetchall()

    pairs = []
    for valid, cloud, model in rows:
        hour = valid.astimezone(dt.timezone.utc).replace(
            minute=0, second=0, microsecond=0, tzinfo=None)
        clear = truth.get(hour)
        if clear is None:
            continue
        lead_days = float(model.split("prev")[1].rstrip("d"))
        pairs.append((float(cloud), lead_days, bool(clear)))
    return pairs


def fit(pairs: list[tuple[float, float, bool]]) -> Calibrator:
    """Logistic fit by maximum likelihood, scored against climatology."""
    if len(pairs) < 100:
        raise ValueError(f"insufficient data to calibrate: {len(pairs)} pairs")

    clouds = np.array([p[0] for p in pairs]) / 100.0
    leads = np.array([p[1] for p in pairs])
    observed = np.array([1.0 if p[2] else 0.0 for p in pairs])

    def negative_log_likelihood(theta):
        z = theta[0] + theta[1] * clouds + theta[2] * leads
        # Clipped so a confident wrong prediction cannot overflow the log.
        probability = np.clip(1.0 / (1.0 + np.exp(-z)), 1e-9, 1 - 1e-9)
        return -np.sum(observed * np.log(probability)
                       + (1 - observed) * np.log(1 - probability))

    best = minimize(negative_log_likelihood, x0=[0.0, -1.0, 0.0], method="Nelder-Mead",
                    options={"maxiter": 4000, "fatol": 1e-8})

    z = best.x[0] + best.x[1] * clouds + best.x[2] * leads
    probability = 1.0 / (1.0 + np.exp(-z))
    base_rate = float(observed.mean())

    return Calibrator(
        intercept=float(best.x[0]),
        cloud_weight=float(best.x[1]),
        lead_weight=float(best.x[2]),
        trained_on=len(pairs),
        brier=float(np.mean((probability - observed) ** 2)),
        # The baseline that costs nothing: always predict the base rate.
        brier_climatology=float(np.mean((base_rate - observed) ** 2)),
        base_rate=base_rate,
        fitted_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )


def reliability(pairs: list[tuple[float, float, bool]], calibrator: Calibrator,
                bins: int = 10) -> list[tuple[float, float, int]]:
    """(predicted, observed, n) per bin -- the curve that exposes overconfidence.

    When the system says 80 %, does it happen 80 % of the time? Nothing else in
    the panel answers that.
    """
    predicted = np.array([calibrator.probability(c, lead * 24) for c, lead, _ in pairs])
    observed = np.array([1.0 if clear else 0.0 for *_, clear in pairs])

    curve = []
    edges = np.linspace(0, 1, bins + 1)
    for low, high in zip(edges[:-1], edges[1:]):
        mask = (predicted >= low) & (predicted < high)
        if mask.sum() >= 20:
            curve.append((float(predicted[mask].mean()),
                          float(observed[mask].mean()), int(mask.sum())))
    return curve


def train(since: str = "2024-02-01", root=None) -> Calibrator:
    truth = observed_clear_hours(since, root)
    pairs = training_pairs(truth, root)
    calibrator = fit(pairs)
    calibrator.save()
    return calibrator


if __name__ == "__main__":
    model = train()
    print(f"entrenado con {model.trained_on} pares | tasa base {model.base_rate:.1%}")
    print(f"  Brier del modelo      : {model.brier:.4f}")
    print(f"  Brier de climatologia : {model.brier_climatology:.4f}")
    print(f"  {'BATE' if model.beats_climatology else 'NO BATE'} a la climatologia")
