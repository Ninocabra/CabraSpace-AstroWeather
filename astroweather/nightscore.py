"""La nota de cada noche: lo que dijimos a las 16 UTC contra lo que paso.

POR QUE EXISTE (26-10-2026). El retroanalisis (`hindcast.py`) midio que el
veredicto publicado acierta lo mismo que decir siempre "Abierto" y que la
probabilidad estaba hundida (Brier 0,160 contra 0,139 de la climatologia), pero
lo midio UNA vez, a mano, dos meses tarde. Un producto que solo se mide cuando
alguien se acuerda no aprende: la curva del techo estuvo seis semanas con un
tope del 72 % sin que nada lo dijera. Esto es la misma medida, cada manana, una
fila por noche, para que la deriva se vea en dias y no en meses, y para que la
curva nueva tenga contra que medirse (`curvecontest.py`).

QUE GUARDA (esquema `night_scores` en `schema.py`; claves y ejemplo en
docs/aprendizaje_continuo.md):

* Lo publicado a las 16 UTC del dia (D0) y de la vispera (D-1): P(abrir),
  veredicto, ventana, horas esperadas y que curva lo produjo. Sale de las
  CAPTURAS que hace el trabajo `publicar` en cada commit de `noche.json`
  (`record_publication`), porque el runner clona el repo de datos con
  profundidad 1 y a la manana siguiente la version de las 16 UTC ya no esta. Donde
  hay historial git completo (el escritorio), tambien de ahi.
* Lo que paso: horas de techo abierto (tabla de AstroCamp por diferencias, o
  lecturas) y con el Sol < -18 grados, la fraccion despejada de fotometro,
  CloudWatcher y allsky, su consenso horario y el voto de la noche.
* Los aciertos: acierto / falsa alarma / sorpresa / cierre bien dicho, el Brier
  de la noche y el error de horas.

LA TABLA DE ASTROCAMP LLEGA TARDE Y SOLO CUANDO CAMBIA. AstroCamp apunta las
horas de la noche en su tabla mensual una vez al dia: en septiembre, con el
colector cada 30 min, el cambio se vio a las 08:01-08:04 UTC 26 dias de 26
(historial git de `open_hours_nerpio.json`), es decir las 10:00 de su hora. Desde
el cambio de hora del 25-10 eso pueden ser las 09:00 UTC; por eso el trabajo
corre desde las 10 UTC. Y una noche cerrada NO cambia la tabla: "no ha
cambiado a las 10" no distingue "0 horas" de "aun no la han apuntado". Se trata
igual que en el retroanalisis (solo cuentan los cambios, `table_changes`), y por
eso cada manana se rehacen las ultimas RESCORE_NIGHTS noches: si una verdad
llega tarde se anade una fila nueva para esa noche, y la vigente es la mas
reciente. Cada fila guarda la foto de la tabla que vio (`table_json`): en el
runner es lo unico que permite saber manana cuanto se abrio hoy.

BARATO A PROPOSITO. El runner factura por minuto y una pasada normal son 45-47 s.
Sin matplotlib, una sola conexion, solo los ultimos dias de cada fuente y el
historial git acotado por fecha. Medido en el escritorio sobre el archivo del
26-10: ver CHANGELOG (26-10-2026).

Relleno: `python -m astroweather.nightscore --backfill` puntua desde el 28-08-2026
con el historial git completo de los dos repos. Control positivo: el veredicto
D0 desde el 05-09 tiene que dar lo del informe del 26-10 (42 noches, 30
aciertos, 1 falsa alarma, 5 sorpresas, 6 cierres bien dichos).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from . import nighttruth as NT
from .site import NERPIO

UTC = dt.timezone.utc
FIRST_NIGHT = dt.date(2026, 8, 28)
# Cada manana se rehacen las ultimas noches, no solo la de ayer: la tabla de
# AstroCamp no cambia con una noche cerrada, y la verdad de esa noche solo queda
# acotada cuando cambia por la siguiente (el 17-20-10, cuatro noches de un tramo).
RESCORE_NIGHTS = 4
# Cuantos dias de fotos de la tabla y de publicaciones se leen en la pasada
# diaria. Tienen que cubrir el tramo sin cambios mas largo que se quiera repartir
# (29-09 a 08-10: nueve noches) y la vispera de la noche mas vieja que se rehace.
LOOKBACK_DAYS = 14
DATASET = "night_scores"
PUBLICATIONS = "night_publications"
VERSION = "nightscore/1"


# --- leer los almacenes ---------------------------------------------------------

def read_rows(root: Path, dataset: str) -> list[dict]:
    """Todas las filas de un almacen de solo-anadir, o [] si aun no existe."""
    import duckdb
    base = Path(root) / dataset
    if not base.is_dir() or not any(base.rglob("*.parquet")):
        return []
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    cursor = con.execute(
        f"SELECT * FROM read_parquet('{base.as_posix()}/**/*.parquet', union_by_name=true)")
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def latest_scores(root: Path) -> dict[dt.date, dict]:
    """La fila vigente de cada noche: la de `scored_utc` mas reciente."""
    best: dict[dt.date, dict] = {}
    for row in read_rows(root, DATASET):
        night = row["date"]
        if night not in best or row["scored_utc"] > best[night]["scored_utc"]:
            best[night] = row
    return dict(sorted(best.items()))


# --- capturar lo publicado ------------------------------------------------------

def _hours_json(hours: list[dict]) -> str:
    return json.dumps([{"t": h["t"].isoformat(), "c": h["cloud"], "pc": h["p_clear"],
                        "pu": h["p_usable"], "s": h["safe"], "l": h["limits"]}
                       for h in hours], separators=(",", ":"), ensure_ascii=False)


def publication_rows(pub: dict, source: str, site_id: str = NERPIO.site_id,
                     retrieved: dt.datetime | None = None) -> list[dict]:
    """Las filas de `night_publications` de una publicacion ya analizada
    (`nighttruth.parse_publication`)."""
    retrieved = retrieved or dt.datetime.now(UTC).replace(microsecond=0)
    rows = []
    for index, e in enumerate(pub["nights"]):
        window = e.get("window")
        rows.append({
            "site_id": site_id, "date": e["night"], "published_utc": pub["generated"],
            "night_index": index, "p_open": e.get("p_open"), "open": e.get("open"),
            "open_derived": e.get("verdict_derived"),
            "window_from": window[0] if window else None,
            "window_to": window[1] if window else None,
            "expected_h": e.get("expected_hours"), "curve_fitted": pub.get("curve_fitted"),
            "truth_curve": e.get("truth"), "cloud_model": e.get("cloud_model"),
            "hours_json": _hours_json(e.get("hours") or []), "source": source,
            "retrieved_at": retrieved, "pipeline_version": VERSION,
        })
    return rows


def record_publication(payload: dict, root: Path | None = None,
                       source: str = "publicar") -> Path | None:
    """Guarda lo que se acaba de publicar. Lo llama `scheduler._job_publish` SOLO
    cuando el commit de `noche.json` se hizo: lo que no llego al repo de datos
    no lo vio nadie y no se puntua."""
    from .archive import write_canonical
    pub = NT.parse_publication(payload)
    return write_canonical(publication_rows(pub, source), dataset=PUBLICATIONS,
                           site_id=NERPIO.site_id, root=root)


def captured_publications(root: Path, since: dt.datetime | None = None) -> list[dict]:
    """Las capturas, rehechas en la forma de `parse_publication`."""
    by_pub: dict[dt.datetime, dict] = {}
    seen: dict[tuple, dt.datetime] = {}
    for r in read_rows(root, PUBLICATIONS):
        when = NT.aware(r["published_utc"])
        if since is not None and when < since:
            continue
        key = (when, r["date"])
        # Una misma publicacion capturada dos veces (relleno + captura viva, o
        # dos maquinas): manda la recogida mas reciente.
        if key in seen and seen[key] >= r["retrieved_at"]:
            continue
        seen[key] = r["retrieved_at"]
        pub = by_pub.setdefault(when, {"generated": when, "nights": {},
                                       "curve_fitted": r["curve_fitted"],
                                       "source": r["source"]})
        hours = [{"t": NT.hour_key(dt.datetime.fromisoformat(h["t"])), "cloud": h["c"],
                  "p_clear": h["pc"], "p_usable": h["pu"], "safe": h["s"], "limits": h["l"]}
                 for h in json.loads(r["hours_json"] or "[]")]
        pub["nights"][r["date"]] = (r["night_index"], {
            "night": r["date"], "p_open": _f(r["p_open"]), "open": r["open"],
            "verdict_derived": r["open_derived"],
            "window": ((NT.aware(r["window_from"]), NT.aware(r["window_to"]))
                       if r["window_from"] is not None else None),
            "expected_hours": _f(r["expected_h"]), "cloud_model": r["cloud_model"],
            "truth": r["truth_curve"], "hours": hours})
    out = []
    for when in sorted(by_pub):
        pub = by_pub[when]
        pub["nights"] = [e for _, e in sorted(pub["nights"].values(), key=lambda x: x[0])]
        out.append(pub)
    return out


def _f(value):
    """float32 del Parquet a float con los decimales que se publicaron (3)."""
    return None if value is None else round(float(value), 4)


def all_publications(root: Path, data_repo: Path | None,
                     since: dt.datetime | None = None) -> list[dict]:
    """Capturas + historial git del repo de datos (si lo hay), sin repetir: la
    misma `generado_utc` es la misma publicacion."""
    pubs = {p["generated"]: p for p in captured_publications(root, since)}
    if data_repo is not None and (Path(data_repo) / ".git").exists():
        try:
            for p in NT.published_history(Path(data_repo), since):
                pubs.setdefault(p["generated"], p)
        except Exception:          # noqa: BLE001 -- sin git o sin historial: las capturas bastan
            pass
    return [pubs[k] for k in sorted(pubs)]


# --- la tabla de AstroCamp, foto a foto -----------------------------------------

def table_snapshots(root: Path, archive_repo: Path | None, latest_scrape: dt.datetime | None,
                    since: dt.datetime | None = None) -> list[tuple[dt.datetime, dict]]:
    """Fotos (instante, {(anio, mes): horas}) de la tabla, de tres sitios: el
    historial git del archivo (completo en el escritorio, solo la punta en el
    runner), las fotos que guardo cada fila de nota, y el fichero de ahora con la
    hora del ultimo raspado de AstroCamp."""
    snaps = []
    if archive_repo is not None and (Path(archive_repo) / ".git").exists():
        try:
            for when, content in NT.file_history(Path(archive_repo), NT.OPEN_HOURS_FILE, since):
                snaps.append((when, NT.table_months(content)))
        except Exception:          # noqa: BLE001
            pass
    for row in read_rows(root, DATASET):
        if row.get("table_json") and row.get("table_seen_utc"):
            when = NT.aware(row["table_seen_utc"])
            if since is None or when >= since:
                snaps.append((when, _table_from_json(row["table_json"])))
    live = Path(root) / NT.OPEN_HOURS_FILE
    if live.exists() and latest_scrape is not None:
        snaps.append((latest_scrape, NT.table_months(json.loads(live.read_text(encoding="utf-8")))))
    return snaps


def _table_to_json(table: dict) -> str:
    years: dict[str, list] = {}
    for (y, m), v in sorted(table.items()):
        years.setdefault(str(y), [None] * 12)[m - 1] = v
    return json.dumps(years, separators=(",", ":"))


def _table_from_json(text: str) -> dict:
    return NT.table_months({y: {"months": months} for y, months in json.loads(text).items()})


# --- puntuar ----------------------------------------------------------------------

def _outcome(said_open, opened) -> str | None:
    if said_open is None or opened is None:
        return None
    if said_open:
        return "acierto" if opened else "falsa_alarma"
    return "sorpresa" if opened else "cierre_bien"


_PUB_KEYS = ("published_utc", "p_open", "open", "window_from", "window_to", "expected_h",
             "curve_fitted", "truth_curve", "source")
_SCORE_KEYS = ("outcome", "hit", "brier", "hours_error", "hours_error_dark")


def _published_fields(prefix: str, chosen, source_of) -> dict:
    """Lo publicado (D0 o D-1) en columnas planas, sin puntuar todavia."""
    out = {f"{prefix}_{k}": None for k in _PUB_KEYS}
    if chosen is None:
        return out
    pub, e = chosen
    window = e.get("window")
    out.update({
        f"{prefix}_published_utc": pub["generated"], f"{prefix}_p_open": e.get("p_open"),
        f"{prefix}_open": e.get("open"),
        f"{prefix}_window_from": window[0] if window else None,
        f"{prefix}_window_to": window[1] if window else None,
        f"{prefix}_expected_h": e.get("expected_hours"),
        f"{prefix}_curve_fitted": pub.get("curve_fitted"),
        f"{prefix}_truth_curve": e.get("truth"), f"{prefix}_source": source_of(pub),
    })
    return out


def _score_fields(row: dict, prefix: str) -> dict:
    """Los aciertos de lo publicado contra el techo, desde la fila plana. Igual que
    `verdict_block` del retroanalisis: solo se puntua con veredicto, probabilidad
    y techo conocidos."""
    out = {f"{prefix}_{k}": None for k in _SCORE_KEYS}
    opened, said, p = row["roof_opened"], row[f"{prefix}_open"], row[f"{prefix}_p_open"]
    expected = row[f"{prefix}_expected_h"]
    if opened is not None and said is not None and p is not None:
        out[f"{prefix}_outcome"] = _outcome(said, opened)
        out[f"{prefix}_hit"] = bool(said) == bool(opened)
        out[f"{prefix}_brier"] = (float(p) - (1.0 if opened else 0.0)) ** 2
    if expected is not None and row["roof_hours"] is not None:
        out[f"{prefix}_hours_error"] = float(expected) - float(row["roof_hours"])
    if expected is not None and row["roof_dark_hours"] is not None:
        out[f"{prefix}_hours_error_dark"] = float(expected) - float(row["roof_dark_hours"])
    return out


def _vote(row: dict) -> str | None:
    """El voto de la NOCHE: cada sensor dice despejada si lo estuvo la mitad de
    sus horas; mayoria de los que midieron, con al menos dos y sin empate."""
    votes = [row[f"{k}_clear_frac"] >= 0.5 for k in ("phot", "cw", "sky")
             if row[f"{k}_clear_frac"] is not None]
    if len(votes) >= 2 and 2 * sum(votes) != len(votes):
        return "despejada" if 2 * sum(votes) > len(votes) else "nublada"
    return None


def _finish(row: dict) -> dict:
    row["vote"] = _vote(row)
    for prefix in ("d0", "dm1"):
        row.update(_score_fields(row, prefix))
    return row


# Cuanto se fia una verdad del techo, de mas a menos. Una noche ya puntuada no
# se vuelve a escribir con una verdad PEOR: el runner no tiene el historial git
# de la tabla, y sin esto su primera pasada tras el relleno habria rehecho las
# ultimas noches sin tabla y tapado su verdad con un "no se sabe".
_ROOF_RANK = {"tabla limpio": 5, "tabla aprox": 4, "lecturas": 3,
              "cota: <=2 h en todo el tramo": 2, "lectura suelta abierta": 1, None: 0}
_ROOF_KEYS = ("roof_hours", "roof_source", "roof_opened", "roof_table_hours",
              "roof_table_quality", "roof_samples")


def merge(new: dict, stored: dict | None) -> dict:
    """La nota nueva de una noche, sin perder nada de lo que ya sabia la vigente:
    la verdad del techo solo se sustituye por otra igual o mejor, lo publicado no
    desaparece y un sensor no "pierde" horas (el fotometro se rellena tarde,
    nunca se vacia)."""
    if stored is None:
        return _finish(dict(new))
    row = dict(new)
    if _ROOF_RANK.get(row["roof_source"], 0) < _ROOF_RANK.get(stored.get("roof_source"), 0):
        row.update({k: stored.get(k) for k in _ROOF_KEYS})
    if row["roof_dark_hours"] is None and stored.get("roof_dark_hours") is not None:
        row["roof_dark_hours"] = stored["roof_dark_hours"]
    for prefix in ("d0", "dm1"):
        if row[f"{prefix}_published_utc"] is None and stored.get(f"{prefix}_published_utc"):
            row.update({f"{prefix}_{k}": stored.get(f"{prefix}_{k}") for k in _PUB_KEYS})
    for key in ("phot", "cw", "sky", "consensus"):
        if (row[f"{key}_hours"] or 0) < (stored.get(f"{key}_hours") or 0):
            row[f"{key}_hours"] = stored[f"{key}_hours"]
            row[f"{key}_clear_frac"] = stored.get(f"{key}_clear_frac")
    return _finish(row)


def _frac(T: dict, night: dt.date) -> tuple[int, float | None]:
    hours = [h for h in T if NT.night_of(h) == night]
    if not hours:
        return 0, None
    return len(hours), sum(1 for h in hours if T[h]) / len(hours)


def score(nights: list[dt.date], *, root: Path, pubs: list[dict], snapshots,
          now: dt.datetime, origin: str, site_id: str = NERPIO.site_id) -> list[dict]:
    """Las filas de nota de `nights` (todas cerradas). Una conexion y una lectura
    por fuente para todas las noches a la vez."""
    from .calibration import observed_clear_hours
    from .queries import connect
    if not nights:
        return []
    nights = sorted(nights)
    con = connect(root)
    first = dt.datetime(nights[0].year, nights[0].month, nights[0].day, 12, tzinfo=UTC)
    photometer = observed_clear_hours(since=nights[0].isoformat(), root=root)
    cw_raw = NT.cloudwatcher_hours(con, first)
    sky_raw = NT.allsky_hours(con, first)
    changes = NT.table_changes(snapshots)
    # Las noches que tocan los intervalos de la tabla, TODAS: si falta una en el
    # marco, el reparto se la salta y la presente sale "limpia" (ver roof_truth).
    start = min([nights[0]] + [(t - dt.timedelta(days=1)).date() for t, _ in changes[:1]])
    every = [start + dt.timedelta(days=i) for i in range((nights[-1] - start).days + 1)]
    frames = NT.night_frames(every)
    samples = NT.roof_samples(con, dt.datetime(start.year, start.month, start.day, 12, tzinfo=UTC))
    roof = NT.roof_truth(frames, samples, NT.table_deltas(changes))

    wanted = set(nights)
    candidate = ({h for h in photometer if NT.night_of(h) in wanted} | set(cw_raw) | set(sky_raw))
    for n in nights:
        candidate.update(NT.night_hours(frames[n]))
    eph = NT.hour_ephemeris(candidate)
    T = NT.sensor_truths(photometer, cw_raw, sky_raw, eph, lambda h: NT.night_of(h) in wanted)

    def source_of(pub):
        # La captura dice de donde salio (`publicar` o el relleno desde git); lo
        # leido del historial git en esta misma pasada no trae marca.
        return pub.get("source") or "git noche.json"

    # La version vigente de la tabla y el PRIMER instante en que se vio. Se guarda
    # ese y no la hora de esta pasada: si el relleno corre de noche, una foto
    # fechada a las 21 UTC partiria la noche en curso y manana no se podria
    # repartir entera.
    seen_at, table_now = changes[-1] if changes else (None, None)
    rows = []
    for n in nights:
        f, r = frames[n], roof[n]
        dark_open = NT.dark_open_hours(f, samples)
        row = {"site_id": site_id, "date": n, "scored_utc": now, "origin": origin,
               "dark_hours": f["dark_hours"], "moon": f["moon"],
               "roof_hours": r["hours"], "roof_source": r["source"], "roof_opened": r["opened"],
               "roof_dark_hours": dark_open, "roof_samples": r["samples"],
               "roof_table_hours": r["table_hours"], "roof_table_quality": r["table_quality"]}
        for key, name in (("phot", "fotometro"), ("cw", "cloudwatcher"), ("sky", "allsky"),
                          ("consensus", "consenso")):
            row[f"{key}_hours"], row[f"{key}_clear_frac"] = _frac(T[name], n)
        row.update(_published_fields("d0", NT.pick_publication(pubs, n, n), source_of))
        row.update(_published_fields("dm1", NT.pick_publication(pubs, n, n - dt.timedelta(days=1)),
                                     source_of))
        row["table_seen_utc"] = seen_at
        row["table_json"] = _table_to_json(table_now) if table_now else None
        row["pipeline_version"] = VERSION
        rows.append(_finish(row))
    return rows


# Lo que NO cuenta para decidir si una noche ha cambiado y merece fila nueva.
_VOLATILE = {"scored_utc", "origin", "table_seen_utc", "table_json", "pipeline_version"}


def _same(a: dict, b: dict) -> bool:
    for k, v in a.items():
        if k in _VOLATILE:
            continue
        w = b.get(k)
        if isinstance(v, float) or isinstance(w, float):
            if (v is None) != (w is None) or (v is not None and abs(float(v) - float(w)) > 1e-3):
                return False
        elif isinstance(v, dt.datetime) or isinstance(w, dt.datetime):
            if (v is None) != (w is None) or (v is not None and NT.aware(v) != NT.aware(w)):
                return False
        elif v != w:
            return False
    return True


# --- las dos formas de correr -------------------------------------------------------

def run_daily(root: Path | None = None, *, now: dt.datetime | None = None,
              data_repo: Path | None = None, archive_repo: Path | None = None,
              write: bool = True) -> dict:
    """Puntua la noche cerrada de ayer y rehace las RESCORE_NIGHTS - 1 anteriores.
    Solo anade fila a una noche nueva o cuya nota ha cambiado."""
    from .archive import write_canonical
    from .queries import DATA_ROOT
    root = Path(root or DATA_ROOT)
    now = (now or dt.datetime.now(UTC)).replace(microsecond=0)
    last = (now - dt.timedelta(days=1)).date()
    nights = [last - dt.timedelta(days=i) for i in range(RESCORE_NIGHTS)][::-1]
    nights = [n for n in nights if n >= FIRST_NIGHT]
    if data_repo is None:
        from .webpublish import repo_path as data_repo_path
        data_repo = data_repo_path()
    if archive_repo is None:
        from .archivepush import repo_path as archive_repo_path
        archive_repo = archive_repo_path()
    since = now - dt.timedelta(days=LOOKBACK_DAYS)
    pubs = all_publications(root, data_repo, since)
    from .queries import connect
    samples = NT.roof_samples(connect(root), since)
    latest = max((t for t, _, _ in samples if t <= now), default=None)
    snaps = table_snapshots(root, archive_repo, latest, since)
    rows = score(nights, root=root, pubs=pubs, snapshots=snaps, now=now, origin="diario")
    stored = latest_scores(root)
    rows = [merge(r, stored.get(r["date"])) for r in rows]
    new = [r for r in rows if r["date"] not in stored or not _same(r, stored[r["date"]])]
    if write and new:
        write_canonical(new, dataset=DATASET, site_id=NERPIO.site_id, root=root)
    return {"noches": [str(n) for n in nights], "filas_nuevas": [str(r["date"]) for r in new],
            "rows": rows}


def backfill(root: Path, *, archive: Path, data_repo: Path, first: dt.date = FIRST_NIGHT,
             last: dt.date | None = None, now: dt.datetime | None = None,
             write: bool = True) -> dict:
    """Todas las noches desde `first` con el historial git completo de los dos
    repos. Escribe tambien las capturas de cada version de `noche.json`, para que
    la aspirante de la curva tenga desde hoy las horas de las noches pasadas."""
    from .archive import write_canonical
    from .queries import connect
    now = (now or dt.datetime.now(UTC)).replace(microsecond=0)
    last = last or (now - dt.timedelta(days=1)).date()
    pubs = NT.published_history(Path(data_repo))
    samples = NT.roof_samples(connect(archive), dt.datetime(first.year, first.month, first.day,
                                                             12, tzinfo=UTC))
    latest = max((t for t, _, _ in samples if t <= now), default=None)
    snaps = table_snapshots(archive, archive, latest)
    nights = [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]
    rows = score(nights, root=archive, pubs=pubs, snapshots=snaps, now=now, origin="relleno")
    if write:
        captures = [row for p in pubs for row in publication_rows(p, "git noche.json",
                                                                  retrieved=now)]
        write_canonical(captures, dataset=PUBLICATIONS, site_id=NERPIO.site_id, root=root)
        write_canonical(rows, dataset=DATASET, site_id=NERPIO.site_id, root=root)
    return {"rows": rows, "publications": len(pubs)}


def verdict_summary(rows: list[dict], prefix: str = "d0",
                    from_night: dt.date | None = None) -> dict:
    """Contingencia, acierto y Brier de un conjunto de filas: lo que se compara con
    la seccion 4 del informe del 26-10-2026."""
    from .skill import auc, brier
    scored = [r for r in rows if r.get(f"{prefix}_outcome")
              and (from_night is None or r["date"] >= from_night)]
    count = {k: sum(1 for r in scored if r[f"{prefix}_outcome"] == k)
             for k in ("acierto", "falsa_alarma", "sorpresa", "cierre_bien")}
    p = [float(r[f"{prefix}_p_open"]) for r in scored]
    o = [1.0 if r["roof_opened"] else 0.0 for r in scored]
    return {"noches": len(scored), **count,
            "acierto_frac": (count["acierto"] + count["cierre_bien"]) / len(scored)
            if scored else None,
            "brier": brier(p, o) if scored else None,
            "auc": auc(p, o) if scored else None,
            "p_media": sum(p) / len(p) if p else None,
            "abrio_frac": sum(o) / len(o) if o else None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--backfill", action="store_true",
                        help="puntua todas las noches desde el 28-08-2026 con el historial git")
    parser.add_argument("--root", type=Path, default=None,
                        help="donde se escribe (por defecto data/ del motor)")
    parser.add_argument("--archive", type=Path, default=None,
                        help="clon del archivo con historial (por defecto ../AstroWeather-Archive)")
    parser.add_argument("--data-repo", type=Path, default=None,
                        help="clon del repo de datos con historial (por defecto ../AstroWeather-Data)")
    parser.add_argument("--dry-run", action="store_true", help="calcula y no escribe nada")
    args = parser.parse_args(argv)
    NT.astropy_offline()
    if args.backfill:
        from .archivepush import repo_path as archive_repo_path
        from .queries import DATA_ROOT
        from .webpublish import repo_path as data_repo_path
        archive = args.archive or archive_repo_path()
        data_repo = args.data_repo or data_repo_path()
        if archive is None or data_repo is None:
            raise SystemExit("hacen falta los clones del archivo y del repo de datos con historial")
        out = backfill(args.root or DATA_ROOT, archive=archive, data_repo=data_repo,
                       write=not args.dry_run)
        rows = out["rows"]
        print(f"[nightscore] {len(rows)} noches puntuadas, {out['publications']} publicaciones")
        for tag, frm in (("D0 todo", None), ("D0 desde 05-09", dt.date(2026, 9, 5)),
                         ("D-1 desde 05-09", dt.date(2026, 9, 5))):
            s = verdict_summary(rows, "dm1" if tag.startswith("D-1") else "d0", frm)
            print(f"  {tag}: {s['noches']} noches, {s['acierto']} aciertos, "
                  f"{s['falsa_alarma']} FA, {s['sorpresa']} sorpresas, {s['cierre_bien']} "
                  f"cierres; Brier {s['brier']:.3f}, AUC {s['auc']:.2f}, "
                  f"p media {s['p_media']:.2f} / abrio {s['abrio_frac']:.2f}")
        return 0
    out = run_daily(args.root, write=not args.dry_run)
    print(f"[nightscore] noches {out['noches']}; filas nuevas: {out['filas_nuevas'] or 'ninguna'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
