"""M16 en la curva del fotometro: curva entrenada con la vista contra curva
entrenada por recogida, evaluadas fuera de muestra con lo que se sabia al
publicar (las 15 UTC). Informe: docs/m16_curva_fotometro.md.

Solo lee el archivo. Mide:

1. El control de que la comparacion discrimina: cuantos pares vivos cambian de
   nube entre la vista (`lead_h // 24`, el codigo de antes, `legacy_rows`) y la
   recogida (`calibration.training_rows`, desde M16).
2. El examen `nube_curva` del 26-10 23:31Z reproducido con la vista (tiene que
   dar la decision archivada) y con la nube as-of; y el examen dia a dia del
   28-09 al 27-10 contra la misma vigente.
3. Prequencial semanal (01-09 -> 25-10): las dos curvas, ajustadas con todo lo
   anterior a cada semana, puntuadas con la nube as-of contra el fotometro y el
   consenso horario (por alcance), y por noche contra el voto, la fraccion de
   consenso y el techo (`night_scores`).
4. Controles en rojo: fuga perfecta (la nube viva dice la verdad) y fuga real
   maxima (la recogida justo antes de la hora), deja-un-mes-fuera sep-oct; la
   nube barajada.
5. La tabla de M17 (sep-oct, nativa contra la vigente del 30-08) con la vista y
   con la nube as-of, y el ranking a 1 dia (`modelo_d1`) antes y despues.

Uso (desde la raiz del repo): python tools/m16_curva_fotometro.py
        [--archive ../AstroWeather-Archive] [--out docs/m16_curva_fotometro]
        [--resamples 2000]
~1,5 min en el escritorio (16 s la verdad del fotometro).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tempfile
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from astroweather import calibration as C  # noqa: E402
from astroweather import curvecontest as CC  # noqa: E402
from astroweather import nighttruth as NT  # noqa: E402
from astroweather import nightscore as NS  # noqa: E402
from astroweather.asof import issue_cutoff, raw_forecast_asof  # noqa: E402
from astroweather.queries import connect  # noqa: E402
from astroweather.skill import NightBootstrap, auc, brier  # noqa: E402

UTC = dt.timezone.utc
FIRST_LIVE = dt.date(2026, 8, 27)            # primera noche con prevision viva
PREQ_FROM = dt.date(2026, 9, 1)
LAST = dt.date(2026, 10, 25)                 # ultima noche con verdad el 27-10
LEADS = range(1, 6)
night = NT.night_of


def legacy_rows(truth, root, model=C.BACKFILL_MODEL, since=C.NATIVE_TCC_SINCE):
    """El `training_rows` de antes de M16: lo vivo por la vista deduplicada con
    `lead_h // 24`. Solo para comparar."""
    con = connect(root)
    prefix = model + "_prev"
    cut = "" if since is None else f"AND run_utc >= TIMESTAMP '{since.isoformat()}'"
    rows = con.execute(f"""
        SELECT valid_utc, cloud_total, model, lead_h FROM forecasts
        WHERE cloud_total IS NOT NULL AND member IS NULL
          AND (starts_with(model, ?) OR model = ?) {cut}
    """, [prefix, model]).fetchall()
    g = defaultdict(list)
    for valid, cloud, name, lead_h in rows:
        if name == model:
            if lead_h is None:
                continue
            d = int(lead_h) // 24
        elif C.is_backfill_of(name, model):
            d = int(name[len(prefix):].rstrip("d"))
        else:
            continue
        h = valid.astimezone(UTC).replace(minute=0, second=0, microsecond=0, tzinfo=None)
        if d in C.BACKFILL_LEAD_DAYS and h in truth:
            g[(h, d)].append(float(cloud))
    return [(h, float(np.mean(v)), float(d), bool(truth[h])) for (h, d), v in sorted(g.items())]


def fit(rows):
    return C.fit([r[1:] for r in rows])


def params(c):
    return [c.intercept, c.cloud_weight, c.lead_weight, c.trained_on]


def diff_block(p, o, g, ref, kinds, resamples):
    """Brier, AUC y p media de cada curva; diferencia pareada contra `ref`, IC90."""
    boot = NightBootstrap(np.asarray(g), resamples)
    out = {"n": int(len(o)), "noches": boot.n_nights, "tasa": float(np.mean(o))}
    binary = len(np.unique(o)) == 2 and set(np.unique(o)) <= {0.0, 1.0}
    for k in kinds:
        out[k] = {"brier": brier(p[k], o), "p_media": float(np.mean(p[k]))}
        if binary:
            out[k]["auc"] = auc(p[k], o)
    for k in kinds:
        if k == ref:
            continue
        d = boot.interval(lambda i, k=k: brier(p[k][i], o[i]) - brier(p[ref][i], o[i]), 5, 95)
        out[f"{k}-{ref}"] = {"d_brier": d[0], "ic90": [d[1], d[2]]}
    return out


def reliability(p, o, bins=10, minimum=10):
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi)
        if m.sum() >= minimum:
            out.append([float(p[m].mean()), float(o[m].mean()), int(m.sum())])
    return out


def run(archive: Path, out_dir: Path, resamples: int) -> dict:
    NT.astropy_offline()
    truth = C.observed_clear_hours(C.NATIVE_TCC_SINCE.isoformat(), archive)
    rows = {"vista": legacy_rows(truth, archive), "asof": C.training_rows(truth, archive)}
    con = connect(archive)
    live = raw_forecast_asof(con, archive, C.BACKFILL_MODEL,
                             dt.datetime(2026, 8, 20, tzinfo=UTC))
    R: dict = {"generado_utc": dt.datetime.now(UTC).isoformat(timespec="seconds"),
               "pares": {k: len(v) for k, v in rows.items()}}

    # --- 1. control: cuantos pares vivos cambian ---------------------------------
    is_live = lambda r: night(r[0]) >= FIRST_LIVE      # noqa: E731
    lv = {(r[0], r[2]): r[1] for r in rows["vista"] if is_live(r)}
    la = {(r[0], r[2]): r[1] for r in rows["asof"] if is_live(r)}
    common = sorted(set(lv) & set(la))
    dif = np.array([abs(lv[k] - la[k]) for k in common])
    ages = []                         # recogida de la vista -> hora valida, alcance 1
    by_label = {}
    for valid, ret, lead_h in con.execute("""
            SELECT valid_utc, retrieved_at, lead_h FROM forecasts
            WHERE model = ? AND member IS NULL AND cloud_total IS NOT NULL
              AND lead_h BETWEEN 24 AND 47""", [C.BACKFILL_MODEL]).fetchall():
        h = valid.astimezone(UTC).replace(minute=0, second=0, microsecond=0, tzinfo=None)
        if (h, 1.0) in lv and ret is not None:
            by_label[h] = (valid - ret).total_seconds() / 3600
    ages = np.array(list(by_label.values()))
    stale = []
    for h in live.by_hour:
        if FIRST_LIVE <= night(h) <= LAST:
            for d in LEADS:
                cut = issue_cutoff(night(h), d)
                i = bisect_right(live.times[h], cut) - 1
                if i >= 0:
                    stale.append((cut - live.times[h][i]).total_seconds() / 3600)
    stale = np.array(stale)
    fresher = defaultdict(list)       # recogida de la vista menos recogida al corte, por alcance
    for valid, lead_h, ret in con.execute("""
            SELECT valid_utc, lead_h, retrieved_at FROM forecasts
            WHERE model = ? AND member IS NULL AND cloud_total IS NOT NULL
              AND lead_h IS NOT NULL""", [C.BACKFILL_MODEL]).fetchall():
        d = int(lead_h) // 24
        h = valid.astimezone(UTC).replace(minute=0, second=0, microsecond=0, tzinfo=None)
        if d in LEADS and h in truth and h in live.times and ret is not None:
            i = bisect_right(live.times[h], issue_cutoff(night(h), d)) - 1
            if i >= 0:
                fresher[d].append((ret - live.times[h][i]).total_seconds() / 3600)
    R["vista_mas_reciente_que_el_corte"] = {
        d: {"n": len(a), "fraccion_mas_reciente": float(np.mean(np.array(a) > 0)),
            "fraccion_mas_vieja": float(np.mean(np.array(a) < 0)),
            "horas_p10_p50_p90": [float(np.percentile(a, q)) for q in (10, 50, 90)]}
        for d, a in sorted(fresher.items())}
    R["control_pares"] = {
        "vivos_vista": len(lv), "vivos_asof": len(la), "comunes": len(common),
        "cambian_mas_de_medio_punto": int((dif > 0.5).sum()),
        "dif_mediana": float(np.median(dif)), "dif_p90": float(np.percentile(dif, 90)),
        "solo_vista": len(set(lv) - set(la)), "solo_asof": len(set(la) - set(lv)),
        "vista_d1_horas_recogida_a_hora_p10_p50_p90": [float(np.percentile(ages, q)) for q in (10, 50, 90)],
        "asof_antiguedad_al_corte_h_p50_p90_max": [float(np.median(stale)), float(np.percentile(stale, 90)),
                                                    float(stale.max())],
        "asof_mas_de_24h": int((stale > 24).sum()), "asof_total": int(len(stale)),
    }
    for name, src in (("vista", lv), ("asof", la)):
        for d in (1.0, 2.0, 3.0):
            ks = [k for k in common if k[1] == d]
            p = [1 - src[k] / 100 for k in ks]
            o = [1.0 if truth[k[0]] else 0.0 for k in ks]
            R["control_pares"][f"nube_cruda_{name}_d{int(d)}"] = {
                "n": len(ks), "auc": auc(p, o), "brier": brier(p, o)}

    # --- 2. el examen del 26-10 y el examen dia a dia ---------------------------
    import duckdb
    dec = duckdb.connect().execute(f"""
        SELECT decided_utc, champion_json, challenger_json, champion_brier,
               challenger_brier, d_brier, d_brier_lo, d_brier_hi
        FROM read_parquet('{archive.as_posix()}/curve_decisions/**/*.parquet', union_by_name=true)
        WHERE curve = 'fotometro' ORDER BY decided_utc LIMIT 1""").fetchone()
    now = dec[0].astimezone(UTC)
    champion = C.Calibrator(**json.loads(dec[1]))
    real = C.Calibrator(**json.loads(dec[2]))
    R["examen_26_10"] = {"archivado": {"decided_utc": now.isoformat(), "champion_brier": dec[3],
                                       "challenger_brier": dec[4], "d_brier": dec[5],
                                       "ic90": [dec[6], dec[7]], "aspirante": params(real)}}
    keys = ("decision", "n_nights", "champion_brier", "challenger_brier", "d_brier",
            "d_brier_lo", "d_brier_hi", "champion_auc", "challenger_auc")
    daily = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for name in ("vista", "asof"):
            champion.save(C.model_path(C.BACKFILL_MODEL, tmp))
            rec = CC.cloud_duel(tmp, now=now, rows=rows[name], write=False)
            R["examen_26_10"][name] = {k: rec[k] for k in keys}
            R["examen_26_10"][name]["aspirante"] = params(C.Calibrator(**json.loads(rec["challenger_json"])))
        for i in range(30):
            day = dt.date(2026, 9, 28) + dt.timedelta(days=i)
            when = dt.datetime(day.year, day.month, day.day, 10, 30, tzinfo=UTC)
            entry = {"dia": str(day)}
            for name in ("vista", "asof"):
                champion.save(C.model_path(C.BACKFILL_MODEL, tmp))
                rec = CC.cloud_duel(tmp, now=when, rows=rows[name], write=False)
                entry[name] = {"decision": rec["decision"], "d_brier": rec.get("d_brier")}
            daily.append(entry)
    R["examenes_diarios"] = daily
    last_night = (now - dt.timedelta(days=1)).date()
    _, exam, _ = CC.cloud_exam_split(rows["asof"], last_night)
    o = [1.0 if r[3] else 0.0 for r in exam]
    g = [night(r[0]).toordinal() for r in exam]
    res = CC.compare(CC._cloud_scores(champion, exam), CC._cloud_scores(real, exam), o, g,
                     margin=CC.CLOUD_MARGIN, min_groups=CC.CLOUD_MIN_NIGHTS)
    R["examen_26_10"]["aspirante_real_sobre_asof"] = {k: res[k] for k in
                                                      ("decision", "champion_brier", "challenger_brier",
                                                       "d_brier", "d_brier_lo", "d_brier_hi")}

    # --- 3. prequencial semanal ---------------------------------------------------
    starts = [PREQ_FROM + dt.timedelta(days=7 * i) for i in range(8)]
    block = lambda n: max(s for s in starts if s <= n)          # noqa: E731

    def nowcast(r):
        v = live.at(r[0], r[0].replace(tzinfo=UTC))
        return None if v is None else (r[0], float(v[0]), r[2], r[3])

    variants = {
        "vista": rows["vista"], "asof": rows["asof"],
        "fuga_nowcast": [x for x in (nowcast(r) if is_live(r) else r for r in rows["asof"]) if x],
        "fuga_verdad": [(r[0], 0.0 if r[3] else 100.0, r[2], r[3]) if is_live(r) else r
                        for r in rows["asof"]],
    }
    preq = {k: {s: fit([r for r in variants[k] if night(r[0]) < s]) for s in starts}
            for k in ("vista", "asof", "fuga_nowcast", "fuga_verdad")}
    R["curvas_prequenciales"] = {k: {str(s): params(c) for s, c in v.items()} for k, v in preq.items()}

    since = dt.datetime(FIRST_LIVE.year, FIRST_LIVE.month, FIRST_LIVE.day, 12, tzinfo=UTC)
    cw_raw, sky_raw = NT.cloudwatcher_hours(con, since), NT.allsky_hours(con, since)
    nights = [PREQ_FROM + dt.timedelta(days=i) for i in range((LAST - PREQ_FROM).days + 1)]
    frames = NT.night_frames(nights)
    cand = {h for n in nights for h in NT.night_hours(frames[n])}
    cand |= {h for h in truth if PREQ_FROM <= night(h) <= LAST} | set(cw_raw) | set(sky_raw)
    eph = NT.hour_ephemeris(cand)
    T = NT.sensor_truths(truth, cw_raw, sky_raw, eph, lambda h: PREQ_FROM <= night(h) <= LAST)

    def cloud(h, d):
        v = live.issued(h, d)
        return None if v is None else float(v[0])

    kinds = ("vista", "asof", "fuga_nowcast", "fuga_verdad")
    R["horario"], R["fiabilidad"] = {}, {}
    for tname in ("fotometro", "consenso"):
        ev = [(night(h), d, c, 1.0 if T[tname][h] else 0.0) for h in sorted(T[tname])
              for d in LEADS if (c := cloud(h, d)) is not None]
        p = {k: np.array([preq[k][block(n)].probability(c, d * 24) for n, d, c, _ in ev])
             for k in kinds}
        o = np.array([e[3] for e in ev])
        g = [e[0].toordinal() for e in ev]
        R["horario"][f"{tname}/1-5"] = diff_block(p, o, g, "vista", kinds, resamples)
        R["fiabilidad"][tname] = {k: reliability(p[k], o) for k in ("vista", "asof")}
        for d in LEADS:
            m = np.array([e[1] == d for e in ev])
            R["horario"][f"{tname}/{d}"] = diff_block({k: p[k][m] for k in ("vista", "asof")}, o[m],
                                                      list(np.array(g)[m]), "vista", ("vista", "asof"),
                                                      resamples)
        if tname == "fotometro":
            rng = np.random.default_rng(1)
            shuffled = rng.permutation([e[2] for e in ev])
            ps = {k: np.array([preq[k][block(e[0])].probability(c, e[1] * 24)
                               for e, c in zip(ev, shuffled)]) for k in ("vista", "asof")}
            R["horario"]["fotometro/barajado"] = diff_block(ps, o, g, "vista", ("vista", "asof"), resamples)
    # la otra mitad del 2x2: puntuadas con la nube de la VISTA
    ev = [r for r in rows["vista"] if PREQ_FROM <= night(r[0]) <= LAST]
    p = {k: np.array([preq[k][block(night(h))].probability(c, d * 24) for h, c, d, _ in ev])
         for k in ("vista", "asof")}
    R["horario"]["fotometro/nube_de_la_vista"] = diff_block(
        p, np.array([1.0 if r[3] else 0.0 for r in ev]), [night(r[0]).toordinal() for r in ev],
        "vista", ("vista", "asof"), resamples)

    # --- 3b. noche a noche (alcance 1, horas de noche astronomica) ----------------
    view_d1 = {}
    for valid, c in con.execute("""
            SELECT valid_utc, cloud_total FROM forecasts
            WHERE model = ? AND member IS NULL AND cloud_total IS NOT NULL
              AND lead_h BETWEEN 24 AND 47""", [C.BACKFILL_MODEL]).fetchall():
        view_d1[valid.astimezone(UTC).replace(minute=0, second=0, microsecond=0, tzinfo=None)] = float(c)
    scores = NS.latest_scores(archive)
    night_rows = []
    for n in nights:
        if n not in scores:
            continue
        hours = [h for h in NT.night_hours(frames[n]) if h in eph and eph[h][0] < -18.0]
        pairs = [(h, cloud(h, 1), view_d1.get(h)) for h in hours]
        pairs = [x for x in pairs if x[1] is not None]
        if not pairs:
            continue
        cur = {k: preq[k][block(n)] for k in kinds}
        sc = scores[n]
        row = {"noche": str(n), "horas": len(pairs),
               "nube_asof": float(np.mean([a for _, a, _ in pairs])),
               "voto": sc["vote"], "consenso": sc["consensus_clear_frac"],
               "fotometro": sc["phot_clear_frac"], "cloudwatcher": sc["cw_clear_frac"],
               "allsky": sc["sky_clear_frac"], "techo_abrio": sc["roof_opened"],
               "techo_horas": sc["roof_hours"], "luna": sc["moon"]}
        for k in kinds:
            row[f"p_{k}"] = float(np.mean([cur[k].probability(a, 24) for _, a, _ in pairs]))
        both = [(a, v) for _, a, v in pairs if v is not None]
        if both:
            row["nube_vista"] = float(np.mean([v for _, v in both]))
            row["nube_asof_mismas_horas"] = float(np.mean([a for a, _ in both]))
            row["p_curva_vigente_nube_vista"] = float(np.mean([real.probability(v, 24) for _, v in both]))
            row["p_curva_vigente_nube_asof"] = float(np.mean([real.probability(a, 24) for a, _ in both]))
        night_rows.append(row)
    R["noches"] = night_rows
    R["nocturno"] = {}
    targets = (("voto", lambda v: 1.0 if v == "despejada" else 0.0),
               ("consenso", float), ("fotometro", float),
               ("techo_abrio", lambda v: 1.0 if v else 0.0))
    for key, tf in targets:
        sel = [r for r in night_rows if r[key] is not None]
        p = {k: np.array([r[f"p_{k}"] for r in sel]) for k in kinds}
        o = np.array([tf(r[key]) for r in sel])
        R["nocturno"][key] = diff_block(p, o, list(range(len(sel))), "vista", kinds, resamples)
        sel = [r for r in sel if "nube_vista" in r]
        p = {"nube_vista": np.array([r["p_curva_vigente_nube_vista"] for r in sel]),
             "nube_asof": np.array([r["p_curva_vigente_nube_asof"] for r in sel])}
        o = np.array([tf(r[key]) for r in sel])
        R["nocturno"][f"{key}/misma_curva"] = diff_block(p, o, list(range(len(sel))), "nube_vista",
                                                         ("nube_vista", "nube_asof"), resamples)

    # --- 4. controles en rojo: deja-un-mes-fuera sep-oct --------------------------
    months = [(2026, 9), (2026, 10)]
    mon = lambda h: (h.year, h.month)            # noqa: E731
    ev = [r for r in rows["asof"] if is_live(r) and mon(r[0]) in months]
    o = np.array([1.0 if r[3] else 0.0 for r in ev])
    g = [night(r[0]).toordinal() for r in ev]
    p = {}
    for k in kinds:
        cur = {m: fit([r for r in variants[k] if mon(r[0]) != m]) for m in months}
        p[k] = np.array([cur[mon(r[0])].probability(r[1], r[2] * 24) for r in ev])
    R["control_fuga"] = diff_block(p, o, g, "asof", kinds, resamples)
    # lo mismo con SOLO lo vivo (sin retroanalisis): cuanto se moveria la curva si lo
    # vivo fuera todo el entrenamiento (lo sera con los anios: el retroanalisis no crece)
    p, curves_live = {}, {}
    for k in ("vista", "asof"):
        cur = {m: fit([r for r in variants[k] if is_live(r) and mon(r[0]) != m]) for m in months}
        curves_live[k] = {f"{m[0]}-{m[1]:02d}": params(c) for m, c in cur.items()}
        p[k] = np.array([cur[mon(r[0])].probability(r[1], r[2] * 24) for r in ev])
    R["control_solo_vivo"] = diff_block(p, o, g, "asof", ("vista", "asof"), resamples)
    R["control_solo_vivo"]["curvas"] = curves_live

    # --- 5. M17 sep-oct y modelo_d1, antes y despues --------------------------------
    R["m17_sepoct"] = {}
    for name in ("vista", "asof"):
        pn, pv, o, g = [], [], [], []
        for m in months:
            cur = fit([r for r in rows[name] if mon(r[0]) != m])
            for r in rows[name]:
                if mon(r[0]) == m:
                    pn.append(cur.probability(r[1], r[2] * 24))
                    pv.append(champion.probability(r[1], r[2] * 24))
                    o.append(1.0 if r[3] else 0.0)
                    g.append(night(r[0]).toordinal())
        R["m17_sepoct"][name] = diff_block({"vigente_30_08": np.array(pv), "nativa": np.array(pn)},
                                           np.array(o), g, "vigente_30_08",
                                           ("vigente_30_08", "nativa"), resamples)
    wn = C.model_rows(truth, archive, C.WEATHERNEXT_MODEL)
    R["modelo_d1"] = {}
    for name, d1 in (("vista", {"ecmwf_ifs025": rows["vista"],
                                "icon_seamless": legacy_rows(truth, archive, "icon_seamless"),
                                "weathernext3": wn}),
                     ("asof", {"ecmwf_ifs025": rows["asof"],
                               "icon_seamless": C.model_rows(truth, archive, "icon_seamless"),
                               "weathernext3": wn})):
        rec = CC.rank_d1(archive, now=now, rows=d1, write=False)
        tab = json.loads(rec["challenger_json"])
        R["modelo_d1"][name] = {"horas": tab["horas"], "noches": rec["n_nights"],
                                "modelos": tab["modelos"]}

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "resultados.json").write_text(json.dumps(R, indent=1, default=float), encoding="utf-8")
    figures(R, out_dir)
    return R


# --- figuras ----------------------------------------------------------------------
BLUE, ORANGE, INK, MUTED, GRID = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e4e3df"


def _axes(ax):
    ax.set_facecolor("#fcfcfb")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def figures(R: dict, out_dir: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.6), facecolor="#fcfcfb")
    for ax, tname, title in zip(axes, ("fotometro", "consenso"),
                                ("Contra el fotometro", "Contra el consenso de tres sensores")):
        _axes(ax)
        ax.plot([0, 1], [0, 1], color=MUTED, linewidth=1, linestyle=(0, (4, 3)), zorder=1)
        h = R["horario"][f"{tname}/1-5"]
        for k, color, marker, label in (("vista", BLUE, "o", "curva vieja (vista)"),
                                        ("asof", ORANGE, "s", "curva por recogida")):
            pts = np.array(R["fiabilidad"][tname][k])
            ax.plot(pts[:, 0], pts[:, 1], color=color, linewidth=2, zorder=2)
            ax.scatter(pts[:, 0], pts[:, 1], s=12 + pts[:, 2] / 6, color=color, marker=marker,
                       edgecolor="#fcfcfb", linewidth=2, zorder=3,
                       label=f"{label}: Brier {h[k]['brier']:.4f}")
        ax.set_xlim(0.5, 1.0)
        ax.set_ylim(0.5, 1.0)
        ax.set_xlabel("P(despejado) prevista", color=INK, fontsize=10)
        ax.set_ylabel("fraccion de horas despejadas", color=INK, fontsize=10)
        ax.set_title(f"{title}\n{h['n']} pares hora-alcance, {h['noches']} noches",
                     color=INK, fontsize=10, loc="left")
        ax.legend(frameon=False, fontsize=8.5, loc="upper left", labelcolor=INK)
    fig.suptitle("Fiabilidad de las dos curvas, prequencial 01-09 -> 25-10, nube disponible a las 15 UTC",
                 color=INK, fontsize=11, x=0.01, ha="left")
    fig.text(0.01, 0.01, "Diagonal = calibracion perfecta. Tamano del punto ~ numero de pares. "
             "Alcances 1-5 dias juntos.", color=MUTED, fontsize=8)
    fig.tight_layout(rect=(0, 0.03, 1, 0.94))
    fig.savefig(out_dir / "fiabilidad.png", dpi=130, facecolor=fig.get_facecolor())
    plt.close(fig)

    rows = [r for r in R["noches"] if "nube_vista" in r]
    x = [dt.date.fromisoformat(r["noche"]) for r in rows]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 6.4), sharex=True, facecolor="#fcfcfb",
                                 gridspec_kw={"height_ratios": [1.2, 1]})
    _axes(a1)
    _axes(a2)
    a1.plot(x, [r["nube_vista"] for r in rows], color=BLUE, linewidth=2, marker="o", markersize=4,
            label="«1 dia» de la vista (la ultima recogida de la etiqueta)")
    a1.plot(x, [r["nube_asof_mismas_horas"] for r in rows], color=ORANGE, linewidth=2, marker="s",
            markersize=4, label="lo que habia a las 15 UTC de la vispera")
    a1.set_ylabel("nube media prevista (%)\nhoras de noche astronomica", color=INK, fontsize=9.5)
    a1.set_ylim(0, 100)
    a1.legend(frameon=False, fontsize=8.5, loc="upper left", labelcolor=INK)
    a1.set_title("Arriba: la entrada que cambia (ECMWF 0,25, alcance 1). Abajo: las dos curvas "
                 "sobre la nube de las 15 UTC, y lo que paso", color=INK, fontsize=10, loc="left")
    a2.plot(x, [r["p_vista"] for r in rows], color=BLUE, linewidth=2, label="curva vieja (vista)")
    a2.plot(x, [r["p_asof"] for r in rows], color=ORANGE, linewidth=2, linestyle=(0, (5, 2)),
            label="curva por recogida")
    cons = [(d, r["consenso"]) for d, r in zip(x, rows) if r["consenso"] is not None]
    a2.scatter([d for d, _ in cons], [v for _, v in cons], s=18, color=MUTED, zorder=3,
               label="fraccion despejada segun el consenso")
    bad = [d for d, r in zip(x, rows) if r["voto"] == "nublada"]
    a2.scatter(bad, [0.10] * len(bad), marker="x", s=40, color=INK, zorder=4,
               label="voto de la noche: nublada")
    closed = [d for d, r in zip(x, rows) if r["techo_abrio"] is False]
    a2.scatter(closed, [0.20] * len(closed), marker="v", s=30, color=INK, zorder=4,
               label="techo cerrado (< 2 h)")
    a2.set_ylim(0, 1.02)
    a2.set_ylabel("P(despejado) media de la noche\no fraccion despejada", color=INK, fontsize=9.5)
    a2.legend(frameon=False, fontsize=8, loc="center", bbox_to_anchor=(0.5, 0.45), ncol=3,
              labelcolor=INK)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_dir / "calendario.png", dpi=130, facecolor=fig.get_facecolor())
    plt.close(fig)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, default=REPO.parent / "AstroWeather-Archive")
    parser.add_argument("--out", type=Path, default=REPO / "docs" / "m16_curva_fotometro")
    parser.add_argument("--resamples", type=int, default=2000)
    args = parser.parse_args(argv)
    R = run(args.archive, args.out, args.resamples)
    e = R["examen_26_10"]
    print("examen 26-10 archivado  ", e["archivado"]["d_brier"])
    for k in ("vista", "asof"):
        print(f"examen 26-10 {k:6s}     {e[k]['decision']} {e[k]['d_brier']:+.5f} "
              f"[{e[k]['d_brier_lo']:+.5f}, {e[k]['d_brier_hi']:+.5f}]")
    same = sum(d["vista"]["decision"] == d["asof"]["decision"] for d in R["examenes_diarios"])
    print(f"examenes diarios con la misma decision: {same} de {len(R['examenes_diarios'])}")
    for key, v in R["horario"].items():
        line = f"{key:28s} n={v['n']:5d} noches={v['noches']:3d}"
        for k, w in v.items():
            if isinstance(w, dict) and "d_brier" in w:
                line += f" | {k} {w['d_brier']:+.5f} [{w['ic90'][0]:+.5f}, {w['ic90'][1]:+.5f}]"
        print(line)
    print("figuras y cifras en", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
