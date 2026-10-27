"""Lo que previmos contra lo que paso: el retroanalisis del periodo archivado.

POR QUE EXISTE. Desde el 28-08-2026 el motor archiva su propia prevision
(`workers/archive_prediction.py`, modelo `astroweather`) y la web guarda en el
`git log` de `noche.json` lo que vio el usuario. El marcador (`scoreboard.py`)
puntua proveedores contra el fotometro, pero lo que dice de "nosotros" es
DENTRO de muestra: recalcula con la curva de hoy. Esto es lo otro: lo que se
dijo ANTES de cada noche, con la curva que habia esa noche, contra lo que se
midio despues. Es la unica medida de acierto del producto que no se puede
cuadrar a posteriori.

LAS CINCO TRAMPAS QUE ESTE MODULO RODEA, y por que cada una cambia la cifra:

1. **Muchas pasadas predicen la misma hora.** Se evalua UNA prediccion por hora
   y por franja de plazo (0-12, 12-24, 24-48 h): la ultima emitida en ella. Para
   la noche, la publicacion vigente a las 16 UTC del dia (D0) y la de la
   vispera (D-1). Nunca diez veces la misma hora.
2. **La probabilidad archivada NO es siempre la misma magnitud.** Hasta el
   05-09 era P(despejado) contra el fotometro; desde entonces, para esta noche,
   es P(techo abierto) (`roofcalibration.py`); y desde el 25-09, para la noche
   siguiente, P(despejado) de WeatherNext. `provider_model_version` nombra la
   curva del fotometro en los tres casos, asi que el regimen se RECONSTRUYE
   comparando el numero archivado con lo que daba cada curva vigente a esa
   hora (historial git del archivo). Brier contra el fotometro de una
   P(techo) es comparar magnitudes distintas: se dice en cada tabla.
3. **Fuga.** Ninguna curva se recalcula con los parametros de hoy. La del
   fotometro (ajustada el 30-08) solo puntua horas desde el 31-08; la de
   WeatherNext (18-09, con datos de agosto y septiembre dentro) solo desde el
   19-09. Lo que aqui se reajusta (ver "prequencial") se ajusta noche a noche
   SOLO con las noches anteriores.
4. **Las horas de una noche no son independientes.** Todos los intervalos son
   bootstrap remuestreando NOCHES enteras.
5. **La etiqueta de pasada de los proveedores miente.** `run_utc` es el dia a
   las 00Z, cada recogida trae la pasada mas reciente, y la vista deduplicada
   se queda con la de las 23 UTC: leerla es leer el futuro. Aqui se reconstruye
   desde el Parquet crudo lo que habia en cada instante (`AsOf`), con un
   control positivo: la nube archivada con nuestra prediccion tiene que salir
   igual que la reconstruida.

LAS TRES VERDADES Y LO QUE NO PUEDE VER CADA UNA (pregunta 1 de las seis: ¿puede
la medida detectar el efecto POR CONSTRUCCION?). Medido aqui, no supuesto:

* Fotometro (`calibration.observed_clear_hours`), la verdad del proyecto. Con
  Luna alta y nube gruesa la nube devuelve un cielo tan brillante como el que
  predice el modelo lunar y la hora sale "despejada" (27-09: cubierto total,
  9 de 9 horas "despejadas"); con Luna baja sale "nublado" con el cielo limpio.
* CloudWatcher (`clear_flag`): el sensor con el que AstroCamp decide abrir. Da
  "nublado" con cielo limpio cuando el cielo esta calido (01-09: -5 C de cielo
  con 400 estrellas en la allsky). Cada 30 min hasta el 28-09; desde el 30-09
  el colector solo raspa 2-3 veces por noche.
* Allsky (`star_count`): estrellas detectadas por hora. Cae a 0 todas las
  noches desde las 03 UTC (no es nube), asi que solo vale de 19 a 03 UTC. El
  umbral sale del valle de su distribucion bimodal, no de cuadrar nada.
* Consenso: mayoria de las disponibles, con al menos dos y sin empate. Es un
  constructo pragmatico y se declara como tal; cada tabla va tambien contra el
  fotometro solo, que es la verdad con la que se entrena el producto.

Y el techo: lecturas de `astrocamp_roofs` (misma cadencia que el CloudWatcher)
y la tabla mensual de horas abiertas de AstroCamp, cuyo historial git da las
horas de cada dia por diferencias. Las dos se cruzan entre si en septiembre.

Ejecutar:  python -m astroweather.hindcast
Escribe docs/verificacion_<fecha>.md y sus figuras en docs/verificacion_<fecha>/.
No modifica el archivo ni el repo de datos: solo los lee (git show).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from collections import defaultdict
from pathlib import Path

import numpy as np

from .asof import NWP_LATENCY, AsOf, raw_forecast_asof  # noqa: F401
# La vara de medir y las verdades viven desde el 26-10-2026 en dos modulos
# comunes, porque la nota diaria (`nightscore.py`) y la aspirante de la curva
# (`curvecontest.py`) tienen que medir EXACTAMENTE igual que este informe. Se
# importan con su nombre de siempre para que nada de aqui cambie.
from .skill import RESAMPLES, SEED, NightBootstrap, auc, brier, bss, sigmoid  # noqa: F401
from .nighttruth import (  # noqa: F401
    ALLSKY_CLEAR_STARS, ALLSKY_HOURS, DECISION_HOUR_UTC, OPEN_HOURS_MIN, aware,
    file_history, gated_usable, git, hour_ephemeris, hour_key, night_frames, night_of,
    parse_publication, pick_publication, sensor_truths)
from . import nighttruth as _truth
from .nighttruth import astropy_offline as _astropy_offline  # noqa: F401

UTC = dt.timezone.utc
ENGINE = Path(__file__).resolve().parent.parent
ARCHIVE = Path(os.environ.get("ASTROWEATHER_ARCHIVE_REPO",
                              ENGINE.parent / "AstroWeather-Archive"))
DATA_REPO = Path(os.environ.get("ASTROWEATHER_DATA_REPO",
                                ENGINE.parent / "AstroWeather-Data"))
REPORT_DATE = "2026-10-26"

# Primera prediccion propia archivada: 28-08-2026 19:46 UTC. La ultima noche
# completa antes de este informe es la del 25-10.
FIRST_NIGHT = dt.date(2026, 8, 28)
LAST_NIGHT = dt.date(2026, 10, 25)
# La curva del fotometro se ajusto el 30-08 17:54 con verdad hasta ese dia.
PHOTO_OOS_FROM = dt.datetime(2026, 8, 31)
# La de WeatherNext, el 18-09 07:28, con todas sus horas con verdad desde enero.
WN_OOS_FROM = dt.datetime(2026, 9, 19)
# Curva del techo y ventana de dos horas desde el 04-09 22:25 UTC (`27b2bc6`).
# El umbral del 50 % se eligio mirando los 74 veredictos anteriores.
ROOF_RULE_FROM = dt.date(2026, 9, 5)
# WeatherNext entra en el producto (noches siguientes) el 25-09 (`b70f6be`).
WN_PRODUCT_FROM = dt.datetime(2026, 9, 25, 12, tzinfo=dt.timezone.utc)

LEAD_BUCKETS = ((0, 12), (12, 24), (24, 49))
LEAD_LABELS = ("0-12 h", "12-24 h", "24-48 h")
# DECISION_HOUR_UTC (16), OPEN_HOURS_MIN (2 h) y el corte de la allsky (19-03 UTC,
# 100 estrellas) viven en `nighttruth.py`, con su porque.
# Ridge de los ajustes prequenciales, sobre variables en fraccion 0-1. Solo
# estabiliza las primeras noches (separacion perfecta); no se ha barrido.
RIDGE = 1.0
PREQ_MIN_NIGHTS = 8
PROVIDERS = ("ecmwf_ifs025", "ecmwf_ifs", "gfs_seamless", "icon_seamless",
             "meteofrance_seamless", "weathernext3")
TRUTHS = ("fotometro", "consenso", "cloudwatcher", "allsky")

# Paleta de referencia (skill dataviz), en orden fijo.
C_BLUE, C_ORANGE, C_AQUA, C_YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
C_MAGENTA, C_GREEN, C_VIOLET, C_RED = "#e87ba4", "#008300", "#6250d6", "#e34948"
C_INK, C_INK2, C_GRID, C_GREY = "#0b0b0b", "#52514e", "#e4e3df", "#a3a29c"


# --- utilidades --------------------------------------------------------------

def kappa(matrix) -> float:
    m = np.asarray(matrix, float)
    n = m.sum()
    if not n:
        return float("nan")
    observed = np.trace(m) / n
    expected = float((m.sum(0) * m.sum(1)).sum()) / n ** 2
    return (observed - expected) / (1 - expected) if expected < 1 else float("nan")


def fit_logistic(X: np.ndarray, y: np.ndarray, ridge: float = RIDGE) -> np.ndarray:
    """Logistica por IRLS con ridge en todo menos el intercepto."""
    w = np.zeros(X.shape[1])
    penalty = np.full(X.shape[1], ridge)
    penalty[0] = 0.0
    for _ in range(100):
        p = sigmoid(X @ w)
        weight = p * (1 - p) + 1e-9
        hessian = X.T @ (X * weight[:, None]) + np.diag(penalty)
        gradient = X.T @ (y - p) - penalty * w
        step = np.linalg.solve(hessian, gradient)
        w += step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w


# --- carga de datos ----------------------------------------------------------


# `AsOf` y la lectura del Parquet crudo viven en `asof.py`: la curva del techo
# (`roofcalibration.py`) se entrena con la misma reconstruccion.


class Data:
    """Todo lo que se lee del archivo y de los dos historiales git."""

    def __init__(self, archive: Path, data_repo: Path):
        from .queries import connect
        self.archive = archive
        self.data_repo = data_repo
        self.con = connect(archive)

    def photometer_truth(self, since: str) -> dict[dt.datetime, bool]:
        from .calibration import observed_clear_hours
        return observed_clear_hours(since=since, root=self.archive)

    # Los tres lectores de sensores viven en `nighttruth.py` (26-10-2026): la nota
    # diaria lee EXACTAMENTE lo mismo. Aqui solo se les pasa la conexion.
    def cloudwatcher(self, since: dt.datetime) -> dict[dt.datetime, tuple[float, int]]:
        return _truth.cloudwatcher_hours(self.con, since)

    def allsky(self, since: dt.datetime) -> dict[dt.datetime, float]:
        return _truth.allsky_hours(self.con, since)

    def roof_samples(self, since: dt.datetime) -> list[tuple[dt.datetime, int, int]]:
        return _truth.roof_samples(self.con, since)

    def tess_hourly(self, since: dt.datetime) -> dict[dt.datetime, float]:
        rows = self.con.execute("""
            SELECT date_trunc('hour', t_utc), median(sky_mag_raw), count(*)
            FROM measurements
            WHERE instrument_type = 'TESS' AND sky_mag_raw IS NOT NULL AND t_utc >= ?
            GROUP BY 1
        """, [since]).fetchall()
        return {hour_key(h): float(m) for h, m, n in rows if n >= 20}

    def pm25_hourly(self, since: dt.datetime) -> dict[dt.datetime, float]:
        rows = self.con.execute("""
            SELECT date_trunc('hour', t_utc), avg(pm2_5) FROM measurements
            WHERE pm2_5 IS NOT NULL AND t_utc >= ? GROUP BY 1
        """, [since]).fetchall()
        return {hour_key(h): float(v) for h, v in rows}

    def ours(self) -> list[dict]:
        # Desde el 27-10 (M7) cada fila dice su curva; lo de antes no, y un
        # archivo sin ninguna fila nueva ni siquiera tiene las columnas.
        from .queries import has_column
        labelled = has_column(self.con, "forecasts", "curve_truth")
        curve = ("curve_truth, curve_model, curve_fitted_at" if labelled
                 else "NULL, NULL, NULL")
        rows = self.con.execute(f"""
            SELECT run_utc, valid_utc, lead_h, cloud_total, p_calibrated,
                   provider_model_version, {curve}
            FROM forecasts WHERE model = 'astroweather' AND p_calibrated IS NOT NULL
        """).fetchall()
        return [{"run": aware(r), "valid": hour_key(v), "lead": int(l), "cloud": float(c),
                 "p": float(p), "version": ver, "truth": truth, "model": model,
                 "fitted_at": fitted}
                for r, v, l, c, p, ver, truth, model, fitted in rows if c is not None]

    # LA PASADA NO ES LA QUE DICE LA ETIQUETA. Para los modelos de Open-Meteo,
    # `run_utc` es el DIA truncado a 00Z, pero cada recogida del dia trae la
    # pasada mas reciente que sirve Open-Meteo (00Z, 06Z, 12Z...), y la vista
    # deduplicada de `queries.py` se queda con la ULTIMA recogida: la de las
    # 21-23 UTC, ya con la noche empezada. Usar esa fila como "la prevision de la
    # tarde" mete retrospectiva. Medido el 26-10: para la hora 22 UTC del 17-09,
    # la "pasada del 17-09" vale 69, 65, 17 y 12 % de nube segun se recogiera a
    # las 02, 10, 16 o 23 UTC. Aqui se lee el Parquet CRUDO y se reconstruye lo
    # que habia en el archivo en cada instante (`AsOf`).
    def raw_asof(self, model: str, since: dt.datetime) -> "AsOf":
        return raw_forecast_asof(self.con, self.archive, model, since)

    def eps_asof(self, since: dt.datetime) -> "AsOf":
        base = (self.archive / "forecasts").as_posix()
        rows = self.con.execute(f"""
            SELECT retrieved_at, valid_utc, avg(1 - cloud_total / 100.0),
                   avg(CASE WHEN cloud_total < 20 THEN 1.0 ELSE 0.0 END), count(DISTINCT member)
            FROM read_parquet('{base}/**/*.parquet', union_by_name=true)
            WHERE model = 'ecmwf_ifs025' AND member IS NOT NULL
              AND cloud_total IS NOT NULL AND valid_utc >= ?
            GROUP BY 1, 2
        """, [since]).fetchall()
        return AsOf((aware(ret), hour_key(v), (float(m), float(f)))
                    for ret, v, m, f, n in rows if n >= 40)


    def published(self) -> list[dict]:
        """Cada version de noche.json: lo que vio el usuario, en orden. El analisis
        de cada version es `nighttruth.parse_publication`, el mismo que usa la
        captura del trabajo `publicar` para la nota diaria."""
        return _truth.published_history(self.data_repo)


# --- calibradores vigentes en cada momento ------------------------------------

class Curves:
    """Las tres curvas, version a version, sacadas del historial del archivo."""

    def __init__(self, archive: Path):
        self.photo = {}
        for _, c in file_history(archive, "cloud_calibration.json"):
            self.photo[c["fitted_at"]] = c
        self.roof = sorted(
            ((dt.datetime.fromisoformat(c["fitted_at"]).astimezone(UTC), c)
             for _, c in file_history(archive, "roof_calibration.json")),
            key=lambda x: x[0])
        wn = file_history(archive, "cloud_calibration_weathernext3.json")
        self.wn = wn[-1][1] if wn else None
        if self.wn and len({c["fitted_at"] for _, c in wn}) > 1:
            raise ValueError("hay varias curvas de WeatherNext: este modulo asume una")
        # La curva del fotometro con la que se RECALCULA: la del 30-08.
        self.photo_current = max(self.photo.values(), key=lambda c: c["fitted_at"])

    @staticmethod
    def photo_p(c: dict, cloud, lead_h):
        return sigmoid(c["intercept"] + c["cloud_weight"] * np.asarray(cloud) / 100.0
                       + c["lead_weight"] * np.asarray(lead_h) / 24.0)

    @staticmethod
    def roof_p(c: dict, cloud):
        return sigmoid(c["intercept"] + c["cloud_weight"] * np.asarray(cloud) / 100.0)

    def roof_active(self, when: dt.datetime, slack: dt.timedelta = dt.timedelta(hours=2)):
        """Las versiones que pudo usar una pasada de `when`."""
        before = [c for t, c in self.roof if t <= when + slack]
        return before[-3:]

    def classify(self, row: dict) -> tuple[str, float]:
        """El regimen de una fila archivada: que curva produjo ese numero.

        Cada curva solo es candidata en su epoca: la del fotometro hasta que
        entra la del techo (04-09 22:25), la de WeatherNext desde que entra en
        el producto (25-09, `b70f6be`). Sin esa acotacion, una hora de
        WeatherNext con un 87 % de nube da el mismo numero que la curva del
        fotometro y se etiquetaba mal.

        Desde el 27-10 (M7) la fila trae su curva (`curve_truth`,
        `curve_model`): entonces el regimen se LEE y no se adivina. Lo de aqui
        abajo queda para las filas archivadas antes.
        """
        if row.get("truth"):
            model = row.get("model") or ""
            return ("weathernext" if model.startswith("weathernext")
                    else row["truth"]), 0.0
        candidates = []
        fitted = (row["version"] or "").split("@")[-1]
        photo = self.photo.get(fitted)
        roof_start = self.roof[0][0] if self.roof else None
        if photo is not None and (roof_start is None or row["run"] < roof_start):
            candidates.append(("fotometro",
                               float(self.photo_p(photo, row["cloud"], row["lead"]))))
        for curve in self.roof_active(row["run"]):
            candidates.append(("techo", float(self.roof_p(curve, row["cloud"]))))
        if self.wn is not None and row["run"] >= WN_PRODUCT_FROM:
            for days in range(1, 6):
                candidates.append(("weathernext", float(self.photo_p(
                    self.wn, row["cloud"], days * 24.0))))
        if not candidates:
            return "desconocido", float("nan")
        name, value = min(candidates, key=lambda c: abs(c[1] - row["p"]))
        gap = abs(value - row["p"])
        return (name if gap < 0.02 else "desconocido"), gap


# --- ephemerides --------------------------------------------------------------

# `hour_ephemeris` y `night_frames` viven en `nighttruth.py`.


# --- la verdad del techo, noche a noche ----------------------------------------

def roof_truth(data: Data, frames: dict, samples) -> dict[dt.date, dict]:
    """Horas de techo abierto de cada noche (`nighttruth.roof_truth`), con la tabla
    leida del historial git del archivo como la leyo el informe del 26-10-2026.

    La tabla mensual de AstroCamp (`open_hours_nerpio.json`) se reescribe una
    vez al dia y el historial git guarda cada version: la diferencia entre dos
    fotos consecutivas de agosto-octubre de 2026 es lo que abrieron entre ellas.
    La nota diaria (`nightscore.py`) usa el mismo reparto con la diferencia mes
    a mes (`nighttruth.table_deltas`); sobre estas noches da lo mismo.
    """
    history = file_history(data.archive, "open_hours_nerpio.json")
    snaps = []
    for when, content in history:
        months = (content.get("2026") or {}).get("months") or []
        snaps.append((when, float(sum(v for v in months[7:10] if v is not None))))
    snaps.sort()
    deltas = [(t0, t1, v1 - v0) for (t0, v0), (t1, v1) in zip(snaps[:-1], snaps[1:])]
    return _truth.roof_truth(frames, samples, deltas)


# --- seleccion y puntuacion -----------------------------------------------------

def bucket_of(lead: int):
    for i, (lo, hi) in enumerate(LEAD_BUCKETS):
        if lo <= lead < hi:
            return i
    return None


def select_by_lead(rows: list[dict]) -> dict[tuple[dt.datetime, int], dict]:
    """UNA prediccion por hora y franja de plazo: la ultima emitida en ella."""
    best: dict = {}
    for r in rows:
        b = bucket_of(r["lead"])
        if b is None:
            continue
        key = (r["valid"], b)
        if key not in best or r["run"] > best[key]["run"]:
            best[key] = r
    return best


def score_set(forecasts: dict[str, np.ndarray], outcome: np.ndarray, nights: np.ndarray,
              reference: str | None = None, resamples: int = RESAMPLES) -> dict:
    boot = NightBootstrap(nights, resamples)
    result = {"n": int(len(outcome)), "nights": boot.n_nights,
              "base_rate": float(outcome.mean()) if len(outcome) else float("nan"),
              "models": {}}
    for name, p in forecasts.items():
        entry = {
            "brier": boot.ci(lambda i, p=p: brier(p[i], outcome[i])),
            "auc": boot.ci(lambda i, p=p: auc(p[i], outcome[i])),
            "bss_clim": boot.ci(lambda i, p=p: bss(p[i], outcome[i],
                                                   np.full(len(i), outcome[i].mean()))),
            "mean_p": float(np.mean(p)),
        }
        if reference and reference in forecasts and name != reference:
            ref = forecasts[reference]
            entry["bss_ref"] = boot.ci(lambda i, p=p: bss(p[i], outcome[i], ref[i]))
            entry["d_brier"] = boot.ci(lambda i, p=p: brier(p[i], outcome[i])
                                       - brier(ref[i], outcome[i]))
            entry["d_auc"] = boot.ci(lambda i, p=p: auc(p[i], outcome[i])
                                     - auc(ref[i], outcome[i]))
        result["models"][name] = entry
    return result


def reliability_curve(p: np.ndarray, o: np.ndarray, bins: int = 10):
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for k, (lo, hi) in enumerate(zip(edges[:-1], edges[1:])):
        mask = (p >= lo) & ((p < hi) if k < bins - 1 else (p <= hi))
        if mask.sum():
            out.append((float(p[mask].mean()), float(o[mask].mean()), int(mask.sum())))
    return out


# `gated_usable` vive en `nighttruth.py`: la aspirante de la curva lo usa igual.


def cloud_category(low, mid, high, total) -> str:
    if total is None:
        return "sin capas del 9 km"
    low, mid, high = (low or 0), (mid or 0), (high or 0)
    if total < 20:
        return "ECMWF despejado (<20 %)"
    if low < 20 and mid < 20 and high >= 20:
        return "solo nube alta (cirros)"
    if low < 20 and mid >= 20:
        return "nube media"
    if low >= 20:
        return "nube baja"
    return "otra"


# --- el analisis completo -------------------------------------------------------

def analyse(archive: Path = ARCHIVE, data_repo: Path = DATA_REPO,
            resamples: int = RESAMPLES) -> dict:
    _astropy_offline()
    data = Data(archive, data_repo)
    curves = Curves(archive)
    R: dict = {"generated": dt.datetime.now(UTC).isoformat(timespec="seconds"),
               "archive_head": git(archive, "log", "-1", "--format=%h %cI").decode().strip(),
               "data_head": git(data_repo, "log", "-1", "--format=%h %cI").decode().strip(),
               "resamples": resamples}
    start = dt.datetime(2026, 8, 27, 12, tzinfo=UTC)
    nights = [FIRST_NIGHT + dt.timedelta(days=i)
              for i in range((LAST_NIGHT - FIRST_NIGHT).days + 1)]
    frames = night_frames(nights)

    def in_period(h):
        return FIRST_NIGHT <= night_of(h) <= LAST_NIGHT

    # ================= las verdades
    print("[hindcast] verdad del fotometro (2024-02 -> hoy, ~2 min)...")
    truth_all = data.photometer_truth("2024-02-01")
    prior = defaultdict(list)
    for h, v in truth_all.items():
        if dt.datetime(2024, 8, 1) <= h < dt.datetime(2026, 8, 28):
            prior[h.month].append(v)
    clim_prior = {m: float(np.mean(v)) for m, v in prior.items()}
    R["clim_prior"] = clim_prior
    cw_raw = data.cloudwatcher(start)
    sky_raw = data.allsky(start)
    samples = data.roof_samples(start)
    tess = data.tess_hourly(start)
    pm25 = data.pm25_hourly(start)

    candidate = {h for h in truth_all if in_period(h)} | set(cw_raw) | set(sky_raw)
    for n in nights:
        t = hour_key(frames[n]["sunset"])
        while t <= hour_key(frames[n]["sunrise"]):
            candidate.add(t)
            t += dt.timedelta(hours=1)
    eph = hour_ephemeris(candidate)

    def deep(h):
        return h in eph and eph[h][0] < -18.0

    # Las tres verdades y su consenso: `nighttruth.sensor_truths`, la misma que
    # usa la nota diaria de cada noche.
    T = sensor_truths(truth_all, cw_raw, sky_raw, eph, in_period)
    R["truth_sizes"] = {k: {"hours": len(v), "clear_rate": float(np.mean(list(v.values())))}
                        for k, v in T.items()}

    # distribucion de la allsky (de donde sale el umbral)
    values = np.array([s for h, s in sky_raw.items() if deep(h) and h.hour in ALLSKY_HOURS])
    bands = [(0, 30), (30, 100), (100, 300), (300, 1e9)]
    R["allsky_bands"] = [(lo, hi, int(((values >= lo) & (values < hi)).sum())) for lo, hi in bands]

    # ================= validacion cruzada de las verdades
    def moon_cat_h(h):
        if eph[h][1] < 0:
            return "Luna bajo el horizonte"
        return "Luna arriba, <50 %" if frames[night_of(h)]["moon"] < 0.5 else "Luna arriba, >=50 %"

    agreement = {}
    for a_name, b_name in (("fotometro", "cloudwatcher"), ("fotometro", "allsky"),
                           ("cloudwatcher", "allsky")):
        table = defaultdict(lambda: np.zeros((2, 2), int))
        for h in set(T[a_name]) & set(T[b_name]):
            table["todas"][int(T[b_name][h]), int(T[a_name][h])] += 1
            table[moon_cat_h(h)][int(T[b_name][h]), int(T[a_name][h])] += 1
        agreement[f"{a_name} vs {b_name}"] = {
            k: {"matrix": m.tolist(), "kappa": kappa(m), "n": int(m.sum())}
            for k, m in table.items()}
    R["truth_agreement"] = agreement

    # cobertura de las fuentes por mes
    coverage = defaultdict(lambda: defaultdict(int))
    for n in nights:
        key = f"{n:%Y-%m}"
        coverage[key]["noches"] += 1
        coverage[key]["horas_oscuras"] += int(round(frames[n]["dark_hours"]))
        for name in TRUTHS:
            coverage[key]["horas_" + name] += sum(1 for h in T[name] if night_of(h) == n)
        coverage[key]["lecturas_techo"] += sum(
            1 for t, _, _ in samples if frames[n]["sunset"] <= t <= frames[n]["sunrise"])
    R["coverage"] = {k: dict(v) for k, v in sorted(coverage.items())}

    # ================= la verdad del techo
    roof = roof_truth(data, frames, samples)
    R["roof"] = {str(k): v for k, v in roof.items()}
    both = [(roof[n]["table_hours"], roof[n]["sample_hours"]) for n in nights
            if roof[n]["table_quality"] == "limpio" and roof[n]["sample_hours"] is not None]
    if both:
        a = np.array(both)
        R["roof_crosscheck"] = {
            "n": len(both), "r": float(np.corrcoef(a[:, 0], a[:, 1])[0, 1]),
            "mae": float(np.mean(np.abs(a[:, 0] - a[:, 1]))),
            "bias": float(np.mean(a[:, 0] - a[:, 1])),
            "agree_open": int(sum((x >= OPEN_HOURS_MIN) == (y >= OPEN_HOURS_MIN)
                                  for x, y in both))}

    # ================= nuestras filas y su regimen
    ours = data.ours()
    regimes = defaultdict(int)
    gaps = []
    for r in ours:
        r["regime"], r["gap"] = curves.classify(r)
        regimes[r["regime"]] += 1
        gaps.append(r["gap"])
    R["regimes"] = dict(regimes)
    R["regime_gap_p99"] = float(np.nanpercentile(gaps, 99))
    first_seen, last_seen = {}, {}
    for r in sorted(ours, key=lambda r: r["run"]):
        first_seen.setdefault(r["regime"], r["run"])
        last_seen[r["regime"]] = r["run"]
    R["regime_span"] = {k: (first_seen[k].isoformat(), last_seen[k].isoformat())
                        for k in first_seen}

    asof = {m: data.raw_asof(m, start - dt.timedelta(days=1)) for m in PROVIDERS}
    eps = data.eps_asof(start - dt.timedelta(days=1))
    photo = curves.photo_current
    selected = select_by_lead(ours)
    # El ECMWF que habia en el archivo AL EMITIR cada prediccion.
    ecmwf_sel = {key: asof["ecmwf_ifs025"].at(key[0], r["run"]) for key, r in selected.items()}
    # Control positivo de la reconstruccion: en los regimenes que usaron el
    # ECMWF, la nube archivada con nuestra prediccion tiene que ser la que la
    # reconstruccion dice que habia. Si no casa, la reconstruccion no vale.
    checks = [abs(ecmwf_sel[key][0] - r["cloud"]) for key, r in selected.items()
              if r["regime"] in ("techo", "fotometro") and ecmwf_sel[key] is not None]
    R["asof_check"] = {"n": len(checks),
                       "exact": float(np.mean([c < 0.5 for c in checks])) if checks else None,
                       "median_abs": float(np.median(checks)) if checks else None,
                       "p90_abs": float(np.percentile(checks, 90)) if checks else None}
    # Y lo que habria dado la vista deduplicada (ultima recogida del dia de la
    # etiqueta): la retrospectiva que la reconstruccion evita.
    dedup = [abs(x[0] - r["cloud"]) for key, r in selected.items()
             if r["regime"] in ("techo", "fotometro")
             for x in [max((v for v in asof["ecmwf_ifs025"].by_hour.get(key[0], [])
                            if v[1][4].date() <= r["run"].date()),
                           key=lambda v: v[0], default=(None, None))[1]]
             if x is not None]
    R["asof_check"]["dedup_exact"] = float(np.mean([c < 0.5 for c in dedup])) if dedup else None
    # Coherencia interna de cada proveedor: nube total contra su capa mayor.
    R["layer_consistency"] = {}
    for m in ("ecmwf_ifs025", "ecmwf_ifs", "icon_seamless", "gfs_seamless"):
        vals = [v for lst in asof[m].by_hour.values() for _, v in lst
                if None not in v[1:4]]
        if vals:
            R["layer_consistency"][m] = {
                "n": len(vals),
                "total_below_max_layer": float(np.mean([v[0] + 1 < max(v[1:4]) for v in vals]))}

    # ================= (a) probabilidad horaria, por plazo y por verdad
    R["a"], R["a_reliability"], R["a_regime"] = {}, {}, {}
    records = []
    for truth_name in TRUTHS:
        truth = T[truth_name]
        R["a"][truth_name], R["a_reliability"][truth_name] = {}, {}
        R["a_regime"][truth_name] = {}
        for b, label in enumerate(LEAD_LABELS):
            rows = [(h, r, ecmwf_sel[(h, bb)]) for (h, bb), r in selected.items()
                    if bb == b and h in truth and h >= PHOTO_OOS_FROM
                    and ecmwf_sel[(h, bb)] is not None]
            if len(rows) < 30:
                continue
            o = np.array([1.0 if truth[h] else 0.0 for h, _, _ in rows])
            nn = np.array([night_of(h).toordinal() for h, _, _ in rows])
            p_pub = np.array([r["p"] for _, r, _ in rows])
            p_raw = np.array([1 - e[0] / 100 for _, _, e in rows])
            p_photo = np.array([float(curves.photo_p(photo, e[0], r["lead"]))
                                for _, r, e in rows])
            fc = {"publicado": p_pub, "ECMWF + curva fotometro": p_photo,
                  "ECMWF crudo": p_raw}
            if truth_name == "fotometro":
                fc["climatologia previa"] = np.array([clim_prior.get(h.month, np.nan)
                                                      for h, _, _ in rows])
            block = score_set(fc, o, nn, reference="ECMWF crudo", resamples=resamples)
            reg = np.array([r["regime"] for _, r, _ in rows])
            block["regimes"] = {str(k): int((reg == k).sum()) for k in np.unique(reg)}
            R["a"][truth_name][label] = block
            if truth_name in ("fotometro", "consenso"):
                R["a_reliability"][truth_name][label] = {
                    k: reliability_curve(v, o) for k, v in fc.items()
                    if k != "climatologia previa"}
                R["a_regime"][truth_name][label] = {}
                for name in np.unique(reg):
                    m = reg == name
                    if m.sum() >= 30:
                        R["a_regime"][truth_name][label][str(name)] = score_set(
                            {"publicado": p_pub[m], "ECMWF + curva fotometro": p_photo[m],
                             "ECMWF crudo": p_raw[m]}, o[m], nn[m],
                            reference="ECMWF crudo", resamples=resamples)
            if truth_name in ("fotometro", "consenso") and b == 0:
                rng = np.random.default_rng(SEED)
                R.setdefault("a_controls", {})[truth_name] = score_set(
                    {"ECMWF + curva fotometro": p_photo,
                     "la misma, barajada": rng.permutation(p_photo),
                     "verdad (perfecta)": o.copy(),
                     "climatologia de la muestra": np.full(len(o), o.mean())},
                    o, nn, resamples=resamples)
    # registros horarios de la franja 0-12 h para los sesgos
    for (h, b), r in selected.items():
        e = ecmwf_sel[(h, b)]
        if b != 0 or e is None or h < PHOTO_OOS_FROM or not in_period(h):
            continue
        if h not in T["fotometro"] and h not in T["consenso"]:
            continue
        # LAS CAPAS, DEL ECMWF DE 9 KM. Las del 0,25 grados no casan con su propia
        # nube total (la mitad de las filas tiene la total por debajo de su capa
        # mayor; ver `layer_consistency`), y una categoria hecha con ellas no
        # describe la prevision que se uso.
        e9 = asof["ecmwf_ifs"].at(h, r["run"]) or (None, None, None, None, None)
        records.append({"h": h, "fotometro": T["fotometro"].get(h),
                        "consenso": T["consenso"].get(h),
                        "p_photo": float(curves.photo_p(photo, e[0], r["lead"])),
                        "p_raw": 1 - e[0] / 100, "p_pub": r["p"], "regime": r["regime"],
                        "low": e9[1], "mid": e9[2], "high": e9[3], "total": e9[0],
                        "moon_alt": eph[h][1] if h in eph else 0.0,
                        "moon": frames[night_of(h)]["moon"]})
    R["bias"] = {t: bias_tables(records, t, resamples) for t in ("fotometro", "consenso")}

    # ================= (a3) la curva del techo contra el techo, hora a hora
    roof_hour = defaultdict(list)
    for t, o_, k in samples:
        if t.hour >= 18 or t.hour <= 5:
            roof_hour[hour_key(t)].append(1.0 if o_ > 0 else 0.0)
    rows = [(h, r) for (h, b), r in selected.items()
            if b == 0 and r["regime"] == "techo" and h in roof_hour]
    if len(rows) >= 30:
        o3 = np.array([1.0 if np.mean(roof_hour[h]) >= 0.5 else 0.0 for h, _ in rows])
        n3 = np.array([night_of(h).toordinal() for h, _ in rows])
        fc3 = {"publicado (curva del techo)": np.array([r["p"] for _, r in rows]),
               "curva del fotometro (control)": np.array(
                   [float(curves.photo_p(photo, r["cloud"], r["lead"])) for _, r in rows]),
               "nube cruda (1-nube)": np.array([1 - r["cloud"] / 100 for _, r in rows])}
        R["a_roof"] = score_set(fc3, o3, n3, resamples=resamples)
        R["a_roof"]["span"] = (min(h for h, _ in rows).isoformat(),
                               max(h for h, _ in rows).isoformat())
        R["a_roof_reliability"] = {k: reliability_curve(v, o3) for k, v in fc3.items()}

    # ================= prequencial 1: la curva del techo ajustada SOLO con horas
    pairs = sorted(((h, r["cloud"], 1.0 if np.mean(roof_hour[h]) >= 0.5 else 0.0, r)
                    for (h, b), r in selected.items()
                    if b == 0 and r["regime"] in ("techo", "fotometro") and h in roof_hour),
                   key=lambda x: x[0])
    roof_curve_by_night = {}
    preq = []
    for n in nights:
        train = [p for p in pairs if night_of(p[0]) < n]
        if len({night_of(p[0]) for p in train}) < PREQ_MIN_NIGHTS:
            continue
        X = np.array([[1.0, c / 100] for _, c, _, _ in train])
        y = np.array([o_ for _, _, o_, _ in train])
        w = fit_logistic(X, y)
        roof_curve_by_night[n] = w
        for h, c, o_, r in pairs:
            if night_of(h) == n and r["regime"] == "techo":
                preq.append((h, float(sigmoid(w[0] + w[1] * c / 100)), o_, r["p"]))
    if len(preq) >= 30:
        o4 = np.array([x[2] for x in preq])
        n4 = np.array([night_of(x[0]).toordinal() for x in preq])
        R["preq_roof"] = score_set({"curva horaria prequencial": np.array([x[1] for x in preq]),
                                    "publicado (curva del techo)": np.array([x[3] for x in preq])},
                                   o4, n4, reference="publicado (curva del techo)",
                                   resamples=resamples)
        R["preq_roof_reliability"] = reliability_curve(np.array([x[1] for x in preq]), o4)
        R["preq_roof_curves"] = [(str(n), float(w[0]), float(w[1]),
                                  float(sigmoid(w[0])), float(-w[0] / w[1]) if w[1] else None)
                                 for n, w in roof_curve_by_night.items()]

    # ================= prequencial 2: ¿las capas de nube anaden algo? (consenso)
    lay = []
    for (h, b), r in selected.items():
        e = ecmwf_sel[(h, b)]
        e9 = asof["ecmwf_ifs"].at(h, r["run"])
        if (b == 0 and e is not None and e9 is not None and h in T["consenso"]
                and h >= PHOTO_OOS_FROM and None not in e9[1:4]):
            lay.append((h, e, r, 1.0 if T["consenso"][h] else 0.0, e9))
    lay.sort(key=lambda x: x[0])
    preq_l = []
    for n in nights:
        train = [x for x in lay if night_of(x[0]) < n]
        if len({night_of(x[0]) for x in train}) < PREQ_MIN_NIGHTS * 2:
            continue
        y = np.array([x[3] for x in train])
        XA = np.array([[1.0, x[1][0] / 100] for x in train])
        XB = np.array([[1.0, x[4][1] / 100, x[4][2] / 100, x[4][3] / 100] for x in train])
        wa, wb = fit_logistic(XA, y), fit_logistic(XB, y)
        for h, e, r, o_, e9 in lay:
            if night_of(h) != n:
                continue
            preq_l.append((h, o_, float(sigmoid(wa @ [1.0, e[0] / 100])),
                           float(sigmoid(wb @ [1.0, e9[1] / 100, e9[2] / 100, e9[3] / 100])),
                           float(curves.photo_p(photo, e[0], r["lead"]))))
        R["preq_layers_last"] = {"total": wa.tolist(), "capas": wb.tolist(), "noche": str(n)}
    if len(preq_l) >= 30:
        o5 = np.array([x[1] for x in preq_l])
        n5 = np.array([night_of(x[0]).toordinal() for x in preq_l])
        R["preq_layers"] = score_set(
            {"curva fija del fotometro (2,5 anios, total)": np.array([x[4] for x in preq_l]),
             "prequencial, nube total": np.array([x[2] for x in preq_l]),
             "prequencial, capas baja/media/alta": np.array([x[3] for x in preq_l])},
            o5, n5, reference="curva fija del fotometro (2,5 anios, total)",
            resamples=resamples)

    # ================= multimodelo: curvas ajustadas en el retroanalisis 2024-26
    # (verdad: fotometro) y puntuadas FUERA de muestra en este periodo. ICON
    # tiene su propio retroanalisis desde el 18-09, asi que esto se puede
    # validar hoy sin esperar noches.
    R["multimodel"] = multimodel_test(data, truth_all, T, asof, resamples)

    # ================= (b) el veredicto de la noche
    pubs = data.published()
    R["n_publications"] = len(pubs)
    R["publications_span"] = (pubs[0]["generated"].isoformat(),
                              pubs[-1]["generated"].isoformat())

    def pick(night: dt.date, day: dt.date):
        return pick_publication(pubs, night, day)

    night_rows = []
    for n in nights:
        f = frames[n]
        row = {"night": n, "moon": f["moon"], "dark_hours": f["dark_hours"], "roof": roof[n],
               "sunrise": f["sunrise"], "dawn": f["dawn"]}
        for name in TRUTHS:
            hours_n = sorted(h for h in T[name] if night_of(h) == n)
            longest = run_ = 0
            previous = None
            for h in hours_n:
                if T[name][h]:
                    run_ = run_ + 1 if (previous is not None and run_
                                        and h - previous == dt.timedelta(hours=1)) else 1
                else:
                    run_ = 0
                longest = max(longest, run_)
                previous = h
            row[name] = {"hours": len(hours_n), "clear": int(sum(T[name][h] for h in hours_n)),
                         "longest": longest}
        row["phot_coverage"] = row["fotometro"]["hours"] / max(1.0, f["dark_hours"])
        d0_when = dt.datetime(n.year, n.month, n.day, DECISION_HOUR_UTC, tzinfo=UTC)
        layers = [asof["ecmwf_ifs"].at(h, d0_when) for h in
                  (hour_key(f["dusk"]) + dt.timedelta(hours=k) for k in
                   range(int(f["dark_hours"]) + 1))] if f["dusk"] else []
        layers = [x for x in layers if x is not None and None not in x[1:]]
        row["ecmwf_layers"] = ([float(np.mean([x[i] for x in layers])) for i in range(4)]
                               if layers else None)
        for tag, chosen in (("d0", pick(n, n)), ("dm1", pick(n, n - dt.timedelta(days=1)))):
            if chosen is None:
                row[tag] = None
                continue
            pub, entry = chosen
            dark = [x for x in entry["hours"] if f["dusk"] and f["dawn"]
                    and f["dusk"] - dt.timedelta(minutes=30) <= aware(x["t"]) <= f["dawn"]]
            gated = [x for x in entry["hours"]
                     if x["p_clear"] is not None and x["p_usable"] is not None
                     and x["p_clear"] >= 0.5 and x["p_usable"] < 0.5]
            window = entry["window"]
            clouds_dark = [x["cloud"] for x in dark if x["cloud"] is not None]
            row[tag] = {
                "generated": pub["generated"], "p": entry["p_open"], "open": entry["open"],
                "derived": entry["verdict_derived"], "window": window,
                "expected_hours": entry["expected_hours"],
                "cloud_model": entry["cloud_model"], "truth": entry["truth"],
                "cloud_dark": float(np.mean(clouds_dark)) if clouds_dark else None,
                "gated": sorted({x["limits"] for x in gated}),
                "max_p_clear": max((x["p_clear"] for x in dark if x["p_clear"] is not None),
                                   default=None),
                "sky_tercile": entry.get("sky_tercile"), "aod_tercile": entry.get("aod_tercile"),
                "seeing_tercile": entry.get("cupula_seeing") or entry.get("seeing_tercile"),
                "darkest_mag": entry.get("darkest_mag"), "aod": entry.get("aod"),
                "hours": entry["hours"],
            }
            if window and tag == "d0":
                inside = [o_ for t, o_, k in samples if window[0] <= t < window[1]]
                row[tag]["window_samples"] = len(inside)
                row[tag]["window_open_frac"] = (float(np.mean([x > 0 for x in inside]))
                                                if inside else None)
        night_rows.append(row)

    def verdict_block(tag: str, from_night: dt.date | None = None, p_override=None) -> dict | None:
        rows = [r for r in night_rows if r[tag] is not None and r["roof"]["opened"] is not None
                and r[tag]["open"] is not None and r[tag]["p"] is not None
                and (from_night is None or r["night"] >= from_night)]
        if p_override is not None:
            rows = [r for r in rows if r["night"] in p_override]
        if len(rows) < 5:
            return None
        p = np.array([p_override[r["night"]] if p_override else r[tag]["p"] for r in rows],
                     float)
        f = (p >= 0.5).astype(float) if p_override else \
            np.array([1.0 if r[tag]["open"] else 0.0 for r in rows])
        o = np.array([1.0 if r["roof"]["opened"] else 0.0 for r in rows])
        boot = NightBootstrap(np.arange(len(rows)), resamples)

        def table(i):
            ff, oo = f[i], o[i]
            return (int(((ff == 1) & (oo == 1)).sum()), int(((ff == 1) & (oo == 0)).sum()),
                    int(((ff == 0) & (oo == 1)).sum()), int(((ff == 0) & (oo == 0)).sum()))

        def hss(i):
            a, b_, c, d = table(i)
            n = a + b_ + c + d
            expected = ((a + b_) * (a + c) + (c + d) * (b_ + d)) / n if n else 0
            return (a + d - expected) / (n - expected) if n - expected else float("nan")

        by_night = {r["night"]: r for r in night_rows}
        persistence = []
        for r in rows:
            prev = by_night.get(r["night"] - dt.timedelta(days=1))
            persistence.append(np.nan if not prev or prev["roof"]["opened"] is None
                               else 1.0 if prev["roof"]["opened"] else 0.0)
        persistence = np.array(persistence)
        mask = np.isfinite(persistence)
        a, b_, c, d = table(boot.everything)
        return {
            "n": len(rows), "hits": a, "false_alarms": b_, "misses": c, "correct_neg": d,
            "accuracy": boot.ci(lambda i: float(np.mean(f[i] == o[i]))),
            "always_open_acc": float(o.mean()),
            "hss": boot.ci(hss),
            "brier": boot.ci(lambda i: brier(p[i], o[i])),
            "brier_clim": boot.ci(lambda i: brier(np.full(len(i), o[i].mean()), o[i])),
            "bss_clim": boot.ci(lambda i: bss(p[i], o[i], np.full(len(i), o[i].mean()))),
            "auc": boot.ci(lambda i: auc(p[i], o[i])),
            "base_rate": float(o.mean()), "forecast_rate": float(f.mean()),
            "mean_p": float(p.mean()),
            "persistence_acc": float(np.mean(persistence[mask] == o[mask])) if mask.any() else None,
            "persistence_n": int(mask.sum()),
            "model_acc_same": float(np.mean(f[mask] == o[mask])) if mask.any() else None,
            "nights_list": [str(r["night"]) for r in rows],
        }

    R["b"] = {"D0 todo": verdict_block("d0"),
              "D0 desde 05-09": verdict_block("d0", ROOF_RULE_FROM),
              "D-1 todo": verdict_block("dm1"),
              "D-1 desde 05-09": verdict_block("dm1", ROOF_RULE_FROM)}
    # noches sin verdad de techo y lo que se dijo
    R["b_unknown"] = [(str(r["night"]), r["d0"]["open"] if r["d0"] else None,
                       r["roof"]["table_quality"], r["roof"]["samples"])
                      for r in night_rows if r["roof"]["opened"] is None]

    # que habria dicho el veredicto con la curva horaria prequencial
    from .gates import opening_probability
    what_if = {}
    for r in night_rows:
        w = roof_curve_by_night.get(r["night"])
        if w is None or not r["d0"] or r["night"] < ROOF_RULE_FROM:
            continue
        usable = gated_usable(r["d0"]["hours"], lambda c, w=w: float(sigmoid(w[0] + w[1] * c / 100)))
        if usable:
            what_if[r["night"]] = opening_probability(usable)
    R["b_whatif"] = verdict_block("d0", ROOF_RULE_FROM, p_override=what_if)
    R["b_published_same"] = verdict_block(
        "d0", ROOF_RULE_FROM, p_override={r["night"]: r["d0"]["p"] for r in night_rows
                                          if r["night"] in what_if and r["d0"]})
    same = [r for r in night_rows if r["night"] in what_if and r["roof"]["opened"] is not None
            and r["d0"] and r["d0"]["p"] is not None]
    if len(same) >= 10:
        o = np.array([1.0 if r["roof"]["opened"] else 0.0 for r in same])
        R["b_whatif_paired"] = score_set(
            {"publicado": np.array([r["d0"]["p"] for r in same]),
             "curva horaria prequencial": np.array([what_if[r["night"]] for r in same])},
            o, np.array([r["night"].toordinal() for r in same]), reference="publicado",
            resamples=resamples)

    # las noches falladas, con su porque
    failures = []
    for r in night_rows:
        for tag in ("d0", "dm1"):
            e = r[tag]
            if not e or e["open"] is None or r["roof"]["opened"] is None:
                continue
            if bool(e["open"]) == bool(r["roof"]["opened"]):
                continue
            failures.append({"night": r["night"], "tag": "D0" if tag == "d0" else "D-1",
                             "kind": "falsa alarma" if e["open"] else "sorpresa",
                             "p": e["p"], "truth_curve": e["truth"], "window": e["window"],
                             "cloud_dark": e["cloud_dark"], "layers": r["ecmwf_layers"],
                             "gated": e["gated"], "roof_hours": r["roof"]["hours"],
                             "roof_source": r["roof"]["source"], "moon": r["moon"],
                             "sky": {k: (r[k]["clear"], r[k]["hours"]) for k in TRUTHS},
                             "why": failure_reason(r, e)})
    R["failures"] = failures

    pairs_h = [(r["d0"]["expected_hours"], r["roof"]["hours"]) for r in night_rows
               if r["d0"] and r["d0"]["expected_hours"] is not None
               and r["roof"]["hours"] is not None and r["night"] >= ROOF_RULE_FROM]
    if pairs_h:
        a = np.array(pairs_h, float)
        R["b_hours"] = {"n": len(pairs_h), "bias": float(np.mean(a[:, 0] - a[:, 1])),
                        "mae": float(np.mean(np.abs(a[:, 0] - a[:, 1]))),
                        "r": float(np.corrcoef(a[:, 0], a[:, 1])[0, 1]),
                        "mean_expected": float(a[:, 0].mean()), "mean_real": float(a[:, 1].mean())}
    win = [r["d0"]["window_open_frac"] for r in night_rows
           if r["d0"] and r["d0"]["open"] and r["d0"].get("window_samples", 0) >= 2
           and r["d0"].get("window_open_frac") is not None]
    R["b_window"] = {"n": len(win), "mean_open_frac": float(np.mean(win)) if win else None,
                     "fully_open": int(sum(1 for x in win if x >= 0.99))}
    outside = defaultdict(lambda: [0, 0, 0])
    for pub in pubs:
        for k, entry in enumerate(pub["nights"]):
            if not entry["window"] or not entry["sunrise"]:
                continue
            key = "esta noche" if k == 0 else "noche siguiente"
            outside[key][0] += 1
            if entry["window"][1] > entry["sunrise"] + dt.timedelta(minutes=1):
                outside[key][1] += 1
            if entry["dark_to"] and (entry["window"][1] > entry["dark_to"] + dt.timedelta(minutes=40)
                                     or entry["window"][0] < entry["dark_from"]
                                     - dt.timedelta(minutes=40)):
                outside[key][2] += 1
    R["windows_outside"] = dict(outside)
    jumps = [(r["night"], r["dm1"]["p"], r["d0"]["p"], r["dm1"]["truth"], r["d0"]["truth"],
              r["dm1"]["open"], r["d0"]["open"])
             for r in night_rows if r["d0"] and r["dm1"] and r["d0"]["p"] is not None
             and r["dm1"]["p"] is not None]
    R["jumps"] = jumps
    wn_jumps = [j for j in jumps if j[3] == "fotometro" and j[4] == "techo"]
    R["jumps_summary"] = {
        "n": len(jumps),
        "flips": int(sum(1 for j in jumps if j[5] != j[6])),
        "n_wn_to_roof": len(wn_jumps),
        "mean_drop_wn_to_roof": (float(np.mean([j[1] - j[2] for j in wn_jumps]))
                                 if wn_jumps else None),
        "mean_abs": float(np.mean([abs(j[1] - j[2]) for j in jumps])) if jumps else None,
    }

    # ================= (c) terciles
    from .nightview import SKY_TERCILES, _tercile
    sky_rows = []
    for r in night_rows:
        if not r["d0"] or not r["d0"]["sky_tercile"]:
            continue
        clear_hours = [h for h in T["consenso"] if night_of(h) == r["night"]
                       and T["consenso"][h] and h in tess]
        if len(clear_hours) < 2:
            continue
        measured = max(tess[h] for h in clear_hours)
        sky_rows.append({"night": r["night"], "pred": r["d0"]["sky_tercile"],
                         "meas": _tercile(measured, *SKY_TERCILES, higher_is_better=True),
                         "meas_mag": measured, "pred_mag": r["d0"]["darkest_mag"],
                         "moon": r["moon"]})
    R["c_sky"] = sky_rows
    confusion = defaultdict(int)
    for x in sky_rows:
        confusion[(x["pred"], x["meas"])] += 1
    R["c_sky_confusion"] = {f"{a}|{b}": v for (a, b), v in confusion.items()}
    R["c_sky_bias"] = {}
    for label, cond in (("Luna <25 %", lambda x: x["moon"] < 0.25),
                        ("Luna >=25 %", lambda x: x["moon"] >= 0.25)):
        diffs = [x["pred_mag"] - x["meas_mag"] for x in sky_rows
                 if cond(x) and x["pred_mag"] is not None]
        if diffs:
            R["c_sky_bias"][label] = {"n": len(diffs), "mean": float(np.mean(diffs)),
                                      "median": float(np.median(diffs)),
                                      "mae": float(np.mean(np.abs(diffs)))}
    aod_rows = []
    for r in night_rows:
        if not r["d0"] or r["d0"]["aod"] is None:
            continue
        vals = [v for h, v in pm25.items() if night_of(h) == r["night"]
                and (h.hour >= 19 or h.hour <= 5)]
        if len(vals) >= 4:
            aod_rows.append((r["night"], float(r["d0"]["aod"]), float(np.median(vals)),
                             r["d0"]["aod_tercile"]))
    R["c_aod"] = aod_rows
    if len(aod_rows) >= 8:
        from scipy.stats import spearmanr
        boot = NightBootstrap(np.arange(len(aod_rows)), resamples)
        a = np.array([[x[1], x[2]] for x in aod_rows])
        R["c_aod_spearman"] = boot.ci(lambda i: float(spearmanr(a[i, 0], a[i, 1])[0]))
    seeing_counts = defaultdict(int)
    for r in night_rows:
        if r["d0"] and r["d0"]["seeing_tercile"]:
            seeing_counts[r["d0"]["seeing_tercile"]] += 1
    R["c_seeing"] = dict(seeing_counts)
    fwhm = data.con.execute("""SELECT count(*), max(t_utc) FROM measurements
                               WHERE fwhm_zenith_500nm IS NOT NULL""").fetchone()
    R["c_fwhm"] = (int(fwhm[0]), str(fwhm[1]))
    R["c_kext"] = int(data.con.execute("""SELECT count(*) FROM measurements
                                          WHERE k_extinction IS NOT NULL""").fetchone()[0])

    # ================= (d) proveedores sobre las mismas horas
    ours_by_valid = defaultdict(list)
    for r in ours:
        ours_by_valid[r["valid"]].append(r)

    def provider_value(model: str, when: dt.datetime, h: dt.datetime):
        """Lo que el archivo tenia de ese proveedor para la hora `h` en `when`."""
        if model in ("eps_media", "eps_frac20"):
            v = eps.at(h, when)
            return None if v is None else (v[0] if model == "eps_media" else v[1])
        if model == "ecmwf + curva":
            v = asof["ecmwf_ifs025"].at(h, when)
            return None if v is None else float(curves.photo_p(
                photo, v[0], (aware(h) - when).total_seconds() / 3600.0))
        if model == "weathernext3 + curva":
            v = asof["weathernext3"].at(h, when)
            return None if v is None or curves.wn is None else float(curves.photo_p(
                curves.wn, v[0], (aware(h) - v[4]).total_seconds() / 3600.0))
        if model == "astroweather (publicado)":
            cands = [r for r in ours_by_valid.get(h, []) if r["run"] <= when]
            return max(cands, key=lambda r: r["run"])["p"] if cands else None
        if model == "ecmwf_ifs025 (capa mayor)":
            v = asof["ecmwf_ifs025"].at(h, when)
            return None if v is None or None in v[1:4] else 1 - max(v[1:4]) / 100.0
        v = asof[model].at(h, when)
        return None if v is None else 1 - v[0] / 100.0

    blocks = {
        "A": (["ecmwf_ifs025", "ecmwf_ifs", "ecmwf_ifs025 (capa mayor)", "gfs_seamless",
               "icon_seamless", "meteofrance_seamless", "eps_media", "eps_frac20",
               "ecmwf + curva", "astroweather (publicado)"], PHOTO_OOS_FROM,
              dt.datetime(2026, 10, 26, 12)),
        "B": (["ecmwf_ifs025", "ecmwf_ifs", "gfs_seamless", "icon_seamless",
               "meteofrance_seamless", "eps_media", "weathernext3"],
              dt.datetime(2026, 8, 28, 12), dt.datetime(2026, 10, 1, 12)),
        "C": (["ecmwf_ifs025", "weathernext3", "ecmwf + curva", "weathernext3 + curva"],
              WN_OOS_FROM, dt.datetime(2026, 10, 1, 12)),
    }
    R["d"] = {}
    for truth_name in ("fotometro", "consenso"):
        truth = T[truth_name]
        for block, (models, t0, t1) in blocks.items():
            for tag, offset in (("D0", 0), ("D-1", 1)):
                rows = []
                for h in sorted(truth):
                    if not (t0 <= h < t1) or not in_period(h):
                        continue
                    day = night_of(h) - dt.timedelta(days=offset)
                    when = dt.datetime(day.year, day.month, day.day, DECISION_HOUR_UTC,
                                       tzinfo=UTC)
                    values = {}
                    for m in models:
                        v = provider_value(m, when, h)
                        if v is None:
                            break
                        values[m] = v
                    else:
                        rows.append((h, values))
                if len(rows) < 40:
                    continue
                o = np.array([1.0 if truth[h] else 0.0 for h, _ in rows])
                nn = np.array([night_of(h).toordinal() for h, _ in rows])
                fc = {m: np.array([v[m] for _, v in rows]) for m in models}
                key = f"{block} {tag} {truth_name}"
                R["d"][key] = score_set(fc, o, nn, reference="ecmwf_ifs025",
                                        resamples=resamples)
                R["d"][key]["span"] = (rows[0][0].isoformat(), rows[-1][0].isoformat())

    # ================= que curva habia y con que se entreno
    R["curves"] = {
        "photo_versions": sorted(curves.photo),
        "photo_current": {k: curves.photo_current[k] for k in
                          ("fitted_at", "trained_on", "base_rate", "brier", "brier_climatology",
                           "intercept", "cloud_weight")},
        "wn": ({k: curves.wn[k] for k in ("fitted_at", "trained_on", "base_rate")}
               if curves.wn else None),
        "roof_versions": len(curves.roof),
        "roof_first": curves.roof[0][0].isoformat() if curves.roof else None,
        "roof_last": ({k: curves.roof[-1][1][k] for k in
                       ("fitted_at", "nights", "trained_on", "method", "crossing",
                        "intercept", "cloud_weight", "slope_low", "slope_high")}
                      if curves.roof else None),
        "roof_slopes": [(t.isoformat(), c["cloud_weight"], c["intercept"], c["nights"],
                         c["trained_on"]) for t, c in curves.roof],
        # Lo maximo que puede decir la curva del techo, con 0 % de nube.
        "roof_cap": ([float(sigmoid(c["intercept"])) for t, c in curves.roof
                      if t >= dt.datetime(2026, 9, 10, tzinfo=UTC)] or [None]),
    }
    R["curves"]["roof_cap_range"] = (min(R["curves"]["roof_cap"]), max(R["curves"]["roof_cap"]))
    del R["curves"]["roof_cap"]
    for r in night_rows:
        for tag in ("d0", "dm1"):
            if r[tag]:
                r[tag].pop("hours", None)
    R["nights"] = night_rows
    return R


def bias_tables(records: list[dict], truth_key: str, resamples: int) -> dict:
    """Observado menos previsto por fase lunar, tipo de nube y hora."""
    records = [r for r in records if r[truth_key] is not None]
    if not records:
        return {}

    def moon_cat(r):
        if r["moon_alt"] < 0:
            return "Luna bajo el horizonte"
        return "Luna arriba, <50 %" if r["moon"] < 0.5 else "Luna arriba, >=50 %"

    def hour_cat(r):
        return {19: "19-21", 20: "19-21", 21: "21-23", 22: "21-23", 23: "23-01", 0: "23-01",
                1: "01-03", 2: "01-03", 3: "03-05", 4: "03-05", 5: "05-06"}.get(r["h"].hour, "otra")

    out = {}
    for name, fn in (("luna", moon_cat),
                     ("nube", lambda r: cloud_category(r["low"], r["mid"], r["high"], r["total"])),
                     ("hora", hour_cat), ("regimen", lambda r: r["regime"])):
        groups = defaultdict(list)
        for r in records:
            groups[fn(r)].append(r)
        table = {}
        for key, rs in groups.items():
            o = np.array([1.0 if r[truth_key] else 0.0 for r in rs])
            p = np.array([r["p_photo"] for r in rs])
            nights = np.array([night_of(r["h"]).toordinal() for r in rs])
            boot = NightBootstrap(nights, resamples)
            table[key] = {
                "n": len(rs), "nights": boot.n_nights, "obs": float(o.mean()),
                "pred_photo": float(p.mean()),
                "pred_raw": float(np.mean([r["p_raw"] for r in rs])),
                "pred_pub": float(np.mean([r["p_pub"] for r in rs])),
                "gap_photo": boot.ci(lambda i: float(o[i].mean() - p[i].mean())),
                "brier_photo": brier(p, o),
            }
        out[name] = table
    return out


def failure_reason(r: dict, e: dict) -> str:
    """El porque de un fallo, con los hechos de esa noche y sin adornar."""
    def frac(name):
        t = r[name]
        return t["clear"] / t["hours"] if t["hours"] >= 3 else None

    lay = r["ecmwf_layers"]
    cw, sky, phot, cons = (frac("cloudwatcher"), frac("allsky"), frac("fotometro"),
                           frac("consenso"))
    # El arbitro es la allsky si hay; si no, el consenso. El fotometro solo no
    # decide: es el que se equivoca con la Luna (seccion 2 del informe).
    arbiter = sky if sky is not None else cons
    if e["open"]:
        window = e["window"]
        if window and r.get("dawn") and window[1] > r["dawn"] + dt.timedelta(minutes=40):
            return (f"la ventana que sostiene el «Abierto» ({window[0]:%H:%M}-{window[1]:%H:%M} "
                    "UTC) cae en el amanecer, no en la noche; de noche el cielo no dejo abrir")
        if cw is not None and cw < 0.5 and arbiter is not None and arbiter >= 0.5:
            return ("cielo limpio segun la allsky, pero el CloudWatcher marco nublado (cielo IR "
                    "calido) y el techo no abrio: decision del sitio, no del cielo")
        if arbiter is not None and arbiter < 0.5:
            return ("nube que la prevision no vio" + (
                f": {e['cloud_dark']:.0f} % previsto en las horas oscuras"
                if e["cloud_dark"] is not None else f" (ECMWF {lay[0]:.0f} %)" if lay else ""))
        return "sin causa clara en los sensores"
    if lay and lay[3] >= 50 and lay[1] < 30 and lay[2] < 20:
        return (f"nube ALTA prevista (ECMWF: total {lay[0]:.0f} %, alta {lay[3]:.0f} %, baja "
                f"{lay[1]:.0f} %): el cielo quedo abrible y abrieron {r['roof']['hours']:.0f} h")
    if e["gated"]:
        cloud_txt = (f"nube prevista {lay[0]:.0f} % (baja {lay[1]:.0f}, alta {lay[3]:.0f}) y "
                     if lay else "")
        return (cloud_txt + "puerta de seguridad (" + ", ".join(e["gated"]) + ") que dejo bajo el "
                f"50 % las horas que pasaban por nube; abrieron {r['roof']['hours']:.0f} h")
    if lay:
        return (f"nube prevista (total {lay[0]:.0f} %, baja {lay[1]:.0f}, media {lay[2]:.0f}, alta "
                f"{lay[3]:.0f}) que no cerro la noche: abrieron {r['roof']['hours']:.0f} h")
    return f"abrieron {r['roof']['hours']:.0f} h contra un Cerrado"


def multimodel_test(data: Data, truth_all: dict, T: dict, asof: dict, resamples: int) -> dict:
    """Curvas del ECMWF, de ICON y de los dos juntos, ajustadas sobre el
    retroanalisis 2024-02 -> 2026-08-27 (verdad: fotometro, como el producto) y
    puntuadas FUERA de muestra sobre lo que habia en el archivo a las 16 UTC
    del dia (D0) o de la vispera (D-1)."""
    back = {}
    for model in ("ecmwf_ifs025", "icon_seamless"):
        for lead in (1, 2):
            rows = data.con.execute("""
                SELECT valid_utc, cloud_total FROM forecasts
                WHERE model = ? AND member IS NULL AND cloud_total IS NOT NULL
            """, [f"{model}_prev{lead}d"]).fetchall()
            back[(model, lead)] = {hour_key(v): float(c) for v, c in rows}
    cutoff = dt.datetime(2026, 8, 28)
    out = {}
    for lead, tag in ((1, "D0"), (2, "D-1")):
        hours = [h for h in truth_all if h < cutoff and h in back[("ecmwf_ifs025", lead)]
                 and h in back[("icon_seamless", lead)]]
        y = np.array([1.0 if truth_all[h] else 0.0 for h in hours])
        e = np.array([back[("ecmwf_ifs025", lead)][h] for h in hours]) / 100
        i = np.array([back[("icon_seamless", lead)][h] for h in hours]) / 100
        ones = np.ones(len(hours))
        w = {"E": fit_logistic(np.c_[ones, e], y, ridge=0.0),
             "I": fit_logistic(np.c_[ones, i], y, ridge=0.0),
             "EI": fit_logistic(np.c_[ones, e, i], y, ridge=0.0)}
        out[f"{tag} entrenamiento"] = {"horas": len(hours), "tasa_base": float(y.mean()),
                                       "pesos": {k: v.tolist() for k, v in w.items()}}
        for truth_name in ("consenso", "fotometro"):
            truth = T[truth_name]
            rows = []
            for h in sorted(truth):
                if h < PHOTO_OOS_FROM:
                    continue
                day = night_of(h) - dt.timedelta(days=lead - 1)
                when = dt.datetime(day.year, day.month, day.day, DECISION_HOUR_UTC, tzinfo=UTC)
                ve = asof["ecmwf_ifs025"].at(h, when)
                vi = asof["icon_seamless"].at(h, when)
                if ve is not None and vi is not None:
                    rows.append((h, ve[0] / 100, vi[0] / 100))
            if len(rows) < 40:
                continue
            ee = np.array([x[1] for x in rows])
            ii = np.array([x[2] for x in rows])
            o = np.array([1.0 if truth[x[0]] else 0.0 for x in rows])
            nn = np.array([night_of(x[0]).toordinal() for x in rows])
            fc = {"ECMWF + curva": sigmoid(w["E"][0] + w["E"][1] * ee),
                  "ICON + curva": sigmoid(w["I"][0] + w["I"][1] * ii),
                  "ECMWF + ICON, curva conjunta": sigmoid(w["EI"][0] + w["EI"][1] * ee
                                                          + w["EI"][2] * ii),
                  "ECMWF crudo": 1 - ee}
            out[f"{tag} {truth_name}"] = score_set(fc, o, nn, reference="ECMWF + curva",
                                                   resamples=resamples)
    return out


# --- M1: la curva del techo nueva, puntuada prequencialmente ---------------------
#
# El control positivo de M1 (`python -m astroweather.hindcast --m1`). Cada noche
# se puntua con curvas ajustadas por el CODIGO DE PRODUCCION (`roofcalibration.
# fit`) solo con las noches anteriores (y los meses anteriores al de la noche),
# y con el MISMO instrumento se puntua la curva que se publico de verdad. Si el
# instrumento no separa la publicada de la nueva como lo hizo el retroanalisis
# (Brier del veredicto 0,155 -> 0,088), algo no se esta midiendo.

# Umbrales de noches que se comparan para elegir NIGHTS_FOR_HOURLY_REFIT.
M1_THRESHOLDS = (8, 15, 20, 30)
# Remuestreos del intervalo de la pendiente en cada ajuste prequencial: el
# intervalo no se puntua aqui, solo se calcula porque `fit` lo calcula.
M1_FIT_ROUNDS = 10
M1_NEW = "nueva (produccion)"
M1_PUB = "publicada"
M1_OLD = "anterior reajustada"
# Lo que el informe del 26-10 dice de M1 una vez hecha (sus cifras: m1_resultados.json).
M1_DONE = '**HECHA el 26-10-2026** (CHANGELOG): curva horaria de dos parámetros, horas de noche astronómica, nube disponible a las 15 UTC por `retrieved_at` (`asof.py`), umbral de 20 noches. Con el instrumento de este informe y el código de producción (`python -m astroweather.hindcast --m1`): veredicto Brier 0.159 → 0.119 (−0.040 [−0.082, +0.001]; arrancando en 8 noches, −0.068 [−0.111, −0.023]), horas esperadas −3.5 → −1.7 h, pero 2 de las 7 noches cerradas pasan a falsa alarma (27-09 y 19-10). Cifras y figura: `verificacion_2026-10-26/m1_resultados.json`, `verificacion_2026-10-26/m1_curva_techo.png`.'
M6_DONE = '**HECHA el 26-10-2026** (CHANGELOG): solo cuentan, para la ventana, la probabilidad de abrir y las horas esperadas, las casillas con el Sol < −18° a la media hora (la constante de M1); la casilla que cruza −18° cuenta en proporción, así que la suma no puede pasar de la noche astronómica. Rejilla y JSON publicados igual (campo nuevo `horas_utilizables_rejilla`, aditivo). Medido con `python -m astroweather.hindcast --m6` sobre las 42 noches del 05-09 al 25-10 (control: sin M6 reproduce m1_resultados.json con diferencia 0): horas esperadas mayores que la noche astronómica en 17 de 42 noches (hasta ×1.28) → 0 (máximo ×0.89); ventanas fuera de la noche astronómica 13 → 0; en las 207 publicaciones (esta noche y la siguiente), ventanas que acaban tras el orto 13 + 26 → 0. NO cambia ningún veredicto de las 42 noches (Brier 0.119 → 0.122, +0.003 [−0.003, +0.011]; acierto 0.83 igual): el 28-09 sigue siendo falsa alarma (0.89 → 0.85, ventana 06-08 → 03:00-04:35 UTC), 27-09 y 19-10 no se mueven. Las horas esperadas bajan de sesgo −1.7 [−2.9, −0.5] a −4.0 [−5.2, −2.7] h contra las horas de techo de la tabla, que incluyen la apertura en crepúsculo; contra las horas abiertas con el Sol < −18° (24 noches con lecturas) de +0.6 [−0.7, +1.9] a −1.9 [−3.1, −0.7] h: la curva de M1 es conservadora (tope 0.89) y las horas de crepúsculo lo tapaban. Cifras: `verificacion_2026-10-26/m6_resultados.json`.'
M7_DONE = '**HECHA el 27-10-2026** (CHANGELOG): la curva la dice `tonight.report`, que es quien la elige, y cada fila de `astroweather` lleva `curve_truth`, `curve_model` y `curve_fitted_at` (columnas nuevas, nulas en lo archivado antes); `provider_model_version` nombra la curva usada. `Curves.classify` lee el régimen cuando la fila lo trae y solo lo reconstruye en las filas viejas.'
M8_DONE = '**HECHA el 27-10-2026** (CHANGELOG): `p_despejado` se queda (la web lo lee) y cada hora lleva al lado `p_techo_abierto` o `p_cielo_despejado` según la verdad terreno de su noche; el JSON publica `campos_hora` con el significado de cada campo, y el README lo tabula.'


def _legacy_roof_pairs(con) -> list[tuple[dt.datetime, float, float]]:
    """El instrumento ANTERIOR, tal cual estaba en `roofcalibration.hourly_pairs`
    hasta el 26-10: horas de 18 a 05 UTC y la nube de la vista deduplicada
    (la ultima recogida de la etiqueta de pasada <= corte). Solo como control."""
    from .roofcalibration import BACKFILL_MODEL, ISSUE_CUTOFF_HOUR_UTC
    roof = con.execute(r"""
        SELECT date_trunc('hour', t_utc) AS h,
               avg(CASE WHEN CAST(regexp_extract(quality_flags, 'open=(\d+)/', 1)
                             AS INT) > 0 THEN 1.0 ELSE 0.0 END) AS open_fraction
        FROM measurements
        WHERE instrument_id = 'astrocamp_roofs' AND quality_flags LIKE 'open=%'
          AND (hour(t_utc) >= 18 OR hour(t_utc) <= 5)
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    pairs = []
    for hour, open_fraction in roof:
        hour = aware(hour)
        night = night_of(hour)
        cutoff = dt.datetime(night.year, night.month, night.day, ISSUE_CUTOFF_HOUR_UTC,
                             tzinfo=UTC)
        row = con.execute("""
            SELECT cloud_total FROM forecasts
            WHERE model = ? AND member IS NULL AND cloud_total IS NOT NULL
              AND valid_utc = ? AND run_utc <= ?
            ORDER BY run_utc DESC LIMIT 1
        """, [BACKFILL_MODEL, hour, cutoff]).fetchone()
        if row:
            pairs.append((hour, float(row[0]), float(open_fraction)))
    return pairs


def _cloud_bins(pairs, edges=(0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 101)):
    """Fraccion observada de techo abierto por bin de nube prevista, con su n."""
    out = []
    clouds = np.array([c for _, c, _ in pairs])
    opened = np.array([o for _, _, o in pairs])
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (clouds >= lo) & (clouds < hi)
        if m.sum():
            out.append((float(clouds[m].mean()), float(opened[m].mean()), int(m.sum()),
                        lo, min(hi, 100)))
    return out


def m1_evaluation(archive: Path = ARCHIVE, data_repo: Path = DATA_REPO,
                  resamples: int = RESAMPLES) -> dict:
    from . import roofcalibration as RC
    from .gates import opening_probability
    _astropy_offline()
    data = Data(archive, data_repo)
    curves = Curves(archive)
    M: dict = {"generated": dt.datetime.now(UTC).isoformat(timespec="seconds"),
               "archive_head": git(archive, "log", "-1", "--format=%h %cI").decode().strip(),
               "data_head": git(data_repo, "log", "-1", "--format=%h %cI").decode().strip(),
               "resamples": resamples, "threshold": RC.NIGHTS_FOR_HOURLY_REFIT,
               "dark_sun_altitude": RC.DARK_SUN_ALTITUDE}
    nights = [FIRST_NIGHT + dt.timedelta(days=i)
              for i in range((LAST_NIGHT - FIRST_NIGHT).days + 1)]
    frames = night_frames(nights)
    samples = data.roof_samples(dt.datetime(2026, 8, 27, 12, tzinfo=UTC))
    roof = roof_truth(data, frames, samples)

    print("[hindcast --m1] pares horarios (nuevo y anterior) y meses...")
    new_pairs = [p for p in RC.hourly_pairs(archive) if night_of(p[0]) <= LAST_NIGHT]
    legacy_pairs = [p for p in _legacy_roof_pairs(data.con) if night_of(p[0]) <= LAST_NIGHT]
    months = RC.monthly_pairs(archive)
    # Sensibilidad: el mismo ajuste con las horas de Sol < -6 (crepusculo nautico
    # dentro). El techo de AstroCamp esta abierto en el nautico casi tanto como
    # de noche cerrada en las noches buenas (medido el 26-10).
    saved = RC.DARK_SUN_ALTITUDE
    RC.DARK_SUN_ALTITUDE = -6.0
    try:
        nautical_pairs = [p for p in RC.hourly_pairs(archive) if night_of(p[0]) <= LAST_NIGHT]
    finally:
        RC.DARK_SUN_ALTITUDE = saved
    M["pairs"] = {"nuevo": {"hours": len(new_pairs), "nights": RC.nights_in(new_pairs)},
                  "anterior": {"hours": len(legacy_pairs), "nights": RC.nights_in(legacy_pairs)},
                  "sol<-6": {"hours": len(nautical_pairs),
                             "nights": RC.nights_in(nautical_pairs)},
                  "months": len(months)}
    # M16 en estas horas: la nube al corte (recogida) contra la de la vista.
    legacy_by_hour = {hour_key(h): c for h, c, _ in legacy_pairs}
    shared = [(c, legacy_by_hour[hour_key(h)]) for h, c, _ in new_pairs
              if hour_key(h) in legacy_by_hour]
    M["pairs"]["asof_vs_view"] = {
        "n": len(shared),
        "equal": float(np.mean([abs(a - b) < 0.5 for a, b in shared])) if shared else None,
        "median_abs": float(np.median([abs(a - b) for a, b in shared])) if shared else None}
    # Que tapaba la ventana fija de 18 a 05 UTC: horas con el Sol arriba.
    eph = hour_ephemeris([hour_key(h) for h, _, _ in legacy_pairs])
    legacy_sun = [(eph[hour_key(h)][0], o) for h, _, o in legacy_pairs if hour_key(h) in eph]
    M["pairs"]["legacy_by_sun"] = [
        (lo, hi, int(sum(1 for a, _ in legacy_sun if lo <= a < hi)),
         float(np.mean([o for a, o in legacy_sun if lo <= a < hi]))
         if any(lo <= a < hi for a, _ in legacy_sun) else None)
        for lo, hi in ((-90, -18), (-18, -12), (-12, -6), (-6, 0), (0, 90))]

    # ---- ajustes prequenciales con el codigo de produccion
    variants = {M1_NEW: (new_pairs, RC.NIGHTS_FOR_HOURLY_REFIT),
                M1_OLD: (legacy_pairs, 10 ** 6),
                "anterior con horas limpias": (new_pairs, 10 ** 6),
                "nueva con Sol < -6": (nautical_pairs, RC.NIGHTS_FOR_HOURLY_REFIT)}
    for t in M1_THRESHOLDS:
        variants[f"nueva, umbral {t} noches"] = (new_pairs, t)
    fitted: dict = {name: {} for name in variants}
    for n in nights:
        months_before = [m for m in months if (m.year, m.month) < (n.year, n.month)]
        for name, (pairs, target) in variants.items():
            train = [p for p in pairs if night_of(p[0]) < n]
            try:
                c = RC.fit(months_before, train, nights_target=target, rounds=M1_FIT_ROUNDS)
            except ValueError:
                continue
            fitted[name][n] = (c.intercept, c.cloud_weight, c.method)
        decision = dt.datetime(n.year, n.month, n.day, DECISION_HOUR_UTC, tzinfo=UTC)
        active = [c for t, c in curves.roof if t <= decision]
        if active:
            fitted.setdefault(M1_PUB, {})[n] = (active[-1]["intercept"],
                                               active[-1]["cloud_weight"],
                                               active[-1]["method"])
    M["fitted"] = {name: {str(n): v for n, v in by.items()} for name, by in fitted.items()}
    names = list(fitted)

    def p_of(name, n, cloud):
        a, b, _ = fitted[name][n]
        return float(sigmoid(a + b * cloud / 100.0))

    # ---- (1) hora a hora: horas de noche astronomica, nube al corte, techo real
    rows = [(h, c, o) for h, c, o in new_pairs
            if night_of(h) >= ROOF_RULE_FROM and all(night_of(h) in fitted[k] for k in names)]
    o1 = np.array([1.0 if o >= 0.5 else 0.0 for _, _, o in rows])
    n1 = np.array([night_of(h).toordinal() for h, _, _ in rows])
    fc1 = {k: np.array([p_of(k, night_of(h), c) for h, c, _ in rows]) for k in names}
    # El AUC de CUALQUIER curva fija y decreciente es el de la nube cruda: lo
    # que una curva prequencial pierde de AUC lo pierde por cambiar de noche a
    # noche (las primeras, planas; el salto de metodo), no por su forma.
    fc1["nube cruda (cualquier curva fija)"] = np.array([1 - c / 100 for _, c, _ in rows])
    M["hourly"] = score_set(fc1, o1, n1, reference=M1_PUB, resamples=resamples)
    M["hourly_reliability"] = {k: reliability_curve(fc1[k], o1)
                               for k in (M1_PUB, M1_OLD, M1_NEW)}

    # ---- (2) el veredicto: misma regla, mismas puertas, lo publicado a las 16 UTC
    pubs = data.published()

    def pick(night: dt.date):
        return pick_publication(pubs, night, night)

    night_rows = []
    control = []
    for n in nights:
        if n < ROOF_RULE_FROM or roof[n]["opened"] is None:
            continue
        chosen = pick(n)
        if chosen is None or chosen[1]["p_open"] is None:
            continue
        pub, entry = chosen
        if not all(n in fitted[k] for k in names):
            continue
        f = frames[n]
        dark = [x for x in entry["hours"] if f["dusk"] and f["dawn"]
                and f["dusk"] - dt.timedelta(minutes=30) <= aware(x["t"]) <= f["dawn"]]
        row = {"night": n, "opened": bool(roof[n]["opened"]), "roof_hours": roof[n]["hours"],
               "roof_source": roof[n]["source"], "p": {}, "expected": {}, "expected_dark": {},
               "published_p": entry["p_open"], "published_expected": entry["expected_hours"],
               "published_open": entry["open"]}
        ok = True
        for k in names:
            usable = gated_usable(entry["hours"], lambda c, k=k: p_of(k, n, c))
            if not usable:
                ok = False
                break
            row["p"][k] = float(opening_probability(usable))
            row["expected"][k] = float(sum(usable))
            usable_dark = gated_usable(dark, lambda c, k=k: p_of(k, n, c)) or []
            row["expected_dark"][k] = float(sum(usable_dark))
        if not ok:
            continue
        # Control del instrumento: la curva vigente AL PUBLICAR, pasada por esta
        # misma maquinaria, tiene que devolver la P(abrir) publicada.
        at_pub = [c for t, c in curves.roof if t <= pub["generated"]]
        if at_pub:
            c = at_pub[-1]
            usable = gated_usable(entry["hours"], lambda x, c=c: float(Curves.roof_p(c, x)))
            if usable:
                control.append(abs(float(opening_probability(usable)) - entry["p_open"]))
        night_rows.append(row)
    M["instrument_control"] = {
        "n": len(control),
        "exact": float(np.mean([x < 0.005 for x in control])) if control else None,
        "max_abs": float(max(control)) if control else None}
    o2 = np.array([1.0 if r["opened"] else 0.0 for r in night_rows])
    n2 = np.array([r["night"].toordinal() for r in night_rows])
    fc2 = {k: np.array([r["p"][k] for r in night_rows]) for k in names}
    fc2["publicado (noche.json)"] = np.array([r["published_p"] for r in night_rows])
    M["verdict"] = score_set(fc2, o2, n2, reference=M1_PUB, resamples=resamples)
    # Los umbrales de noches entre si, pareados contra el de produccion.
    M["verdict_thresholds"] = score_set(
        {k: fc2[k] for k in names if k.startswith("nueva, umbral") or k == M1_NEW},
        o2, n2, reference=M1_NEW, resamples=resamples)
    boot = NightBootstrap(n2, resamples)
    M["verdict_acc"] = {}
    for k, p in fc2.items():
        f = (p >= 0.5).astype(float)
        M["verdict_acc"][k] = {
            "accuracy": boot.ci(lambda i, f=f: float(np.mean(f[i] == o2[i]))),
            "d_accuracy": boot.ci(lambda i, f=f: float(np.mean(f[i] == o2[i]))
                                  - float(np.mean((fc2[M1_PUB][i] >= 0.5) == o2[i]))),
            "hits": int(((f == 1) & (o2 == 1)).sum()),
            "false_alarms": int(((f == 1) & (o2 == 0)).sum()),
            "misses": int(((f == 0) & (o2 == 1)).sum()),
            "correct_neg": int(((f == 0) & (o2 == 0)).sum())}
    M["always_open_acc"] = float(o2.mean())
    M["verdict_reliability"] = {k: reliability_curve(fc2[k], o2, bins=5)
                                for k in (M1_PUB, M1_NEW)}
    # ---- (3) horas esperadas contra horas reales
    with_hours = [i for i, r in enumerate(night_rows) if r["roof_hours"] is not None]
    real = np.array([night_rows[i]["roof_hours"] for i in with_hours])
    boot_h = NightBootstrap(np.array([night_rows[i]["night"].toordinal() for i in with_hours]),
                            resamples)
    M["hours"] = {"n": len(with_hours), "mean_real": float(real.mean())}
    for k in (M1_PUB, M1_OLD, M1_NEW, "nueva con Sol < -6"):
        for tag, key in (("toda la rejilla", "expected"), ("solo noche astronomica",
                                                            "expected_dark")):
            e = np.array([night_rows[i][key][k] for i in with_hours])
            M["hours"][f"{k} | {tag}"] = {
                "mean": float(e.mean()),
                "bias": boot_h.ci(lambda i, e=e: float(np.mean(e[i] - real[i]))),
                "mae": boot_h.ci(lambda i, e=e: float(np.mean(np.abs(e[i] - real[i])))),
                "r": float(np.corrcoef(e, real)[0, 1])}
    pe = np.array([night_rows[i]["published_expected"] for i in with_hours
                   if night_rows[i]["published_expected"] is not None])
    M["hours"]["publicado (noche.json)"] = {"mean": float(pe.mean()) if len(pe) else None}
    # ---- (4) las noches cerradas
    M["closed"] = [{"night": str(r["night"]), "roof_hours": r["roof_hours"],
                    "source": r["roof_source"], "published": r["published_p"],
                    **{k: r["p"][k] for k in (M1_PUB, M1_OLD, M1_NEW, "nueva con Sol < -6")}}
                   for r in night_rows if not r["opened"]]
    M["nights"] = [{"night": str(r["night"]), "opened": r["opened"],
                    "roof_hours": r["roof_hours"], "source": r["roof_source"],
                    "p_pub": r["p"][M1_PUB], "p_new": r["p"][M1_NEW],
                    "p_old": r["p"][M1_OLD], "published_p": r["published_p"],
                    "new_method": fitted[M1_NEW][r["night"]][2]}
                   for r in night_rows]
    # el calendario completo, tambien con las noches sin verdad de techo
    M["calendar"] = []
    for n in nights:
        chosen = pick(n) if n >= ROOF_RULE_FROM else None
        entry = chosen[1] if chosen else None
        row = {"night": str(n), "opened": roof[n]["opened"], "roof_hours": roof[n]["hours"],
               "source": roof[n]["source"], "p_pub": None, "p_new": None}
        if entry and entry["p_open"] is not None:
            row["p_published"] = entry["p_open"]
            for k, key in ((M1_PUB, "p_pub"), (M1_NEW, "p_new")):
                if n in fitted[k]:
                    usable = gated_usable(entry["hours"], lambda c, k=k: p_of(k, n, c))
                    if usable:
                        row[key] = float(opening_probability(usable))
        M["calendar"].append(row)
    # ---- (5) las curvas de hoy: anterior y nueva con TODAS las noches
    final_new = RC.fit(months, new_pairs)
    final_old = RC.fit(months, legacy_pairs, nights_target=10 ** 6)
    published_now = curves.roof[-1][1] if curves.roof else None

    def describe(a, b, extra=None):
        d = {"intercept": a, "cloud_weight": b,
             "crossing": (-a / b) if b else None,
             "p": {str(c): float(sigmoid(a + b * c / 100.0)) for c in (0, 30, 60, 100)}}
        d.update(extra or {})
        return d

    M["final"] = {
        "nueva": describe(final_new.intercept, final_new.cloud_weight,
                          {"method": final_new.method, "slope_p10_p90":
                           (final_new.slope_low, final_new.slope_high),
                           "monthly_mae": final_new.monthly_mae,
                           "held_out_mae": final_new.held_out_mae,
                           "nights": final_new.nights, "hours": final_new.trained_on}),
        "anterior reajustada hoy": describe(final_old.intercept, final_old.cloud_weight,
                                            {"monthly_mae": final_old.monthly_mae,
                                             "held_out_mae": final_old.held_out_mae,
                                             "nights": final_old.nights,
                                             "hours": final_old.trained_on}),
    }
    if published_now:
        M["final"]["publicada (archivo)"] = describe(
            published_now["intercept"], published_now["cloud_weight"],
            {"fitted_at": published_now["fitted_at"], "method": published_now["method"]})
    # MAE mensual en meses que NINGUNA de las dos ha visto por horas (antes del 08-2026)
    clean = [m for m in months if (m.year, m.month) >= (2026, 1) and (m.year, m.month) < (2026, 8)]
    if clean:
        M["final"]["clean_months"] = {
            "n": len(clean),
            "nueva": float(np.abs(RC._monthly_errors(clean, final_new.intercept,
                                                     final_new.cloud_weight)).mean()),
            "anterior": float(np.abs(RC._monthly_errors(clean, final_old.intercept,
                                                        final_old.cloud_weight)).mean())}
    M["cloud_bins"] = {"nuevo": _cloud_bins(new_pairs), "anterior": _cloud_bins(legacy_pairs)}
    return M


def m1_report_lines(M: dict) -> list[str]:
    """Las cifras de M1 en texto, para la consola y el informe."""
    L = []
    w = L.append
    v, h = M["verdict"]["models"], M["hourly"]["models"]
    acc = M["verdict_acc"]
    w(f"pares: {M['pairs']}")
    w(f"control del instrumento (P publicada recalculada): {M['instrument_control']}")
    w(f"veredicto: {M['verdict']['n']} noches, base {M['verdict']['base_rate']:.2f}, "
      f"siempre Abierto {M['always_open_acc']:.2f}")
    for k in v:
        e = v[k]
        a = acc[k]
        w(f"  {k:32s} Brier {_ci(e['brier'])} AUC {_ci(e['auc'], 2)} "
          f"dBrier {_ci(e['d_brier']) if 'd_brier' in e else '-'} "
          f"dAUC {_ci(e['d_auc'], 2) if 'd_auc' in e else '-'} acierto {_ci(a['accuracy'], 2)} "
          f"{a['hits']}/{a['false_alarms']}/{a['misses']}/{a['correct_neg']} p media "
          f"{e['mean_p']:.2f}")
    w(f"horario: {M['hourly']['n']} h, {M['hourly']['nights']} noches, base "
      f"{M['hourly']['base_rate']:.2f}")
    for k in h:
        e = h[k]
        w(f"  {k:32s} Brier {_ci(e['brier'])} AUC {_ci(e['auc'], 2)} "
          f"dBrier {_ci(e['d_brier']) if 'd_brier' in e else '-'} "
          f"dAUC {_ci(e['d_auc'], 2) if 'd_auc' in e else '-'} p media {e['mean_p']:.2f}")
    w("umbrales, pareados contra el de produccion:")
    for k, e in M["verdict_thresholds"]["models"].items():
        w(f"  {k:32s} Brier {_ci(e['brier'])} dBrier {_ci(e['d_brier']) if 'd_brier' in e else '-'}")
    w(f"horas: {M['hours']['n']} noches, reales {M['hours']['mean_real']:.1f}")
    for k, e in M["hours"].items():
        if isinstance(e, dict) and "bias" in e:
            w(f"  {k:55s} media {e['mean']:.1f} sesgo {_ci(e['bias'], 1)} MAE "
              f"{_ci(e['mae'], 1)} r {e['r']:.2f}")
    w("noches cerradas:")
    for c in M["closed"]:
        w(f"  {c}")
    w(f"curvas de hoy: {json.dumps(M['final'], default=_json_default)}")
    return L


# --- M6: la ventana y las horas esperadas, dentro de la noche astronomica ---------
#
# `python -m astroweather.hindcast --m6`. Cada noche se puntua con la curva del
# techo ajustada prequencialmente por el CODIGO DE PRODUCCION (la misma de
# `--m1`) y con la misma rejilla publicada a las 16 UTC; lo unico que cambia
# entre un par de brazos es si `NightReport` conoce el ocaso y el orto
# astronomicos (M6 puesto) o no (M6 quitado = el comportamiento anterior). Los
# brazos pasan por `tonight.NightReport`, no por una reimplementacion: lo que se
# mide es lo que corre en produccion.
#
# El control positivo (c): con M6 quitado, las cifras de M1 de
# `m1_resultados.json` tienen que salir EXACTAS (`m6_control`).

M6_OFF = "M1 (sin M6)"
M6_ON = "M1 + M6"
M6_PUB_OFF = "publicada (sin M6)"
M6_PUB_ON = "publicada + M6"
M6_ARMS = (M6_PUB_OFF, M6_PUB_ON, M6_OFF, M6_ON)
# Noches que se miran una a una (las del informe).
M6_FOCUS = ("2026-09-27", "2026-09-28", "2026-10-19")
# Una noche cuenta para la verdad de horas OSCURAS si el scraper cubrio al menos
# esta fraccion de sus horas de noche astronomica.
M6_DARK_COVERAGE = 0.8


def _night_report(rows: list[dict], usable: list[float], frame: dict, m6: bool):
    """Un `NightReport` de verdad con las probabilidades dadas. `m6=False` no le
    da el ocaso y el orto astronomicos: es el comportamiento de antes de M6."""
    from .gates import evaluate
    from .tonight import HourForecast, NightReport
    hours = [HourForecast(
        t_utc=aware(x["t"]), cloud_total=x["cloud"], clear_probability=None,
        usable_probability=float(u), moon_altitude=0.0,
        safety=evaluate(t_utc=aware(x["t"]), hours_ahead=12.0), calibrated=True)
        for x, u in zip(rows, usable)]
    return NightReport(
        night=frame["sunset"].date(), site="hindcast", hours=hours, moon_illumination=0.0,
        dark_hours=frame["dark_hours"], calibration_note="hindcast",
        dusk_astronomical=frame["dusk"] if m6 else None,
        dawn_astronomical=frame["dawn"] if m6 else None)


def _arm_summary(rep, frame: dict) -> dict:
    window = rep.verdict_window
    return {"p": float(rep.opening_probability), "expected": float(rep.usable_hours),
            "window": ([window[0].isoformat(), window[1].isoformat()] if window else None),
            "window_end_after_sunrise": bool(window and frame["sunrise"]
                                             and window[1] > frame["sunrise"]
                                             + dt.timedelta(minutes=1)),
            "window_outside_dark": bool(window and frame["dusk"] and frame["dawn"]
                                        and (window[1] > frame["dawn"] + dt.timedelta(seconds=1)
                                             or window[0] < frame["dusk"]
                                             - dt.timedelta(seconds=1)))}


def m6_evaluation(archive: Path = ARCHIVE, data_repo: Path = DATA_REPO,
                  resamples: int = RESAMPLES) -> dict:
    from . import roofcalibration as RC
    _astropy_offline()
    data = Data(archive, data_repo)
    curves = Curves(archive)
    M: dict = {"generated": dt.datetime.now(UTC).isoformat(timespec="seconds"),
               "archive_head": git(archive, "log", "-1", "--format=%h %cI").decode().strip(),
               "data_head": git(data_repo, "log", "-1", "--format=%h %cI").decode().strip(),
               "resamples": resamples, "threshold": RC.NIGHTS_FOR_HOURLY_REFIT,
               "dark_sun_altitude": RC.DARK_SUN_ALTITUDE}
    nights = [FIRST_NIGHT + dt.timedelta(days=i)
              for i in range((LAST_NIGHT - FIRST_NIGHT).days + 1)]
    frames = night_frames(nights)
    samples = data.roof_samples(dt.datetime(2026, 8, 27, 12, tzinfo=UTC))
    roof = roof_truth(data, frames, samples)

    print("[hindcast --m6] pares horarios y meses...")
    new_pairs = [p for p in RC.hourly_pairs(archive) if night_of(p[0]) <= LAST_NIGHT]
    months = RC.monthly_pairs(archive)
    M["pairs"] = {"nuevo": {"hours": len(new_pairs), "nights": RC.nights_in(new_pairs)},
                  "months": len(months)}

    # ---- la curva de M1, ajustada noche a noche solo con las anteriores
    print("[hindcast --m6] ajuste prequencial de la curva de M1...")
    fitted: dict = {M6_OFF: {}, M1_PUB: {}}
    for n in nights:
        months_before = [m for m in months if (m.year, m.month) < (n.year, n.month)]
        train = [p for p in new_pairs if night_of(p[0]) < n]
        try:
            c = RC.fit(months_before, train, nights_target=RC.NIGHTS_FOR_HOURLY_REFIT,
                       rounds=M1_FIT_ROUNDS)
            fitted[M6_OFF][n] = (c.intercept, c.cloud_weight, c.method)
        except ValueError:
            pass
        decision = dt.datetime(n.year, n.month, n.day, DECISION_HOUR_UTC, tzinfo=UTC)
        active = [c for t, c in curves.roof if t <= decision]
        if active:
            fitted[M1_PUB][n] = (active[-1]["intercept"], active[-1]["cloud_weight"],
                                 active[-1]["method"])
    curve_of = {M6_PUB_OFF: M1_PUB, M6_PUB_ON: M1_PUB, M6_OFF: M6_OFF, M6_ON: M6_OFF}
    m6_of = {M6_PUB_OFF: False, M6_PUB_ON: True, M6_OFF: False, M6_ON: True}

    def p_of(curve, n, cloud):
        a, b, _ = fitted[curve][n]
        return float(sigmoid(a + b * cloud / 100.0))

    # ---- verdad de horas OSCURAS: horas abiertas con el Sol < -18, donde el
    # scraper las cubrio (las horas del roof_truth incluyen la apertura en
    # crepusculo, asi que contra ellas toda cuenta solo-oscura sale corta).
    dark_truth: dict = {}
    by_night: dict = defaultdict(list)
    for h, _, o in new_pairs:
        by_night[night_of(h)].append(o)
    for n, opened_fractions in by_night.items():
        f = frames.get(n)
        if f and f["dark_hours"] and len(opened_fractions) >= M6_DARK_COVERAGE * f["dark_hours"]:
            dark_truth[n] = float(np.mean(opened_fractions)) * f["dark_hours"]

    # ---- el veredicto, con lo publicado a las 16 UTC
    pubs = data.published()

    def pick(night: dt.date):
        return pick_publication(pubs, night, night)

    night_rows = []
    for n in nights:
        if n < ROOF_RULE_FROM or roof[n]["opened"] is None:
            continue
        chosen = pick(n)
        if chosen is None or chosen[1]["p_open"] is None:
            continue
        pub, entry = chosen
        if not all(n in fitted[k] for k in (M6_OFF, M1_PUB)):
            continue
        f = frames[n]
        row = {"night": n, "opened": bool(roof[n]["opened"]), "roof_hours": roof[n]["hours"],
               "roof_source": roof[n]["source"], "dark_hours": f["dark_hours"],
               "dark_truth_hours": dark_truth.get(n), "published_p": entry["p_open"],
               "arms": {}}
        ok = True
        for arm in M6_ARMS:
            curve = curve_of[arm]
            usable = gated_usable(entry["hours"], lambda c, curve=curve: p_of(curve, n, c))
            if not usable:
                ok = False
                break
            row["arms"][arm] = _arm_summary(
                _night_report(entry["hours"], usable, f, m6_of[arm]), f)
        if ok:
            night_rows.append(row)

    nt = np.array([r["night"].toordinal() for r in night_rows])
    outcome = np.array([1.0 if r["opened"] else 0.0 for r in night_rows])
    fc = {a: np.array([r["arms"][a]["p"] for r in night_rows]) for a in M6_ARMS}
    M["verdict"] = score_set(fc, outcome, nt, reference=M6_OFF, resamples=resamples)
    boot = NightBootstrap(nt, resamples)
    ref_flag = (fc[M6_OFF] >= 0.5).astype(float)
    M["verdict_acc"] = {}
    for a, p in fc.items():
        flag = (p >= 0.5).astype(float)
        M["verdict_acc"][a] = {
            "accuracy": boot.ci(lambda i, flag=flag: float(np.mean(flag[i] == outcome[i]))),
            "d_accuracy": boot.ci(lambda i, flag=flag: float(np.mean(flag[i] == outcome[i]))
                                  - float(np.mean(ref_flag[i] == outcome[i]))),
            "hits": int(((flag == 1) & (outcome == 1)).sum()),
            "false_alarms": int(((flag == 1) & (outcome == 0)).sum()),
            "misses": int(((flag == 0) & (outcome == 1)).sum()),
            "correct_neg": int(((flag == 0) & (outcome == 0)).sum())}
    M["always_open_acc"] = float(outcome.mean())

    # ---- horas esperadas contra horas reales de techo
    def hours_block(index: list[int], real: np.ndarray) -> dict:
        b = NightBootstrap(np.array([night_rows[i]["night"].toordinal() for i in index]),
                           resamples)
        block = {"n": len(index), "mean_real": float(real.mean())}
        ref = np.array([night_rows[i]["arms"][M6_OFF]["expected"] for i in index])
        for a in M6_ARMS:
            e = np.array([night_rows[i]["arms"][a]["expected"] for i in index])
            block[a] = {
                "mean": float(e.mean()),
                "bias": b.ci(lambda i, e=e: float(np.mean(e[i] - real[i]))),
                "mae": b.ci(lambda i, e=e: float(np.mean(np.abs(e[i] - real[i])))),
                "d_mae": b.ci(lambda i, e=e: float(np.mean(np.abs(e[i] - real[i]))
                                                   - np.mean(np.abs(ref[i] - real[i])))),
                "r": float(np.corrcoef(e, real)[0, 1])}
        return block

    with_hours = [i for i, r in enumerate(night_rows) if r["roof_hours"] is not None]
    M["hours"] = hours_block(with_hours,
                             np.array([night_rows[i]["roof_hours"] for i in with_hours]))
    with_dark = [i for i, r in enumerate(night_rows) if r["dark_truth_hours"] is not None]
    M["hours_dark_truth"] = (hours_block(with_dark, np.array(
        [night_rows[i]["dark_truth_hours"] for i in with_dark])) if len(with_dark) >= 5 else None)

    # ---- las invariantes, noche a noche
    ratio_on = [r["arms"][M6_ON]["expected"] / r["dark_hours"] for r in night_rows]
    ratio_off = [r["arms"][M6_OFF]["expected"] / r["dark_hours"] for r in night_rows]
    M["invariants"] = {
        "nights": len(night_rows),
        "expected_over_dark": {
            "sin M6: noches con horas esperadas > noche astronomica":
                int(sum(1 for x in ratio_off if x > 1 + 1e-9)),
            "sin M6: maximo cociente": float(max(ratio_off)),
            "con M6: noches con horas esperadas > noche astronomica":
                int(sum(1 for x in ratio_on if x > 1 + 1e-9)),
            "con M6: maximo cociente": float(max(ratio_on))},
        "window_end_after_sunrise": {a: int(sum(r["arms"][a]["window_end_after_sunrise"]
                                                for r in night_rows)) for a in M6_ARMS},
        "window_outside_dark": {a: int(sum(r["arms"][a]["window_outside_dark"]
                                           for r in night_rows)) for a in M6_ARMS}}

    # ---- noches cuyo veredicto cambia (y las que se miran una a una)
    def flag_of(r, a):
        return r["arms"][a]["p"] >= 0.5

    M["flips"] = [{"night": str(r["night"]), "opened": r["opened"],
                   "roof_hours": r["roof_hours"],
                   **{a: r["arms"][a] for a in (M6_OFF, M6_ON)}}
                  for r in night_rows if flag_of(r, M6_OFF) != flag_of(r, M6_ON)]
    M["pub_flips"] = [{"night": str(r["night"]), "opened": r["opened"],
                       **{a: r["arms"][a] for a in (M6_PUB_OFF, M6_PUB_ON)}}
                      for r in night_rows if flag_of(r, M6_PUB_OFF) != flag_of(r, M6_PUB_ON)]
    M["focus"] = [{"night": str(r["night"]), "opened": r["opened"],
                   "roof_hours": r["roof_hours"], "published_p": r["published_p"],
                   "arms": r["arms"]}
                  for r in night_rows if str(r["night"]) in M6_FOCUS]
    M["nights"] = [{"night": str(r["night"]), "opened": r["opened"],
                    "roof_hours": r["roof_hours"], "dark_hours": r["dark_hours"],
                    "dark_truth_hours": r["dark_truth_hours"],
                    **{a: {"p": r["arms"][a]["p"], "expected": r["arms"][a]["expected"],
                           "window": r["arms"][a]["window"]} for a in M6_ARMS}}
                   for r in night_rows]

    # ---- todas las publicaciones: que habria dicho M6 con lo que se publico
    # (la curva vigente entonces y sus p_utilizable), no solo a las 16 UTC.
    from .gates import evaluate
    from .tonight import HourForecast, NightReport
    published = {"esta noche": defaultdict(int), "noche siguiente": defaultdict(int)}
    for pub in pubs:
        for k, entry in enumerate(pub["nights"]):
            if (not entry["hours"] or not entry["sunrise"] or not entry["dark_from"]
                    or entry["p_open"] is None
                    or any(x["p_usable"] is None for x in entry["hours"])):
                continue
            key = "esta noche" if k == 0 else "noche siguiente"
            hours = [HourForecast(
                t_utc=aware(x["t"]), cloud_total=x["cloud"], clear_probability=None,
                usable_probability=float(x["p_usable"]), moon_altitude=0.0,
                safety=evaluate(t_utc=aware(x["t"]), hours_ahead=12.0), calibrated=True)
                for x in entry["hours"]]
            rep = NightReport(night=entry["night"], site="hindcast", hours=hours,
                              moon_illumination=0.0, dark_hours=0.0, calibration_note="",
                              dusk_astronomical=entry["dark_from"],
                              dawn_astronomical=entry["dark_to"])
            after = rep.verdict_window
            before = entry["window"]
            tally = published[key]
            for name in ("publicaciones", "antes: ventana termina despues del orto",
                         "despues: ventana termina despues del orto",
                         "antes: ventana fuera de la noche astronomica (±40 min)",
                         "despues: ventana fuera de la noche astronomica",
                         "la ventana cambia", "el veredicto pasa a Cerrado",
                         "el veredicto pasa a Abierto"):
                tally.setdefault(name, 0)
            tally["publicaciones"] += 1
            if before and before[1] > entry["sunrise"] + dt.timedelta(minutes=1):
                tally["antes: ventana termina despues del orto"] += 1
            if before and (before[1] > entry["dark_to"] + dt.timedelta(minutes=40)
                           or before[0] < entry["dark_from"] - dt.timedelta(minutes=40)):
                tally["antes: ventana fuera de la noche astronomica (±40 min)"] += 1
            if after and (after[1] > entry["dark_to"] + dt.timedelta(seconds=1)
                          or after[0] < entry["dark_from"] - dt.timedelta(seconds=1)):
                tally["despues: ventana fuera de la noche astronomica"] += 1
            if after and entry["sunrise"] and after[1] > entry["sunrise"] + dt.timedelta(minutes=1):
                tally["despues: ventana termina despues del orto"] += 1
            if before and after and (abs((before[0] - after[0]).total_seconds()) > 60
                                     or abs((before[1] - after[1]).total_seconds()) > 60):
                tally["la ventana cambia"] += 1
            if (rep.opening_probability >= 0.5) != (entry["p_open"] >= 0.5):
                tally["el veredicto pasa a Abierto" if rep.opening_probability >= 0.5
                      else "el veredicto pasa a Cerrado"] += 1
    M["published_windows"] = {k: dict(v) for k, v in published.items()}
    return M


def m6_control(M: dict, m1_path: Path) -> dict:
    """Control positivo (c): el brazo sin M6 contra `m1_resultados.json`, cifra a cifra."""
    if not m1_path.exists():
        return {"available": False}
    old = json.loads(m1_path.read_text(encoding="utf-8"))
    by_night = {r["night"]: r for r in old["nights"]}
    mine = {r["night"]: r for r in M["nights"]}
    common = sorted(set(by_night) & set(mine))
    out = {"available": True, "nights_m1": len(by_night), "nights_m6": len(mine),
           "same_nights": sorted(by_night) == sorted(mine), "common": len(common)}
    out["max_abs_dp_m1"] = max((abs(mine[n][M6_OFF]["p"] - by_night[n]["p_new"])
                                for n in common), default=None)
    out["max_abs_dp_published_curve"] = max((abs(mine[n][M6_PUB_OFF]["p"] - by_night[n]["p_pub"])
                                             for n in common), default=None)
    pairs = {"Brier M1": (M["verdict"]["models"][M6_OFF]["brier"],
                          old["verdict"]["models"]["nueva (produccion)"]["brier"]),
             "Brier publicada": (M["verdict"]["models"][M6_PUB_OFF]["brier"],
                                 old["verdict"]["models"]["publicada"]["brier"]),
             "acierto M1": (M["verdict_acc"][M6_OFF]["accuracy"],
                            old["verdict_acc"]["nueva (produccion)"]["accuracy"]),
             "horas esperadas M1: sesgo": (M["hours"][M6_OFF]["bias"],
                                           old["hours"]["nueva (produccion) | toda la rejilla"]["bias"]),
             "horas esperadas M1: MAE": (M["hours"][M6_OFF]["mae"],
                                         old["hours"]["nueva (produccion) | toda la rejilla"]["mae"]),
             "horas esperadas publicada: sesgo": (
                 M["hours"][M6_PUB_OFF]["bias"],
                 old["hours"]["publicada | toda la rejilla"]["bias"])}
    out["max_abs_diff"] = {k: float(max(abs(a - b) for a, b in zip(x, y)))
                           for k, (x, y) in pairs.items()}
    out["hours_means"] = {"M1 sin M6": M["hours"][M6_OFF]["mean"],
                          "M1 en m1_resultados": old["hours"]["nueva (produccion) | toda la rejilla"]["mean"]}
    out["exact"] = bool(out["same_nights"] and out["max_abs_dp_m1"] < 1e-9
                        and out["max_abs_dp_published_curve"] < 1e-9
                        and max(out["max_abs_diff"].values()) < 1e-9)
    return out


def m6_report_lines(M: dict) -> list[str]:
    L = []
    w = L.append
    v, acc = M["verdict"]["models"], M["verdict_acc"]
    w(f"pares: {M['pairs']}")
    c = M.get("control")
    if c:
        w(f"control positivo (sin M6 contra m1_resultados.json): {json.dumps(c, default=_json_default)}")
    w(f"veredicto: {M['verdict']['n']} noches, base {M['verdict']['base_rate']:.2f}, "
      f"siempre Abierto {M['always_open_acc']:.2f}")
    for k in M6_ARMS:
        e, a = v[k], acc[k]
        w(f"  {k:22s} Brier {_ci(e['brier'])} AUC {_ci(e['auc'], 2)} "
          f"dBrier(vs {M6_OFF}) {_ci(e['d_brier']) if 'd_brier' in e else '-'} "
          f"acierto {_ci(a['accuracy'], 2)} dAcierto {_ci(a['d_accuracy'], 2)} "
          f"{a['hits']}/{a['false_alarms']}/{a['misses']}/{a['correct_neg']} "
          f"p media {e['mean_p']:.2f}")
    for label, key in (("horas reales de techo (tabla/lecturas, crepusculo incluido)", "hours"),
                       ("horas reales de techo en noche astronomica (lecturas)",
                        "hours_dark_truth")):
        H = M.get(key)
        if not H:
            continue
        w(f"{label}: {H['n']} noches, reales {H['mean_real']:.1f} h")
        for k in M6_ARMS:
            e = H[k]
            w(f"  {k:22s} media {e['mean']:.1f} sesgo {_ci(e['bias'], 1)} MAE {_ci(e['mae'], 1)} "
              f"dMAE(vs {M6_OFF}) {_ci(e['d_mae'], 1)} r {e['r']:.2f}")
    w(f"invariantes: {json.dumps(M['invariants'], default=_json_default, ensure_ascii=False)}")
    w("veredicto que cambia (M1 sin M6 -> con M6):")
    for f in M["flips"]:
        a, b = f[M6_OFF], f[M6_ON]
        w(f"  {f['night']} abrio={f['opened']} techo {f['roof_hours']}: p {a['p']:.2f} -> {b['p']:.2f}"
          f"  ventana {a['window']} -> {b['window']}")
    w("veredicto que cambia con la curva publicada:")
    for f in M["pub_flips"]:
        a, b = f[M6_PUB_OFF], f[M6_PUB_ON]
        w(f"  {f['night']} abrio={f['opened']}: p {a['p']:.2f} -> {b['p']:.2f}")
    w("noches que se miran una a una:")
    for f in M["focus"]:
        w(f"  {f['night']} abrio={f['opened']} techo {f['roof_hours']} h, publicada "
          f"{f['published_p']:.2f}")
        for k in M6_ARMS:
            e = f["arms"][k]
            w(f"    {k:22s} p {e['p']:.2f} esperadas {e['expected']:.1f} h ventana {e['window']}")
    w(f"todas las publicaciones: {json.dumps(M['published_windows'], ensure_ascii=False)}")
    return L


# --- figuras ------------------------------------------------------------------

def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _style(ax):
    ax.grid(True, color=C_GRID, lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(C_GREY)
    ax.tick_params(colors=C_INK2, labelsize=9)


def figure_reliability(R: dict, path: Path) -> None:
    plt = _plt()
    styles = {"ECMWF + curva fotometro": (C_ORANGE, "s"), "ECMWF crudo": (C_AQUA, "^"),
              "publicado (curva del techo)": (C_BLUE, "o"),
              "curva del fotometro (control)": (C_ORANGE, "s"),
              "nube cruda (1-nube)": (C_AQUA, "^"),
              "curva horaria prequencial": (C_VIOLET, "D")}
    panels = []
    for truth, label, title in (("fotometro", "0-12 h", "Plazo 0-12 h, contra el fotometro"),
                                ("consenso", "0-12 h", "Plazo 0-12 h, contra el consenso\n"
                                                       "(fotometro + CloudWatcher + allsky)"),
                                ("consenso", "24-48 h", "Plazo 24-48 h, contra el consenso")):
        curves = R["a_reliability"].get(truth, {}).get(label)
        if curves:
            panels.append((title, {k: v for k, v in curves.items() if k != "publicado"},
                           R["a"][truth][label], "fraccion de horas despejadas",
                           "ECMWF + curva fotometro"))
    if "a_roof_reliability" in R:
        curves = dict(R["a_roof_reliability"])
        if "preq_roof_reliability" in R:
            curves["curva horaria prequencial"] = R["preq_roof_reliability"]
        panels.append(("Lo publicado: P(techo abierto), 0-12 h,\ncontra el techo real",
                       curves, R["a_roof"], "fraccion de horas con el techo abierto",
                       "publicado (curva del techo)"))
    fig, axes = plt.subplots(1, len(panels), figsize=(4.5 * len(panels), 5.3), squeeze=False)
    for ax, (title, curves, scores, ylabel, labelled) in zip(axes[0], panels):
        _style(ax)
        ax.plot([0, 1], [0, 1], color=C_GREY, lw=1, ls="--", zorder=1)
        for name, points in curves.items():
            if not points:
                continue
            color, marker = styles.get(name, (C_MAGENTA, "v"))
            xs, ys, ns = zip(*points)
            ax.plot(xs, ys, color=color, lw=2, zorder=2)
            ax.scatter(xs, ys, s=[16 + 9 * np.sqrt(n) for n in ns], color=color, marker=marker,
                       edgecolor="white", linewidth=1.5, zorder=3, label=name)
            if name == labelled:
                for x, y, n in points:
                    ax.annotate(str(n), (x, y), textcoords="offset points", xytext=(6, -12),
                                fontsize=7.5, color=C_INK2)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1.02)
        ax.set_xlabel("probabilidad prevista", color=C_INK2, fontsize=9)
        ax.set_ylabel(ylabel, color=C_INK2, fontsize=9)
        ax.set_title(f"{title}\n(n={scores['n']} h, {scores['nights']} noches)",
                     fontsize=9.5, color=C_INK)
        ax.legend(fontsize=7.5, loc="lower right", frameon=False)
    fig.suptitle("Diagrama de fiabilidad: si se dice X %, ¿pasa el X % de las veces? Diagonal = "
                 "calibracion perfecta; tamaño del punto ~ nº de horas (cifra junto a la serie\n"
                 "principal de cada panel). Los tres primeros miden la probabilidad de cielo "
                 "despejado; el cuarto, la que se publica desde el 05-09 (techo abierto).",
                 fontsize=10, color=C_INK)
    fig.tight_layout(rect=(0, 0, 1, 0.91))
    fig.savefig(path, dpi=130, facecolor="white")
    plt.close(fig)


def figure_calendar(R: dict, path: Path) -> None:
    plt = _plt()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    rows = R["nights"]
    fig, axes = plt.subplots(3, 1, figsize=(15, 10), sharex=True,
                             gridspec_kw={"height_ratios": [1.3, 1, 1]})
    ax = axes[0]
    _style(ax)
    ax.axhline(0.5, color=C_GREY, lw=1, ls="--")
    for i, r in enumerate(rows):
        for tag, filled, dx in (("dm1", False, -0.2), ("d0", True, 0.2)):
            e = r[tag]
            if not e or e["p"] is None:
                continue
            color = C_BLUE if e["open"] else C_GREY
            ax.scatter(i + dx, e["p"], s=44, marker="D" if e["truth"] == "fotometro" else "o",
                       facecolor=color if filled else "white", edgecolor=color, linewidth=1.8,
                       zorder=3)
            opened = r["roof"]["opened"]
            if opened is not None and e["open"] is not None and bool(e["open"]) != bool(opened):
                ax.scatter(i + dx, e["p"], s=190, facecolor="none", edgecolor=C_RED,
                           linewidth=1.6, zorder=4)
                ax.annotate("FA" if e["open"] else "S", (i + dx, e["p"]),
                            textcoords="offset points", xytext=(0, 9), ha="center", fontsize=7,
                            color=C_RED, fontweight="bold")
    ax.set_ylim(-0.03, 1.05)
    ax.set_ylabel("P(abrir) publicada", color=C_INK2, fontsize=9)
    ax.set_title("1. Lo previsto: P(abrir) publicada a las 16 UTC del dia (D0, relleno) y de la "
                 "vispera (D-1, hueco). Azul = 'Abierto', gris = 'Cerrado'.\n    Circulo = curva "
                 "del techo; rombo = curva del fotometro o de WeatherNext. Aro rojo = fallo contra "
                 "el techo (FA = falsa alarma, S = sorpresa)", fontsize=9.3, color=C_INK, loc="left")
    ax = axes[1]
    _style(ax)
    for i, r in enumerate(rows):
        roof = r["roof"]
        if roof["hours"] is None:
            ax.bar(i, 1.0, color="white", edgecolor=C_GREY, hatch="///", width=0.7)
            if roof["opened"]:
                ax.annotate("visto\nabierto", (i, 1.2), ha="center", fontsize=6, color=C_GREEN)
            continue
        approx = roof["source"] and not roof["source"].startswith(("tabla limpio", "lecturas"))
        if roof["hours"] < 0.3:
            ax.scatter(i, 0.25, marker="x", color=C_INK2, s=36, zorder=3)
            continue
        ax.bar(i, roof["hours"], width=0.7, color=C_GREEN if roof["opened"] else C_GREY,
               alpha=0.5 if approx else 0.9, edgecolor="white", linewidth=1)
    ax.axhline(OPEN_HOURS_MIN, color=C_INK2, lw=1, ls=":")
    ax.set_ylabel("horas de techo abierto", color=C_INK2, fontsize=9)
    ax.set_title("2. Lo que paso en el techo (tabla diaria de AstroCamp y lecturas): verde = abrio "
                 ">= 2 h (linea de puntos), gris = menos, x = cerrado; palido = cifra aproximada; "
                 "rayado = sin dato fiable", fontsize=9.3, color=C_INK, loc="left")
    ax = axes[2]
    _style(ax)
    for i, r in enumerate(rows):
        for name, color, dx in (("fotometro", C_ORANGE, -0.27), ("cloudwatcher", C_VIOLET, 0.0),
                                ("allsky", C_AQUA, 0.27)):
            t = r[name]
            if t["hours"] >= 2:
                ax.bar(i + dx, max(0.02, t["clear"] / t["hours"]), width=0.26, color=color,
                       alpha=0.95 if t["hours"] >= 4 else 0.35, edgecolor="white", linewidth=0.5)
    ax.plot(range(len(rows)), [r["moon"] for r in rows], color=C_YELLOW, lw=2)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("fraccion despejada", color=C_INK2, fontsize=9)
    ax.set_title("3. Lo que dijo el cielo: fraccion de horas de noche cerrada despejadas segun "
                 "fotometro, CloudWatcher y allsky (palido = menos de 4 h medidas); linea amarilla "
                 "= Luna", fontsize=9.3, color=C_INK, loc="left")
    ticks = [i for i, r in enumerate(rows) if r["night"].day in (1, 8, 15, 22, 29)]
    ax.set_xticks(ticks)
    ax.set_xticklabels([rows[i]["night"].strftime("%d-%m") for i in ticks])
    ax.set_xlim(-0.8, len(rows) - 0.2)
    handles = [Line2D([], [], marker="o", ls="", markerfacecolor=C_BLUE, markeredgecolor=C_BLUE,
                      label="D0 Abierto"),
               Line2D([], [], marker="o", ls="", markerfacecolor=C_GREY, markeredgecolor=C_GREY,
                      label="D0 Cerrado"),
               Line2D([], [], marker="o", ls="", markerfacecolor="white", markeredgecolor=C_BLUE,
                      label="D-1 (hueco)"),
               Line2D([], [], marker="D", ls="", markerfacecolor="white", markeredgecolor=C_INK2,
                      label="curva fotometro / WN"),
               Line2D([], [], marker="o", ls="", markerfacecolor="none", markeredgecolor=C_RED,
                      markersize=11, label="fallo"),
               Patch(color=C_GREEN, label="techo >= 2 h"), Patch(color=C_GREY, label="techo < 2 h"),
               Patch(facecolor="white", edgecolor=C_GREY, hatch="///", label="techo sin dato"),
               Patch(color=C_ORANGE, label="fotometro"), Patch(color=C_VIOLET, label="CloudWatcher"),
               Patch(color=C_AQUA, label="allsky"), Line2D([], [], color=C_YELLOW, lw=2, label="Luna")]
    fig.legend(handles=handles, loc="lower center", ncol=12, fontsize=8, frameon=False)
    fig.tight_layout(rect=(0, 0.035, 1, 1))
    fig.savefig(path, dpi=120, facecolor="white")
    plt.close(fig)


def figure_providers(R: dict, path: Path) -> None:
    plt = _plt()
    rows = []
    for key, label in (("A D0 consenso", "31-08 a 25-10, lo que habia a las 16 UTC del dia (D0)"),
                       ("A D-1 consenso", "31-08 a 25-10, lo que habia a las 16 UTC de la vispera (D-1)"),
                       ("B D0 consenso", "28-08 a 30-09 (con WeatherNext), D0"),
                       ("B D-1 consenso", "28-08 a 30-09 (con WeatherNext), D-1")):
        if key in R["d"]:
            rows.append((R["d"][key], label, "ecmwf_ifs025 crudo"))
    for key, label in (("D0 consenso", "curvas del retroanalisis 2024-26, D0"),
                       ("D-1 consenso", "curvas del retroanalisis 2024-26, D-1")):
        if key in R.get("multimodel", {}):
            rows.append((R["multimodel"][key], label, "ECMWF + curva"))
    fig, axes = plt.subplots(len(rows), 2, figsize=(13, 2.3 * len(rows) + 1.2), squeeze=False)
    for (block, label, reference), axrow in zip(rows, axes):
        names = [m for m in block["models"] if "d_brier" in block["models"][m]]
        names.sort(key=lambda m: block["models"][m]["d_brier"][0])
        for ax, metric, title, better in ((axrow[0], "d_brier", "Diferencia de Brier", "<- mejor"),
                                          (axrow[1], "d_auc", "Diferencia de AUC", "mejor ->")):
            _style(ax)
            for j, m in enumerate(names):
                pt, lo, hi = block["models"][m][metric]
                significant = lo > 0 or hi < 0
                color = (C_BLUE if m.startswith("astroweather") else C_ORANGE if "curva" in m
                         else C_VIOLET if m.startswith("weathernext") else C_AQUA
                         if m.startswith("eps") else C_GREY)
                ax.errorbar(pt, j, xerr=[[pt - lo], [hi - pt]], fmt="o", color=color,
                            ecolor=color, elinewidth=2, capsize=3, markersize=7,
                            markerfacecolor=color if significant else "white")
                absolute = block["models"][m]["brier" if metric == "d_brier" else "auc"][0]
                ax.annotate(f"{absolute:.3f}", (hi, j), textcoords="offset points",
                            xytext=(5, -3), fontsize=7.5, color=C_INK2)
            ax.axvline(0, color=C_RED, lw=1, ls="--")
            ax.set_yticks(range(len(names)))
            labels = [("astroweather publicado (P techo)" if m.startswith("astroweather") else m)
                      for m in names]
            ax.set_yticklabels(labels if metric == "d_brier" else [""] * len(names), fontsize=8)
            ax.set_ylim(len(names) - 0.4, -0.6)
            ax.set_title(f"{title} contra {reference} ({better})\n{label}; n={block['n']} h, "
                         f"{block['nights']} noches", fontsize=8.5, color=C_INK)
    fig.suptitle("Proveedores sobre las MISMAS horas, contra el consenso de sensores: diferencia "
                 "PAREADA con la referencia de cada fila, IC95 remuestreando noches.\nPunto relleno "
                 "= el IC no cruza el cero. Cifra = valor absoluto. Gris = modelo crudo (1 - nube), "
                 "aguamarina = EPS de 51 miembros, violeta = WeatherNext 3,\nnaranja = con curva de "
                 "calibracion, azul = lo publicado (es P(techo), no P(despejado): su Brier no es "
                 "comparable)", fontsize=9.5, color=C_INK)
    fig.tight_layout(rect=(0, 0, 1, 0.955))
    fig.savefig(path, dpi=120, facecolor="white")
    plt.close(fig)


def figure_bias(R: dict, path: Path) -> None:
    plt = _plt()
    from matplotlib.lines import Line2D
    names = ("luna", "nube", "hora")
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.0), squeeze=False)
    titles = {"luna": "Por Luna", "nube": "Por tipo de nube prevista (ECMWF)",
              "hora": "Por hora UTC de la noche"}
    orders = {"hora": ["19-21", "21-23", "23-01", "01-03", "03-05", "05-06"]}
    for ax, name in zip(axes[0], names):
        _style(ax)
        base = R["bias"]["consenso"].get(name, {})
        phot = R["bias"]["fotometro"].get(name, {})
        keys = orders.get(name, sorted(set(base) | set(phot),
                                       key=lambda k: -(base.get(k) or phot.get(k))["n"]))
        # Una categoria de menos de 10 horas es una anecdota, no un sesgo.
        keys = [k for k in keys if max((base.get(k) or {}).get("n", 0),
                                       (phot.get(k) or {}).get("n", 0)) >= 10]
        for j, k in enumerate(keys):
            for table, color, dy in ((base, C_BLUE, -0.14), (phot, C_ORANGE, 0.14)):
                if k not in table:
                    continue
                pt, lo, hi = table[k]["gap_photo"]
                ax.errorbar(pt, j + dy, xerr=[[pt - lo], [hi - pt]], fmt="o", color=color,
                            ecolor=color, elinewidth=2, capsize=3, markersize=6)
        ax.axvline(0, color=C_GREY, lw=1)
        ax.set_yticks(range(len(keys)))
        ax.set_yticklabels([f"{k}\n{base[k]['n']} h / {base[k]['nights']} noches" if k in base
                            else k for k in keys], fontsize=8)
        ax.invert_yaxis()
        ax.set_xlabel("observado - previsto (fraccion de horas despejadas)", color=C_INK2,
                      fontsize=8.5)
        ax.set_title(titles[name], fontsize=10, color=C_INK)
    handles = [Line2D([], [], marker="o", color=C_BLUE, label="contra el consenso (n en la etiqueta)"),
               Line2D([], [], marker="o", color=C_ORANGE, label="contra el fotometro")]
    fig.legend(handles=handles, loc="upper right", ncol=2, fontsize=8.5, frameon=False,
               bbox_to_anchor=(0.99, 0.93))
    fig.suptitle("Sesgos de la curva del fotometro aplicada al ECMWF (plazo 0-12 h, IC95 por "
                 "noches). > 0 = el cielo estuvo mas despejado de lo previsto (la curva fue "
                 "pesimista)", fontsize=10, color=C_INK)
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(path, dpi=125, facecolor="white")
    plt.close(fig)


# --- informe ------------------------------------------------------------------

def figure_m1(M: dict, path: Path) -> None:
    """Antes/despues de M1: las dos curvas sobre lo observado, y la noche a noche."""
    plt = _plt()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    fig = plt.figure(figsize=(15, 10.5))
    outer = fig.add_gridspec(2, 1, height_ratios=[1.25, 1.45], hspace=0.30)
    top = outer[0].subgridspec(1, 2, width_ratios=[1.15, 1], wspace=0.18)
    bottom = outer[1].subgridspec(2, 1, height_ratios=[1, 0.38], hspace=0.10)
    # ---- 1. las curvas
    ax = fig.add_subplot(top[0, 0])
    _style(ax)
    x = np.linspace(0, 100, 201)
    pub = M["final"].get("publicada (archivo)")
    new = M["final"]["nueva"]
    if pub:
        ax.plot(x, sigmoid(pub["intercept"] + pub["cloud_weight"] * x / 100), color=C_BLUE, lw=2.6,
                label=f"ANTES: publicada el {pub['fitted_at'][:10]} (cruce mensual 30 %); "
                      f"P(0 %) = {pub['p']['0']:.2f}")
    ax.plot(x, sigmoid(new["intercept"] + new["cloud_weight"] * x / 100), color=C_VIOLET, lw=2.6,
            label=f"DESPUES: horaria, 2 parametros ({new['nights']} noches, {new['hours']} h); "
                  f"P(0 %) = {new['p']['0']:.2f}")
    for bins, color, filled, dy, label in (
            (M["cloud_bins"]["nuevo"], C_INK, True, -13,
             "observado: horas de noche astronomica, nube disponible a las 15 UTC"),
            (M["cloud_bins"]["anterior"], C_GREY, False, 8,
             "observado con el instrumento anterior (18-05 UTC, nube de la vista)")):
        xs = [b[0] for b in bins]
        ys = [b[1] for b in bins]
        ns = [b[2] for b in bins]
        ax.scatter(xs, ys, s=[14 + 7 * np.sqrt(n) for n in ns],
                   facecolor=color if filled else "white", edgecolor=color, linewidth=1.6,
                   zorder=4, label=label)
        for xx, yy, n in zip(xs, ys, ns):
            ax.annotate(f"n={n}", (xx, yy), textcoords="offset points", xytext=(5, dy),
                        fontsize=7.2, color=color if filled else C_INK2)
    ax.axhline(0.5, color=C_GREY, lw=1, ls="--")
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.02, 1.08)
    ax.set_xlabel("nube total prevista (ECMWF 0,25 grados), %", color=C_INK2, fontsize=9)
    ax.set_ylabel("P(techo abierto) / fraccion abierta", color=C_INK2, fontsize=9)
    ax.set_title("1. La curva P(techo abierto | nube prevista): antes y despues.\n"
                 "Puntos = fraccion de horas con el techo abierto por bin de 10 % de nube (n = horas)",
                 fontsize=9.5, color=C_INK, loc="left")
    ax.legend(fontsize=7.6, loc="lower left", frameon=False)
    # ---- 2. fiabilidad hora a hora, prequencial
    ax = fig.add_subplot(top[0, 1])
    _style(ax)
    ax.plot([0, 1], [0, 1], color=C_GREY, lw=1, ls="--")
    hourly = M["hourly"]
    for name, color, marker, label in ((M1_PUB, C_BLUE, "o", "ANTES (la curva publicada cada dia)"),
                                       (M1_NEW, C_VIOLET, "D", "DESPUES (reajustada cada noche "
                                                                "solo con las anteriores)")):
        pts = M["hourly_reliability"][name]
        if not pts:
            continue
        xs, ys, ns = zip(*pts)
        b = hourly["models"][name]["brier"]
        ax.plot(xs, ys, color=color, lw=2)
        ax.scatter(xs, ys, s=[16 + 9 * np.sqrt(n) for n in ns], color=color, marker=marker,
                   edgecolor="white", linewidth=1.5, zorder=3,
                   label=f"{label}; Brier {b[0]:.3f} [{b[1]:.3f}, {b[2]:.3f}]")
        for xx, yy, n in pts:
            ax.annotate(str(n), (xx, yy), textcoords="offset points", xytext=(6, -12),
                        fontsize=7.2, color=color)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.05)
    ax.set_xlabel("P(techo abierto) prevista para la hora", color=C_INK2, fontsize=9)
    ax.set_ylabel("fraccion de horas con el techo abierto", color=C_INK2, fontsize=9)
    ax.set_title(f"2. Fiabilidad hora a hora, fuera de muestra ({hourly['n']} h de noche "
                 f"astronomica,\n{hourly['nights']} noches desde el 05-09). Diagonal = calibrada; "
                 "cifra = horas del bin", fontsize=9.5, color=C_INK, loc="left")
    ax.legend(fontsize=7.6, loc="lower right", frameon=False)
    # ---- 3. el calendario
    cal = M["calendar"]
    ax = fig.add_subplot(bottom[0, 0])
    axh = fig.add_subplot(bottom[1, 0], sharex=ax)
    _style(ax)
    _style(axh)
    for i, r in enumerate(cal):
        if r["opened"] is None:
            ax.axvspan(i - 0.5, i + 0.5, facecolor="white", edgecolor=C_GRID, hatch="///", lw=0)
        else:
            ax.axvspan(i - 0.5, i + 0.5, color=C_GREEN if r["opened"] else C_GREY,
                       alpha=0.10 if r["opened"] else 0.28, lw=0)
    ax.axhline(0.5, color=C_INK2, lw=1, ls="--")
    for i, r in enumerate(cal):
        for key, color, marker, dx in (("p_published", C_BLUE, "o", -0.17),
                                       ("p_new", C_VIOLET, "D", 0.17)):
            p = r.get(key)
            if p is None:
                continue
            ax.scatter(i + dx, p, s=40, marker=marker, color=color, edgecolor="white",
                       linewidth=0.8, zorder=3)
            if r["opened"] is not None and (p >= 0.5) != bool(r["opened"]):
                ax.scatter(i + dx, p, s=150, facecolor="none", edgecolor=C_RED, linewidth=1.5,
                           zorder=4)
    ax.set_ylim(-0.03, 1.05)
    ax.set_ylabel("P(abrir) de la noche", color=C_INK2, fontsize=9)
    acc = M["verdict_acc"]
    vb = M["verdict"]["models"]
    ax.set_title(
        "3. Noche a noche, P(abrir) a las 16 UTC del dia: ANTES = lo publicado (azul), DESPUES = la "
        "misma regla y las mismas puertas con la curva nueva ajustada solo con las noches anteriores "
        f"(violeta).\n    Fondo verde = el techo abrio >= 2 h; gris = no abrio; rayado = sin verdad. "
        f"Aro rojo = veredicto fallado. Brier {vb[M1_PUB]['brier'][0]:.3f} -> "
        f"{vb[M1_NEW]['brier'][0]:.3f}; acierto {acc[M1_PUB]['accuracy'][0]:.2f} -> "
        f"{acc[M1_NEW]['accuracy'][0]:.2f} ({M['verdict']['n']} noches)",
        fontsize=9.3, color=C_INK, loc="left")
    for i, r in enumerate(cal):
        if r["roof_hours"] is None:
            continue
        if r["roof_hours"] < 0.3:
            axh.scatter(i, 0.6, marker="x", color=C_INK2, s=30)
            continue
        axh.bar(i, r["roof_hours"], width=0.7, color=C_GREEN if r["opened"] else C_GREY)
    axh.axhline(OPEN_HOURS_MIN, color=C_INK2, lw=1, ls=":")
    axh.set_ylabel("horas de\ntecho abierto", color=C_INK2, fontsize=8.5)
    ticks = [i for i, r in enumerate(cal) if dt.date.fromisoformat(r["night"]).day in (1, 8, 15, 22, 29)]
    axh.set_xticks(ticks)
    axh.set_xticklabels([dt.date.fromisoformat(cal[i]["night"]).strftime("%d-%m") for i in ticks])
    axh.set_xlim(-0.8, len(cal) - 0.2)
    plt.setp(ax.get_xticklabels(), visible=False)
    handles = [Line2D([], [], marker="o", ls="", color=C_BLUE, label="ANTES: P(abrir) publicada"),
               Line2D([], [], marker="D", ls="", color=C_VIOLET, label="DESPUES: curva nueva"),
               Line2D([], [], marker="o", ls="", markerfacecolor="none", markeredgecolor=C_RED,
                      markersize=10, label="veredicto fallado (p >= 0,5 contra el techo)"),
               Patch(color=C_GREEN, alpha=0.3, label="techo abrio >= 2 h"),
               Patch(color=C_GREY, alpha=0.5, label="techo no abrio"),
               Patch(facecolor="white", edgecolor=C_GREY, hatch="///", label="sin verdad de techo"),
               Line2D([], [], marker="x", ls="", color=C_INK2, label="0 h")]
    fig.legend(handles=handles, loc="lower center", ncol=7, fontsize=8.2, frameon=False)
    fig.suptitle("M1 - curva del techo: ajuste horario de los dos parametros, solo noche astronomica, "
                 "nube disponible a las 15 UTC (sin fuga). Retroanalisis 05-09 -> 25-10-2026",
                 fontsize=11, color=C_INK, y=0.995)
    fig.subplots_adjust(left=0.05, right=0.99, top=0.93, bottom=0.07)
    fig.savefig(path, dpi=120, facecolor="white")
    plt.close(fig)


def _ci(t, d=3, pct=False):
    if t is None:
        return "-"
    pt, lo, hi = t
    if pt is None or not np.isfinite(pt):
        return "-"
    if pct:
        return f"{pt * 100:+.1f} [{lo * 100:+.1f}, {hi * 100:+.1f}]"
    return f"{pt:.{d}f} [{lo:.{d}f}, {hi:.{d}f}]"


def _score_table(block: dict, ref_label: str = "ECMWF crudo") -> list[str]:
    lines = [f"| previsión | Brier [IC95] | AUC [IC95] | BSS vs clim. de la muestra (%) | "
             f"BSS vs {ref_label} (%) | p media |",
             "|---|---|---|---|---|---|"]
    for name, e in block["models"].items():
        lines.append(f"| {name} | {_ci(e['brier'])} | {_ci(e['auc'])} | "
                     f"{_ci(e['bss_clim'], pct=True)} | {_ci(e.get('bss_ref'), pct=True)} | "
                     f"{e['mean_p']:.2f} |")
    return lines


def _verdict_table(blocks: dict) -> list[str]:
    lines = ["| conjunto | noches | aciertos | falsas alarmas | sorpresas | cerradas bien | "
             "acierto [IC95] | 'siempre Abierto' | persistencia (mismas noches) | HSS [IC95] | "
             "Brier [IC95] | Brier clim. | AUC [IC95] | p media / abrió |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, b in blocks.items():
        if not b:
            continue
        lines.append(
            f"| {name} | {b['n']} | {b['hits']} | {b['false_alarms']} | {b['misses']} | "
            f"{b['correct_neg']} | {_ci(b['accuracy'], 2)} | {b['always_open_acc']:.2f} | "
            f"{b['persistence_acc']:.2f} vs {b['model_acc_same']:.2f} (n={b['persistence_n']}) | "
            f"{_ci(b['hss'], 2)} | {_ci(b['brier'])} | {b['brier_clim'][0]:.3f} | "
            f"{_ci(b['auc'], 2)} | {b['mean_p']:.2f} / {b['base_rate']:.2f} |")
    return lines


def render_report(R: dict, fig_dir: str) -> str:
    """El informe, con cada cifra leida de `R`: si se rehace el analisis, el
    texto cambia con el. Lo que es juicio y no medida va marcado como tal."""
    A, AR, D, MM, B = R["a"], R.get("a_regime", {}), R["d"], R.get("multimodel", {}), R["b"]

    def mdl(block, name, metric):
        return block["models"][name][metric]

    ta = R["truth_agreement"]
    k_cw = ta["fotometro vs cloudwatcher"]["todas"]
    k_sky = ta["fotometro vs allsky"]["todas"]
    k_cs = ta["cloudwatcher vs allsky"]["todas"]
    m_cw = k_cw["matrix"]          # [verdad b][verdad a]
    cw_cloudy = m_cw[0][0] + m_cw[0][1]
    phot_false_clear = m_cw[0][1]
    cons0 = A["consenso"]["0-12 h"]
    phot0 = A["fotometro"]["0-12 h"]
    cons24 = A["consenso"]["24-48 h"]
    roof = R.get("a_roof")
    preq = R.get("preq_roof")
    wif = R.get("b_whatif_paired")
    bh = R.get("b_hours", {})
    d0 = B["D0 desde 05-09"]
    dm1 = B["D-1 desde 05-09"]
    curves = R["curves"]
    cap_lo, cap_hi = curves["roof_cap_range"]
    pc = curves["photo_current"]
    photo_range = (float(sigmoid(pc["intercept"] + pc["cloud_weight"])),
                   float(sigmoid(pc["intercept"])))
    bias_c = R["bias"]["consenso"]
    bias_p = R["bias"]["fotometro"]
    nube = bias_c.get("nube", {})
    cir = nube.get("solo nube alta (cirros)")
    low = nube.get("nube baja")
    mid = nube.get("nube media")
    clr = nube.get("ECMWF despejado (<20 %)")
    moonless = bias_c.get("luna", {}).get("Luna bajo el horizonte")
    js = R["jumps_summary"]
    wo = R["windows_outside"]
    cov = R["coverage"]
    mm0 = MM.get("D0 consenso")
    mm1 = MM.get("D-1 consenso")
    mm0p = MM.get("D0 fotometro")
    wn_oos = AR.get("consenso", {}).get("24-48 h", {}).get("weathernext")
    bD0 = D.get("B D0 consenso")
    aD0 = D.get("A D0 consenso")
    fails_d0 = [f for f in R["failures"] if f["tag"] == "D0"]
    sky_conf = R["c_sky_confusion"]
    sky_ok = sum(v for k, v in sky_conf.items() if k.split("|")[0] == k.split("|")[1])
    sky_n = sum(sky_conf.values())
    sb = R["c_sky_bias"].get("Luna <25 %", {})
    seeing = R["c_seeing"]
    unknown = R["b_unknown"]
    oct_ = cov.get("2026-10", {})
    sep_ = cov.get("2026-09", {})
    roof_last = curves["roof_last"]
    roof_rel = R.get("a_roof_reliability", {}).get("publicado (curva del techo)", [])
    top = [(x, y) for x, y, n in roof_rel if x >= 0.6]
    top_txt = (f"cuando dice {min(x for x, _ in top):.2f}-{max(x for x, _ in top):.2f} el techo "
               f"estaba abierto el {min(y for _, y in top):.0%}-{max(y for _, y in top):.0%} de "
               f"las horas" if top else "")
    slopes_sep = [s[1] for s in curves["roof_slopes"] if s[0] < "2026-10"]
    slopes_oct = [s[1] for s in curves["roof_slopes"] if s[0] >= "2026-10"]
    before_oct = max((s[4] for s in curves["roof_slopes"] if s[0] < "2026-10"), default=0)
    oct_new_hours = curves["roof_slopes"][-1][4] - before_oct
    raw_first = [R["a_reliability"][t]["0-12 h"]["ECMWF crudo"][0][1]
                 for t in ("fotometro", "consenso") if "0-12 h" in R["a_reliability"].get(t, {})]
    auc_cons = [A["consenso"][l]["models"]["ECMWF crudo"]["auc"][0]
                for l in LEAD_LABELS if l in A.get("consenso", {})]
    auc_phot = [A["fotometro"][l]["models"]["ECMWF crudo"]["auc"][0]
                for l in LEAD_LABELS if l in A.get("fotometro", {})]

    L = []
    w = L.append
    w(f"# Lo que previmos contra lo que pasó — AstroCamp, noches del 28-08 al 25-10-2026")
    w("")
    w(f"Generado por `python -m astroweather.hindcast` el {R['generated'][:16].replace('T', ' ')} UTC "
      f"sobre el archivo `{R['archive_head']}` y el repo de datos `{R['data_head']}`; "
      f"{R['resamples']} remuestreos por noches en cada intervalo. Cifras en `{fig_dir}/resultados.json`.")
    w("")
    w("Todo intervalo es un IC95 remuestreando NOCHES enteras. Con ~50 noches, una diferencia "
      "cuyo IC cruza el cero no se da por buena aquí, aunque el punto parezca mejor.")
    w("")
    # ------------------------------------------------------------------ 0
    w("## 0. Resumen")
    w("")
    w(f"1. **El veredicto de la noche acierta lo que acertaría decir siempre «Abierto».** Desde "
      f"el 05-09 (regla actual), a las 16 UTC del día: {d0['n']} noches con techo conocido, "
      f"{d0['hits']} aciertos, {d0['false_alarms']} falsa(s) alarma(s), {d0['misses']} sorpresas, "
      f"{d0['correct_neg']} cierres bien dichos: acierto {_ci(d0['accuracy'], 2)} contra "
      f"{d0['always_open_acc']:.2f} de «siempre Abierto» y {d0['persistence_acc']:.2f} de «como "
      f"anoche». Discrimina (AUC {_ci(d0['auc'], 2)}), pero la probabilidad publicada está "
      f"hundida: media {d0['mean_p']:.2f} cuando el techo abrió el {d0['base_rate']:.0%} de las "
      f"noches (Brier {d0['brier'][0]:.3f} contra {d0['brier_clim'][0]:.3f} de la climatología).")
    w(f"2. **La causa principal está medida: la curva del techo no puede pasar de "
      f"{cap_lo:.0%}-{cap_hi:.0%}** ni con 0 % de nube (cruce en el {roof_last['crossing']:.0%} "
      f"fijado por los meses). Hora a hora, {top_txt}. Las horas esperadas publicadas se quedan "
      f"cortas en "
      f"{-bh.get('bias', float('nan')):.1f} h por noche ({bh.get('mean_expected', float('nan')):.1f} "
      f"contra {bh.get('mean_real', float('nan')):.1f} reales).")
    if wif:
        wm = wif["models"]["curva horaria prequencial"]
        w(f"3. **Arreglo validado sin fuga:** la misma regla con una curva horaria reajustada noche "
          f"a noche solo con las noches anteriores baja el Brier del veredicto de "
          f"{wif['models']['publicado']['brier'][0]:.3f} a {wm['brier'][0]:.3f} "
          f"(diferencia pareada {_ci(wm['d_brier'])}, {wif['n']} noches), con el mismo acierto.")
    w(f"4. **La verdad del fotómetro no sirve sola para puntuar nubes en este periodo.** Contra el "
      f"CloudWatcher, de {cw_cloudy} horas nubladas el fotómetro llamó despejadas "
      f"{phot_false_clear} (κ = {k_cw['kappa']:.2f}; con la allsky κ = {k_sky['kappa']:.2f}). "
      f"Falla en las dos direcciones y siempre con la Luna: 27-09 cubierto total con Luna llena, "
      f"9 de 9 horas «despejadas»; 21-25-09 Luna baja, cielo limpio (1.000-1.500 estrellas), "
      f"horas «nubladas». Contra el consenso de tres sensores el ECMWF ordena las horas con AUC "
      f"{_ci(mdl(cons0, 'ECMWF crudo', 'auc'), 2)}; contra el fotómetro solo, "
      f"{_ci(mdl(phot0, 'ECMWF crudo', 'auc'), 2)}.")
    w(f"5. **La probabilidad horaria de despejado (ECMWF + curva del fotómetro) no bate a la "
      f"climatología con claridad**: BSS {_ci(mdl(cons0, 'ECMWF + curva fotometro', 'bss_clim'), pct=True)} % "
      f"contra el consenso a 0-12 h, {_ci(mdl(phot0, 'ECMWF + curva fotometro', 'bss_clim'), pct=True)} % "
      f"contra el fotómetro. Sí mejora mucho al ECMWF crudo "
      f"(BSS {_ci(mdl(phot0, 'ECMWF + curva fotometro', 'bss_ref'), pct=True)} %). Su recorrido "
      f"es {photo_range[0]:.2f}-{photo_range[1]:.2f}: nunca dice «probablemente nublado».")
    if mm0:
        w(f"6. **ICON calibrado ordena mejor que el ECMWF calibrado a D0, fuera de muestra; a D-1 "
          f"no.** Curvas ajustadas en el retroanálisis 2024-26 y puntuadas con lo que había a las "
          f"16 UTC: a D0 contra el consenso, AUC {_ci(mdl(mm0, 'ICON + curva', 'd_auc'))} y Brier "
          f"{_ci(mdl(mm0, 'ICON + curva', 'd_brier'))}; contra el fotómetro, Brier "
          f"{_ci(mdl(mm0p, 'ICON + curva', 'd_brier'))}; a D-1 contra el consenso, AUC "
          f"{_ci(mdl(mm1, 'ICON + curva', 'd_auc'))}.")
    if cir:
        w(f"7. **El sesgo sistemático más grande es el tipo de nube.** Con solo nube alta prevista "
          f"(capas del ECMWF de 9 km) el cielo estuvo despejado el {cir['obs']:.0%} de las horas y "
          f"la curva decía {cir['pred_photo']:.0%} (la publicada, {cir['pred_pub']:.0%}); con nube "
          f"media prevista, {mid['obs']:.0%} contra {mid['pred_photo']:.0%}. "
          f"{sum(1 for f in fails_d0 if f['why'].startswith('nube ALTA'))} de las {len(fails_d0)} "
          f"noches falladas del D0 son cirros previstos con el techo abierto toda la noche.")
    ac = R.get("asof_check", {})
    w(f"8. **El archivo de proveedores mezcla pasadas bajo una misma etiqueta, y eso mete "
      f"retrospectiva** en quien lo lea por la vista deduplicada: `run_utc` es el día a las 00Z, "
      f"pero cada recogida trae la pasada más reciente y la vista se queda con la de las 21-23 "
      f"UTC. Reconstruyendo recogida a recogida, la nube que usó el producto casa en el "
      f"{ac.get('exact', 0):.0%} de {ac.get('n', 0)} horas; la vista, solo en el "
      f"{ac.get('dedup_exact', 0):.0%}. La curva del techo se entrena así (`roofcalibration.py:222`). "
      f"Y el ECMWF de 0,25° trae una nube total por debajo de su propia capa mayor en el "
      f"{R.get('layer_consistency', {}).get('ecmwf_ifs025', {}).get('total_below_max_layer', 0):.0%} "
      f"de las filas.")
    w(f"9. **Huecos que limitan todo lo anterior:** desde el 30-09 el colector solo lee el techo y "
      f"el CloudWatcher 2-3 veces por noche (octubre: {oct_.get('lecturas_techo', 0)} lecturas de "
      f"techo contra {sep_.get('lecturas_techo', 0)} en septiembre; {oct_.get('horas_cloudwatcher', 0)} "
      f"horas de CloudWatcher); el fotómetro de octubre cubre {oct_.get('horas_fotometro', 0)} de "
      f"{oct_.get('horas_oscuras', 0)} horas oscuras por un fallo de `top_up` recuperable; "
      f"WeatherNext de octubre no está en el archivo (se agrupa al cerrar el mes); seeing y AOD "
      f"no tienen verdad. {len(unknown)} noches (29-09 a 07-10) quedan sin verdad de techo.")
    w("")
    w(f"Lista de mejoras: sección 10 (M1-M18). Figuras: `{fig_dir}/calendario.png` (noche a "
      f"noche), `{fig_dir}/fiabilidad.png`, `{fig_dir}/proveedores.png`, `{fig_dir}/sesgos.png`.")
    w("")
    # ------------------------------------------------------------------ 1
    w("## 1. Qué se comparó y con qué")
    w("")
    w("**Lo previsto.** (1) Las 5.093 filas `model='astroweather'` del archivo: la probabilidad "
      "horaria que salió en cada pasada, con la curva vigente ese día. (2) Las "
      f"{R['n_publications']} versiones de `noche.json` en el historial del repo de datos "
      f"({R['publications_span'][0][:10]} a {R['publications_span'][1][:10]}): lo que vio el "
      "usuario (probabilidad de abrir, veredicto, ventana, horas esperadas, terciles). (3) Los "
      "proveedores archivados en el mismo periodo.")
    w("")
    w("**Deduplicación.** Una predicción por hora y franja de plazo (0-12, 12-24, 24-48 h desde la "
      "emisión): la última emitida dentro de la franja. Por noche: la publicación vigente a las "
      "16 UTC del día (D0) y la de la víspera (D-1). Proveedores: lo que había en el archivo "
      "a esas mismas horas, reconstruido recogida a recogida (sección 1 bis).")
    w("")
    w("**Tres magnitudes distintas bajo el mismo nombre.** La columna `p_calibrated` (y "
      "`p_despejado` en la web) fue P(despejado) contra el fotómetro hasta el 04-09; desde "
      "entonces, para esta noche, P(techo abierto); desde el 25-09, para la noche siguiente, "
      "P(despejado) de WeatherNext. La etiqueta `provider_model_version` solo nombra la curva "
      "del fotómetro, así que el régimen se reconstruyó comparando cada número con lo que daba "
      f"cada curva vigente (historial git): "
      + ", ".join(f"{k} {v} filas" for k, v in R['regimes'].items())
      + f"; error p99 de la reconstrucción "
      f"{R['regime_gap_p99']:.4f}. Comparar el Brier de una P(techo) con una verdad de nubes no "
      "tiene sentido: en esas tablas solo vale su AUC.")
    w("")
    w("| mes | noches | horas oscuras | fotómetro | CloudWatcher | allsky | consenso | lecturas de techo |")
    w("|---|---|---|---|---|---|---|---|")
    for k, v in cov.items():
        w(f"| {k} | {v['noches']} | {v['horas_oscuras']} | {v['horas_fotometro']} | "
          f"{v['horas_cloudwatcher']} | {v['horas_allsky']} | {v['horas_consenso']} | "
          f"{v['lecturas_techo']} |")
    w("")
    w("## 1 bis. Qué había en el archivo en cada momento")
    w("")
    lc = R.get("layer_consistency", {})
    w(f"Para los modelos de Open-Meteo, `run_utc` es el primer instante de la serie pedida "
      f"(`workers/fetch_forecast.py:101`: el día a las 00Z), no la hora de inicialización. Cada "
      f"recogida del día trae la pasada más reciente que sirve Open-Meteo y la vista deduplicada "
      f"(`queries.py:52`) se queda con la última, la de las 21-23 UTC. Ejemplo, hora 22 UTC del "
      f"17-09: la «pasada del 17-09» del ECMWF de 0,25° vale 69, 65, 17 y 12 % de nube según se "
      f"recogiera a las 02, 10, 16 o 23 UTC. Leerla por la etiqueta es leer el futuro.")
    w("")
    w(f"Este informe lee el Parquet crudo y reconstruye lo que había en cada instante (la "
      f"recogida más reciente anterior). **Control positivo**: en las {ac.get('n', 0)} horas en "
      f"que el producto usó el ECMWF, la nube archivada con nuestra predicción y la reconstruida "
      f"coinciden en el {ac.get('exact', 0):.1%} (mediana de la diferencia "
      f"{ac.get('median_abs', 0):.1f} puntos); la vista deduplicada habría coincidido en el "
      f"{ac.get('dedup_exact', 0):.1%}. WeatherNext lleva su inicialización de verdad pero agosto y "
      f"septiembre llegaron con un relleno: su disponibilidad se fecha como inicialización + 7 h "
      f"(supuesto, NO medido). Los proveedores se comparan con lo que había a las 16 UTC.")
    w("")
    w("Coherencia interna de cada proveedor (fracción de filas con la nube total por debajo de "
      "su capa mayor, imposible con cualquier regla de solape): " + ", ".join(
        f"{m} {v['total_below_max_layer']:.1%} (n={v['n']})" for m, v in lc.items()) +
      ". El 0,25° del ECMWF, que es la nube del producto, no casa con sus capas; por eso los "
      "cortes por tipo de nube usan las capas del ECMWF de 9 km. Qué variable sirve Open-Meteo "
      "como total del 0,25°: NO VERIFICADO.")
    w("")
    # ------------------------------------------------------------------ 2
    w("## 2. Antes de puntuar: validar las verdades")
    w("")
    w("Pregunta 1 de las seis: ¿puede la verdad, por construcción, ver lo que se le pide? Se "
      "cruzaron tres sensores independientes hora a hora (noche cerrada, Sol < −18°):")
    w("")
    w("| par | Luna | horas | κ | ambos nublado | solo el 2º nublado | solo el 1º nublado | ambos despejado |")
    w("|---|---|---|---|---|---|---|---|")
    for pair, d in ta.items():
        for moon, e in d.items():
            m = e["matrix"]
            w(f"| {pair} | {moon} | {e['n']} | {e['kappa']:.2f} | {m[0][0]} | {m[0][1]} | "
              f"{m[1][0]} | {m[1][1]} |")
    w("")
    w("Lectura (columnas: «solo el 2º nublado» = el segundo sensor dice nublado y el primero "
      "despejado). Casos mirados uno a uno en el archivo:")
    w("")
    w("- **Fotómetro, falso despejado con Luna llena y nube gruesa**: 27/28-09, CloudWatcher a "
      "−2/−6 °C de cielo, allsky 0-34 estrellas, ECMWF 88-98 % de nube, techo cerrado; el "
      "fotómetro, 9 de 9 horas «despejadas». La nube devuelve un cielo tan brillante como el que "
      "predice el modelo lunar y pasa el corte de nivel (`CLEAR_LEVEL_TOLERANCE` = 0,511).")
    w("- **Fotómetro, falso nublado con Luna baja**: 21-25-09, Luna a 7-21° de altura, "
      "CloudWatcher despejado, allsky 960-1.570 estrellas, ECMWF 0 %; el fotómetro, nublado. Es "
      "el error del modelo lunar cerca del horizonte que `calibration.py` ya declaraba.")
    w("- **CloudWatcher, falso nublado con cielo cálido**: 01-09, cielo IR a −5/−8 °C toda la "
      "noche, allsky ~400 estrellas con Luna al 75 %, fotómetro despejado, ECMWF 0-13 %. El "
      "techo NO abrió (1 h): AstroCamp decide con este sensor. Causa NO VERIFICADA (humedad o "
      "calima; no hay PM2.5 esa noche).")
    w("- **Allsky**: 0 estrellas desde las 03 UTC todas las noches (no es nube; mediana 0 en las "
      "15 noches limpias), así que solo cuenta de 19 a 03 UTC. Umbral: 100 estrellas, el valle "
      "de su distribución: " + ", ".join(f"{lo:.0f}-{'∞' if hi > 1e8 else f'{hi:.0f}'}: {n} h"
                                          for lo, hi, n in R["allsky_bands"]) +
      ". Con Luna llena alta y cirros baja de 100 (26-09) aunque el techo estuvo abierto.")
    w("")
    w(f"**Consenso** = mayoría de las disponibles, con al menos dos y sin empate "
      f"({R['truth_sizes']['consenso']['hours']} horas, {R['truth_sizes']['consenso']['clear_rate']:.0%} "
      f"despejadas). Es un constructo y se declara: cada tabla va también contra el fotómetro "
      f"solo, que es la verdad con la que se entrena el producto.")
    w("")
    rc = R.get("roof_crosscheck")
    if rc:
        w(f"**Verdad del techo, control positivo.** La tabla diaria de AstroCamp (diferencias "
          f"entre versiones del historial git) contra las lecturas cada 30 min, en las "
          f"{rc['n']} noches de septiembre con las dos: r = {rc['r']:.3f}, MAE {rc['mae']:.2f} h, "
          f"sesgo {rc['bias']:+.2f} h (la tabla cuenta algo más), y la misma decisión «abrió ≥ 2 h» "
          f"en {rc['agree_open']} de {rc['n']}. Las dos fuentes se validan entre sí; en octubre "
          f"solo queda la tabla.")
    w("")
    for truth_name, ctl in R.get("a_controls", {}).items():
        real = ctl["models"]["ECMWF + curva fotometro"]
        shuf = ctl["models"]["la misma, barajada"]
        w(f"**Controles del puntuador contra el {truth_name}** (franja 0-12 h): la misma "
          f"previsión con las horas barajadas debe salir peor, y la verdad misma, perfecta.")
        w("")
        L.extend(_score_table(ctl, "-"))
        w("")
        separated = real["auc"][1] > shuf["auc"][2]
        w(f"La previsión real: AUC {real['auc'][0]:.2f} y Brier {real['brier'][0]:.3f}; barajada: "
          f"AUC {shuf['auc'][0]:.2f} y Brier {shuf['brier'][0]:.3f}. "
          + ("Los IC del AUC no se tocan: contra esta verdad la previsión se distingue de su "
             "propio azar." if separated else
             "Los IC del AUC se solapan y en Brier no se distinguen: **contra esta verdad la "
             "previsión apenas se separa de su propio barajado**, y eso es un fallo de la verdad "
             "tanto como de la previsión (sección 2)."))
        w("")
    # ------------------------------------------------------------------ 3
    w("## 3. (a) Probabilidad horaria de despejado, por plazo")
    w("")
    w("«ECMWF + curva fotómetro» es la curva del 30-08 (la que el producto usó hasta el 04-09 y "
      "la base de todo lo demás) sobre la pasada del ECMWF que había al emitir: es la previsión "
      "de NUBE del motor, fuera de muestra desde el 31-08. «Publicado» es lo archivado (sobre "
      "todo P(techo)). «Climatología previa»: fracción despejada del mismo mes en 2024-08 → "
      "2026-07 según el fotómetro.")
    w("")
    for truth in ("fotometro", "consenso"):
        for label in LEAD_LABELS:
            block = A.get(truth, {}).get(label)
            if not block:
                continue
            w(f"**Contra el {truth}, plazo {label}** — {block['n']} h, {block['nights']} noches, "
              f"tasa base {block['base_rate']:.2f}; regímenes de lo publicado: "
              + ", ".join(f"{k} {v}" for k, v in block['regimes'].items()))
            w("")
            L.extend(_score_table(block))
            w("")
    w("Contra cada sensor por separado, solo la previsión de nube (ECMWF + curva):")
    w("")
    w("| verdad | plazo | horas | noches | tasa base | Brier | AUC | BSS vs clim. (%) |")
    w("|---|---|---|---|---|---|---|---|")
    for truth in TRUTHS:
        for label in LEAD_LABELS:
            block = A.get(truth, {}).get(label)
            if block:
                e = block["models"]["ECMWF + curva fotometro"]
                w(f"| {truth} | {label} | {block['n']} | {block['nights']} | "
                  f"{block['base_rate']:.2f} | {_ci(e['brier'])} | {_ci(e['auc'])} | "
                  f"{_ci(e['bss_clim'], pct=True)} |")
    w("")
    w(f"![Fiabilidad]({fig_dir}/fiabilidad.png)")
    w("")
    w("*Figura 1. Diagrama de fiabilidad. Paneles 1-3: probabilidad de cielo despejado (naranja: "
      "ECMWF + curva del fotómetro; verde: ECMWF crudo, 1 − nube) contra el fotómetro y contra el "
      "consenso. Panel 4: lo que se publica desde el 05-09, P(techo abierto), contra el techo "
      "real hora a hora (azul), con la curva del fotómetro como control (naranja) y la curva "
      "horaria prequencial (violeta). Diagonal = calibración perfecta; tamaño ~ nº de horas.*")
    w("")
    top_cons = [(x, y, n) for x, y, n in R["a_reliability"].get("consenso", {})
                .get("0-12 h", {}).get("ECMWF + curva fotometro", []) if x >= 0.88]
    w("Lo que dice la figura, medido: la curva del fotómetro vive entre "
      f"{photo_range[0]:.2f} y {photo_range[1]:.2f}; contra el consenso, su casilla alta"
      + (f" ({top_cons[-1][0]:.2f}, {top_cons[-1][2]} h) acierta el {top_cons[-1][1]:.0%}: "
         "es demasiado prudente arriba" if top_cons else "") + ". El ECMWF crudo está mal "
      "calibrado (en su casilla de 0-10 % de «despejado» el cielo estuvo despejado el "
      + "/".join(f"{x:.0%}" for x in raw_first) + " de las horas, fotómetro/consenso), "
      "que es para lo que existe la curva.")
    w("")
    if roof:
        w(f"**Lo publicado contra lo que pretende predecir: el techo, hora a hora** "
          f"({roof['n']} h de 18 a 05 UTC con lecturas, {roof['nights']} noches, "
          f"{roof['span'][0][:10]} a {roof['span'][1][:10]}; tasa base {roof['base_rate']:.2f}):")
        w("")
        L.extend(_score_table(roof, "-"))
        w("")
        ctl_mid = [(x, y) for x, y, n in R["a_roof_reliability"].get(
            "curva del fotometro (control)", []) if x < 0.9]
        w(f"La curva del techo ordena bien (AUC {mdl(roof, 'publicado (curva del techo)', 'auc')[0]:.2f}) "
          f"pero está hundida: p media {mdl(roof, 'publicado (curva del techo)', 'mean_p'):.2f} "
          f"contra un techo abierto el {roof['base_rate']:.0%} de esas horas."
          + (f" El control del 05-09 se reproduce en rojo: la curva del fotómetro es optimista "
             f"en la zona media (dice {min(x for x, _ in ctl_mid):.2f}-{max(x for x, _ in ctl_mid):.2f} "
             f"y el techo abre el {min(y for _, y in ctl_mid):.0%}-{max(y for _, y in ctl_mid):.0%})."
             if ctl_mid else ""))
        w("")
    if preq:
        pm = preq["models"]["curva horaria prequencial"]
        last = R["preq_roof_curves"][-1]
        w(f"**Curva horaria prequencial** (logística de dos parámetros reajustada cada noche solo "
          f"con las horas de techo de las noches anteriores, desde la {PREQ_MIN_NIGHTS}ª): Brier "
          f"{_ci(pm['brier'])} contra {_ci(preq['models']['publicado (curva del techo)']['brier'])} "
          f"publicado (diferencia {_ci(pm['d_brier'])}); AUC {_ci(pm['d_auc'])} peor, porque las "
          f"primeras curvas eran casi planas. Su última versión ({last[0]}): P(0 % de nube) = "
          f"{last[3]:.2f} y cruce del 50 % en {last[4]:.0%} de nube, contra {cap_hi:.2f} y "
          f"{roof_last['crossing']:.0%} de la curva publicada. Las horas dicen que el cruce mensual "
          f"del 30 % es demasiado bajo para este otoño.")
        w("")
    if wn_oos:
        w(f"**WeatherNext calibrado, fuera de muestra** (noche siguiente, 25-09 → 25-10, plazo "
          f"24-48 h, contra el consenso, {wn_oos['n']} h, {wn_oos['nights']} noches): lo "
          f"publicado con su curva, Brier {_ci(mdl(wn_oos, 'publicado', 'brier'))} y AUC "
          f"{_ci(mdl(wn_oos, 'publicado', 'auc'))}, contra ECMWF + curva "
          f"{_ci(mdl(wn_oos, 'ECMWF + curva fotometro', 'brier'))} y "
          f"{_ci(mdl(wn_oos, 'ECMWF + curva fotometro', 'auc'))}. Mejor en el punto, dentro del "
          f"ruido en Brier.")
        w("")
    # ------------------------------------------------------------------ 4
    w("## 4. (b) El veredicto publicado contra lo que hizo AstroCamp")
    w("")
    w(f"Verdad: «abrió» = techo abierto ≥ {OPEN_HOURS_MIN:.0f} h (la ventana de dos horas que "
      "promete «Abierto»), de la tabla diaria de AstroCamp; donde no, de las lecturas. Para las "
      "noches sin regla escrita (antes del 05-09) el veredicto se deriva como p ≥ 0,5.")
    w("")
    L.extend(_verdict_table(B))
    w("")
    w(f"Hay que leerla con su tamaño: solo {d0['correct_neg'] + d0['false_alarms']} noches "
      f"cerradas en el conjunto D0 desde el 05-09. Con tan pocas negativas el HSS y la tasa de "
      f"falsas alarmas apenas se pueden medir, y lo que sí se puede afirmar es que el veredicto "
      f"no supera a «siempre Abierto» ni a «como anoche» en este otoño seco.")
    w("")
    if unknown:
        w(f"**Sesgo de selección.** {len(unknown)} noches sin verdad de techo: "
          + ", ".join(f"{n} ({'Abierto' if o else 'Cerrado' if o is not None else '-'})"
                      for n, o, q, s in unknown)
          + f". La tabla solo da 8 h para todo el tramo 27-09 → 08-10 y el colector leyó el "
            f"techo una o dos veces por noche. {sum(1 for x in unknown if x[1] is False)} de las "
            f"{len(unknown)} se dijeron «Cerrado»: si fueron cierres reales (8 h en todo el tramo "
            f"lo hace probable para la mayoría) el veredicto saldría algo mejor de lo que dice la "
            f"tabla. NO VERIFICADO.")
        w("")
    if wif:
        w("**¿Y con la curva horaria prequencial?** Misma regla, mismas puertas (el factor de "
          "seguridad de cada hora se toma de lo publicado), mismas noches:")
        w("")
        L.extend(_score_table(wif, "lo publicado"))
        w("")
        bw = R.get("b_whatif", {})
        bp = R.get("b_published_same", {})
        if bw and bp:
            w(f"Contingencia: publicada {bp['hits']}/{bp['false_alarms']}/{bp['misses']}/"
              f"{bp['correct_neg']} (aciertos/FA/sorpresas/cierres) contra prequencial "
              f"{bw['hits']}/{bw['false_alarms']}/{bw['misses']}/{bw['correct_neg']}. Gana la "
              f"probabilidad, no el sí/no.")
            w("")
    w(f"**Horas esperadas contra horas reales** (desde el 05-09, {bh.get('n', 0)} noches): "
      f"sesgo {bh.get('bias', float('nan')):+.1f} h, MAE {bh.get('mae', float('nan')):.1f} h, "
      f"r = {bh.get('r', float('nan')):.2f}.")
    w("")
    bwin = R.get("b_window", {})
    w(f"**La ventana.** En las {bwin.get('n', 0)} noches «Abierto» con al menos dos lecturas "
      f"dentro de la ventana, el techo estaba abierto en el {bwin.get('mean_open_frac', 0) or 0:.0%} "
      f"de ellas ({bwin.get('fully_open', 0)} noches del todo). Pero sobre las "
      f"{wo['esta noche'][0]} publicaciones, {wo['esta noche'][1]} ventanas de esta noche y "
      f"{wo['noche siguiente'][1]} de la siguiente terminan DESPUÉS del orto, y "
      f"{wo['esta noche'][2]} y {wo['noche siguiente'][2]} caen fuera de la noche astronómica "
      f"(±40 min): la rejilla va de ocaso−1 h a orto+1 h y nada impide elegir el amanecer. La "
      f"falsa alarma del 28-09 es eso: «Abierto 68 %» con ventana 06-08 UTC del 29-09.")
    w("")
    w(f"**D-1 → D0, la misma noche cambia de magnitud.** En {js['n_wn_to_roof']} noches la víspera "
      f"publicó P(despejado) de WeatherNext y el día P(techo): la probabilidad cae de media "
      f"{js['mean_drop_wn_to_roof']:.2f} sin que cambie el tiempo. El veredicto se dio la vuelta "
      f"en {js['flips']} de {js['n']} noches entre la víspera y el día.")
    w("")
    w("**Noches falladas** (contra el techo; D0 y D-1). El porqué sale de los hechos de esa noche "
      "(capas del ECMWF que había a las 16 UTC del día, en las horas oscuras; sensores; "
      "puertas):")
    w("")
    w("| noche | cuándo | tipo | p | curva | ventana (UTC) | nube oscura (%) | capas ECMWF 9 km total/baja/media/alta | techo (h) | fotómetro · consenso · CloudWatcher · allsky (horas despejadas/medidas) | porqué |")
    w("|---|---|---|---|---|---|---|---|---|---|---|")
    for f in R["failures"]:
        win = (f"{str(f['window'][0])[11:16]}-{str(f['window'][1])[11:16]}"
               if f["window"] else "-")
        lay = ("/".join(f"{x:.0f}" for x in f["layers"]) if f["layers"] else "-")
        sky = " · ".join(f"{c}/{h}" for c, h in f["sky"].values())
        cloud = "-" if f["cloud_dark"] is None else f"{f['cloud_dark']:.0f}"
        w(f"| {f['night']} | {f['tag']} | {f['kind']} | {f['p']:.2f} | {f['truth_curve'] or '-'} | "
          f"{win} | {cloud} | {lay} | {f['roof_hours']:.0f} | {sky} | {f['why']} |")
    w("")
    w("Las más instructivas: **26-09** (Cerrado 7 % con cirros al 90 %: abrieron 13 h, los tres "
      "sensores despejado), **05-09** (Cerrado 41 %, cirros al 100 %, abrieron 8 h), **28-09** "
      "(Abierto por una ventana al amanecer), **01-09** (Abierto 92 % con el cielo limpio; no "
      "abrieron porque su CloudWatcher marcó nublado: ningún modelo de cielo lo habría acertado) "
      "y **14/15-10** (Cerrado con 47-69 % de nube, mezcla de baja al 40-50 % y alta al 100 %: "
      "abrieron 13 h cada una).")
    w("")
    w(f"![Calendario]({fig_dir}/calendario.png)")
    w("")
    w("*Figura 2. Noche a noche. Fila 1: P(abrir) publicada a las 16 UTC del día (D0, relleno) y "
      "de la víspera (D-1, hueco); azul = «Abierto», gris = «Cerrado»; círculo = curva del techo, "
      "rombo = curva del fotómetro o WeatherNext; aro rojo = fallo (FA falsa alarma, S sorpresa). "
      "Fila 2: horas de techo abierto (verde ≥ 2 h, gris < 2 h, x = cerrado, pálido = "
      "aproximado, rayado = sin dato). Fila 3: fracción de horas despejadas según fotómetro "
      "(naranja), CloudWatcher (violeta) y allsky (verde agua); línea amarilla = Luna.*")
    w("")
    # ------------------------------------------------------------------ 5
    w("## 5. (c) Terciles publicados")
    w("")
    w(f"- **Cielo** (tercil del brillo de cénit más oscuro, D0): coincide con el medido "
      f"(máximo horario del fotómetro en horas despejadas por consenso) en {sky_ok} de {sky_n} "
      f"noches: " + ", ".join(f"{k.replace('|', ' → ')}: {v}" for k, v in sky_conf.items()) +
      f". La magnitud publicada es más brillante que la medida: sin Luna "
      f"{sb.get('mean', float('nan')):+.2f} mag de media (n={sb.get('n', 0)}, MAE "
      f"{sb.get('mae', float('nan')):.2f}).")
    if R.get("c_aod_spearman"):
        w(f"- **AOD**: no hay AOD medido (`k_extinction`: {R['c_kext']} filas). El único sustituto, "
          f"el PM2.5 de AstroCamp, no sigue al AOD publicado: Spearman "
          f"{_ci(R['c_aod_spearman'], 2)} en {len(R['c_aod'])} noches. **No verificable.**")
    w(f"- **Seeing**: tercil publicado (D0) en {sum(seeing.values())} noches: " +
      ", ".join(f"«{k}» {v}" for k, v in seeing.items()) +
      f". Un tercil que sale igual todas las noches no informa, y `seeing.py:62` dice que los "
      f"cortes salen de la distribución del propio modelo: ya no la describen. Verdad: "
      f"{R['c_fwhm'][0]} medidas de FWHM, la última del {R['c_fwhm'][1][:10]}. **No verificable.**")
    w("")
    # ------------------------------------------------------------------ 6
    w("## 6. (d) Proveedores sobre las mismas horas")
    w("")
    w(f"![Proveedores]({fig_dir}/proveedores.png)")
    w("")
    w("*Figura 3. Diferencia pareada de Brier (izquierda, menos es mejor) y de AUC (derecha) contra "
      "la referencia de cada fila, sobre las mismas horas y contra el consenso; IC95 por noches; "
      "punto relleno = el IC no cruza el cero; la cifra es el valor absoluto. Filas 1-2: todo el "
      "periodo; 3-4: hasta el 30-09, con WeatherNext; 5-6: curvas ajustadas en el retroanálisis "
      "2024-26 y puntuadas aquí.*")
    w("")
    for key in ("A D0 consenso", "A D-1 consenso", "B D0 consenso", "B D-1 consenso",
                "A D0 fotometro", "B D0 fotometro"):
        block = D.get(key)
        if not block:
            continue
        w(f"**{key}** — {block['n']} h, {block['nights']} noches ({block['span'][0][:10]} a "
          f"{block['span'][1][:10]}), tasa base {block['base_rate']:.2f}. Referencia: ecmwf_ifs025 crudo.")
        w("")
        L.extend(_score_table(block, "ecmwf_ifs025 crudo"))
        w("")
    for key in ("D0 consenso", "D-1 consenso", "D0 fotometro", "D-1 fotometro"):
        block = MM.get(key)
        if not block:
            continue
        tr = MM[key.split()[0] + " entrenamiento"]
        w(f"**Curvas del retroanálisis, {key}** — entrenadas con {tr['horas']} horas de 2024-02 a "
          f"2026-08-27 (fotómetro); puntuadas en {block['n']} h, {block['nights']} noches. "
          f"Referencia: ECMWF + curva.")
        w("")
        L.extend(_score_table(block, "ECMWF + curva"))
        w("")
    w("Lectura (con lo que había a las 16 UTC). El EPS medio es la única entrada cruda que mejora "
      "el Brier del ECMWF determinista, y por poco; ICON (a D0) y WeatherNext (hasta el 30-09) "
      "ordenan mejor; calibrado con su propio retroanálisis, ICON mejora al ECMWF calibrado en AUC "
      "a D0 y casi nada en Brier, y a D-1 no se distingue. GFS y Météo-France son los peores. La "
      "capa mayor del 0,25° ordena peor que su total. Lo publicado (P(techo)) no se compara en "
      "Brier.")
    w("")
    # ------------------------------------------------------------------ 7
    w("## 7. Sesgos sistemáticos")
    w("")
    w(f"![Sesgos]({fig_dir}/sesgos.png)")
    w("")
    w("*Figura 4. Observado menos previsto (fracción de horas despejadas) para la curva del "
      "fotómetro sobre el ECMWF, franja 0-12 h; azul contra el consenso, naranja contra el "
      "fotómetro; IC95 por noches. > 0: la curva fue pesimista.*")
    w("")
    w("| corte | categoría | horas | noches | observado (consenso) | previsto (curva) | previsto (publicado) | obs − prev [IC95] | obs − prev, fotómetro [IC95] |")
    w("|---|---|---|---|---|---|---|---|---|")
    for cut in ("luna", "nube", "hora", "regimen"):
        for cat, e in bias_c.get(cut, {}).items():
            ep = bias_p.get(cut, {}).get(cat)
            w(f"| {cut} | {cat} | {e['n']} | {e['nights']} | {e['obs']:.2f} | {e['pred_photo']:.2f} | "
              f"{e['pred_pub']:.2f} | {_ci(e['gap_photo'], 2)} | {_ci(ep['gap_photo'], 2) if ep else '-'} |")
    w("")
    w("- **Tipo de nube (medido, el mayor):** con solo nube alta prevista la curva es pesimista "
      f"en {cir['gap_photo'][0]:+.2f} [{cir['gap_photo'][1]:+.2f}, {cir['gap_photo'][2]:+.2f}]; con "
      f"baja, optimista en {low['gap_photo'][0]:+.2f}; con media, {mid['gap_photo'][0]:+.2f} (4 "
      "noches). La nube total no distingue un velo de cirros de un estrato." if cir and low and mid else "")
    w(f"- **Cielo limpio previsto:** observado {clr['obs']:.3f} contra {clr['pred_photo']:.3f} "
      "(la curva no pasa de 0,92 porque su verdad mete ~8 % de falsos nublados)." if clr else "")
    w(f"- **Luna:** sin Luna la curva es pesimista ({moonless['gap_photo'][0]:+.2f} "
      f"[{moonless['gap_photo'][1]:+.2f}, {moonless['gap_photo'][2]:+.2f}]); con Luna llena alta el "
      "IC cruza el cero. Coherente con el techo de 0,92 de la curva; con 23 noches de Luna no "
      "se separa de que las rachas nubladas cayeran en Luna llena (26-28-09, 17-20-10)."
      if moonless else "")
    off = [f"{k} ({e['n']} h)" for k, e in bias_c.get("hora", {}).items()
           if e["gap_photo"][1] > 0 or e["gap_photo"][2] < 0]
    w("- **Hora de la noche:** " + (
        "excluyen el cero contra el consenso solo " + ", ".join(off) + "; con cinco cortes, uno "
        "marginal es lo esperable por azar (y de 03 a 05 UTC la allsky ya no vota). No se da por "
        "sesgo." if off else "ningún IC excluye el cero. Sin sesgo horario medible."))
    w(f"- **Plazo:** la curva no empeora de 0-12 a 24-48 h en este periodo (ver sección 3); la "
      f"pasada del modelo es la misma y el plazo del producto no es el del modelo.")
    w(f"- **Versión de la curva del techo:** {curves['roof_versions']} versiones entre el "
      f"{curves['roof_first'][:10]} y el {roof_last['fitted_at'][:10]}. La pendiente fue de "
      f"{max(slopes_sep):.2f} a {min(slopes_sep):.2f} en septiembre y se quedó entre "
      f"{max(slopes_oct):.2f} y {min(slopes_oct):.2f} en octubre, cuando ya casi no entraban "
      f"horas nuevas ({curves['roof_slopes'][-1][4]} horas al final, {oct_new_hours} en todo "
      f"octubre). El método nunca pasó a horario: {roof_last['nights']} noches de 60 "
      f"({roof_last['method']}).")
    w("")
    # ------------------------------------------------------------------ 8
    w("## 8. Fuga entre entrenamiento y evaluación")
    w("")
    w("| curva | ajustada | con qué | en este informe |")
    w("|---|---|---|---|")
    w(f"| fotómetro (ECMWF) | {pc['fitted_at'][:16]} | {pc['trained_on']} pares, retroanálisis "
      f"2024-02 → 2026-08, verdad fotómetro | solo horas desde el 31-08: fuera de muestra |")
    if curves["wn"]:
        w(f"| WeatherNext | {curves['wn']['fitted_at'][:16]} | {curves['wn']['trained_on']} pares "
          f"desde 2026-01, incluye 28-08 → 18-09 | lo publicado con ella empieza el 25-09: fuera "
          f"de muestra. La comparación de proveedores usa WeatherNext crudo |")
    w(f"| techo | a diario, {curves['roof_versions']} versiones | meses 2024-02 → mes anterior y "
      f"horas de techo hasta el día del ajuste | lo publicado usó la versión vigente al emitir "
      f"(prequencial): fuera de muestra. No se recalcula nada con la de hoy |")
    w("| curvas ICON / ECMWF+ICON (sección 6) | aquí | retroanálisis hasta el 27-08 | fuera de muestra |")
    w("| nube de los proveedores (secciones 3, 6, 7) | - | - | lo que había en el archivo a la hora "
      "de emitir o a las 16 UTC, reconstruido recogida a recogida (sección 1 bis); la vista "
      "deduplicada habría metido retrospectiva |")
    w(f"| curva horaria del techo (sección 3-4) | aquí, noche a noche | solo noches anteriores | fuera de muestra |")
    w("")
    w("Quedan dos cosas fuera de esta limpieza, y se declaran. Una: el umbral del 50 % y la regla de la "
      "ventana se eligieron el 04-09 mirando los 74 veredictos anteriores; por eso se separa "
      "«desde 05-09». Dos: la curva del techo publicada lleva retrospectiva en su pendiente (M16), "
      "es decir, se ajustó con nube recogida ya de noche, más acertada que la que ve a las 16 UTC: "
      "su pendiente puede estar inflada (efecto NO medido aquí). Y el número de modelos "
      "comparados (siete proveedores, cuatro curvas) "
      "invita a encontrar un ganador por azar: la ventaja de ICON solo aguanta a D0 y se encogió "
      "al quitar la retrospectiva; es UN otoño.")
    w("")
    # ------------------------------------------------------------------ 9
    w("## 9. Las seis preguntas")
    w("")
    w(f"1. **¿Puede la métrica detectar el efecto?** La verdad del fotómetro no ve la nube gruesa "
      f"con Luna llena ni el cielo limpio con Luna baja (κ {k_cw['kappa']:.2f} con el "
      f"CloudWatcher): por eso todo va también contra el consenso. El Brier de una P(techo) contra "
      f"nubes no mide nada (solo AUC). Con {d0['correct_neg'] + d0['false_alarms']} noches "
      f"cerradas, la contingencia del veredicto no puede medir la detección de noches malas.")
    ctl_c = R.get("a_controls", {}).get("consenso", {}).get("models", {})
    ctl_p = R.get("a_controls", {}).get("fotometro", {}).get("models", {})
    w("2. **¿La he visto fallar?** Sí, y una de las veces fue la propia verdad: contra el "
      f"fotómetro la previsión casi no se separa de su versión barajada (AUC "
      f"{ctl_p.get('ECMWF + curva fotometro', {}).get('auc', [float('nan')])[0]:.2f} contra "
      f"{ctl_p.get('la misma, barajada', {}).get('auc', [float('nan')])[0]:.2f}); contra el "
      f"consenso sí (AUC {ctl_c.get('ECMWF + curva fotometro', {}).get('auc', [float('nan')])[0]:.2f} "
      f"contra {ctl_c.get('la misma, barajada', {}).get('auc', [float('nan')])[0]:.2f}). La verdad "
      "perfecta da Brier 0; el fotómetro contra el CloudWatcher sale en rojo; la curva del "
      "fotómetro contra el techo sale optimista en la zona media (el control del 05-09); la "
      f"tabla del techo contra sus lecturas, r = {rc['r']:.2f}." if rc else "")
    w(f"3. **¿Se ejecutó?** Filas clasificadas por régimen: "
      + ", ".join(f"{k} {v}" for k, v in R["regimes"].items())
      + f" (ninguna sin régimen si no aparece «desconocido»); la reconstrucción del archivo "
        f"devuelve la nube que usó el producto en el {ac.get('exact', 0):.1%} de "
        f"{ac.get('n', 0)} horas; cada tabla dice sus horas y noches; las cuatro figuras se "
        f"generaron y se miraron.")
    w("4. **¿De dónde sale cada constante?** Medidas aquí: el umbral de la allsky (valle de la "
      "distribución) y su corte de las 03 UTC. Por definición: «abrió» = 2 h (la ventana del "
      "veredicto). Supuestas y NO medidas: la hora de decisión (16 UTC), la latencia de 7 h de "
      "una pasada para WeatherNext, el ridge de 1,0 y el mínimo de 8 noches de los ajustes prequenciales, y "
      "la regla del consenso.")
    w(f"5. **¿El mecanismo explica la escala?** El tope de la curva del techo "
      f"({cap_lo:.2f}-{cap_hi:.2f}) explica que la p media sea {d0['mean_p']:.2f} con un "
      f"{d0['base_rate']:.0%} de noches abiertas. NO explica entero el déficit de horas "
      f"({bh.get('bias', float('nan')):+.1f} h): el tope por ~13 h de noche daría ~9 h; el resto "
      f"son las horas de crepúsculo de la rejilla y el factor 0,85 de las puertas en vigilancia "
      f"(plausible, no desglosado). Los cirros explican 2 de las {len(fails_d0)} noches falladas del "
      f"D0, no todas.")
    w("6. **¿Estoy suavizando la señal?** La verdad horaria agrega 10-min a hora y el "
      "CloudWatcher por mayoría: un claro de media hora se pierde. El consenso esconde los "
      "desacuerdos, por eso cada sensor tiene su fila.")
    w("")
    # ------------------------------------------------------------------ 10
    w("## 10. Mejoras propuestas")
    w("")
    w("Implementadas: M1 y M6 (26-10-2026); M7 y M8 (27-10-2026). El resto, no. «Validable ya» = medido aquí fuera de muestra; "
      "«necesita noches» = la evidencia es descriptiva o de pocas noches.")
    w("")
    if wif:
        wm = wif["models"]["curva horaria prequencial"]
        w(f"**M1. Curva del techo: ajuste horario de los dos parámetros, ya, y solo con horas "
          f"oscuras.** `roofcalibration.py:71` (`NIGHTS_FOR_HOURLY_REFIT = 60`), `:298-306` "
          f"(el cruce sale de `crossing_from_months`) y `:208` (las horas 18-05 UTC meten el "
          f"crepúsculo, cuando el techo está cerrado por horario y no por tiempo). Evidencia: tope "
          f"de {cap_lo:.2f}-{cap_hi:.2f} con 0 % de nube; hora a hora, {top_txt}; "
          f"horas esperadas {bh.get('bias', float('nan')):+.1f} h; el veredicto con la curva "
          f"horaria prequencial baja el Brier {_ci(wm['d_brier'])}. Ganancia esperada: "
          f"probabilidad de abrir calibrada (Brier ~{wm['brier'][0]:.2f} en vez de "
          f"~{wif['models']['publicado']['brier'][0]:.2f}) y horas esperadas sin el déficit. "
          f"**Validable ya** (hecho aquí); vigilar el AUC, que bajó "
          f"{wm['d_auc'][0]:+.2f} por las primeras curvas planas."
          + " " + M1_DONE)
    if mm0:
        w(f"**M2. Probar ICON (o ECMWF + ICON con curva conjunta) como nube de esta noche.** "
          f"`calibration.py:227` (`BACKFILL_MODEL`), `:333` (`training_pairs`) y `tonight.py` "
          f"(`wanted = calibrator.trained_on_model`). Evidencia fuera de muestra (curvas del "
          f"retroanálisis 2024-26, nube de las 16 UTC): D0 contra el consenso, ICON AUC "
          f"{_ci(mdl(mm0, 'ICON + curva', 'd_auc'))} y Brier {_ci(mdl(mm0, 'ICON + curva', 'd_brier'))}; "
          f"la curva conjunta, Brier {_ci(mdl(mm0, 'ECMWF + ICON, curva conjunta', 'd_brier'))}; a D-1 "
          f"nada ({_ci(mdl(mm1, 'ICON + curva', 'd_auc'))} de AUC). Ganancia esperada: ordenar mejor "
          f"las horas de esta noche; en probabilidad, poca. **Validable ya** con el retroanálisis "
          f"(dejar-un-mes-fuera sobre 2024-26, como el examen de WeatherNext del 18-09) antes de "
          f"tocar el producto: esto es un otoño, y la ventaja de ICON se encogió a la mitad al "
          f"quitar la retrospectiva de la vista deduplicada.")
    w(f"**M3. Arreglar la verdad de entrenamiento con la Luna, o votarla.** `calibration.py:286` "
      f"(`observed_clear_hours`), `:214` (`CLEAR_LEVEL_TOLERANCE`), `:134` (`MOON_NOISE_FACTOR`). "
      f"Evidencia: {phot_false_clear} de {cw_cloudy} horas nubladas por el CloudWatcher salen "
      f"despejadas (κ {k_cw['kappa']:.2f}); falsos en las dos direcciones ligados a la Luna; la "
      f"curva entrenada con esa verdad no pasa de {photo_range[1]:.2f} cuando el cielo previsto "
      f"limpio lo está el {clr['obs']:.1%} según el consenso. Propuesta: desde el 29-08 etiquetar "
      f"con el voto fotómetro + allsky + CloudWatcher; hacia atrás, guardar la fracción de luz "
      f"lunar junto a la etiqueta (lo que el propio módulo pide). Ganancia: curva más empinada "
      f"y una verdad que discrimina (el ECMWF da AUC {min(auc_cons):.2f}-{max(auc_cons):.2f} contra "
      f"el consenso y {min(auc_phot):.2f}-{max(auc_phot):.2f} contra el fotómetro). "
      f"**Necesita noches** (dos meses "
      f"de consenso)." if clr else "")
    w(f"**M4. Meter las capas (baja/media/alta) en la curva.** `calibration.py:412` (`fit`, una "
      f"sola covariable de nube). Evidencia: cirros {cir['gap_photo'][0]:+.2f}, nube baja "
      f"{low['gap_photo'][0]:+.2f}, media {mid['gap_photo'][0]:+.2f}; la logística prequencial "
      f"con capas pesa la alta {R.get('preq_layers_last', {}).get('capas', [0, 0, 0, 0])[3]:.2f} "
      f"contra {R.get('preq_layers_last', {}).get('capas', [0, 0, 0, 0])[1]:.2f} la baja y "
      f"{R.get('preq_layers_last', {}).get('capas', [0, 0, 0, 0])[2]:.2f} la media (indicativo). "
      f"Pero el retroanálisis NO trae capas (`workers/backfill_history.py:26`): solo el archivo "
      f"vivo desde el 27-08, y la prueba prequencial aún pierde contra la curva fija. **Necesita "
      f"noches** (un invierno con nube baja y media)." if cir and low and mid else "")
    w(f"**M5. Una sola magnitud para las dos noches.** `tonight.py:245-252` (rama de "
      f"WeatherNext) y `:343` (`_weathernext_calibrator`): la noche siguiente sale con la curva "
      f"del fotómetro y esta noche con la del techo. Evidencia: caída media de "
      f"{js['mean_drop_wn_to_roof']:.2f} de la víspera al día en {js['n_wn_to_roof']} noches, "
      f"{js['flips']} veredictos dados la vuelta; las falsas alarmas D-1 del 27-09 y 19-10 son "
      f"WeatherNext con curva de fotómetro. Arreglo: pasar la nube de WeatherNext por la curva "
      f"del techo (o una curva del techo propia). **Validable ya** el síntoma; la curva propia "
      f"necesita noches.")
    w(f"**M6. La ventana del veredicto, dentro de la noche astronómica.** `tonight.py:101` "
      f"(`verdict_window`) y `gates.py:154` (`opening_window`) recorren toda la rejilla, que va de "
      f"ocaso−1 h a orto+1 h (`ephem.py:27`). Evidencia: {wo['esta noche'][1]} + "
      f"{wo['noche siguiente'][1]} ventanas publicadas acaban después del orto; la falsa alarma "
      f"del 28-09 fue una ventana 06-08 UTC. **Validable ya.** " + M6_DONE)
    w("**M7. Que la fila archivada diga qué curva la produjo.** `workers/archive_prediction.py:51`: "
      "la versión es siempre la del fotómetro aunque el número salga de la del techo o de "
      "WeatherNext. Guardar verdad + modelo + `fitted_at` de la curva usada. **Validable ya** "
      "(sin esto, este informe tuvo que reconstruir el régimen). " + M7_DONE)
    w("**M8. Nombre del campo.** `webexport.py:69` publica como `p_despejado` una P(techo "
      "abierto) desde el 05-09. Renombrar o documentar en el JSON. **Validable ya.** " + M8_DONE)
    w(f"**M9. Re-derivar los terciles de seeing.** `seeing.py:62-63` (1,35\" / 1,90\"): "
      f"«favorable» {seeing.get('favorable', 0)} de {sum(seeing.values())} noches; el modelo cambió "
      f"(el jet por hora, 01-09) y los cortes ya no son sus terciles. **Validable ya** contra la "
      f"propia distribución archivada; contra el cielo, sin FWHM no.")
    w(f"**M10. Sesgo del cielo más oscuro.** La magnitud publicada (`webexport.py:305`) es "
      f"{abs(sb.get('mean', float('nan'))):.2f} mag más brillante que la medida sin Luna. El tercil no "
      f"se entera (acierta {sky_ok}/{sky_n}), la cifra sí. **Necesita** mirar si el suelo en uso "
      f"(`sky_floor.json`) y el de la serie (`epoch_floor`) son el mismo número.")
    w("")
    w("Huecos de datos que impiden medir:")
    w("")
    w(f"**M11. Cadencia del colector desde el 30-09.** Octubre: {oct_.get('lecturas_techo', 0)} "
      f"lecturas de techo y {oct_.get('horas_cloudwatcher', 0)} horas de CloudWatcher (septiembre: "
      f"{sep_.get('lecturas_techo', 0)} y {sep_.get('horas_cloudwatcher', 0)}). `cloud.html` solo "
      f"publica el instante: lo no leído no se recupera. Además deja sin datos a la curva del "
      f"techo ({oct_new_hours} horas nuevas en todo octubre: el cambio a horario de M1 no "
      f"llegaría solo nunca). "
      f"Causa NO VERIFICADA (el reloj externo de `colector.yml:75` o los minutos de Actions).")
    w(f"**M12. `fetch_tess.top_up` no rellena huecos.** `workers/fetch_tess.py:231`: pide desde la "
      f"ÚLTIMA medida archivada, que `tess_vivo` adelanta en cada pasada, así que las horas entre "
      f"pasadas no se piden nunca. Octubre: {oct_.get('horas_fotometro', 0)} de "
      f"{oct_.get('horas_oscuras', 0)} horas oscuras. STARS4ALL las sirve: es recuperable con un "
      f"relleno de octubre, y el arreglo es pedir los últimos N días enteros.")
    w("**M13. La allsky se apaga a las 03 UTC** (0 estrellas todas las noches): media noche sin la "
      "verdad más independiente. Revisar la captura en el servidor de AstroCamp (NO VERIFICADO "
      "si es exposición, horario o rocío).")
    w("**M14. WeatherNext de octubre fuera del archivo hasta que cierre el mes** "
      "(`archivepush.py:82` y `:102-107`, por diseño) y sin las pasadas del 29-09. Hasta el "
      "01-11 solo se puede puntuar lo que pasó por nuestra fila archivada (noche siguiente).")
    w(f"**M15. Sin verdad de seeing ni de AOD.** FWHM: {R['c_fwhm'][0]} medidas, la última del "
      f"{R['c_fwhm'][1][:10]}; `k_extinction`: {R['c_kext']} filas; el PM2.5 no es sustituto "
      f"(Spearman {R['c_aod_spearman'][0]:+.2f}). Los terciles de seeing y AOD no se pueden "
      f"verificar." if R.get("c_aod_spearman") else "")
    w(f"**M16. La etiqueta de pasada y la vista deduplicada.** `workers/fetch_forecast.py:101` "
      f"guarda como `run_utc` el primer instante de la serie, no la inicialización, y "
      f"`queries.py:52` se queda con la última recogida. Quien pregunta «la pasada de hoy anterior "
      f"a las 15 UTC» recibe la recogida de las 23 UTC: lo hace `roofcalibration.py:222` (la "
      f"pendiente de la curva del techo se ajusta con nube recogida ya de noche) y "
      f"`scoreboard.py:127` (los plazos de los modelos en vivo están mal rotulados). Medido: la "
      f"vista coincide con lo que de verdad se usó en el {ac.get('dedup_exact', 0):.0%} de las "
      f"horas. Arreglo: elegir por `retrieved_at <= corte` y no por la etiqueta. **Validable ya.**")
    w(f"**M17. El total del ECMWF de 0,25° no casa con sus capas** "
      f"({lc.get('ecmwf_ifs025', {}).get('total_below_max_layer', 0):.0%} de las filas con la total "
      f"por debajo de su capa mayor, contra {lc.get('ecmwf_ifs', {}).get('total_below_max_layer', 0):.0%} "
      f"en el de 9 km). Es la nube de la que cuelgan las dos curvas. Comprobar en Open-Meteo qué "
      f"campo se sirve y si el retroanálisis tiene el mismo defecto; mientras, el total ordena "
      f"mejor que la capa mayor (sección 6). **Validable ya** contra la documentación y el crudo.")
    w(f"**M18. Pocas noches cerradas.** En este otoño solo hubo {d0['correct_neg'] + d0['false_alarms']} "
      f"noches cerradas puntuables desde el 05-09: la capacidad del veredicto de avisar de las "
      f"malas no está medida. Repetir este informe en enero (`python -m astroweather.hindcast` "
      f"con `LAST_NIGHT` movido) antes de cambiar el umbral del 50 %.")
    w("")
    # ------------------------------------------------------------------ 11
    w("## 11. Medido, plausible y no verificado")
    w("")
    w("- **Medido**: todas las tablas y figuras; las causas de las noches falladas (capas del "
      "ECMWF y sensores de esa noche); el fallo de `top_up` (leído en el código y visto en la "
      "cobertura).")
    w(f"- **Plausible, no medido**: que las {len(unknown)} noches sin verdad de techo (29-09 a "
      f"07-10) fueran casi todas cierres; que el CloudWatcher del 01-09 marcara nublado por humedad o calima; el desglose "
      "del déficit de horas esperadas.")
    w("- **NO VERIFICADO**: la causa de la caída de cadencia del colector; la causa del apagado "
      "de la allsky a las 03 UTC; que ICON siga ganando fuera de este otoño.")
    w("")
    w("Reproducir: `python -m astroweather.hindcast` desde el repo del motor (lee "
      "`../AstroWeather-Archive` y `../AstroWeather-Data`, o `ASTROWEATHER_ARCHIVE_REPO` / "
      "`ASTROWEATHER_DATA_REPO`); ~3 min. `--from-json` rehace figuras e informe sin recalcular.")
    w("")
    out = []
    for line in L:
        if line is None:
            continue
        # Cada mejora en su parrafo: en Markdown dos lineas seguidas se funden.
        if line.startswith("**M") and out and out[-1] != "":
            out.append("")
        out.append(line)
    return "\n".join(out)


def write_report(R: dict, path: Path, fig_dir_name: str) -> Path:
    path.write_text(render_report(R, fig_dir_name), encoding="utf-8")
    return path


def _json_default(o):
    if isinstance(o, (dt.datetime, dt.date)):
        return o.isoformat()
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(type(o))


def main() -> int:
    parser = argparse.ArgumentParser(description="Retroanalisis: previsto contra medido")
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument("--data-repo", type=Path, default=DATA_REPO)
    parser.add_argument("--resamples", type=int, default=RESAMPLES)
    parser.add_argument("--out", type=Path, default=ENGINE / "docs")
    parser.add_argument("--from-json", action="store_true",
                        help="rehacer figuras e informe desde resultados.json, sin recalcular")
    parser.add_argument("--m1", action="store_true",
                        help="solo la evaluacion prequencial de M1 (curva del techo): "
                             "m1_resultados.json y m1_curva_techo.png; no toca el informe")
    parser.add_argument("--m6", action="store_true",
                        help="solo la evaluacion de M6 (ventana y horas esperadas dentro de la "
                             "noche astronomica) frente a M1 solo: m6_resultados.json, con el "
                             "control positivo contra m1_resultados.json; no toca el informe")
    args = parser.parse_args()
    fig_dir = args.out / f"verificacion_{REPORT_DATE}"
    fig_dir.mkdir(parents=True, exist_ok=True)
    if args.m6:
        target = fig_dir / "m6_resultados.json"
        if args.from_json:
            M = json.loads(target.read_text(encoding="utf-8"))
        else:
            M = m6_evaluation(args.archive, args.data_repo, args.resamples)
            M["control"] = m6_control(M, fig_dir / "m1_resultados.json")
            target.write_text(json.dumps(M, default=_json_default, indent=1, ensure_ascii=False),
                              encoding="utf-8")
        print("\n".join(m6_report_lines(M)))
        print(f"[hindcast --m6] {target}")
        return 0
    if args.m1:
        target = fig_dir / "m1_resultados.json"
        if args.from_json:
            M = json.loads(target.read_text(encoding="utf-8"))
        else:
            M = m1_evaluation(args.archive, args.data_repo, args.resamples)
            target.write_text(json.dumps(M, default=_json_default, indent=1, ensure_ascii=False),
                              encoding="utf-8")
        print("\n".join(m1_report_lines(M)))
        figure_m1(M, fig_dir / "m1_curva_techo.png")
        print(f"[hindcast --m1] {target} y {fig_dir / 'm1_curva_techo.png'}")
        return 0
    if args.from_json:
        R = json.loads((fig_dir / "resultados.json").read_text(encoding="utf-8"))
        for r in R["nights"]:
            r["night"] = dt.date.fromisoformat(r["night"])
    else:
        R = analyse(args.archive, args.data_repo, args.resamples)
        (fig_dir / "resultados.json").write_text(
            json.dumps(R, default=_json_default, indent=1, ensure_ascii=False), encoding="utf-8")
    figure_reliability(R, fig_dir / "fiabilidad.png")
    figure_calendar(R, fig_dir / "calendario.png")
    figure_providers(R, fig_dir / "proveedores.png")
    figure_bias(R, fig_dir / "sesgos.png")
    doc = write_report(R, args.out / f"verificacion_{REPORT_DATE}.md", fig_dir.name)
    print(f"[hindcast] informe: {doc}")
    print(f"[hindcast] figuras y resultados: {fig_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
