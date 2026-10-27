# -*- coding: utf-8 -*-
"""P(techo abierto | nube prevista): la curva ajustada contra el TECHO REAL.

Hasta el 2026-09-04 la probabilidad de cada hora salia de `calibration.py`,
cuya verdad terreno es el residuo del fotometro contra el modelo de cielo. Esa
verdad mide la Luna ademas de las nubes, y sobre 31 meses de techo real
(2024-02 a 2026-08) la curva del fotometro sale 26 puntos OPTIMISTA: promete
mas horas abiertas de las que el observatorio abre, mes tras mes. En 74
veredictos publicados nunca habia salido "Cerrado".

La comparacion que vale es otra: el estado real del techo de Nerpio contra el
tiempo previsto para ESAS MISMAS fechas y horas. Aqui se hace a dos escalas,
porque cada una determina un parametro distinto de la logistica
`P = sigmoid(a + b * nube)`:

  * EL CRUCE (donde P = 0,5) sale de los MESES. AstroCamp publica sus horas
    abiertas por mes desde 2012, y contra la fraccion de horas nocturnas con
    nube prevista por debajo de un umbral el ajuste es r = 0,89 con sesgo cero
    cuando el umbral es el 30 %. Pero 31 medias mensuales no distinguen la
    pendiente: el ajuste libre se va a un escalon (medido: a = 1002, b = -3338,
    o sea un escalon en 0,30). Un escalon es una probabilidad de 0 o 1 por
    hora, y eso es mas confianza de la que hay.
  * LA PENDIENTE sale de las HORAS: el scraper guarda el recuento de techos
    cada 5-30 min desde el 2026-08-27. Con el cruce fijado en el mensual, la
    pendiente se ajusta por maxima verosimilitud sobre esas horas, y su
    incertidumbre se mide con un bootstrap POR NOCHES (las horas de una misma
    noche no son independientes). Con 6 noches y 53 horas salio b = -4,5 con
    un intervalo del 10-90 % de [-19,6; -1,9]: la pendiente esta mal
    determinada y el fichero lo dice.

LA NOTA DE LAS 60 NOCHES, Y POR QUE SE ADELANTO (26-10-2026, M1 del
retroanalisis `docs/verificacion_2026-10-26.md`). El cruce mensual ponia un
TECHO a la curva: con el cruce en el 30 % y la pendiente de las horas, P con 0 %
de nube era sigmoid(0,955) = 0,72, y hora a hora, cuando la curva decia
0,64-0,73, el techo estaba abierto el 96-98 % de las horas. La base mensual
(0,56) es la de TODAS las horas con el Sol bajo el horizonte de 31 meses, con
sus noches cubiertas; no es la de una hora con cielo limpio. Desde entonces:

  * Con `NIGHTS_FOR_HOURLY_REFIT` noches, los DOS parametros salen de las horas
    (`METHOD_HOURLY`), y los meses quedan como comprobacion (MAE mensual).
  * Las horas son de NOCHE ASTRONOMICA (Sol < -18 grados a la media hora, de
    `ephem`), no de 18 a 05 UTC: esa ventana fija metia a finales de agosto
    horas con el Sol aun sobre el horizonte, con el techo cerrado por horario y
    no por el tiempo, y bajaba la curva.
  * La nube es la que habia en el archivo al corte de la tarde
    (`ISSUE_CUTOFF_HOUR_UTC`), elegida por `retrieved_at` en el Parquet crudo
    (`asof.py`). La vista deduplicada daba la recogida de las 21-23 UTC de esa
    misma noche: una pendiente ajustada con nube que nadie tenia a las 15 UTC.
  * Por debajo del umbral, o si el ajuste de dos parametros sale sin sentido
    fisico (pendiente >= 0), se cae al metodo anterior (cruce mensual +
    pendiente horaria, con las mismas horas limpias); y sin horas suficientes
    `fit()` se niega, `load_calibrator` cae a la curva del fotometro y el
    arranque en frio sigue como estaba.

Validado prequencialmente (cada noche con una curva ajustada SOLO con las
noches anteriores): `python -m astroweather.hindcast --m1`, figura y cifras en
`docs/verificacion_2026-10-26/m1_*`.

Lo que esta curva NO es: no es "probabilidad de cielo despejado". Es la
probabilidad de que el techo este abierto dada la nube prevista, y lleva
dentro las decisiones del observatorio (abre 30-60 min despues de que su
CloudWatcher pase a despejado; no se ha visto cerrar por tiempo a media noche
en 9 noches). Las puertas de seguridad de `gates.py` siguen multiplicando
encima: son de PREVISION de racha/lluvia/CAPE, y el techo real solo sabe lo
que paso, no lo que se preveia.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import minimize, minimize_scalar

from .asof import ISSUE_CUTOFF_HOUR_UTC, raw_forecast_asof  # noqa: F401
from .calibration import BACKFILL_MODEL, Calibrator
from .ephem import ASTRONOMICAL, sun_positions
from .queries import DATA_ROOT, connect
from .roof import dark_hours
from .site import NERPIO, Site

ROOF_MODEL_PATH = DATA_ROOT / "roof_calibration.json"
OPEN_HOURS_PATH = DATA_ROOT / "open_hours_nerpio.json"

# Con cuantas noches de techo+prevision se deja de necesitar el mensual para
# el cruce. 60 era la cifra pactada el 2026-09-04 (con 9 noches el bootstrap de
# la pendiente cubria un factor 10). 20 desde el 26-10, con la evaluacion
# prequencial de M1 delante (`hindcast --m1`, 42 noches del 05-09 al 25-10):
#   * Brier del veredicto con el umbral en 8 / 15 / 20 / 30 noches: 0,092 /
#     0,105 / 0,119 / 0,123, contra 0,159 de la curva publicada. Cualquier
#     umbral gana; el de 8 es el que reproduce el retroanalisis (0,088), y
#     gana al de 20 por -0,027 [-0,052, -0,011] (IC95, noches remuestreadas).
#   * Pero con 8-16 noches las curvas horarias salian PLANAS: P(100 % de nube)
#     de 0,52 a 0,58, o sea "Abierto" con el cielo cubierto previsto, porque en
#     las primeras noches de septiembre casi no hubo horas cerradas con nube. La
#     pendiente se asento (-2,6 a -3,1, P(100 %) < 0,4) a partir de 18 noches,
#     cuando llegaron las primeras noches cubiertas.
#   * Este otono seco premia decir "Abierto" (83 % de noches abiertas); con un
#     invierno de nube baja una curva plana seria una falsa alarma tras otra.
#     Se paga Brier en las primeras noches por no publicar curvas planas: es un
#     JUICIO sobre el riesgo, no algo que este otono pueda medir.
# Hoy hay 55 noches: el umbral solo decide si el archivo de techo se pierde o
# se reinicia. Y no hay que tocarlo a mano: `fit()` lo cuenta.
NIGHTS_FOR_HOURLY_REFIT = 20
METHOD_HOURLY = ("horario: 2 parametros, horas de noche astronomica, "
                 "nube disponible a las 15 UTC")
METHOD_MONTHLY = "cruce mensual + pendiente horaria"
# Hora "de noche": Sol por debajo de esto a la media hora (noche astronomica).
DARK_SUN_ALTITUDE = ASTRONOMICAL
# Menos que esto y la pendiente ni se intenta: se cae a la curva del fotometro.
MIN_HOURS_FOR_SLOPE = 30
MIN_HOURS_PER_MONTH = 200
BACKFILL_PREV1D = BACKFILL_MODEL + "_prev1d"
# La prevision "de esa tarde": la ultima RECOGIDA anterior a
# `ISSUE_CUTOFF_HOUR_UTC` (15) del dia en que empieza la noche. Es la que ve
# quien decide si sube. La constante y la cuenta viven en `asof.py` desde M16
# (27-10-2026); se reexporta aqui porque `hindcast` la importa de este modulo.
BOOTSTRAP_ROUNDS = 300


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -60, 60)))


@dataclass
class MonthPair:
    year: int
    month: int
    open_fraction: float          # horas abiertas / horas con el Sol bajo el horizonte
    clouds: np.ndarray            # nube prevista (0-1) de las horas nocturnas del mes


@dataclass
class RoofCalibrator:
    """Misma interfaz que `Calibrator`, otra verdad terreno."""

    intercept: float
    cloud_weight: float
    trained_on: int                    # horas de techo con prevision
    brier: float                       # error cuadratico medio MENSUAL de la curva
    brier_climatology: float           # ...y el de predecir siempre la media
    base_rate: float                   # fraccion abierta media de los meses
    fitted_at: str
    crossing: float                    # nube (0-1) en la que P = 0,5
    slope_low: float                   # bootstrap por noches, percentil 10
    slope_high: float                  # percentil 90
    months: int
    nights: int
    monthly_mae: float
    monthly_mae_climatology: float
    method: str                        # "cruce mensual + pendiente horaria" | "horario"
    lead_weight: float = 0.0
    trained_on_model: str = BACKFILL_MODEL
    truth: str = "techo"
    held_out_mae: float | None = None  # meses del ultimo anio, sin verlos
    nights_target: int = NIGHTS_FOR_HOURLY_REFIT

    def probability(self, cloud_percent: float, lead_hours: float = 24.0) -> float:
        z = self.intercept + self.cloud_weight * (cloud_percent / 100.0)
        return float(_sigmoid(z))

    @property
    def beats_climatology(self) -> bool:
        return self.monthly_mae < self.monthly_mae_climatology

    @property
    def nights_missing(self) -> int:
        return max(0, self.nights_target - self.nights)

    def save(self, path: Path | None = None) -> Path:
        path = path or ROOF_MODEL_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path | None = None) -> "RoofCalibrator | None":
        path = path or ROOF_MODEL_PATH
        if not path.exists():
            return None
        return cls(**json.loads(path.read_text(encoding="utf-8")))


def load_calibrator(root: Path | None = None):
    """La curva del techo si existe; si no, la del fotometro. Nunca ninguna en
    silencio: quien la use publica `truth`."""
    roof = RoofCalibrator.load((root / "roof_calibration.json") if root else None)
    if roof is not None:
        return roof
    return Calibrator.load((root / "cloud_calibration.json") if root else None)


# --- las dos verdades -------------------------------------------------------

def monthly_pairs(root: Path | None = None, open_hours_path: Path | None = None,
                  site: Site = NERPIO) -> list[MonthPair]:
    """Fraccion abierta del mes contra la nube prevista de sus horas nocturnas.

    La prevision es el retroanalisis a un dia (`ecmwf_ifs025_prev1d`), que es
    lo que existe para 2024-2026. Horas nocturnas = Sol bajo el horizonte, el
    unico denominador que no produce meses imposibles (ver `roof.py`).
    """
    # La tabla de horas abiertas, de la MISMA raiz que la prevision: hasta el
    # 26-10 se leia siempre de DATA_ROOT aunque se pasara otra raiz.
    path = open_hours_path or ((root / "open_hours_nerpio.json") if root else OPEN_HOURS_PATH)
    if not path.exists():
        return []
    open_hours = json.loads(path.read_text(encoding="utf-8"))
    rows = connect(root).execute("""
        SELECT valid_utc, cloud_total FROM forecasts
        WHERE model = ? AND member IS NULL AND cloud_total IS NOT NULL
        QUALIFY row_number() OVER (PARTITION BY valid_utc ORDER BY run_utc DESC) = 1
        ORDER BY valid_utc
    """, [BACKFILL_PREV1D]).fetchall()
    if not rows:
        return []
    times = [r[0] for r in rows]
    altitudes = [p.altitude for p in sun_positions(times, site)]
    by_month: dict[tuple[int, int], list[float]] = {}
    for (t, cloud), altitude in zip(rows, altitudes):
        if altitude >= 0.0:
            continue
        by_month.setdefault((t.year, t.month), []).append(cloud / 100.0)
    pairs = []
    for (year, month), clouds in sorted(by_month.items()):
        record = open_hours.get(str(year), {}).get("months") or [None] * 12
        hours = record[month - 1]
        if hours is None or len(clouds) < MIN_HOURS_PER_MONTH:
            continue
        pairs.append(MonthPair(year, month, hours / dark_hours(year, month, site),
                               np.asarray(clouds)))
    return pairs


def hourly_pairs(root: Path | None = None,
                 site: Site = NERPIO) -> list[tuple[dt.datetime, float, float]]:
    """(hora UTC, nube prevista %, fraccion de muestras con techo abierto).

    Horas de NOCHE ASTRONOMICA (Sol < `DARK_SUN_ALTITUDE` a la media hora) con
    al menos una muestra del scraper, y la nube del modelo del calibrador tal
    como estaba en el archivo a las `ISSUE_CUTOFF_HOUR_UTC` del dia en que
    empieza la noche: la ultima RECOGIDA anterior al corte (`asof.py`), no la
    ultima fila de la etiqueta de pasada, que es la de las 21-23 UTC.
    """
    root = root or DATA_ROOT
    connection = connect(root)
    try:
        roof = connection.execute(r"""
            SELECT date_trunc('hour', t_utc) AS h,
                   avg(CASE WHEN CAST(regexp_extract(quality_flags, 'open=(\d+)/', 1)
                                 AS INT) > 0 THEN 1.0 ELSE 0.0 END) AS open_fraction
            FROM measurements
            WHERE instrument_id = 'astrocamp_roofs' AND quality_flags LIKE 'open=%'
            GROUP BY 1 ORDER BY 1
        """).fetchall()
    except Exception:
        return []
    if not roof:
        return []
    utc = dt.timezone.utc
    hours = [h if h.tzinfo else h.replace(tzinfo=utc) for h, _ in roof]
    altitudes = [p.altitude for p in
                 sun_positions([h + dt.timedelta(minutes=30) for h in hours], site)]
    try:
        cloud = raw_forecast_asof(connection, root, BACKFILL_MODEL,
                                  min(hours) - dt.timedelta(days=3))
    except Exception:
        return []
    pairs = []
    for hour, (_, open_fraction), altitude in zip(hours, roof, altitudes):
        if altitude >= DARK_SUN_ALTITUDE:
            continue
        value = cloud.issued(hour.astimezone(utc).replace(tzinfo=None))
        if value is not None:
            pairs.append((hour, float(value[0]), float(open_fraction)))
    return pairs


def nights_in(pairs) -> int:
    return len({(t - dt.timedelta(hours=12)).date() for t, _, _ in pairs})


# --- los dos ajustes ---------------------------------------------------------

def crossing_from_months(pairs: list[MonthPair]) -> float:
    """El umbral de nube cuyo escalon reproduce mejor las fracciones mensuales."""
    grid = np.arange(0.05, 0.96, 0.01)
    errors = [np.mean([(p.open_fraction - float((p.clouds < c).mean())) ** 2
                       for p in pairs]) for c in grid]
    return float(grid[int(np.argmin(errors))])


def _slope_ml(clouds: np.ndarray, opened: np.ndarray, crossing: float) -> float:
    def nll(b):
        p = np.clip(_sigmoid(b * (clouds - crossing)), 1e-9, 1 - 1e-9)
        return -np.sum(opened * np.log(p) + (1 - opened) * np.log(1 - p))
    return float(minimize_scalar(nll, bounds=(-40.0, -0.01), method="bounded").x)


def slope_from_hours(pairs, crossing: float, rounds: int = BOOTSTRAP_ROUNDS,
                     seed: int = 1) -> tuple[float, float, float]:
    """Pendiente por maxima verosimilitud con el cruce fijo, y su intervalo
    10-90 % por bootstrap de NOCHES enteras."""
    clouds = np.array([c for _, c, _ in pairs]) / 100.0
    opened = np.array([o for _, _, o in pairs])
    nights = np.array([(t - dt.timedelta(hours=12)).toordinal() for t, _, _ in pairs])
    slope = _slope_ml(clouds, opened, crossing)
    unique = np.unique(nights)
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(rounds):
        pick = rng.choice(unique, len(unique))
        index = np.concatenate([np.flatnonzero(nights == n) for n in pick])
        draws.append(_slope_ml(clouds[index], opened[index], crossing))
    low, high = np.percentile(draws, [10, 90])
    return slope, float(low), float(high)


def _fit_hourly(pairs) -> tuple[float, float]:
    """Los dos parametros por maxima verosimilitud, cuando hay noches para ello."""
    clouds = np.array([c for _, c, _ in pairs]) / 100.0
    opened = np.array([o for _, _, o in pairs])

    def nll(theta):
        p = np.clip(_sigmoid(theta[0] + theta[1] * clouds), 1e-9, 1 - 1e-9)
        return -np.sum(opened * np.log(p) + (1 - opened) * np.log(1 - p))

    best = minimize(nll, x0=[1.0, -4.0], method="Nelder-Mead",
                    options={"maxiter": 4000, "fatol": 1e-8})
    return float(best.x[0]), float(best.x[1])


def _hourly_slope_interval(pairs, rounds: int = BOOTSTRAP_ROUNDS,
                           seed: int = 1) -> tuple[float, float]:
    """Intervalo 10-90 % de la pendiente con los DOS parametros reajustados en
    cada remuestreo de noches enteras (no con el cruce fijo: ya no lo esta)."""
    nights = np.array([(t - dt.timedelta(hours=12)).toordinal() for t, _, _ in pairs])
    unique = np.unique(nights)
    groups = [np.flatnonzero(nights == n) for n in unique]
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(rounds):
        index = np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))])
        draws.append(_fit_hourly([pairs[i] for i in index])[1])
    if not draws:
        slope = _fit_hourly(pairs)[1]
        return slope, slope
    low, high = np.percentile(draws, [10, 90])
    return float(low), float(high)


def _monthly_errors(pairs: list[MonthPair], intercept: float, slope: float):
    return np.array([p.open_fraction - float(_sigmoid(intercept + slope * p.clouds).mean())
                     for p in pairs])


def fit(month_pairs: list[MonthPair], hour_pairs, *,
        nights_target: int = NIGHTS_FOR_HOURLY_REFIT,
        rounds: int = BOOTSTRAP_ROUNDS) -> RoofCalibrator:
    if len(month_pairs) < 12:
        raise ValueError(f"hacen falta 12 meses de techo, hay {len(month_pairs)}")
    if len(hour_pairs) < MIN_HOURS_FOR_SLOPE:
        raise ValueError(f"hacen falta {MIN_HOURS_FOR_SLOPE} horas de techo con "
                         f"prevision, hay {len(hour_pairs)}")
    nights = nights_in(hour_pairs)
    hourly = _fit_hourly(hour_pairs) if nights >= nights_target else None
    # Una pendiente >= 0 (mas nube, mas techo abierto) no es fisica: con pocas
    # horas o sin noches cubiertas el ajuste libre puede salir asi, y entonces
    # manda el metodo anterior, que es el que estaba en produccion.
    if hourly is not None and hourly[1] < 0:
        intercept, slope = hourly
        crossing = -intercept / slope
        low, high = _hourly_slope_interval(hour_pairs, rounds)
        method = METHOD_HOURLY
    else:
        crossing = crossing_from_months(month_pairs)
        slope, low, high = slope_from_hours(hour_pairs, crossing, rounds)
        intercept = -slope * crossing
        method = METHOD_MONTHLY

    errors = _monthly_errors(month_pairs, intercept, slope)
    fractions = np.array([p.open_fraction for p in month_pairs])
    base = float(fractions.mean())
    # Meses del ultimo anio, con el cruce reajustado sin ellos: lo unico que
    # dice si la curva PREDICE o solo describe.
    last_year = max(p.year for p in month_pairs)
    train_months = [p for p in month_pairs if p.year < last_year]
    test_months = [p for p in month_pairs if p.year == last_year]
    held_out = None
    if method == METHOD_HOURLY and test_months:
        # La curva horaria no se ajusta con los meses: los del ultimo anio son
        # su comprobacion externa (solo sus horas de techo, desde el 27-08-2026,
        # caen dentro de los ultimos de ellos).
        held_out = float(np.abs(_monthly_errors(test_months, intercept, slope)).mean())
    elif len(train_months) >= 12 and test_months:
        c = crossing_from_months(train_months)
        held_out = float(np.abs(_monthly_errors(test_months, -slope * c, slope)).mean())

    return RoofCalibrator(
        intercept=float(intercept), cloud_weight=float(slope),
        trained_on=len(hour_pairs),
        brier=float(np.mean(errors ** 2)),
        brier_climatology=float(np.mean((fractions - base) ** 2)),
        base_rate=base,
        fitted_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        crossing=float(crossing), slope_low=low, slope_high=high,
        months=len(month_pairs), nights=nights,
        monthly_mae=float(np.abs(errors).mean()),
        monthly_mae_climatology=float(np.abs(fractions - base).mean()),
        method=method, held_out_mae=held_out, nights_target=nights_target,
    )


def train(root: Path | None = None) -> RoofCalibrator:
    return fit(monthly_pairs(root), hourly_pairs(root))


def summary(model: RoofCalibrator) -> str:
    text = (f"curva del techo ({model.method}): cruce al {model.crossing:.0%} de nube, "
            f"pendiente {model.cloud_weight:.1f} [{model.slope_low:.1f}; "
            f"{model.slope_high:.1f}], {model.months} meses y {model.nights} noches "
            f"({model.trained_on} h); MAE mensual {model.monthly_mae:.3f} contra "
            f"{model.monthly_mae_climatology:.3f} de climatologia")
    if model.held_out_mae is not None:
        text += f", {model.held_out_mae:.3f} fuera de muestra"
    if model.nights_missing:
        text += f"; faltan {model.nights_missing} noches para el ajuste horario"
    elif model.method != METHOD_HOURLY:
        text += "; el ajuste horario salio sin sentido fisico y manda el metodo anterior"
    else:
        text += "; ajuste horario en vigor"
    text += (f"; P(0 %) = {model.probability(0):.2f}, P(30 %) = {model.probability(30):.2f}, "
             f"P(60 %) = {model.probability(60):.2f}, P(100 %) = {model.probability(100):.2f}")
    return text


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Reajusta la curva del techo")
    parser.add_argument("--root", type=Path, default=None,
                        help="raiz del archivo (por defecto DATA_ROOT); el JSON se "
                             "escribe en ella")
    args = parser.parse_args(argv)
    model = train(args.root)
    path = model.save((args.root / "roof_calibration.json") if args.root else None)
    print(summary(model))
    print(f"guardado en {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
