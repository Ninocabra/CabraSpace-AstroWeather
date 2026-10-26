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
la verdad y a que noche pertenece cada elemento, y devuelve la decision. La
curva del fotometro (`calibration.py`, verdad por hora) se examinaria con la
misma funcion pasando sus horas agrupadas por noche; `log_decision` escribe en
el mismo registro con `curve='fotometro'`.
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
    if cold_start or p0 is None:
        out["decision"], out["reason"] = "arranque_en_frio", "no hay curva vigente: entra la aspirante"
    elif n_groups < min_groups:
        out["decision"] = "se_queda"
        out["reason"] = (f"solo {n_groups} noches puntuables en la ventana (minimo "
                         f"{min_groups}): no hay examen, se queda la vigente")
    elif out["d_brier"] <= -margin:
        out["decision"] = "sustituye"
        out["reason"] = (f"la aspirante gana por {-out['d_brier']:.3f} de Brier en "
                         f"{n_groups} noches (margen {margin:.3f})")
    else:
        out["decision"] = "se_queda"
        out["reason"] = (f"la aspirante no gana por el margen: diferencia "
                         f"{out['d_brier']:+.3f} de Brier en {n_groups} noches "
                         f"(hace falta <= {-margin:.3f})")
    return out


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
    def f(x, d=3):
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
    args = parser.parse_args(argv)
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
