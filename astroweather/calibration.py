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
is what recovers cloudiness back to 2019. That also means the truth INHERITS
every error of the clear-sky model, which is how it spent months measuring the
Moon; the correlation with lunar illumination is the control that has to be run
against the truth itself, not only against the forecast.

What the archive actually says, measured over 33,799 hours (this is the shape
the fit reproduces, not an artefact of it):

    forecast 0-10 % cloud   ->  90.1 % of hours were clear
    forecast 30-50 %        ->  71.9 %
    forecast 50-60 %        ->  65.6 %
    forecast exactly 100 %  ->  41.6 %

Monotonic, real and now STEEP. It was not steep before, and the reason is worth
recording because it was the biggest defect this project has found. Against the
old sky model the same table read 75.3 % / 59.5 / 40.0, and the flatness was an
artefact of the truth, not of the forecast: the moonlight law was Mauna Kea's,
so a clear night with the Moon up came out 0.7-1.8 mag off the model and was
filed as cloudy. That mislabelling landed almost entirely on hours the forecast
had called CLEAR, which is exactly where the curve was flattest. Fixing the sky
model (moonscatter.py) moved the top of the curve from 75.3 % to 90.1 % and left
the overcast end alone, and the skill score went from +3.5 % to +11.7 % over
climatology on the same forecasts.

The base rate moved with it, from 53.5 % of deep-night hours clear to 74.1 %.
Half the difference is nights that were always clear and were being thrown away.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from .moonscatter import IN_FIELD_DEG as MOONSCATTER_IN_FIELD_DEG
from .queries import DATA_ROOT, connect
from .skymodel import predict

# Residual scatter within an hour below which the sky tracked the clear-sky
# model.
#
# This constant used to cite a validation against the observatory's dome-open
# record ("64 % here against ~57 % there"). That justification has been removed
# because it could not be reproduced: roof_open is populated in 42 of 3,666,893
# measurement rows, and nights.any_roof_open is empty, so no such comparison is
# available from this archive. The column exists and the query returns a number
# -- which is exactly the failure mode this project keeps meeting, and the
# reason verification.baselines() reports persistence over zero nights.
#
# What the value IS defensible on: it is the within-hour scatter of a sky that
# tracks the model, and the level test below is what carries the rest.
#
# DEJADO FIJO A PROPOSITO, y esto es un limite conocido, no un descuido. Este
# umbral NO tiene la misma sensibilidad con Luna que sin ella: una nube fina
# redistribuye luz lunar y mueve el fotometro, mientras que sin Luna apenas
# toca el airglow. Medido sobre horas que la prevision determinista -- que no
# sale del fotometro -- da por despejadas o por nubladas:
#
#     dispersion intrahoraria, con Luna llena contra sin Luna
#         prevision 0-10 % de nubes    x1,59
#         prevision 70-100 %           x2,96
#
# Si el exceso fuera error del modelo escalaria igual en las dos filas. No lo
# hace: con Luna se VE nube que sin Luna se pierde. Por eso queda una
# correlacion de -0,43 entre iluminacion lunar y fraccion de noche despejada
# que NO se debe eliminar ensanchando este umbral: un g que la lleva a -0,05
# hace que las horas con prevision de 90-100 % de nubes salgan despejadas el
# 71,5 % de las veces, contra el 42,1 % de ahora. Eso no es una verdad mejor,
# es un detector apagado.
#
# Lo que queda por hacer bien: guardar la sensibilidad JUNTO a la etiqueta
# (la fraccion de luz que pone la Luna) para que el calibrador pueda
# condicionar en ella, en vez de igualar por la fuerza dos etiquetas que
# significan cosas distintas.
CLEAR_SCATTER_THRESHOLD = 0.10
MIN_SAMPLES_PER_HOUR = 4

# Scatter alone is NOT enough, and this was a real hole in the truth chain
# (external audit, 2026-08-27). A flat, homogeneous stratus deck holds the
# photometer at a CONSTANT reading for a whole hour, so its scatter is tiny and
# the hour was being filed as clear. Measured over 4,690 stable hours, 22 % sat
# more than 0.4 mag off the clear-sky model -- and 97 % of those were DARKER,
# not brighter. That direction is the signature of this site: with no city
# below, cloud does not reflect light pollution back at us, it swallows the
# airglow. So the hour must also sit at the right LEVEL, not just be steady.
#
# Both numbers come from the archive, not from taste: the stable-hour residuals
# have a robust centre (the clear-sky model's own bias) and a spread, and the
# cut is placed at three of those sigmas.
#
# They MUST be re-derived whenever the clear-sky model changes, because they
# describe that model's residual and nothing else. Adding the galactic term
# moved the centre from -0.04 to +0.10 and tightened the spread from 0.268 to
# 0.248 -- the model got better, so its residual got narrower. The residual
# offset is the zodiacal elongation gain, which the joint fit of the sky
# budget did not model; it lands here rather than being tuned away.
# Re-derivados el 2026-08-28 contra el modelo de cielo con la ley de dispersion
# lunar medida aqui (moonscatter.py). Sobre 11.766 horas estables de noches de
# AJUSTE: centro robusto -0,0164, sigma robusta 0,2290, corte a tres sigmas.
#
# Lo que arreglan, que era el fallo mas grande del proyecto: contra el modelo
# anterior el residuo con la Luna arriba valia -0,713 y el corte estaba a 0,74
# del centro 0,10, asi que una noche despejada con Luna se archivaba como
# nublada -- la mitad de la muestra. Ahora el centro no depende de la Luna:
#
#     fraccion de luz que pone la Luna   centro    sigma
#         0,00-0,05  (7.171 horas)       -0,029    0,213
#         0,05-0,50  (1.079)             -0,023    0,204
#         0,50-0,90  (1.400)             +0,029    0,244
#         0,90-1,00  (2.116)             +0,003    0,297
#
# DEUDA CON FECHA. Estos dos numeros son de los siete anios juntos, y el suelo
# del cielo NO es estacionario: la mediana sin Luna sube 0,56 mag entre 2019 y
# 2026 (SPEC v8 seccion 2.2 bis). Dentro de la verdad ya se nota, aunque todavia
# no en la tasa global sino en POR QUE LADO se rechaza:
#
#     anio   nivel mediano   rechazo por brillante / por oscuro
#     2019      -0,121              5,9 %  /  2,6 %
#     2024      +0,042              1,5 %  /  1,4 %
#     2026      +0,287              0,2 %  /  5,0 %
#
# La tolerancia de 0,687 absorbe la deriva por ahora (la tasa de despejado se
# queda entre 67 y 77 %), pero al ritmo actual de +0,08 mag/anio el nivel mediano
# alcanza la tolerancia hacia **2031**, y mucho antes empieza a sesgar. Cuando se
# resuelva 2.2 bis, esto se re-deriva con un termino temporal en vez de una media.
CLEAR_LEVEL_CENTRE = -0.016
CLEAR_LEVEL_TOLERANCE = 0.687

# La Luna dentro del campo del fotometro no es brillo de cielo: el instrumento
# la esta mirando. Son 144 muestras de 131.096 -- solo ocurre cerca de una
# parada mayor lunar, porque a esta latitud la Luna no pasa de 80,5 grados --
# pero contaminan el residuo por un factor 2,8 y no se pueden interpretar.
MOON_IN_FIELD_ALTITUDE = 90.0 - MOONSCATTER_IN_FIELD_DEG
MODEL_PATH = DATA_ROOT / "cloud_calibration.json"


# El modelo del que sale el retroanalisis con el que se entrena. La curva de
# calibracion es SUYA y de nadie mas: aplicarla a otro proveedor, o a otra malla
# del mismo proveedor, es usar una correccion medida sobre numeros distintos.
BACKFILL_MODEL = "ecmwf_ifs025"


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
    # Con que modelo se entreno. Tiene valor por defecto para que los ficheros
    # guardados antes del 2026-08-28 sigan cargando; hasta esa fecha el
    # acoplamiento era implicito y por eso nadie vio que el producto consumia
    # ecmwf_ifs mientras la curva venia de ecmwf_ifs025.
    trained_on_model: str = BACKFILL_MODEL

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
        if clear_sky.sun_altitude >= -18.0:       # deep night only
            continue
        if clear_sky.moon_altitude > MOON_IN_FIELD_ALTITUDE:
            continue                              # el fotometro mira a la Luna
        key = time.astimezone(dt.timezone.utc).replace(
            minute=0, second=0, microsecond=0, tzinfo=None)
        by_hour[key].append(measured - clear_sky.predicted_mag)

    return {
        hour: (float(np.std(values)) < CLEAR_SCATTER_THRESHOLD
               and abs(float(np.mean(values)) - CLEAR_LEVEL_CENTRE)
               < CLEAR_LEVEL_TOLERANCE)
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


def fit(pairs: list[tuple[float, float, bool]],
        model: str = BACKFILL_MODEL) -> Calibrator:
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
        trained_on_model=model,
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
