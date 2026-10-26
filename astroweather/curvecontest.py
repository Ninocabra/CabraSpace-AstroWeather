"""Una curva nueva solo entra si GANA: aspirante contra campeona, cada dia.

POR QUE (26-10-2026). Hasta hoy `_job_techo_curva` reentrenaba la curva del
techo cada manana y la guardaba a ciegas. Nadie comprobaba que la nueva fuera
mejor que la que sustituia, y el retroanalisis midio lo que eso cuesta: la
curva publicada estuvo seis semanas con un tope del 72 % con 0 % de nube y,
hora a hora, cuando decia 0,64-0,73 el techo estaba abierto el 96-98 % de las
horas. Una curva que se reajusta sola sin examen puede empeorar sola.

EL EXAMEN. La aspirante se ajusta con el codigo de produccion
(`roofcalibration.fit`) SOLO con las noches anteriores a las ultimas K noches
puntuables, y las dos curvas se miden sobre esas K noches con la vara del
retroanalisis: el Brier del VEREDICTO por noche (P(abrir) con la regla de
produccion -- la hora mas floja de la mejor ventana de dos horas, M6, y las
mismas puertas de seguridad que se publicaron --, contra "el techo abrio
>= 2 h"). Tambien se registra el AUC, que con 1-3 noches cerradas en K casi
nunca esta definido y por eso no decide. La aspirante sustituye a la vigente
solo si su Brier es menor por al menos MARGIN. Si no, se queda la vigente. Sin
vigente (arranque en frio), entra la aspirante.

POR QUE LA AJUSTADA HASTA N-K Y NO REAJUSTADA CON TODO. Lo que se examina es
una curva concreta; reajustarla despues con las K noches meteria en produccion
una curva que nadie ha examinado. El precio es que la curva publicada va K
noches por detras de los datos. La ventaja: en regimen, la campeona tambien se
ajusto K noches antes de ser examinada, asi que las dos llegan a las K noches
sin haberlas visto y la comparacion es limpia. (La campeona de hoy, guardada a
ciegas por el trabajo anterior, si las vio: eso la favorece, y es el lado
prudente.)

K Y MARGIN, MEDIDOS (docs/aprendizaje_continuo.md, seccion 2): el protocolo
simulado dia a dia desde el 20-09 sobre el archivo del 26-10. K_NIGHTS = 14 y
MARGIN = 0.02 de Brier: el porque, con sus cifras, junto a las constantes.

REUTILIZABLE. `compare` no sabe de techos: recibe dos vectores de probabilidad,
la verdad y a que noche pertenece cada elemento, y devuelve la decision. Desde
el 26-10-2026 examina tambien la curva del fotometro (`calibration.py`, verdad
por hora; `cloud_duel`, con su propio K y margen, medidos) y la de WeatherNext,
y `rank_d1` puntua los modelos de nube a 1 dia; todo al mismo registro.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from . import nighttruth as NT
from .site import NERPIO
from .skill import NightBootstrap, auc, brier

UTC = dt.timezone.utc
DATASET = "curve_decisions"
VERSION = "curvecontest/1"

# K: cuantas noches puntuables se reservan para el examen. MARGIN: cuanto Brier
# tiene que ganar la aspirante. Elegidos con el protocolo simulado dia a dia del
# 20-09 al 26-10 sobre el archivo del 26-10-2026
# (`python -m astroweather.curvecontest --simular`; tabla completa en
# docs/aprendizaje_continuo.md, seccion 2):
#   * NOCHES CERRADAS EN EL EXAMEN. El Brier de dos curvas razonables difiere
#     poco en las noches abiertas y mucho en las cerradas, y este otono hubo
#     pocas: con K = 7, 7 de 37 ventanas no tienen NINGUNA noche cerrada (el
#     examen solo mediria quien dice "Abierto" mas alto); con K = 10, 1 de 37;
#     con K = 14, ninguna (minimo 1, mediana 3).
#   * LO QUE CUESTA RESERVAR. La curva publicada va K noches por detras. Medido
#     como el Brier del veredicto que cada protocolo habria publicado la noche
#     siguiente (22 noches comunes, 6 cerradas): K = 7, 10 y 14 dan 0,125-0,137,
#     indistinguibles entre si y de reajustar a ciegas con todo (0,133; todas
#     las diferencias con IC90 de +-0,04 que cruzan el cero); K = 21 da 0,169,
#     +0,036 [-0,018, +0,093] peor que a ciegas: demasiado retraso. Lo
#     publicado de verdad esas noches, 0,173.
#   * MARGIN = el p90 del RUIDO DE REAJUSTE: la diferencia de Brier, sobre las
#     14 noches del examen, entre dos aspirantes ajustadas con un dia de
#     diferencia (el mismo metodo y practicamente la misma curva). Su |dif|
#     tiene mediana 0,001, p90 0,020 y p95 0,027 (37 ventanas). Con MARGIN = 0
#     la curva cambio 18 veces en 37 dias por ese ruido; con 0,02, 3 veces, sin
#     coste medible en el Brier de la noche siguiente (0,137 contra 0,130, IC
#     que cruza el cero). La mejora real que midio el retroanalisis (curva de
#     M1 contra la publicada: -0,040 en 42 noches) pasa el margen.
#   * Se probo tambien examinar con reserva y publicar la aspirante REAJUSTADA
#     con todo: se atasca (con margen >= 0,005, 0-1 cambios en 37 dias, porque
#     la campeona ya vio las noches del examen y gana por estar dentro de
#     muestra). Descartado.
K_NIGHTS = 14
MARGIN = 0.02
# Con menos noches puntuables que esto en la ventana no se decide: se queda la
# vigente. La mitad de K: por debajo, una sola noche pesa mas de un 14 %.
MIN_NIGHTS = 7
BOOT_RESAMPLES = 1000
BOOT_SEED = 20261026


# --- la parte que no sabe de techos ------------------------------------------------

def compare(p_champion, p_challenger, outcome, groups, *, margin: float = MARGIN,
            min_groups: int = MIN_NIGHTS, cold_start: bool = False) -> dict:
    """Decide entre dos curvas con sus probabilidades sobre los MISMOS casos.

    `groups` dice a que noche pertenece cada caso: el intervalo de la diferencia
    se remuestrea por noches enteras (IC90 pareado), igual que el retroanalisis.
    """
    p0 = np.asarray(p_champion, float) if p_champion is not None else None
    p1 = np.asarray(p_challenger, float)
    o = np.asarray(outcome, float)
    g = np.asarray(groups)
    n_groups = len(np.unique(g)) if len(g) else 0
    out = {"n_nights": n_groups, "margin": margin,
           "challenger_brier": brier(p1, o) if len(o) else None,
           "challenger_auc": auc(p1, o) if len(o) else None,
           "champion_brier": None, "champion_auc": None,
           "d_brier": None, "d_brier_lo": None, "d_brier_hi": None}
    if p0 is not None and len(o):
        out["champion_brier"] = brier(p0, o)
        out["champion_auc"] = auc(p0, o)
        boot = NightBootstrap(g, BOOT_RESAMPLES, BOOT_SEED)
        point, lo, hi = boot.interval(lambda i: brier(p1[i], o[i]) - brier(p0[i], o[i]), 5, 95)
        out.update({"d_brier": point, "d_brier_lo": lo, "d_brier_hi": hi})
    d = _digits(margin)
    if cold_start or p0 is None:
        out["decision"], out["reason"] = "arranque_en_frio", "no hay curva vigente: entra la aspirante"
    elif n_groups < min_groups:
        out["decision"] = "se_queda"
        out["reason"] = (f"solo {n_groups} noches puntuables en la ventana (minimo "
                         f"{min_groups}): no hay examen, se queda la vigente")
    elif out["d_brier"] <= -margin:
        out["decision"] = "sustituye"
        out["reason"] = (f"la aspirante gana por {-out['d_brier']:.{d}f} de Brier en "
                         f"{n_groups} noches (margen {margin:.{d}f})")
    else:
        out["decision"] = "se_queda"
        out["reason"] = (f"la aspirante no gana por el margen: diferencia "
                         f"{out['d_brier']:+.{d}f} de Brier en {n_groups} noches "
                         f"(hace falta <= {-margin:.{d}f})")
    return out


def _digits(margin) -> int:
    """Decimales para escribir un Brier al lado de su margen: con el del techo
    (0,02) bastan tres; con el del fotometro (0,0005) tres lo redondean a 0,001."""
    return 3 if margin is None or margin >= 0.01 else 4


def log_decision(record: dict, root: Path | None = None) -> Path | None:
    """Una fila en el registro de solo-anadir `curve_decisions`."""
    from .archive import write_canonical
    row = {k: record.get(k) for k in (
        "site_id", "date", "decided_utc", "curve", "decision", "reason", "k_nights",
        "margin", "n_nights", "nights", "champion_fitted_at", "challenger_fitted_at",
        "champion_brier", "challenger_brier", "d_brier", "d_brier_lo", "d_brier_hi",
        "champion_auc", "challenger_auc", "champion_json", "challenger_json")}
    row["pipeline_version"] = VERSION
    # NaN (AUC sin noches cerradas) no es un dato: se guarda como nulo.
    for k, v in list(row.items()):
        if isinstance(v, float) and not np.isfinite(v):
            row[k] = None
    return write_canonical([row], dataset=DATASET, site_id=row["site_id"], root=root)


def decisions(root: Path | None = None, curve: str | None = None) -> list[dict]:
    from .nightscore import read_rows
    from .queries import DATA_ROOT
    rows = read_rows(Path(root or DATA_ROOT), DATASET)
    rows = [r for r in rows if curve is None or r["curve"] == curve]
    return sorted(rows, key=lambda r: r["decided_utc"])


def last_decision(root: Path | None = None, curve: str = "techo") -> dict | None:
    rows = decisions(root, curve)
    return rows[-1] if rows else None


# --- la curva del techo -----------------------------------------------------------------

def roof_exam_set(scores: dict, pubs: list[dict], last_night: dt.date,
                  k: int = K_NIGHTS) -> list[tuple[dt.date, dict, bool]]:
    """Las ultimas `k` noches puntuables hasta `last_night`: con verdad de techo y
    con la publicacion D0 (16 UTC del dia) y sus horas con nube."""
    out = []
    for night in sorted(scores, reverse=True):
        if night > last_night:
            continue
        opened = scores[night].get("roof_opened")
        chosen = NT.pick_publication(pubs, night, night)
        if opened is None or chosen is None:
            continue
        hours = chosen[1].get("hours") or []
        if not hours or any(h["cloud"] is None for h in hours):
            continue
        out.append((night, chosen[1], bool(opened)))
        if len(out) == k:
            break
    return out[::-1]


def roof_duel(root: Path | None = None, *, now: dt.datetime | None = None,
              k: int = K_NIGHTS, margin: float = MARGIN, data_repo: Path | None = None,
              challenger=None, write: bool = True, pairs=None, months=None) -> dict:
    """El examen diario de la curva del techo. Guarda la ganadora y registra la
    decision. `challenger` permite forzar una aspirante (el control en rojo de
    los tests: una curva estropeada tiene que salir rechazada)."""
    from . import roofcalibration as RC
    from .nightscore import all_publications, latest_scores
    from .queries import DATA_ROOT
    root = Path(root or DATA_ROOT)
    now = (now or dt.datetime.now(UTC)).replace(microsecond=0)
    if data_repo is None:
        from .webpublish import repo_path
        data_repo = repo_path()
    model_path = root / "roof_calibration.json"
    champion = RC.RoofCalibrator.load(model_path)
    scores = latest_scores(root)
    pubs = all_publications(root, data_repo, now - dt.timedelta(days=3 * k + 21))
    last_night = (now - dt.timedelta(days=1)).date()
    exam = roof_exam_set(scores, pubs, last_night, k)

    if challenger is None:
        pairs = pairs if pairs is not None else RC.hourly_pairs(root)
        months = months if months is not None else RC.monthly_pairs(root)
        if exam:
            first = exam[0][0]
            pairs = [p for p in pairs if NT.night_of(p[0]) < first]
            months = [m for m in months if (m.year, m.month) < (first.year, first.month)]
        try:
            challenger = RC.fit(months, pairs)
        except ValueError as error:
            challenger = None
            reason = f"no se pudo ajustar la aspirante: {error}"

    record = {"site_id": NERPIO.site_id, "date": now.date(), "decided_utc": now,
              "curve": "techo", "k_nights": k, "margin": margin,
              "nights": json.dumps([str(n) for n, _, _ in exam]),
              "champion_fitted_at": champion.fitted_at if champion else None,
              "challenger_fitted_at": challenger.fitted_at if challenger else None,
              "champion_json": json.dumps(asdict(champion)) if champion else None,
              "challenger_json": json.dumps(asdict(challenger)) if challenger else None}
    if challenger is None:
        record.update({"decision": "se_queda", "reason": reason, "n_nights": len(exam)})
    else:
        frames = NT.night_frames([n for n, _, _ in exam])
        p0, p1, o, g = [], [], [], []
        for night, entry, opened in exam:
            q1 = NT.night_open_probability(entry["hours"], challenger.probability, frames[night])
            q0 = (NT.night_open_probability(entry["hours"], champion.probability, frames[night])
                  if champion else 0.0)
            if q1 is None or q0 is None:
                continue
            p0.append(q0), p1.append(q1), o.append(1.0 if opened else 0.0)
            g.append(night.toordinal())
        result = compare(p0 if champion else None, p1, o, g, margin=margin,
                         min_groups=min(MIN_NIGHTS, k))
        record.update(result)
        if result["decision"] in ("sustituye", "arranque_en_frio") and write:
            challenger.save(model_path)
    if write:
        log_decision(record, root)
    return record


# --- la curva del fotometro (y la de WeatherNext) ------------------------------------
#
# La MISMA regla con otra curva: P(despejado | nube prevista) de `calibration.py`,
# la que usan las noches siguientes a la de hoy (si no hay curva del techo, tambien
# esta noche) y la rama de WeatherNext. Es HORARIA: el examen son todas las horas
# (por alcance) de las ultimas K noches con verdad del fotometro y prevision, y el
# IC se remuestrea por noches enteras igual que el del techo. La aspirante se
# ajusta con el codigo de produccion (`calibration.fit`) sobre los pares ANTERIORES
# a esas noches y solo de la escala nativa (`calibration.NATIVE_TCC_SINCE`).
#
# K Y MARGIN DE ESTA CURVA, MEDIDOS (`python -m astroweather.curvecontest
# --simular-nube --archive <copia>`; tabla en docs/aprendizaje_continuo.md,
# seccion 4): el protocolo dia a dia del 01-03 al 26-10-2026 (240 dias) sobre el
# archivo del 26-10, como el del techo. K cuenta noches CON verdad del fotometro.
#   * HORAS NUBLADAS EN EL EXAMEN (pares hora x alcance con el fotometro diciendo
#     nublado). Con K = 7 y K = 14 hay ventanas con CERO (mediana 25 y 50): el
#     examen solo mediria quien dice "despejado" mas alto. Con K = 30, minimo 15 y
#     mediana 156. Es el mismo criterio que fijo K = 14 en el techo.
#   * LO QUE CUESTA RESERVAR. Brier de la noche siguiente (206 noches, 7.212 pares,
#     IC90 pareado por noches) contra reajustar a ciegas con todo hasta ayer: con
#     K <= 30, de +0,0003 a +0,0015 -- la curva se ajusta con ~10.000 pares y 30
#     noches mas o menos apenas la mueven. K = 60, +0,009 [+0,004, +0,016]: la
#     serie empieza el 21-11-2025 y reservar dos meses la deja sin datos en
#     primavera.
#   * MARGIN = el p90 del RUIDO DE REAJUSTE con K = 30: |dif de Brier| sobre el
#     examen entre dos aspirantes ajustadas con un dia de diferencia. p50 0,00006,
#     p90 0,0005, p95 0,0009. Cuarenta veces menor que el del techo (0,020)
#     porque esta curva se ajusta con ~50 veces mas datos: con el margen del techo
#     no entraria nada, ni el cambio de escala de M17 (-0,002 fuera de muestra en
#     sep-oct). Con margen 0 la curva cambia 66 veces en 240 dias; con 0,0005, 7;
#     con 0,001, 5; el Brier de la noche siguiente no los distingue (+0,0005,
#     +0,0010, +0,0012 contra a ciegas, IC90 de +-0,0007).
CLOUD_K_NIGHTS = 30
CLOUD_MARGIN = 0.0005
CLOUD_MIN_NIGHTS = CLOUD_K_NIGHTS // 2
# Un examen cuya ultima noche es mas vieja que esto no examina lo de ahora: el
# fotometro caido, o WeatherNext, cuyo mes en curso no llega al archivo hasta
# que cierra (M14). Se dice y no se hace nada.
CLOUD_STALE_DAYS = 7
CLOUD_CURVES = {"ecmwf_ifs025": "fotometro", "weathernext3": "fotometro_weathernext3"}
# Los modelos que se puntuan para la noche siguiente (`rank_d1`).
D1_MODELS = ("ecmwf_ifs025", "icon_seamless", "weathernext3")
D1_LEAD_DAYS = 1.0

# La verdad del fotometro cuesta 14-16 s (la ley de cielo con astropy, hora a
# hora desde el 21-11-2025) y la usan los dos examenes y el ranking: una vez por
# proceso y dia.
_TRUTH_CACHE: dict = {}


def photometer_truth(root: Path, today: dt.date | None = None) -> dict:
    from .calibration import NATIVE_TCC_SINCE, observed_clear_hours
    key = (str(root), today or dt.date.today())
    if key not in _TRUTH_CACHE:
        _TRUTH_CACHE.clear()
        _TRUTH_CACHE[key] = observed_clear_hours(NATIVE_TCC_SINCE.isoformat(), root)
    return _TRUTH_CACHE[key]


def cloud_exam_split(rows, last_night: dt.date, k: int = CLOUD_K_NIGHTS):
    """(entrenamiento, examen, noches del examen) de filas (hora, nube, alcance,
    despejada): el examen son las ultimas `k` noches con filas hasta `last_night`
    y el entrenamiento todo lo de antes de la primera."""
    nights = sorted({NT.night_of(r[0]) for r in rows if NT.night_of(r[0]) <= last_night})
    exam_nights = nights[-k:]
    if not exam_nights:
        return [], [], []
    first, chosen = exam_nights[0], set(exam_nights)
    train = [r for r in rows if NT.night_of(r[0]) < first]
    exam = [r for r in rows if NT.night_of(r[0]) in chosen]
    return train, exam, exam_nights


def _cloud_scores(curve, rows):
    return [curve.probability(c, lead * 24.0) for _, c, lead, _ in rows]


def cloud_duel(root: Path | None = None, *, model: str = "ecmwf_ifs025",
               now: dt.datetime | None = None, k: int = CLOUD_K_NIGHTS,
               margin: float = CLOUD_MARGIN, challenger=None, write: bool = True,
               rows=None, truth=None) -> dict:
    """El examen de la curva P(despejado | nube) de `model`. Guarda la ganadora y
    registra la decision. `challenger` fuerza una aspirante (los controles en
    rojo); `rows` y `truth` permiten no recalcular (y los tests sin archivo)."""
    from . import calibration as C
    from .queries import DATA_ROOT
    root = Path(root or DATA_ROOT)
    now = (now or dt.datetime.now(UTC)).replace(microsecond=0)
    path = C.model_path(model, root)
    champion = C.Calibrator.load(path)
    if champion is not None and champion.trained_on_model != model:
        champion = None                   # una curva de otro modelo no defiende nada
    if rows is None:
        truth = truth if truth is not None else photometer_truth(root, now.date())
        rows = C.model_rows(truth, root, model)
    last_night = (now - dt.timedelta(days=1)).date()
    train, exam, nights = cloud_exam_split(rows, last_night, k)
    record = {"site_id": NERPIO.site_id, "date": now.date(), "decided_utc": now,
              "curve": CLOUD_CURVES.get(model, f"fotometro_{model}"), "k_nights": k,
              "margin": margin, "nights": json.dumps([str(n) for n in nights]),
              "n_nights": len(nights),
              "champion_fitted_at": champion.fitted_at if champion else None,
              "champion_json": json.dumps(asdict(champion)) if champion else None}

    newest = max((NT.night_of(r[0]) for r in rows), default=None)
    if not nights or nights[-1] < last_night - dt.timedelta(days=CLOUD_STALE_DAYS):
        why = (" (M14: el mes en curso de WeatherNext no llega al archivo hasta que "
               "cierra)" if model == C.WEATHERNEXT_MODEL else "")
        record.update({"decision": "sin_examen",
                       "reason": f"sin datos recientes: la ultima noche con prevision de "
                                 f"{model} y verdad del fotometro es {newest}{why}; "
                                 f"no se examina ni se toca la curva"})
        return record                     # nada que registrar: no se ha decidido nada

    if challenger is None:
        try:
            challenger = C.fit([r[1:] for r in train], model=model)
        except ValueError as error:
            record.update({"decision": "se_queda",
                           "reason": f"no se pudo ajustar la aspirante: {error}"})
            if write:
                log_decision(record, root)
            return record
    record.update({"challenger_fitted_at": challenger.fitted_at,
                   "challenger_json": json.dumps(asdict(challenger))})
    o = [1.0 if r[3] else 0.0 for r in exam]
    g = [NT.night_of(r[0]).toordinal() for r in exam]
    result = compare(_cloud_scores(champion, exam) if champion else None,
                     _cloud_scores(challenger, exam), o, g, margin=margin,
                     min_groups=min(CLOUD_MIN_NIGHTS, k))
    record.update(result)
    if result["decision"] in ("sustituye", "arranque_en_frio") and write:
        challenger.save(path)
    if write:
        log_decision(record, root)
    return record


def rank_d1(root: Path | None = None, *, now: dt.datetime | None = None,
            truth=None, rows: dict | None = None, write: bool = True) -> dict:
    """Que modelo de nube acierta mas LA NOCHE SIGUIENTE (alcance 1 dia), con el
    examen de WeatherNext del 18-09: deja-un-mes-fuera, cada modelo con SU curva
    (`calibration.fit` sobre sus pares de los demas meses, todos los alcances,
    escala nativa) y puntuados en las MISMAS horas, las que tienen los tres
    modelos a 1 dia y verdad del fotometro. Diferencias pareadas por noches
    contra el ECMWF, que es el que usa el producto.

    Solo MIDE: no cambia el modelo del producto. Se registra en
    `curve_decisions` con `curve='modelo_d1'` y `decision` = el ganador; la
    tabla de los tres va en `challenger_json`."""
    from . import calibration as C
    from .queries import DATA_ROOT
    root = Path(root or DATA_ROOT)
    now = (now or dt.datetime.now(UTC)).replace(microsecond=0)
    if rows is None:
        truth = truth if truth is not None else photometer_truth(root, now.date())
        rows = {m: C.model_rows(truth, root, m) for m in D1_MODELS}
    lead1 = {m: {r[0]: r for r in rows[m] if r[2] == D1_LEAD_DAYS} for m in D1_MODELS}
    common = sorted(set.intersection(*[set(v) for v in lead1.values()]))
    month = lambda h: (h.year, h.month)           # noqa: E731
    months = sorted({month(h) for h in common})
    record = {"site_id": NERPIO.site_id, "date": now.date(), "decided_utc": now,
              "curve": "modelo_d1", "nights": json.dumps([f"{y}-{m:02d}" for y, m in months])}
    if len(months) < 2:
        record.update({"decision": "sin_examen", "n_nights": 0,
                       "reason": f"{len(months)} meses con los tres modelos a 1 dia: no hay "
                                 f"deja-un-mes-fuera"})
        return record
    p = {m: [] for m in D1_MODELS}
    o, g = [], []
    for ym in months:
        hours = [h for h in common if month(h) == ym]
        for m in D1_MODELS:
            curve = C.fit([r[1:] for r in rows[m] if month(r[0]) != ym], model=m)
            p[m].extend(_cloud_scores(curve, [lead1[m][h] for h in hours]))
        o.extend(1.0 if lead1[D1_MODELS[0]][h][3] else 0.0 for h in hours)
        g.extend(NT.night_of(h).toordinal() for h in hours)
    p = {m: np.asarray(v) for m, v in p.items()}
    o, g = np.asarray(o), np.asarray(g)
    boot = NightBootstrap(g, BOOT_RESAMPLES, BOOT_SEED)
    ref = D1_MODELS[0]
    table = {}
    for m in D1_MODELS:
        d = boot.interval(lambda i, m=m: brier(p[m][i], o[i]) - brier(p[ref][i], o[i]), 5, 95)
        table[m] = {"brier": brier(p[m], o), "auc": auc(p[m], o), "p_media": float(p[m].mean()),
                    "d_brier_vs_ecmwf": d[0], "d_brier_ic90": [d[1], d[2]]}
    best = min(D1_MODELS, key=lambda m: table[m]["brier"])
    rival = min((m for m in D1_MODELS if m != ref), key=lambda m: table[m]["brier"])
    lo, hi = table[rival]["d_brier_ic90"]
    clear = "fuera del ruido" if (hi < 0 or lo > 0) else "dentro del ruido (el IC90 cruza el cero)"
    record.update({
        "decision": best, "n_nights": boot.n_nights,
        "reason": (f"a 1 dia gana {best} en Brier sobre {len(o)} horas de {boot.n_nights} noches "
                   f"({months[0][0]}-{months[0][1]:02d} a {months[-1][0]}-{months[-1][1]:02d}); "
                   f"el mejor rival del ECMWF, {rival}, {table[rival]['d_brier_vs_ecmwf']:+.4f} "
                   f"[{lo:+.4f}, {hi:+.4f}], {clear}. Solo medida: el producto no cambia"),
        "champion_brier": table[ref]["brier"], "champion_auc": table[ref]["auc"],
        "challenger_brier": table[rival]["brier"], "challenger_auc": table[rival]["auc"],
        "d_brier": table[rival]["d_brier_vs_ecmwf"], "d_brier_lo": lo, "d_brier_hi": hi,
        "champion_fitted_at": ref, "challenger_fitted_at": rival,
        "challenger_json": json.dumps({"horas": len(o), "tasa_base": float(o.mean()),
                                       "modelos": table}, default=float)})
    if write:
        log_decision(record, root)
    return record


def simulate_cloud(archive: Path, *, ks=(7, 14, 30, 60),
                   margins=(0.0, 0.0005, 0.001, 0.002, 0.005, 0.02),
                   first_day: dt.date = dt.date(2026, 3, 1),
                   last_day: dt.date = dt.date(2026, 10, 26), truth=None,
                   every: int = 1) -> dict:
    """El protocolo de la curva del fotometro dia a dia sobre el archivo, como
    `simulate` para el techo: ruido de reajuste, horas nubladas del examen,
    cambios de curva y el Brier de LA NOCHE SIGUIENTE (que ninguna curva ha visto)
    contra reajustar a ciegas con todo hasta ayer. `every` = examinar solo uno de
    cada `every` dias (7: el trabajo semanal); la noche siguiente se puntua
    todos los dias. No escribe nada."""
    from . import calibration as C
    truth = truth if truth is not None else photometer_truth(archive)
    rows = C.model_rows(truth, archive, C.BACKFILL_MODEL)
    by_night: dict[dt.date, list] = {}
    for r in rows:
        by_night.setdefault(NT.night_of(r[0]), []).append(r)
    nights_all = sorted(by_night)
    fits: dict = {}

    def fit_before(night: dt.date):
        if night not in fits:
            try:
                fits[night] = C.fit([r[1:] for n in nights_all if n < night for r in by_night[n]])
            except ValueError:             # el principio de la serie: sin pares
                fits[night] = None
        return fits[night]

    days = [first_day + dt.timedelta(days=i) for i in range((last_day - first_day).days + 1)]
    errors: dict[str, dict[dt.date, np.ndarray]] = {"ciega (todo hasta ayer)": {}}
    out: dict = {"null": {}, "cloudy_in_exam": {}, "swaps": {}}
    for k in ks:
        champions = {m: None for m in margins}
        swaps = {m: 0 for m in margins}
        for m in margins:
            errors[f"K={k} margen={m:g}"] = {}
        nulls, cloudy = [], []
        for day in days:
            _, exam, nights = cloud_exam_split(rows, day - dt.timedelta(days=1), k)
            if len(nights) < k:
                continue
            challenger = fit_before(nights[0])
            twin = fit_before(nights[0] - dt.timedelta(days=1))
            if challenger is None:
                continue
            cloudy.append(sum(1 for r in exam if not r[3]))
            o = np.array([1.0 if r[3] else 0.0 for r in exam])
            g = [NT.night_of(r[0]).toordinal() for r in exam]
            p1 = np.array(_cloud_scores(challenger, exam))
            if twin is not None:
                nulls.append(brier(p1, o) - brier(np.array(_cloud_scores(twin, exam)), o))
            target = by_night.get(day)
            if target:
                ot = np.array([1.0 if r[3] else 0.0 for r in target])
                if day not in errors["ciega (todo hasta ayer)"]:
                    blind = fit_before(day)
                    errors["ciega (todo hasta ayer)"][day] = (
                        np.array(_cloud_scores(blind, target)) - ot) ** 2
            exam_day = (day - first_day).days % every == 0
            for m in margins:
                champ = champions[m]
                if champ is not None and not exam_day:
                    result = {"decision": "sin_examen"}
                else:
                    p0 = np.array(_cloud_scores(champ, exam)) if champ else None
                    result = compare(p0, p1, o, g, margin=m,
                                     min_groups=min(CLOUD_MIN_NIGHTS, k))
                if result["decision"] in ("sustituye", "arranque_en_frio"):
                    swaps[m] += champ is not None
                    champions[m] = challenger
                if target:
                    errors[f"K={k} margen={m:g}"][day] = (
                        np.array(_cloud_scores(champions[m], target)) - ot) ** 2
        a = np.abs(nulls)
        out["null"][k] = ({"n": len(a), "abs_p50": float(np.percentile(a, 50)),
                           "abs_p90": float(np.percentile(a, 90)),
                           "abs_p95": float(np.percentile(a, 95)), "abs_max": float(a.max())}
                          if len(a) else None)
        out["cloudy_in_exam"][k] = ({"min": int(min(cloudy)), "median": float(np.median(cloudy)),
                                     "windows": len(cloudy)} if cloudy else None)
        for m in margins:
            out["swaps"][f"K={k} margen={m:g}"] = swaps[m]
    common = sorted(set.intersection(*[set(e) for e in errors.values() if e]))
    flat = lambda e: np.concatenate([e[d] for d in common])   # noqa: E731
    g = np.concatenate([[i] * len(errors["ciega (todo hasta ayer)"][d])
                        for i, d in enumerate(common)])
    ref = flat(errors["ciega (todo hasta ayer)"])
    boot = NightBootstrap(g, BOOT_RESAMPLES, BOOT_SEED)
    out["realized"] = {"nights": len(common), "pairs": int(len(ref)), "by": {}}
    for name, e in errors.items():
        x = flat(e)
        out["realized"]["by"][name] = {
            "brier": float(x.mean()),
            "vs_ciega": boot.interval(lambda i, x=x: float(np.mean(x[i] - ref[i])), 5, 95)}
    return out


# --- de donde salen K y MARGIN ---------------------------------------------------------

def simulate(archive: Path, data_repo: Path, *, ks=(7, 10, 14, 21),
             margins=(0.0, 0.005, 0.01, 0.02), first_day: dt.date = dt.date(2026, 9, 20),
             last_day: dt.date = dt.date(2026, 10, 26), rounds: int = 30) -> dict:
    """El protocolo, dia a dia, sobre el archivo: que curva habria publicado cada
    (K, MARGIN) y como le habria ido LA NOCHE SIGUIENTE, que ninguna curva ha
    visto. Es la medida que importa: el Brier del veredicto que se habria
    publicado. Ademas, el ruido de reajuste: la diferencia de Brier, sobre las K
    noches del examen, entre dos aspirantes ajustadas con un dia de diferencia.

    Solo escritorio (historial git completo); no escribe nada."""
    from . import roofcalibration as RC
    from .nightscore import backfill
    NT.astropy_offline()
    scores = {r["date"]: r for r in backfill(archive, archive=archive, data_repo=data_repo,
                                              write=False)["rows"]}
    pubs = NT.published_history(data_repo)
    pairs = RC.hourly_pairs(archive)
    months = RC.monthly_pairs(archive)
    fits: dict = {}

    def fit_before(night: dt.date):
        if night not in fits:
            try:
                fits[night] = RC.fit([m for m in months if (m.year, m.month) < (night.year, night.month)],
                                     [p for p in pairs if NT.night_of(p[0]) < night], rounds=rounds)
            except ValueError:
                fits[night] = None
        return fits[night]

    frames_cache: dict = {}

    def p_night(curve, night, entry):
        if night not in frames_cache:
            frames_cache[night] = NT.night_frames([night])[night]
        return NT.night_open_probability(entry["hours"], curve.probability, frames_cache[night])

    days = [first_day + dt.timedelta(days=i) for i in range((last_day - first_day).days + 1)]
    # Error de cada noche de "hoy" (la que se publica tras decidir) por protocolo.
    errors: dict[str, dict[dt.date, float]] = {"publicado": {}, "ciega (todo hasta ayer)": {}}
    out: dict = {"null": {}, "closed_in_exam": {}, "swaps": {}}
    for day in days:
        today = scores.get(day)
        if today and today.get("d0_brier") is not None:
            errors["publicado"][day] = float(today["d0_brier"])
    for k in ks:
        nulls, closed = [], []
        variants = [(m, False) for m in margins] + [(m, True) for m in margins]
        champions = {v: None for v in variants}
        swaps = {v: 0 for v in variants}
        for v in variants:
            errors[_label(k, *v)] = {}
        for day in days:
            exam = roof_exam_set(scores, pubs, day - dt.timedelta(days=1), k)
            if not exam:
                continue
            closed.append(sum(1 for _, _, o in exam if not o))
            challenger = fit_before(exam[0][0])
            twin = fit_before(exam[0][0] - dt.timedelta(days=1))
            full = fit_before(day)
            if challenger is None:
                continue
            o = [1.0 if op else 0.0 for _, _, op in exam]
            g = [n.toordinal() for n, _, _ in exam]
            p1 = [p_night(challenger, n, e) for n, e, _ in exam]
            if twin is not None:
                p2 = [p_night(twin, n, e) for n, e, _ in exam]
                nulls.append(brier(p1, o) - brier(p2, o))
            today = scores.get(day)
            chosen = NT.pick_publication(pubs, day, day)
            target = None
            if today and today.get("roof_opened") is not None and chosen:
                target = (chosen[1], 1.0 if today["roof_opened"] else 0.0)
            if target and full is not None:
                errors["ciega (todo hasta ayer)"][day] = (p_night(full, day, target[0])
                                                           - target[1]) ** 2
            for v in variants:
                m, refit = v
                champ = champions[v]
                p0 = [p_night(champ, n, e) for n, e, _ in exam] if champ else None
                result = compare(p0, p1, o, g, margin=m, min_groups=min(MIN_NIGHTS, k))
                if result["decision"] in ("sustituye", "arranque_en_frio"):
                    if champ is not None:
                        swaps[v] += 1
                    # Variante B: lo examinado es el METODO; se publica reajustada
                    # con todas las noches hasta ayer.
                    champions[v] = full if (refit and full is not None) else challenger
                if target:
                    errors[_label(k, *v)][day] = (p_night(champions[v], day, target[0])
                                                  - target[1]) ** 2
        out["null"][k] = {"n": len(nulls), "abs_p50": float(np.percentile(np.abs(nulls), 50)),
                          "abs_p90": float(np.percentile(np.abs(nulls), 90)),
                          "abs_p95": float(np.percentile(np.abs(nulls), 95)),
                          "abs_max": float(np.max(np.abs(nulls)))} if nulls else None
        out["closed_in_exam"][k] = {"min": int(min(closed)), "median": float(np.median(closed)),
                                    "windows_without_closed": int(sum(1 for c in closed if c == 0)),
                                    "windows": len(closed)} if closed else None
        for v in variants:
            out["swaps"][_label(k, *v)] = swaps[v]
    # Las mismas noches para todos, y diferencias pareadas por noche (IC90).
    common = sorted(set.intersection(*[set(e) for e in errors.values() if e]))
    ref = np.array([errors["ciega (todo hasta ayer)"][d] for d in common])
    pub = np.array([errors["publicado"][d] for d in common])
    boot = NightBootstrap(np.arange(len(common)), BOOT_RESAMPLES, BOOT_SEED)
    out["realized"] = {"nights": len(common), "closed": int(sum(
        1 for d in common if not scores[d]["roof_opened"])), "by": {}}
    for name, e in errors.items():
        x = np.array([e[d] for d in common])
        out["realized"]["by"][name] = {
            "brier": float(x.mean()),
            "vs_ciega": boot.interval(lambda i, x=x: float(np.mean(x[i] - ref[i])), 5, 95),
            "vs_publicado": boot.interval(lambda i, x=x: float(np.mean(x[i] - pub[i])), 5, 95)}
    return out


def _label(k: int, margin: float, refit: bool) -> str:
    return f"K={k} margen={margin:g}" + (" reajustada" if refit else "")


def summary(record: dict) -> str:
    digits = _digits(record.get("margin"))

    def f(x, d=digits):
        return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"
    return (f"{record['decision']}: {record['reason']} | Brier vigente "
            f"{f(record.get('champion_brier'))} / aspirante {f(record.get('challenger_brier'))}"
            f" (dif {f(record.get('d_brier'))} [{f(record.get('d_brier_lo'))}, "
            f"{f(record.get('d_brier_hi'))}]); AUC {f(record.get('champion_auc'), 2)} / "
            f"{f(record.get('challenger_auc'), 2)}")


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Aspirante contra campeona de la curva del techo")
    parser.add_argument("--simular", action="store_true",
                        help="el protocolo dia a dia sobre el archivo (de donde salen K y MARGIN)")
    parser.add_argument("--archive", type=Path, default=None)
    parser.add_argument("--data-repo", type=Path, default=None)
    parser.add_argument("--en-seco", action="store_true",
                        help="examina y dice que haria, sin guardar curva ni registro")
    parser.add_argument("--nube", action="store_true",
                        help="el examen de las curvas del fotometro (ECMWF y WeatherNext)")
    parser.add_argument("--modelo-d1", action="store_true",
                        help="puntua ECMWF, ICON y WeatherNext para la noche siguiente")
    parser.add_argument("--simular-nube", action="store_true",
                        help="el protocolo de la curva del fotometro dia a dia "
                             "(de donde salen CLOUD_K_NIGHTS y CLOUD_MARGIN)")
    parser.add_argument("--cada", type=int, default=1,
                        help="con --simular-nube: examinar uno de cada N dias")
    parser.add_argument("--k", type=int, nargs="*", default=None,
                        help="con --simular-nube: los K a probar")
    args = parser.parse_args(argv)
    if args.simular_nube or args.nube or args.modelo_d1:
        from .queries import DATA_ROOT
        NT.astropy_offline()
        root = args.archive or DATA_ROOT
        if args.simular_nube:
            kw = {"ks": tuple(args.k)} if args.k else {}
            print(json.dumps(simulate_cloud(root, every=args.cada, **kw), indent=1,
                             default=str))
        if args.nube:
            for model in CLOUD_CURVES:
                print(model, "->", summary(cloud_duel(root, model=model, write=not args.en_seco)))
        if args.modelo_d1:
            print(rank_d1(root, write=not args.en_seco)["reason"])
        return 0
    if args.simular:
        from .archivepush import repo_path as archive_repo_path
        from .webpublish import repo_path as data_repo_path
        archive = args.archive or archive_repo_path()
        data_repo = args.data_repo or data_repo_path()
        print(json.dumps(simulate(archive, data_repo), indent=1, default=str))
        return 0
    NT.astropy_offline()
    print(summary(roof_duel(write=not args.en_seco)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
