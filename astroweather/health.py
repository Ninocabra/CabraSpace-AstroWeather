"""La salud del producto en un bloque: ¿acertamos?, ¿llegan los sensores?, ¿que
decidio la curva?

POR QUE EXISTE (26-10-2026). El retroanalisis encontro dos fallos que llevaban
semanas sin que nada avisara: desde el 30-09 el colector solo leia el techo y
el CloudWatcher 2-4 veces al dia (octubre: 45 lecturas de techo contra 608 en
septiembre, M11), y la allsky da 0 estrellas desde las 03 UTC todas las noches
(M13). Los dos se ven en una consulta de un segundo; lo que faltaba era que
alguien la hiciera cada dia. Esto la hace en cada pasada del colector y deja el
resultado en `estado_colector.json` (bloque `salud`) y, cuando cambia algo que
importa, en `marcador.json` del repo de datos para que la web lo pinte.

TRES NIVELES, `ok` / `aviso` / `rojo`, y el nivel general es el peor. Cada
umbral dice de donde sale junto a su constante. Formato estable, claves,
unidades y ejemplo: docs/aprendizaje_continuo.md.

BARATO: lee solo las particiones de este mes y del anterior de `measurements`
(sin la vista deduplicada, que recorre los 75 MB), las filas de nota y el
registro de decisiones. Medido en el CHANGELOG del 26-10-2026.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np

from . import nighttruth as NT

UTC = dt.timezone.utc
VERSION = 1
FILENAME = "salud.json"
LEVELS = ("ok", "aviso", "rojo")

# --- la nota movil -------------------------------------------------------------
# 14 noches: dos semanas, lo que tarda en verse una deriva de la curva sin que
# una sola noche rara mande. Con menos de 7 noches puntuables no se juzga.
SCORE_WINDOW = 14
SCORE_MIN_NIGHTS = 7
# "aviso" mientras no bata a la climatologia por al menos un 10 % de Brier. Con
# 14 noches el BSS tiene un IC90 de unos +-0,3 (medido sobre el relleno), asi que
# rojo = peor que la climatologia (lo pedido) y la franja 0-0,10 solo dice "no
# se distingue de no saber nada": vigilar, no actuar.
BSS_WARN = 0.10
# La climatologia de la nota es la fraccion de noches abiertas en las noches
# puntuadas ANTES de la ventana (fuera de muestra); con menos de estas, la de la
# propia ventana, y se dice.
CLIM_MIN_NIGHTS = 14

# --- los sensores ----------------------------------------------------------------
# Lecturas por dia ESPERADAS: la media de septiembre de 2026 (02-09 a 28-09),
# cuando el reloj de cron-job.org servia una pasada cada 30 min, contando solo las
# lecturas con el dato que importa y sin repetidas. Medido: techo 35,7, estacion
# 35,7, CloudWatcher 34,0 (cloudwatcher4), fotometro 630 (586 a 722 segun dura la
# noche: solo mide de noche) y allsky 4.812.
# Edad maxima de la ultima lectura: el hueco mas largo entre lecturas de
# septiembre fue de 2,0 h en los sensores de `cloud.html` (p99 1,5 h); el
# fotometro no mide de dia (hueco de 13,9 h cada dia) y la allsky llega en
# tandas de 5 h cada 4 h (hueco maximo 4,0 h).
SENSORS = {
    "techo": {"sql": "instrument_id = 'astrocamp_roofs' AND quality_flags LIKE 'open=%'",
              "esperadas_dia": 36, "edad_aviso_h": 2.0, "edad_rojo_h": 6.0},
    "cloudwatcher": {"sql": "instrument_id LIKE 'cloudwatcher%' AND clear_flag IS NOT NULL",
                     "esperadas_dia": 34, "edad_aviso_h": 2.0, "edad_rojo_h": 6.0},
    "estacion": {"sql": "instrument_type = 'WeatherStation' AND t2m IS NOT NULL",
                 "esperadas_dia": 36, "edad_aviso_h": 2.0, "edad_rojo_h": 6.0},
    "fotometro": {"sql": "instrument_type = 'TESS' AND sky_mag_raw IS NOT NULL",
                  "esperadas_dia": 600, "edad_aviso_h": 16.0, "edad_rojo_h": 24.0},
    "allsky": {"sql": "instrument_type = 'ALLSKY' AND star_count IS NOT NULL",
               "esperadas_dia": 4800, "edad_aviso_h": 6.0, "edad_rojo_h": 12.0},
}
# Fraccion de lo esperado en el ultimo dia completo: por debajo de 0,75 es que
# falta una de cada cuatro pasadas (aviso); por debajo de 0,25, que el sensor
# practicamente no se esta leyendo (rojo). El techo de octubre: 2-4 de 36.
CADENCE_WARN = 0.75
CADENCE_RED = 0.25
SENSOR_DAYS = 7

# --- la allsky que se apaga ------------------------------------------------------
# M13: 0 estrellas desde las 03 UTC todas las noches, tambien las limpias. Se
# cuenta en las ultimas 7 noches cuantas tienen ese corte: la mediana de las
# horas oscuras anteriores con >= 100 estrellas (el umbral de la verdad: habia
# cielo) y, desde una hora y hasta el final de la noche astronomica, cada hora
# con menos del 5 % de esa mediana. Medido sobre el archivo: del 13 al 19-09, 5
# de 7 noches cortan a las 03 UTC (0, 0, 0, 2 y 1 estrellas contra medianas de
# 100 a 1.300 antes); del 19 al 25-10 solo 2 (la camara llega a las 04 UTC con
# 79-216 estrellas en las demas): el corte de M13 no se ve esta semana. Una nube
# que entra a la misma hora y se queda hasta el alba noche tras noche es
# rarisima; la camara apagada, no. Aviso con 3 o mas de 7. No es rojo: la
# verdad ya ignora esas horas (`nighttruth.ALLSKY_HOURS`).
ALLSKY_NIGHTS = 7
ALLSKY_CUT_WARN = 3
ALLSKY_CUT_FRACTION = 0.05

# --- la decision de la curva -------------------------------------------------------
# El examen es diario: sin decision en 2 dias se ha saltado uno (aviso); en 7,
# el trabajo esta muerto (rojo).
DECISION_WARN_DAYS = 2
DECISION_RED_DAYS = 7


def _worst(*levels: str) -> str:
    return max(levels, key=LEVELS.index) if levels else "ok"


def _r(x, d=3):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), d)


# --- la nota movil -----------------------------------------------------------------

def score_block(scores: dict, today: dt.date, prefix: str = "d0") -> dict:
    """Nota de las ultimas SCORE_WINDOW noches frente a la climatologia."""
    from .skill import NightBootstrap, brier
    last = today - dt.timedelta(days=1)
    first = last - dt.timedelta(days=SCORE_WINDOW - 1)
    window = [r for n, r in scores.items() if first <= n <= last and r.get(f"{prefix}_outcome")]
    before = [r for n, r in scores.items() if n < first and r.get(f"{prefix}_outcome")]
    out = {"desde": first.isoformat(), "hasta": last.isoformat(), "noches": len(window)}
    counts = {k: sum(1 for r in window if r[f"{prefix}_outcome"] == k)
              for k in ("acierto", "falsa_alarma", "sorpresa", "cierre_bien")}
    out.update({"aciertos": counts["acierto"], "falsas_alarmas": counts["falsa_alarma"],
                "sorpresas": counts["sorpresa"], "cierres_bien": counts["cierre_bien"]})
    if not window:
        out.update({"nivel": "aviso", "motivo": "ninguna noche puntuable en la ventana"})
        return out
    o = np.array([1.0 if r["roof_opened"] else 0.0 for r in window])
    p = np.array([float(r[f"{prefix}_p_open"]) for r in window])
    said = np.array([1.0 if r[f"{prefix}_open"] else 0.0 for r in window])
    if len(before) >= CLIM_MIN_NIGHTS:
        clim = float(np.mean([1.0 if r["roof_opened"] else 0.0 for r in before]))
        source = f"noches anteriores a la ventana (n={len(before)})"
    else:
        clim = float(o.mean())
        source = f"la propia ventana (solo {len(before)} noches antes)"
    c = np.full(len(o), clim)
    b, bc = brier(p, o), brier(c, o)
    bss = 1.0 - b / bc if bc > 0 else float("nan")
    boot = NightBootstrap(np.arange(len(o)), 1000, 20261026)
    _, lo, hi = boot.interval(
        lambda i: (1.0 - brier(p[i], o[i]) / brier(c[i], o[i])) if brier(c[i], o[i]) > 0
        else float("nan"), 5, 95)
    accuracy = float(np.mean(said == o))
    accuracy_clim = float(np.mean((1.0 if clim >= 0.5 else 0.0) == o))
    out.update({"acierto": _r(accuracy), "acierto_climatologia": _r(accuracy_clim),
                "brier": _r(b), "brier_climatologia": _r(bc), "bss": _r(bss),
                "bss_ic90": [_r(lo), _r(hi)], "climatologia_p": _r(clim),
                "climatologia_fuente": source,
                "p_media": _r(float(p.mean())), "abrio_frac": _r(float(o.mean()))})
    if len(window) < SCORE_MIN_NIGHTS:
        out["nivel"], out["motivo"] = "aviso", (f"solo {len(window)} noches puntuables "
                                                f"(minimo {SCORE_MIN_NIGHTS})")
    elif b > bc:
        out["nivel"], out["motivo"] = "rojo", (f"Brier {b:.3f} peor que la climatologia "
                                               f"{bc:.3f} en {len(window)} noches")
    elif bss < BSS_WARN or accuracy < accuracy_clim:
        out["nivel"], out["motivo"] = "aviso", (
            f"no bate a la climatologia con claridad (BSS {bss:+.2f}, acierto "
            f"{accuracy:.2f} contra {accuracy_clim:.2f})")
    else:
        out["nivel"], out["motivo"] = "ok", (f"BSS {bss:+.2f} y acierto {accuracy:.2f} "
                                             f"contra {accuracy_clim:.2f}")
    return out


# --- los sensores ------------------------------------------------------------------

def _recent_globs(root: Path, now: dt.datetime) -> list[str]:
    """Las particiones de este mes y del anterior. Un fichero se particiona por su
    PRIMERA fila, asi que un relleno de dias atras puede caer en el mes anterior;
    mas atras no llega ninguno de los trabajos que corren a diario."""
    first = now.replace(day=1)
    previous = (first - dt.timedelta(days=1)).replace(day=1)
    out = []
    for t in (previous, first):
        d = Path(root) / "measurements" / "site=nerpio" / f"year={t:%Y}" / f"month={t:%m}"
        if d.is_dir() and any(d.glob("*.parquet")):
            out.append((d / "*.parquet").as_posix())
    return out


def sensor_block(root: Path, now: dt.datetime) -> dict:
    import duckdb
    globs = _recent_globs(root, now)
    today = now.date()
    days = [today - dt.timedelta(days=i) for i in range(SENSOR_DAYS, 0, -1)]
    out = {}
    by_sensor: dict[str, list] = {name: [] for name in SENSORS}
    if globs:
        con = duckdb.connect()
        con.execute("SET TimeZone = 'UTC'")
        files = "[" + ", ".join(f"'{g}'" for g in globs) + "]"
        label = "CASE " + " ".join(f"WHEN {spec['sql']} THEN '{name}'"
                                   for name, spec in SENSORS.items()) + " END"
        # UNA lectura para los cinco (eran cinco y costaban 2,1 s de los 2,5 del
        # bloque). Por instrumento y sin repetidos -- el raspador archiva la
        # misma lectura varias veces --; de un grupo con dos instrumentos, el que
        # mas lee.
        rows = con.execute(f"""
            SELECT sensor, instrument_id, CAST(t_utc AS DATE), count(DISTINCT t_utc), max(t_utc)
            FROM (SELECT {label} AS sensor, instrument_id, t_utc
                  FROM read_parquet({files}, union_by_name=true)
                  WHERE t_utc >= ? AND t_utc <= ?)
            WHERE sensor IS NOT NULL
            GROUP BY 1, 2, 3
        """, [dt.datetime.combine(days[0], dt.time(), UTC), now]).fetchall()
        for sensor, _, day, n, t in rows:
            by_sensor[sensor].append((day, int(n), NT.aware(t)))
    for name, spec in SENSORS.items():
        counts, last = {}, None
        for day, n, t in by_sensor[name]:
            counts[day] = max(counts.get(day, 0), n)
            last = t if last is None or t > last else last
        yesterday = counts.get(days[-1], 0)
        fraction = yesterday / spec["esperadas_dia"]
        age = (now - last).total_seconds() / 3600.0 if last else None
        cadence = ("rojo" if fraction < CADENCE_RED else
                   "aviso" if fraction < CADENCE_WARN else "ok")
        fresh = ("rojo" if age is None or age > spec["edad_rojo_h"] else
                 "aviso" if age > spec["edad_aviso_h"] else "ok")
        level = _worst(cadence, fresh)
        reasons = []
        if cadence != "ok":
            reasons.append(f"{yesterday} lecturas el {days[-1]:%d-%m} de {spec['esperadas_dia']} "
                           f"esperadas ({fraction:.0%})")
        if fresh != "ok":
            reasons.append("sin lecturas" if age is None else
                           f"ultima lectura hace {age:.1f} h (aviso > {spec['edad_aviso_h']:g} h)")
        out[name] = {"nivel": level, "motivo": "; ".join(reasons) or "al dia",
                     "ultima_utc": last.isoformat(timespec="seconds") if last else None,
                     "edad_h": _r(age, 1), "lecturas_ayer": yesterday,
                     "esperadas_dia": spec["esperadas_dia"], "fraccion": _r(fraction, 2),
                     "ultimos_dias": {d.isoformat(): counts.get(d, 0) for d in days}}
    return out


def allsky_cut_block(root: Path, now: dt.datetime) -> dict:
    """¿Se apaga la allsky de madrugada? (M13)."""
    import duckdb
    globs = _recent_globs(root, now)
    nights = [now.date() - dt.timedelta(days=i) for i in range(ALLSKY_NIGHTS, 0, -1)]
    frames = NT.night_frames(nights)
    by_hour: dict[dt.datetime, float] = {}
    if globs:
        files = "[" + ", ".join(f"'{g}'" for g in globs) + "]"
        con = duckdb.connect()
        con.execute("SET TimeZone = 'UTC'")
        rows = con.execute(f"""
            SELECT date_trunc('hour', t_utc), median(star_count), count(*)
            FROM (SELECT DISTINCT t_utc, star_count FROM read_parquet({files}, union_by_name=true)
                  WHERE instrument_type = 'ALLSKY' AND star_count IS NOT NULL AND t_utc >= ?)
            GROUP BY 1
        """, [frames[nights[0]]["sunset"]]).fetchall()
        by_hour = {NT.hour_key(h): float(m) for h, m, n in rows if n >= 20}
    cuts, looked = [], 0
    for n in nights:
        f = frames[n]
        if not f["dusk"] or not f["dawn"]:
            continue
        hours = [h for h in NT.night_hours(f)
                 if f["dusk"] <= NT.aware(h) + dt.timedelta(minutes=30) <= f["dawn"]]
        seen = [(h, by_hour[h]) for h in hours if h in by_hour]
        if len(seen) < 4:
            continue
        looked += 1
        # La hora desde la que todo cae por debajo del 5 % de lo de antes, hasta
        # el final de lo medido; "lo de antes" se recalcula para cada candidata.
        start = None
        for i in range(len(seen) - 1, 0, -1):
            earlier = float(np.median([s for _, s in seen[:i]]))
            if earlier < NT.ALLSKY_CLEAR_STARS:
                break
            if all(s <= ALLSKY_CUT_FRACTION * earlier for _, s in seen[i:]):
                start = seen[i][0]
            else:
                break
        if start is not None:
            cuts.append(start.hour)
    level = "aviso" if len(cuts) >= ALLSKY_CUT_WARN else "ok"
    hour = max(set(cuts), key=cuts.count) if cuts else None
    return {"nivel": level, "noches_miradas": looked, "noches_con_corte": len(cuts),
            "hora_corte_utc": hour,
            "motivo": (f"sin estrellas (<5 % de lo anterior) desde las {hour:02d} UTC en {len(cuts)} de {looked} noches "
                       f"con cielo visto antes" if cuts else "sin cortes de madrugada")}


# --- la decision de la curva ----------------------------------------------------------

def decision_block(root: Path, now: dt.datetime) -> dict:
    from .curvecontest import last_decision
    d = last_decision(root, "techo")
    if d is None:
        return {"nivel": "aviso", "motivo": "aun no hay ninguna decision registrada"}
    when = NT.aware(d["decided_utc"])
    days = (now - when).total_seconds() / 86400.0
    level = ("rojo" if days > DECISION_RED_DAYS else
             "aviso" if days > DECISION_WARN_DAYS else "ok")
    return {"nivel": level,
            "motivo": d["reason"] if level == "ok" else f"la ultima decision es de hace {days:.1f} dias",
            "fecha_utc": when.isoformat(timespec="seconds"), "decision": d["decision"],
            "razon": d["reason"], "k_noches": d["k_nights"], "margen": _r(d["margin"]),
            "noches_examinadas": d["n_nights"], "brier_vigente": _r(d["champion_brier"]),
            "brier_aspirante": _r(d["challenger_brier"]), "dif_brier": _r(d["d_brier"]),
            "dif_brier_ic90": [_r(d["d_brier_lo"]), _r(d["d_brier_hi"])],
            "auc_vigente": _r(d["champion_auc"]), "auc_aspirante": _r(d["challenger_auc"]),
            "curva_vigente_ajustada": (d["challenger_fitted_at"]
                                       if d["decision"] in ("sustituye", "arranque_en_frio")
                                       else d["champion_fitted_at"])}


# --- todo junto ----------------------------------------------------------------------

def compute(root: Path | None = None, now: dt.datetime | None = None) -> dict:
    """El bloque `salud`. Cada parte se calcula aparte: una que falla sale en
    `aviso` con su error y no tumba a las demas."""
    from .nightscore import latest_scores
    from .queries import DATA_ROOT
    root = Path(root or DATA_ROOT)
    now = (now or dt.datetime.now(UTC)).replace(microsecond=0)
    block: dict = {"version": VERSION, "calculado_utc": now.isoformat(timespec="seconds")}

    def guard(name, fn):
        try:
            return fn()
        except Exception as error:          # noqa: BLE001 -- la salud no tumba la pasada
            return {"nivel": "aviso", "motivo": f"no se pudo calcular: {type(error).__name__}: {error}"}

    def nota():
        scores = latest_scores(root)
        d0, dm1 = score_block(scores, now.date(), "d0"), score_block(scores, now.date(), "dm1")
        return {"nivel": d0["nivel"], "motivo": d0["motivo"], "ventana_noches": SCORE_WINDOW,
                "d0": d0, "dm1": dm1}

    block["nota"] = guard("nota", nota)
    block["sensores"] = guard("sensores", lambda: sensor_block(root, now))
    # El corte de la allsky mira noches cerradas: cambia una vez al dia. Se
    # reutiliza el de la ultima pasada si es de hoy (1,0 s de los 1,6 del bloque,
    # en cada una de las ~48 pasadas diarias).
    previous = _previous(root)
    cut = (previous or {}).get("allsky_corte")
    if (cut and "noches_miradas" in cut
            and str(previous.get("calculado_utc", ""))[:10] == now.date().isoformat()):
        block["allsky_corte"] = cut
    else:
        block["allsky_corte"] = guard("allsky", lambda: allsky_cut_block(root, now))
    block["curva_techo"] = guard("curva", lambda: decision_block(root, now))
    sensors = block["sensores"]
    sensor_levels = ([s["nivel"] for s in sensors.values()] if "nivel" not in sensors
                     else [sensors["nivel"]])
    block["nivel"] = _worst(block["nota"]["nivel"], *sensor_levels,
                            block["allsky_corte"]["nivel"], block["curva_techo"]["nivel"])
    return block


def _previous(root: Path) -> dict | None:
    """El bloque de la pasada anterior: `salud.json` si sigue en el disco, o el
    que viajo dentro de `estado_colector.json` (en el runner, el unico que
    sobrevive de una pasada a otra)."""
    block = read(root)
    if block is not None:
        return block
    try:
        return json.loads((Path(root) / "estado_colector.json").read_text(
            encoding="utf-8")).get("salud")
    except (ValueError, OSError):
        return None


def last_nights(root: Path, today: dt.date, n: int = SCORE_WINDOW) -> list[dict]:
    """Las ultimas noches puntuadas en forma compacta, para la web."""
    from .nightscore import latest_scores
    out = []
    for night, r in latest_scores(root).items():
        if night < today - dt.timedelta(days=n) or night >= today:
            continue
        out.append({
            "noche": night.isoformat(), "techo_abrio": r["roof_opened"],
            "techo_horas": _r(r["roof_hours"], 1), "techo_fuente": r["roof_source"],
            "techo_horas_oscuras": _r(r["roof_dark_hours"], 1),
            "d0": {"p_abrir": _r(r["d0_p_open"]), "abierto": r["d0_open"],
                   "resultado": r["d0_outcome"], "brier": _r(r["d0_brier"]),
                   "error_horas": _r(r["d0_hours_error"], 1)},
            "dm1": {"p_abrir": _r(r["dm1_p_open"]), "abierto": r["dm1_open"],
                    "resultado": r["dm1_outcome"], "brier": _r(r["dm1_brier"])},
            "despejado": {"fotometro": _r(r["phot_clear_frac"], 2),
                          "cloudwatcher": _r(r["cw_clear_frac"], 2),
                          "allsky": _r(r["sky_clear_frac"], 2),
                          "consenso": _r(r["consensus_clear_frac"], 2)},
            "voto": r["vote"]})
    return out


def write(block: dict, root: Path | None = None) -> Path:
    from .queries import DATA_ROOT
    path = Path(root or DATA_ROOT) / FILENAME
    path.write_text(json.dumps(block, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def read(root: Path | None = None) -> dict | None:
    from .queries import DATA_ROOT
    path = Path(root or DATA_ROOT) / FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def marcador(block: dict, root: Path | None = None, now: dt.datetime | None = None) -> dict:
    """Lo que se publica en `marcador.json`: la salud y las ultimas noches."""
    from .queries import DATA_ROOT
    now = now or dt.datetime.fromisoformat(block["calculado_utc"])
    return {"generado_utc": block["calculado_utc"], "formato": "docs/aprendizaje_continuo.md",
            "salud": block, "ultimas_noches": last_nights(Path(root or DATA_ROOT), now.date())}


# Lo que cambia en cada pasada sin que cambie nada que importe: la hora y la edad
# de las lecturas. Comparar con esto dentro haria del historial un latido.
_VOLATILE = {"generado_utc", "calculado_utc", "edad_h", "ultima_utc"}


def significant(payload) -> str:
    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k not in _VOLATILE}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    return json.dumps(strip(payload), sort_keys=True, ensure_ascii=False)
