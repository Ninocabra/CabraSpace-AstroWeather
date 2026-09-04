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

LA NOTA DE LAS 60 NOCHES. Cuando el archivo tenga `NIGHTS_FOR_HOURLY_REFIT`
noches de techo con prevision, el ajuste pasa SOLO a horario en los dos
parametros (`method = "horario"`), y el mensual queda como comprobacion. No
hay que acordarse: `fit()` lo decide contando noches, `webexport` publica
cuantas faltan, y `tests/test_roofcalibration.py` cubre el cambio de metodo.
Lo que SI hay que hacer ese dia es MIRAR: que el cruce horario siga cerca del
30 % mensual, y que el MAE mensual de la curva horaria no empeore al del
cruce mensual. Si discrepan, gana el que prediga meses que no ha visto.

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

from .calibration import BACKFILL_MODEL, Calibrator
from .ephem import sun_positions
from .queries import DATA_ROOT, connect
from .roof import dark_hours
from .site import NERPIO, Site

ROOF_MODEL_PATH = DATA_ROOT / "roof_calibration.json"
OPEN_HOURS_PATH = DATA_ROOT / "open_hours_nerpio.json"

# Con cuantas noches de techo+prevision se deja de necesitar el mensual para
# el cruce. 60 es la cifra pactada el 2026-09-04: con 9 noches el bootstrap de
# la pendiente cubria un factor 10, y el cruce horario iba de 0 a 47 %.
NIGHTS_FOR_HOURLY_REFIT = 60
# Menos que esto y la pendiente ni se intenta: se cae a la curva del fotometro.
MIN_HOURS_FOR_SLOPE = 30
MIN_HOURS_PER_MONTH = 200
BACKFILL_PREV1D = BACKFILL_MODEL + "_prev1d"
# La prevision "de esa tarde": la ultima pasada emitida antes de esta hora UTC
# del dia en que empieza la noche. Es la que ve quien decide si sube.
ISSUE_CUTOFF_HOUR_UTC = 15
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
    path = open_hours_path or OPEN_HOURS_PATH
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


def hourly_pairs(root: Path | None = None) -> list[tuple[dt.datetime, float, float]]:
    """(hora UTC, nube prevista %, fraccion de muestras con techo abierto).

    Horas de 18 a 05 UTC con al menos una muestra del scraper, y la prevision
    del modelo del calibrador emitida antes de las 15 UTC del dia de la noche.
    """
    connection = connect(root)
    try:
        roof = connection.execute(r"""
            SELECT date_trunc('hour', t_utc) AS h,
                   avg(CASE WHEN CAST(regexp_extract(quality_flags, 'open=(\d+)/', 1)
                                 AS INT) > 0 THEN 1.0 ELSE 0.0 END) AS open_fraction
            FROM measurements
            WHERE instrument_id = 'astrocamp_roofs' AND quality_flags LIKE 'open=%'
              AND (hour(t_utc) >= 18 OR hour(t_utc) <= 5)
            GROUP BY 1 ORDER BY 1
        """).fetchall()
    except Exception:
        return []
    pairs = []
    for hour, open_fraction in roof:
        hour = hour if hour.tzinfo else hour.replace(tzinfo=dt.timezone.utc)
        night = (hour - dt.timedelta(hours=12)).date()
        cutoff = dt.datetime(night.year, night.month, night.day,
                             ISSUE_CUTOFF_HOUR_UTC, tzinfo=dt.timezone.utc)
        row = connection.execute("""
            SELECT cloud_total FROM forecasts
            WHERE model = ? AND member IS NULL AND cloud_total IS NOT NULL
              AND valid_utc = ? AND run_utc <= ?
            ORDER BY run_utc DESC LIMIT 1
        """, [BACKFILL_MODEL, hour, cutoff]).fetchone()
        if row:
            pairs.append((hour, float(row[0]), float(open_fraction)))
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
    if nights >= nights_target:
        intercept, slope = _fit_hourly(hour_pairs)
        crossing = -intercept / slope
        _, low, high = slope_from_hours(hour_pairs, crossing, rounds)
        method = "horario"
    else:
        crossing = crossing_from_months(month_pairs)
        slope, low, high = slope_from_hours(hour_pairs, crossing, rounds)
        intercept = -slope * crossing
        method = "cruce mensual + pendiente horaria"

    errors = _monthly_errors(month_pairs, intercept, slope)
    fractions = np.array([p.open_fraction for p in month_pairs])
    base = float(fractions.mean())
    # Meses del ultimo anio, con el cruce reajustado sin ellos: lo unico que
    # dice si la curva PREDICE o solo describe.
    last_year = max(p.year for p in month_pairs)
    train_months = [p for p in month_pairs if p.year < last_year]
    test_months = [p for p in month_pairs if p.year == last_year]
    held_out = None
    if len(train_months) >= 12 and test_months:
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
    else:
        text += "; ajuste horario en vigor"
    return text


def main() -> int:
    model = train()
    path = model.save()
    print(summary(model))
    print(f"guardado en {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
