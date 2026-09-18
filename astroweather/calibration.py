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

# LA VERDAD MEDIA LA LUNA, y esto es lo que quedaba de ese fallo (30-08-2026).
#
# El umbral de arriba se aplicaba igual con Luna que sin ella, y el residuo NO
# tiene el mismo ruido en los dos casos: la ley de dispersion lunar cae como
# 10^(-theta/48,55), asi que cualquier error pequenio en ella se convierte en
# una rampa DENTRO de la hora. Medido sobre horas con 0-10 % de nubes previstas
# -- o sea casi con seguridad limpias, y con una fuente independiente de
# nuestra etiqueta -- la dispersion mediana del residuo es:
#
#     Luna bajo -6 grados   0,0180
#     Luna arriba           0,0339      -> factor 1,89
#
# Con un umbral fijo, eso rechazaba una de cada cuatro horas limpias con Luna:
# la sensibilidad caia del 96,4 % sin Luna al 75,6 % con ella, y la etiqueta
# correlaba con la altura lunar a r = -0,39. La verdad terreno de NUBES estaba
# midiendo la Luna.
#
# EL FACTOR SALE DE LA MEDIDA, no de cuadrar el resultado, y esa distincion
# importa porque el resultado se puede cuadrar solo: ensanchando el umbral la
# tasa de despejado sube siempre. Lo que dice que 1,89 es el numero honesto es
# que a ese valor los dos regimenes pasan a EQUIVOCARSE IGUAL -- con 90-100 %
# de nubes previstas se etiqueta despejado el 53,1 % de las veces sin Luna y el
# ~50 % con ella, cuando con el umbral fijo era 53,1 % contra 21,0 %. Ese 53 %
# alto en ambos casos no es nuestro fallo: es que una prevision de 90-100 % de
# nubes acierta poco en Nerpio, y por eso sirve para comparar regimenes entre
# si pero no como medida de acierto absoluto.
#
# Lo que NO arregla: con Luna la sensibilidad se queda en 85,7 % contra 96,4 %.
# Queda un resto de 10 puntos que ya no es umbral, es que el modelo lunar tiene
# mas error del que tiene el modelo sin Luna. Se declara en vez de taparse
# ensanchando mas.
MOON_UP_ALTITUDE = -6.0
MOON_NOISE_FACTOR = 1.89


def scatter_threshold(moon_altitude: float) -> float:
    """El umbral de dispersion que le toca a esa hora segun donde este la Luna."""
    return (CLEAR_SCATTER_THRESHOLD * MOON_NOISE_FACTOR
            if moon_altitude > MOON_UP_ALTITUDE else CLEAR_SCATTER_THRESHOLD)

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
# RE-DERIVADOS EL 30-08-2026 contra el modelo con el SUELO DE EPOCA.
#
# La deuda que dejaba el bloque de arriba ("cuando se resuelva 2.2 bis, esto se
# re-deriva con un termino temporal") vencia aqui. El suelo dejo de ser una
# constante de siete anios el 29-08, pero quedo siendo UN numero: el de hoy,
# aplicado tambien a 2019, cuando el cielo era medio punto de magnitud mas
# claro. Sobre 15.514 horas estables el residuo salia asi:
#
#     anio    2019    2020    2021    2022    2023    2024    2025    2026
#     centro -0,374  -0,374  -0,405  -0,312  -0,244  -0,182  -0,071  +0,064
#
# Una rampa de 0,437 mag que no es del modelo: es el cielo cambiando debajo. Y
# se comia el 64 % de la tolerancia sin que nadie lo notara, porque la
# tolerancia era ancha.
#
# Con `epoch_floor(when)` -- el suelo interpolado de la serie medida, 22 puntos
# de 2019 a 2026 -- el rango entre anios cae a 0,077 mag y el centro global de
# -0,239 a +0,000. El modelo pasa a ser insesgado en TODAS las epocas, no en la
# media de todas.
#
# Sobre 11.790 horas estables de noches de AJUSTE: centro robusto +0,0007,
# sigma robusta 0,1704 (era 0,2290: la deriva la inflaba una cuarta parte),
# corte a tres sigmas.
CLEAR_LEVEL_CENTRE = 0.001
CLEAR_LEVEL_TOLERANCE = 0.511

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
# Los retroanalisis se llaman `<modelo>_prev<N>d`, y desde el 18-09-2026 no solo
# los hay del ECMWF: ICON, GFS y Meteo-France tienen el suyo para compararse.
# Todo lo que es del ECMWF (entrenar esta curva, aplicarla, su fila en el
# marcador) tiene que casar con ESTE prefijo, no con "prev" a secas: con
# `LIKE '%prev%'` la curva del ECMWF se reentrenaba en silencio con una mezcla
# de cuatro modelos.
BACKFILL_PREFIX = BACKFILL_MODEL + "_prev"


def is_backfill_of(model: str, base: str = BACKFILL_MODEL) -> bool:
    """¿Es `model` el retroanalisis (`<base>_prev<N>d`) de ESE modelo base?"""
    lead = model[len(base) + len("_prev"):] if model.startswith(base + "_prev") else ""
    return len(lead) > 1 and lead.endswith("d") and lead[:-1].isdigit()


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
        -- SOLO EL FOTOMETRO. La allsky escribe en esta misma columna una
        -- magnitud INSTRUMENTAL, monotona en el ADU y con punto cero
        -- ARBITRARIO (`fetch_allsky` lo dice en su comentario): valores de
        -- -16 donde el cielo son 21. Sin este filtro entran en la mediana y
        -- salen residuos de -34 mag contra el modelo. `skybackground` ya
        -- filtraba asi; aqui no, y es el mismo concepto.
        WHERE instrument_type = 'TESS' AND sky_mag_raw IS NOT NULL
          AND t_utc >= '{since}'
        GROUP BY t ORDER BY t
    """).fetchall()
    if not rows:
        return {}

    predictions = predict([t for t, _ in rows])
    by_hour: dict[dt.datetime, list[float]] = defaultdict(list)
    moon_by_hour: dict[dt.datetime, float] = {}
    for (time, measured), clear_sky in zip(rows, predictions):
        if clear_sky.sun_altitude >= -18.0:       # deep night only
            continue
        if clear_sky.moon_altitude > MOON_IN_FIELD_ALTITUDE:
            continue                              # el fotometro mira a la Luna
        key = time.astimezone(dt.timezone.utc).replace(
            minute=0, second=0, microsecond=0, tzinfo=None)
        by_hour[key].append(measured - clear_sky.predicted_mag)
        # La Luna MAS ALTA de la hora: es la que marca cuanto ruido puede meter
        # su ley en el residuo, y quedarse con la media dejaria pasar por
        # tranquila una hora en la que la Luna sale.
        moon_by_hour[key] = max(moon_by_hour.get(key, -90.0),
                                clear_sky.moon_altitude)

    return {
        hour: (float(np.std(values)) < scatter_threshold(moon_by_hour[hour])
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
        WHERE cloud_total IS NOT NULL AND member IS NULL
          AND starts_with(model, ?)
    """, [BACKFILL_PREFIX]).fetchall()

    pairs = []
    for valid, cloud, model in rows:
        if not is_backfill_of(model):
            continue
        hour = valid.astimezone(dt.timezone.utc).replace(
            minute=0, second=0, microsecond=0, tzinfo=None)
        clear = truth.get(hour)
        if clear is None:
            continue
        lead_days = float(model[len(BACKFILL_PREFIX):].rstrip("d"))
        pairs.append((float(cloud), lead_days, bool(clear)))
    return pairs


# WeatherNext 3 tiene su PROPIA curva, en su propio fichero. No sustituye a la
# del ECMWF ni entra en el veredicto: el veredicto va contra el techo real
# (roofcalibration) y WeatherNext solo tiene ~20 noches de techo con prevision.
# Lo que si esta medido (18-09-2026, deja-un-mes-fuera sobre feb-ago 2026, 6126
# pares hora-alcance en 174 noches): calibrado, bate al ECMWF calibrado en los
# cinco alcances, Brier -0,0069 [-0,0126, -0,0013] remuestreando noches, AUC
# 0,789 frente a 0,730. Mezclar los dos NO mejora a WeatherNext solo (+0,0006,
# IC por encima de cero), y la dispersion de sus miembros tampoco suma. Gemini
# lo replico por separado y coincidio a cinco decimales (coop/gemini/).
WEATHERNEXT_MODEL = "weathernext3"
# Los alcances con los que se valido. El dia 0 no tiene ECMWF contra el que
# medirse y el 6 solo existe para la pasada de 00Z: fuera de 1-5 la curva no
# esta comprobada y no se entrena con ello.
WEATHERNEXT_LEAD_DAYS = range(1, 6)


def model_path(model: str = BACKFILL_MODEL, root: Path | None = None) -> Path:
    """Donde vive la curva de cada modelo. La del ECMWF conserva su nombre de
    siempre porque el producto y el runner la leen por ese nombre."""
    base = root if root is not None else DATA_ROOT
    name = ("cloud_calibration.json" if model == BACKFILL_MODEL
            else f"cloud_calibration_{model}.json")
    return base / name


def training_pairs_weathernext(truth: dict[dt.datetime, bool], root=None):
    """(nube %, alcance en dias, despejada) de la media de WeatherNext.

    El alcance sale de `lead_h`, no del nombre del modelo como en el
    retroanalisis del ECMWF. Las pasadas de 00Z y 12Z validas para la misma
    hora y el mismo dia de alcance se PROMEDIAN en un solo par: son dos
    opiniones sobre la misma hora, no dos horas, y contarlas por separado
    duplicaria el peso de las horas que caen en las dos pasadas.
    """
    connection = connect(root)
    rows = connection.execute(f"""
        SELECT valid_utc, lead_h, cloud_total
        FROM forecasts
        WHERE model = '{WEATHERNEXT_MODEL}' AND member IS NULL
          AND cloud_total IS NOT NULL AND lead_h IS NOT NULL
    """).fetchall()

    grouped: dict[tuple[dt.datetime, int], list[float]] = defaultdict(list)
    for valid, lead_h, cloud in rows:
        days = int(lead_h) // 24
        if days not in WEATHERNEXT_LEAD_DAYS:
            continue
        hour = valid.astimezone(dt.timezone.utc).replace(
            minute=0, second=0, microsecond=0, tzinfo=None)
        if hour in truth:
            grouped[(hour, days)].append(float(cloud))
    return [(float(np.mean(clouds)), float(days), bool(truth[hour]))
            for (hour, days), clouds in sorted(grouped.items())]


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


def train(since: str = "2024-02-01", root=None,
          model: str = BACKFILL_MODEL) -> Calibrator:
    truth = observed_clear_hours(since, root)
    if model == BACKFILL_MODEL:
        pairs = training_pairs(truth, root)
    elif model == WEATHERNEXT_MODEL:
        pairs = training_pairs_weathernext(truth, root)
    else:
        raise ValueError(f"no se como entrenar la curva de {model!r}")
    calibrator = fit(pairs, model=model)
    calibrator.save(model_path(model, root))
    return calibrator


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ajusta la curva P(despejado | nube)")
    parser.add_argument("--model", default=BACKFILL_MODEL,
                        choices=(BACKFILL_MODEL, WEATHERNEXT_MODEL))
    parser.add_argument("--since", default=None,
                        help="primera fecha de verdad (por defecto 2024-02-01; "
                             "2026-01-01 para WeatherNext, que empieza ahi)")
    args = parser.parse_args()
    since = args.since or ("2026-01-01" if args.model == WEATHERNEXT_MODEL else "2024-02-01")
    model = train(since=since, model=args.model)
    print(f"{args.model}: entrenado con {model.trained_on} pares | tasa base {model.base_rate:.1%}")
    print(f"  curva: sigmoid({model.intercept:.3f} {model.cloud_weight:+.3f}*nube "
          f"{model.lead_weight:+.3f}*dias) -> {model_path(args.model)}")
    print(f"  Brier del modelo      : {model.brier:.4f}")
    print(f"  Brier de climatologia : {model.brier_climatology:.4f}")
    print(f"  {'BATE' if model.beats_climatology else 'NO BATE'} a la climatologia")
