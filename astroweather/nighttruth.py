"""Lo que paso cada noche, y lo que se dijo de ella: las verdades compartidas.

POR QUE EXISTE (26-10-2026). Hasta hoy esto vivia dentro de `hindcast.py`, el
retroanalisis que se corre a mano. Ahora tres piezas necesitan EXACTAMENTE la
misma verdad: el retroanalisis, la nota automatica de cada noche
(`nightscore.py`) y la comparacion aspirante-contra-campeona de la curva del
techo (`curvecontest.py`). Si cada una tuviera su copia, el dia que el
retroanalisis dijera "30 aciertos" y la nota diaria "29" no habria forma de
saber cual esta mal. Se movio sin cambiar el calculo: `hindcast.py` importa de
aqui y sus cifras salen iguales (control: `--resamples 50` antes y despues).

Lo que hay:

* El marco de cada noche (`night_frames`) y la altura del Sol y la Luna por hora
  (`hour_ephemeris`).
* Las tres verdades de nubes por hora y su consenso (`sensor_truths`), con los
  limites de cada una medidos en el retroanalisis (seccion 2 del informe del
  26-10-2026): el fotometro no ve la nube gruesa con Luna llena, el CloudWatcher
  da nublado con cielo calido, la allsky se apaga a las 03 UTC.
* La verdad del techo por noche (`roof_truth`): la tabla diaria de AstroCamp por
  diferencias entre fotos sucesivas, repartida entre las noches que solapa, y las
  lecturas sueltas donde hay cadencia.
* Lo publicado (`parse_publication`): una version de `noche.json`, venga del
  historial git del repo de datos o de la captura que hace el trabajo `publicar`.
* La probabilidad de abrir de una noche con OTRA curva y las MISMAS puertas
  (`gated_usable`, `night_open_probability`).
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo


UTC = dt.timezone.utc

# La decision de subir se toma por la tarde: la publicacion vigente a esta hora.
DECISION_HOUR_UTC = 16
OPEN_HOURS_MIN = 2.0          # "abrio" = al menos la ventana de dos horas del veredicto
# Allsky: horas validas y umbral. El umbral es el valle de la distribucion de
# medianas horarias (5 horas entre 73 y 109 estrellas contra 49-85 por intervalo
# por encima de 239).
#
# LAS HORAS VAN POR EL RELOJ DE ESPANA, NO POR UTC (M13, 27-10-2026). La camara
# de AstroCamp deja de dar estrellas cada madrugada a una hora del reloj local:
# medido en las 45 noches limpias del 02-09 al 25-10, el corte cae entre las
# 04:54 y las 06:13 hora local (mediana 05:26), y el 25-10, con el cambio de
# hora, salto de las 03:44 a las 04:49 UTC: una hora justa en UTC y ninguna en
# el reloj. Hasta hoy la ventana era 19-02 UTC fija, que en verano son las 21-04
# locales pero desde el 25-10 tiraba la hora 03 UTC (04 local) con 332 y 410
# estrellas las dos primeras noches. Cuenta la casilla horaria que TERMINA a las
# 05:00 locales o antes; el principio lo pone la noche cerrada (Sol < -18).
ALLSKY_TZ = ZoneInfo("Europe/Madrid")
ALLSKY_LAST_LOCAL_HOUR = 5
ALLSKY_CLEAR_STARS = 100
OPEN_HOURS_FILE = "open_hours_nerpio.json"


# --- utilidades ----------------------------------------------------------------

def astropy_offline() -> None:
    """Sin red para el IERS astropy se niega a dar la altura del Sol. UT1-UTC
    < 0,9 s mueve las alturas < 0,004 grados: irrelevante para etiquetar horas."""
    from astropy.utils import iers
    iers.conf.auto_download = False
    iers.conf.auto_max_age = None


def hour_key(t: dt.datetime) -> dt.datetime:
    """Hora UTC naive, la clave de `observed_clear_hours`."""
    if t.tzinfo is not None:
        t = t.astimezone(UTC).replace(tzinfo=None)
    return t.replace(minute=0, second=0, microsecond=0)


def night_of(h: dt.datetime) -> dt.date:
    return (h - dt.timedelta(hours=12)).date()


def aware(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          check=True).stdout


def file_history(repo: Path, path: str,
                 since: dt.datetime | None = None) -> list[tuple[dt.datetime, dict]]:
    """Cada version de un JSON en el historial git: (fecha del commit, contenido).

    `since` acota por fecha de commit: la nota diaria solo necesita los ultimos
    dias, y leer las 216 versiones de `noche.json` (660 kB cada una) costaria
    mas que toda la pasada del colector."""
    extra = [f"--since={since.isoformat()}"] if since is not None else []
    log = git(repo, "log", "--reverse", "--format=%H %cI", *extra, "--", path).decode()
    out = []
    for line in log.splitlines():
        if not line.strip():
            continue
        commit, when = line.split()
        try:
            content = json.loads(git(repo, "show", f"{commit}:{path}").decode("utf-8"))
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        out.append((dt.datetime.fromisoformat(when).astimezone(UTC), content))
    return out


# --- los sensores, por hora ----------------------------------------------------

def cloudwatcher_hours(con, since: dt.datetime) -> dict[dt.datetime, tuple[float, int]]:
    rows = con.execute("""
        SELECT date_trunc('hour', t_utc) AS h,
               avg(CASE WHEN clear_flag THEN 1.0 ELSE 0.0 END), count(*)
        FROM measurements
        WHERE starts_with(instrument_id, 'cloudwatcher') AND clear_flag IS NOT NULL
          AND t_utc >= ?
        GROUP BY 1
    """, [since]).fetchall()
    return {hour_key(h): (float(f), int(n)) for h, f, n in rows}


def allsky_hours(con, since: dt.datetime) -> dict[dt.datetime, float]:
    rows = con.execute("""
        SELECT date_trunc('hour', t_utc), median(star_count), count(*)
        FROM measurements
        WHERE instrument_type = 'ALLSKY' AND star_count IS NOT NULL AND t_utc >= ?
        GROUP BY 1
    """, [since]).fetchall()
    return {hour_key(h): float(m) for h, m, n in rows if n >= 20}


def roof_samples(con, since: dt.datetime) -> list[tuple[dt.datetime, int, int]]:
    rows = con.execute(r"""
        SELECT t_utc,
               CAST(regexp_extract(quality_flags, 'open=(\d+)/', 1) AS INT),
               CAST(regexp_extract(quality_flags, 'open=\d+/(\d+)', 1) AS INT)
        FROM measurements
        WHERE instrument_id = 'astrocamp_roofs' AND quality_flags LIKE 'open=%'
          AND t_utc >= ?
        ORDER BY t_utc
    """, [since]).fetchall()
    return [(aware(t), int(a), int(b)) for t, a, b in rows if a is not None]


# --- ephemerides ----------------------------------------------------------------

def hour_ephemeris(hours) -> dict[dt.datetime, tuple[float, float]]:
    """(altura del Sol, altura de la Luna) a la media hora de cada hora."""
    from .ephem import moon_positions, sun_positions
    hours = sorted(set(hours))
    if not hours:
        return {}
    mids = [h.replace(tzinfo=UTC) + dt.timedelta(minutes=30) for h in hours]
    sun = sun_positions(mids)
    moon = moon_positions(mids)
    return {h: (s.altitude, m.altitude) for h, s, m in zip(hours, sun, moon)}


def night_frames(nights) -> dict[dt.date, dict]:
    from .ephem import moon_illumination, night_for
    out = {}
    for n in nights:
        frame = night_for(dt.datetime(n.year, n.month, n.day, 12, tzinfo=UTC))
        midnight = dt.datetime(n.year, n.month, n.day, 23, 30, tzinfo=UTC)
        out[n] = {"sunset": frame.sunset, "sunrise": frame.sunrise,
                  "dusk": frame.dusk_astronomical, "dawn": frame.dawn_astronomical,
                  "dark_hours": frame.dark_hours, "moon": moon_illumination(midnight)}
    return out


def night_hours(frame: dict) -> list[dt.datetime]:
    """Las horas (clave naive) de ocaso a orto de una noche."""
    out = []
    t = hour_key(frame["sunset"])
    while t <= hour_key(frame["sunrise"]):
        out.append(t)
        t += dt.timedelta(hours=1)
    return out


# --- las tres verdades de nubes y su consenso ------------------------------------

def allsky_hour_valid(h: dt.datetime) -> bool:
    """La casilla horaria que empieza en `h` termina antes del corte local de la
    camara (`ALLSKY_LAST_LOCAL_HOUR`). Las horas de tarde siempre valen: quien
    las acota es la noche cerrada."""
    local = aware(h).astimezone(ALLSKY_TZ)
    return not (ALLSKY_LAST_LOCAL_HOUR < local.hour + 1 <= 12)


def sensor_truths(photometer: dict[dt.datetime, bool],
                  cw_raw: dict[dt.datetime, tuple[float, int]],
                  sky_raw: dict[dt.datetime, float],
                  eph: dict[dt.datetime, tuple[float, float]],
                  keep) -> dict[str, dict[dt.datetime, bool]]:
    """Fotometro, CloudWatcher, allsky y consenso, hora a hora (True = despejado).

    `keep(h)` acota el periodo. El CloudWatcher y la allsky solo cuentan en noche
    cerrada (Sol < -18 a la media hora); la allsky ademas solo hasta las 05:00
    locales (`allsky_hour_valid`). El
    consenso es la mayoria de las disponibles, con al menos dos y sin empate: es
    un constructo pragmatico y se declara como tal.
    """
    def deep(h):
        return h in eph and eph[h][0] < -18.0

    T = {
        "fotometro": {h: v for h, v in photometer.items() if keep(h)},
        "cloudwatcher": {h: f >= 0.5 for h, (f, n) in cw_raw.items()
                         if deep(h) and keep(h)},
        "allsky": {h: s >= ALLSKY_CLEAR_STARS for h, s in sky_raw.items()
                   if deep(h) and allsky_hour_valid(h) and keep(h)},
    }
    consensus = {}
    for h in set(T["fotometro"]) | set(T["cloudwatcher"]) | set(T["allsky"]):
        votes = [T[k][h] for k in ("fotometro", "cloudwatcher", "allsky") if h in T[k]]
        if len(votes) >= 2 and 2 * sum(votes) != len(votes):
            consensus[h] = 2 * sum(votes) > len(votes)
    T["consenso"] = consensus
    return T


# --- la verdad del techo, noche a noche ------------------------------------------

def table_months(content: dict) -> dict[tuple[int, int], float]:
    """La tabla de horas abiertas de AstroCamp como {(anio, mes): horas}."""
    out = {}
    for year, record in (content or {}).items():
        if not str(year).isdigit():
            continue
        for month, value in enumerate((record or {}).get("months") or [], start=1):
            if value is not None:
                out[(int(year), month)] = float(value)
    return out


def table_changes(snapshots) -> list[tuple[dt.datetime, dict]]:
    """Solo las fotos en las que la tabla CAMBIO, cada una en el primer instante
    en que se vio. Es lo que da el `git log` del fichero, y lo que hace que una
    foto repetida (la de cada manana del colector) no parta un intervalo: que la
    tabla no haya cambiado a las 10 UTC no prueba que la noche fuera de 0 h, solo
    que AstroCamp aun no la ha apuntado."""
    out: list[tuple[dt.datetime, dict]] = []
    for when, table in sorted(snapshots, key=lambda s: s[0]):
        if out and out[-1][1] == table:
            continue
        out.append((when, table))
    return out


def table_deltas(changes) -> list[tuple[dt.datetime, dt.datetime, float]]:
    """Horas abiertas entre dos cambios consecutivos de la tabla.

    Solo cuentan los meses que el intervalo toca (un dia de margen por delante,
    por la noche que cruza el cambio de mes): si AstroCamp corrige un mes viejo,
    esa correccion no se le apunta a la noche de ayer.
    """
    out = []
    for (t0, a), (t1, b) in zip(changes[:-1], changes[1:]):
        months, day = set(), (t0 - dt.timedelta(days=1)).date()
        while day <= t1.date():
            months.add((day.year, day.month))
            day += dt.timedelta(days=1)
        delta = sum(b.get(m, 0.0) - a.get(m, 0.0) for m in months)
        out.append((t0, t1, float(delta)))
    return out


def roof_truth(frames: dict, samples, deltas) -> dict[dt.date, dict]:
    """Horas de techo abierto de cada noche, de las dos fuentes y cruzadas.

    `deltas` son intervalos (t0, t1, horas abiertas entre las dos fotos de la
    tabla diaria de AstroCamp). Se reparten entre las noches que solapan el
    intervalo en proporcion a lo solapado:
      limpio  -- la noche cae entera en intervalos que no tocan otra noche;
      aprox   -- una foto de tarde partio la noche (reparto proporcional);
      reparto -- un intervalo de muchas noches (30-09 a 08-10, 17-10 a 21-10).
    Las lecturas sueltas del techo dan la misma cifra donde hay cadencia
    (>= 12 lecturas por noche, hasta el 28-09).

    OJO: `frames` tiene que cubrir TODAS las noches que toca cada intervalo; si
    falta una, el reparto se la salta y la noche presente sale "limpia".
    """
    intervals = {n: (frames[n]["sunset"], frames[n]["sunrise"]) for n in frames}
    alloc: dict[dt.date, list] = defaultdict(list)
    for t0, t1, delta in deltas:
        touched = []
        for n, (a, b) in intervals.items():
            overlap = (min(b, t1) - max(a, t0)).total_seconds() / 3600.0
            if overlap > 0:
                touched.append((n, overlap, (b - a).total_seconds() / 3600.0))
        span = sum(o for _, o, _ in touched)
        for n, overlap, length in touched:
            alloc[n].append({"delta": delta, "share": overlap / span,
                             "covers": overlap / length, "nights": len(touched)})
    out = {}
    for n in frames:
        a, b = intervals[n]
        parts = alloc.get(n, [])
        night_len = (b - a).total_seconds() / 3600.0
        entry = {"table_hours": None, "table_quality": None, "table_span_max": None,
                 "samples": 0, "samples_open": 0, "sample_hours": None, "hours": None,
                 "opened": None, "source": None}
        if parts and sum(p["covers"] for p in parts) > 0.95:
            nights_max = max(p["nights"] for p in parts)
            entry["table_hours"] = sum(p["delta"] * p["share"] for p in parts)
            entry["table_quality"] = ("limpio" if nights_max == 1 else
                                      "aprox" if nights_max <= 2 else "reparto")
            entry["table_span_max"] = max(p["delta"] for p in parts)
        inside = [(t, o, k) for t, o, k in samples if a <= t <= b]
        entry["samples"] = len(inside)
        entry["samples_open"] = sum(1 for _, o, _ in inside if o > 0)
        if len(inside) >= 12:
            entry["sample_hours"] = entry["samples_open"] / len(inside) * night_len
        if entry["table_quality"] in ("limpio", "aprox"):
            entry["hours"], entry["source"] = entry["table_hours"], "tabla " + entry["table_quality"]
        elif entry["sample_hours"] is not None:
            entry["hours"], entry["source"] = entry["sample_hours"], "lecturas"
        elif (entry["table_quality"] == "reparto" and entry["table_span_max"] <= OPEN_HOURS_MIN
              and entry["samples_open"] == 0):
            entry["hours"], entry["source"] = 0.0, "cota: <=2 h en todo el tramo"
        if entry["hours"] is not None:
            entry["opened"] = entry["hours"] >= OPEN_HOURS_MIN
        elif entry["samples_open"] > 0:
            entry["opened"], entry["source"] = True, "lectura suelta abierta"
        out[n] = entry
    return out


def dark_open_hours(frame: dict, samples, minimum: int = 12) -> float | None:
    """Horas abiertas con el Sol < -18 grados, de las lecturas: la fraccion de
    lecturas abiertas dentro de la noche astronomica por su duracion. None con
    menos de `minimum` lecturas dentro (desde el 30-09 casi siempre: M11)."""
    if not frame.get("dusk") or not frame.get("dawn"):
        return None
    inside = [o for t, o, _ in samples if frame["dusk"] <= t <= frame["dawn"]]
    if len(inside) < minimum:
        return None
    return sum(1 for o in inside if o > 0) / len(inside) * frame["dark_hours"]


# --- lo publicado ----------------------------------------------------------------

def parse_publication(d: dict) -> dict:
    """Una version de `noche.json`: lo que vio el usuario, en la forma que puntuan
    el retroanalisis y la nota diaria."""
    generated = dt.datetime.fromisoformat(d["generado_utc"]).astimezone(UTC)
    cupula = d.get("cupula") or {}
    confianza = d.get("confianza") or {}

    def when(text):
        return dt.datetime.fromisoformat(text).astimezone(UTC) if text else None

    nights = []
    for n in d.get("noches", []):
        verdict = n.get("veredicto_abrir") or {}
        window = verdict.get("ventana") or {}
        twilight = n.get("crepusculo") or {}
        cielo = n.get("cielo") or {}
        trans = cielo.get("transparencia") or {}
        entry = {
            "night": dt.date.fromisoformat(n["noche"]),
            "p_open": n.get("probabilidad_de_abrir"),
            "open": verdict.get("abierto"),
            "verdict_derived": "abierto" not in verdict,
            "window": ((when(window["desde"]), when(window["hasta"]))
                       if window.get("desde") else None),
            "expected_hours": n.get("horas_utilizables_esperadas"),
            "cloud_model": n.get("modelo_nubes"),
            "truth": n.get("verdad_terreno") or confianza.get("verdad_terreno"),
            "moon": n.get("luna_iluminacion"),
            "sunset": when(twilight.get("ocaso")),
            "sunrise": when(twilight.get("orto")),
            "dark_from": when(twilight.get("astronomico_desde")),
            "dark_to": when(twilight.get("astronomico_hasta")),
            "darkest_mag": cielo.get("mag_cenit_mas_oscuro"),
            "aod": trans.get("aod"),
            "seeing_tercile": (cielo.get("seeing") or {}).get("tercil"),
            "hours": [{
                "t": hour_key(dt.datetime.fromisoformat(h["t_utc"])),
                "cloud": h.get("nubes_previstas"),
                "p_clear": h.get("p_despejado"),
                "p_usable": h.get("p_utilizable"),
                "safe": h.get("seguro_para_abrir"),
                "limits": h.get("limita"),
            } for h in n.get("horas", [])],
        }
        if cupula.get("night") == n.get("noche"):
            entry["sky_tercile"] = cupula.get("sky_tercile")
            entry["aod_tercile"] = cupula.get("aod_tercile")
            entry["cupula_seeing"] = (cupula.get("seeing") or {}).get("tercile")
        if entry["open"] is None and entry["p_open"] is not None:
            entry["open"] = entry["p_open"] >= 0.5
        nights.append(entry)
    return {"generated": generated, "nights": nights,
            "curve_fitted": confianza.get("curva_ajustada_el")}


def published_history(data_repo: Path, since: dt.datetime | None = None) -> list[dict]:
    """Cada version de `noche.json` en el historial del repo de datos, analizada y
    en orden de generacion."""
    pubs = [parse_publication(d) for _, d in file_history(data_repo, "noche.json", since)
            if d.get("generado_utc")]
    pubs.sort(key=lambda p: p["generated"])
    return pubs


def pick_publication(pubs: list[dict], night: dt.date, day: dt.date):
    """(publicacion, entrada) vigente a las DECISION_HOUR_UTC de `day` para
    `night`, o None. `pubs` en orden de generacion."""
    cutoff = dt.datetime(day.year, day.month, day.day, DECISION_HOUR_UTC, tzinfo=UTC)
    chosen = None
    for pub in pubs:
        if pub["generated"] > cutoff:
            break
        for entry in pub["nights"]:
            if entry["night"] == night:
                chosen = (pub, entry)
    return chosen


# --- la probabilidad de abrir con otra curva ---------------------------------------

def gated_usable(hours: list[dict], p_of_cloud) -> list[float] | None:
    """P(utilizable) de cada hora publicada con OTRA curva y las MISMAS puertas.

    El factor de seguridad de cada hora se toma de lo publicado (p_utilizable /
    p_despejado); donde la curva publicada dio 0, del `seguro_para_abrir`. None
    si alguna hora no trae nube.
    """
    usable = []
    for x in hours:
        if x["cloud"] is None:
            return None
        if x["p_clear"]:
            gate = (x["p_usable"] or 0.0) / x["p_clear"]
        else:
            gate = 0.0 if x["safe"] is False else 1.0
        usable.append(p_of_cloud(x["cloud"]) * gate)
    return usable


def night_open_probability(hours: list[dict], p_of_cloud, frame: dict | None) -> float | None:
    """P(abrir) de una noche publicada con otra curva, con la regla de produccion
    de hoy: la hora mas floja de la mejor ventana de dos horas, y desde M6 solo
    con las casillas de noche astronomica (`ephem.dark_slot_weights`)."""
    from .ephem import dark_slot_weights
    from .gates import opening_probability
    usable = gated_usable(hours, p_of_cloud)
    if not usable:
        return None
    starts = [aware(x["t"]) for x in hours]
    step = ((starts[1] - starts[0]).total_seconds() / 3600.0) if len(starts) > 1 else 1.0
    step = step if step > 0 else 1.0
    weights = (dark_slot_weights(starts, step, frame.get("dusk"), frame.get("dawn"))
               if frame else None)
    return float(opening_probability(usable, step_hours=step, weights=weights))
