"""Which forecast actually gets this sky right, scored against what we measured.

The question is Nino's: with several providers predicting the same night, which
one matches the sensors? Nothing here is new physics; it is bookkeeping that has
to be started EARLY, because a scoreboard can only score what was archived while
it was running.

Three decisions worth defending, because each of them changes the answer.

**The truth is the photometer, not `clear_flag`.** The TESS cloud index needs
`tamb` and `tsky`, and AstroCamp's station publishes a literal zero for both:
that flag is populated for 2019-2021 and then stops dead, while the forecasts
being scored start in 2024. Joining them gives 51 rows of overlap out of 22000
and a Brier of 0.000 that reads as perfect skill and is two days of a different
sensor. The truth used here is the one the calibrator already relies on: an hour
is clear when the photometer tracks the clear-sky model in level AND scatter.
Overlap with the backfill: 6822 hours instead of 51.

**A cloud percentage is used as a probability, not as a threshold.** So
`p_clear = 1 - cloud_total/100`. Thresholding throws away everything the model
said about how sure it was, and 70 % cloud is not the same forecast as 100 %.

**Brier AND AUC, because they answer different questions.** Providers do not
agree on what "cloud cover" means, so a model can rank nights perfectly and
still lose on Brier for having its own idea of the scale. Brier says "can I
trust this number as it comes"; AUC says "does this model know which nights are
the good ones", and only the second survives a recalibration. A model that beats
climatology on neither is not adding information, and the report says so.

Run:  python -m astroweather.scoreboard
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

import numpy as np

from .calibration import (WEATHERNEXT_LEAD_DAYS, WEATHERNEXT_MODEL, Calibrator,
                          is_backfill_of, model_path, observed_clear_hours)
from .queries import connect

# Below this a score is noise. 200 hours is roughly a season of usable nights
# here, and it is stated in the report rather than hidden.
MIN_SAMPLES = 200


def _hour(stamp: dt.datetime) -> dt.datetime:
    return stamp.astimezone(dt.timezone.utc).replace(
        minute=0, second=0, microsecond=0, tzinfo=None)


def _auc(probabilities: np.ndarray, outcomes: np.ndarray) -> float | None:
    """Rank-based AUC: the chance a clear hour was given a higher p than a
    cloudy one. 0.5 is a coin, 1.0 is perfect ordering. Immune to any monotonic
    recalibration, which is exactly why it sits next to Brier."""
    positives = int(outcomes.sum())
    negatives = len(outcomes) - positives
    if positives == 0 or negatives == 0:
        return None
    order = probabilities.argsort()
    ranks = np.empty(len(probabilities), float)
    ranks[order] = np.arange(1, len(probabilities) + 1)
    # Average the ranks over ties, or a model that emits many identical values
    # (0 % and 100 % cloud are both common) would be scored on tie-break order.
    _, inverse, counts = np.unique(probabilities, return_inverse=True,
                                   return_counts=True)
    sums = np.zeros(len(counts))
    np.add.at(sums, inverse, ranks)
    ranks = (sums / counts)[inverse]
    return float((ranks[outcomes == 1].sum() - positives * (positives + 1) / 2)
                 / (positives * negatives))


def _brier(probabilities: np.ndarray, outcomes: np.ndarray) -> float:
    return float(((probabilities - outcomes) ** 2).mean())


_BACKFILL_SUFFIX = re.compile(r"_prev(\d+)d$")


def _is_backfill(model: str) -> bool:
    """Any model's backfill (`<model>_prev<N>d`), ECMWF or not."""
    return _BACKFILL_SUFFIX.search(model) is not None


def _lead_days(model: str, lead_hours: int) -> int:
    """The backfill encodes its lead in the model name (`..._prev3d`); a live
    run carries it in `lead_h`. Both end up as whole days so the two can sit in
    the same table. Generic on purpose: ICON's backfill reads the same way."""
    match = _BACKFILL_SUFFIX.search(model)
    if match:
        return int(match.group(1))
    return int(lead_hours) // 24


def cloud_scores(root=None, since: str = "2024-02-01") -> dict:
    """Score every model that predicted cloud cover against the photometer."""
    truth = observed_clear_hours(since=since, root=root)
    if not truth:
        return {"truth_hours": 0, "base_rate": None, "models": [], "baselines": {}}

    connection = connect(root)
    rows = connection.execute("""
        SELECT model, lead_h, valid_utc, cloud_total, member
        FROM forecasts WHERE cloud_total IS NOT NULL
    """).fetchall()

    # Ensemble members are averaged into one probability per (model, lead, hour);
    # a deterministic run contributes its own single value.
    calibrator = Calibrator.load()
    # La curva de WeatherNext es suya: solo se aplica a SU media y en los
    # alcances con los que se valido. La etiqueta no empieza por "calibrado" a
    # proposito: esas filas son las del ECMWF en `decay_with_lead`, y esta va
    # con el resto de proveedores.
    wn_calibrator = Calibrator.load(model_path(WEATHERNEXT_MODEL, root))
    if wn_calibrator is not None and wn_calibrator.trained_on_model != WEATHERNEXT_MODEL:
        wn_calibrator = None
    ensemble: dict[tuple, list[float]] = defaultdict(list)
    single: dict[tuple, list[tuple[dt.datetime, float]]] = defaultdict(list)
    for model, lead, valid, cloud, member in rows:
        hour = _hour(valid)
        if hour not in truth:
            continue
        days = _lead_days(model, lead)
        if member is None:
            single[(model, days)].append((hour, 1.0 - float(cloud) / 100.0))
        else:
            ensemble[(model, days, hour)].append(1.0 - float(cloud) / 100.0)
        # Our own calibrated probability, scored as one more competitor. It is
        # the raw number of the same model passed through the logistic fit, so
        # the difference between the two rows IS what the calibration adds.
        # SOLO sobre el retroanalisis del ECMWF: la curva es suya, y la de ICON
        # o GFS con ella encima seria corregir numeros que no son los suyos.
        if calibrator is not None and member is None and is_backfill_of(model):
            single[("calibrado (nuestro)", days)].append(
                (hour, calibrator.probability(float(cloud), days * 24.0)))
        if (wn_calibrator is not None and member is None
                and model == WEATHERNEXT_MODEL and days in WEATHERNEXT_LEAD_DAYS):
            single[(f"{WEATHERNEXT_MODEL}_calibrado", days)].append(
                (hour, wn_calibrator.probability(float(cloud), days * 24.0)))
    for (model, days, hour), values in ensemble.items():
        single[(model, days)].append((hour, float(np.mean(values))))

    # Baselines on the truth alone: climatology is the base rate, persistence is
    # "the same hour last night". Both are free, and a forecast that does not
    # beat them is not adding information.
    base_rate = float(np.mean([1.0 if v else 0.0 for v in truth.values()]))
    yesterday = {h: truth[h - dt.timedelta(days=1)]
                 for h in truth if (h - dt.timedelta(days=1)) in truth}
    persistence_brier = None
    if yesterday:
        persistence_brier = _brier(
            np.array([1.0 if yesterday[h] else 0.0 for h in yesterday]),
            np.array([1.0 if truth[h] else 0.0 for h in yesterday]))

    scores = []
    for (model, days), pairs in single.items():
        # One value per hour: several runs can be valid for the same hour, and
        # the newest is not necessarily the one archived last.
        by_hour: dict[dt.datetime, list[float]] = defaultdict(list)
        for hour, probability in pairs:
            by_hour[hour].append(probability)
        hours = sorted(by_hour)
        if len(hours) < 5:
            continue
        probabilities = np.array([float(np.mean(by_hour[h])) for h in hours])
        outcomes = np.array([1.0 if truth[h] else 0.0 for h in hours])
        brier = _brier(probabilities, outcomes)
        climatology = _brier(np.full(len(outcomes), outcomes.mean()), outcomes)
        scores.append({
            "model": model,
            "lead_days": days,
            "n": len(hours),
            "base_rate": float(outcomes.mean()),
            "brier": brier,
            "bss_climatology": (1.0 - brier / climatology) if climatology > 0 else None,
            "auc": _auc(probabilities, outcomes),
            "enough": len(hours) >= MIN_SAMPLES,
        })
    scores.sort(key=lambda entry: (not entry["enough"], entry["brier"]))
    return {
        "truth_hours": len(truth),
        "base_rate": base_rate,
        "models": scores,
        "baselines": {
            "climatology_brier": base_rate * (1.0 - base_rate),
            "persistence_brier": persistence_brier,
            "persistence_n": len(yesterday),
        },
    }


def decay_with_lead(scores: list[dict]) -> list[dict]:
    """ECMWF's backfill is the one series long enough to show how a forecast
    decays with lead time. Kept apart on purpose: it is ONE model at five
    leads, plus our calibration of it, not five models competing."""
    wanted = [entry for entry in scores
              if entry["enough"] and (is_backfill_of(entry["model"])
                                      or entry["model"].startswith("calibrado"))]
    return sorted(wanted, key=lambda entry: (entry["model"].startswith("calibrado"),
                                             entry["lead_days"]))


def persistence_gap(root=None, resamples: int = 2000, seed: int = 11) -> dict | None:
    """Does the model beat "tonight like last night"? Scored on the SAME hours.

    Two traps this walks around. Comparing two scores computed over different
    samples is the easiest way to crown the wrong one, so both forecasts are
    evaluated on the intersection. And the hours inside one night are anything
    but independent — 8 of them on average — so the confidence interval comes
    from resampling WHOLE NIGHTS, not hours.
    """
    truth = observed_clear_hours(root=root)
    calibrator = Calibrator.load()
    if not truth or calibrator is None:
        return None

    rows = connect(root).execute("""
        SELECT valid_utc, cloud_total FROM forecasts
        WHERE cloud_total IS NOT NULL AND member IS NULL
          AND model = 'ecmwf_ifs025_prev1d'
    """).fetchall()
    clouds: dict[dt.datetime, list[float]] = defaultdict(list)
    for valid, cloud in rows:
        hour = _hour(valid)
        if hour in truth:
            clouds[hour].append(float(cloud))

    hours = [h for h in sorted(clouds) if (h - dt.timedelta(days=1)) in truth]
    if len(hours) < MIN_SAMPLES:
        return None
    outcomes = np.array([1.0 if truth[h] else 0.0 for h in hours])
    persistence = np.array([1.0 if truth[h - dt.timedelta(days=1)] else 0.0 for h in hours])
    model = np.array([calibrator.probability(float(np.mean(clouds[h])), 24.0)
                      for h in hours])
    # An hour before noon belongs to the night that started the day before.
    nights = np.array([(h - dt.timedelta(hours=12)).date().toordinal() for h in hours])

    def skill(probabilities, index):
        actual = outcomes[index]
        reference = ((actual.mean() - actual) ** 2).mean()
        if reference <= 0:
            return np.nan
        return 1.0 - ((probabilities[index] - actual) ** 2).mean() / reference

    everything = np.arange(len(hours))
    observed = skill(persistence, everything) - skill(model, everything)

    unique_nights = np.unique(nights)
    by_night = {night: np.where(nights == night)[0] for night in unique_nights}
    generator = np.random.default_rng(seed)
    differences = []
    for _ in range(resamples):
        drawn = generator.choice(unique_nights, size=len(unique_nights), replace=True)
        index = np.concatenate([by_night[night] for night in drawn])
        differences.append(skill(persistence, index) - skill(model, index))
    differences = np.array(differences)
    return {
        "hours": len(hours),
        "nights": len(unique_nights),
        "persistence_bss": float(skill(persistence, everything)),
        "model_bss": float(skill(model, everything)),
        "gap_points": float(observed * 100.0),
        "ci95": (float(np.percentile(differences, 2.5) * 100.0),
                 float(np.percentile(differences, 97.5) * 100.0)),
        "model_wins_fraction": float((differences < 0).mean()),
    }


def report(root=None) -> str:
    result = cloud_scores(root=root)
    lines = ["MARCADOR DE MODELOS - nubes contra el fotometro del sitio",
             "=" * 62]
    if not result["models"]:
        lines.append("  Sin datos suficientes todavia.")
        return "\n".join(lines)

    lines.append("  Verdad: %d horas de noche cerrada desde 2024-02, %.1f %% despejadas."
                 % (result["truth_hours"], result["base_rate"] * 100.0))
    line = "  A batir: climatologia Brier=%.3f" % result["baselines"]["climatology_brier"]
    if result["baselines"]["persistence_brier"] is not None:
        line += (", persistencia Brier=%.3f (n=%d)"
                 % (result["baselines"]["persistence_brier"],
                    result["baselines"]["persistence_n"]))
    lines += [line, ""]

    decay = decay_with_lead(result["models"])
    if decay:
        lines.append("  ECMWF CRUDO Y CALIBRADO, POR ALCANCE (2,5 anios de retroanalisis)")
        lines.append("    que                      dias      n    Brier   BSS vs clim.    AUC")
        for entry in decay:
            bss = ("%+6.1f %%" % (entry["bss_climatology"] * 100.0)
                   if entry["bss_climatology"] is not None else "      -")
            auc = "%.3f" % entry["auc"] if entry["auc"] is not None else "    -"
            etiqueta = ("calibrado (nuestro)" if entry["model"].startswith("calibrado")
                        else "ECMWF crudo")
            lines.append("    %-22s %5d  %5d   %.4f      %s       %s"
                         % (etiqueta, entry["lead_days"], entry["n"],
                            entry["brier"], bss, auc))
        lines.append("")
        lines.append("    El crudo ORDENA (AUC muy por encima de 0,5) pero como probabilidad")
        lines.append("    es peor que la climatologia: por eso existe el calibrador. Su fila")
        lines.append("    es DENTRO DE MUESTRA (se entreno con estos mismos datos): optimista.")
        lines.append("")

    gap = persistence_gap(root=root)
    if gap is not None:
        # El titular sale del signo del numero, no de lo que se creia el dia
        # que se escribio: este bloque afirmaba que la persistencia ganaba, y
        # lo siguio afirmando cuando ya perdia por 54 puntos.
        persistence_wins = gap["persistence_bss"] > gap["model_bss"]
        lines.append("  LA LINEA DE BASE GRATUITA: 'COMO ANOCHE' CONTRA EL MODELO")
        lines.append("    Misma muestra, %d horas en %d noches."
                     % (gap["hours"], gap["nights"]))
        lines.append("    persistencia BSS %+.1f %%   contra   modelo calibrado BSS %+.1f %%"
                     % (gap["persistence_bss"] * 100.0, gap["model_bss"] * 100.0))
        lines.append("    Diferencia %+.1f puntos, IC95 [%+.1f, %+.1f] remuestreando NOCHES"
                     % (gap["gap_points"], gap["ci95"][0], gap["ci95"][1]))
        lines.append("    (las horas de una noche no son independientes). El modelo gana en")
        lines.append("    el %.1f %% de las remuestras." % (gap["model_wins_fraction"] * 100.0))
        if persistence_wins:
            lines.append("    La linea de base gratuita bate al modelo: eso no lo tira a la")
            lines.append("    basura -sumados van mejor que cualquiera de los dos- pero si")
            lines.append("    dice que la persistencia tiene que ENTRAR en el calibrador.")
        else:
            lines.append("    El modelo bate a la linea de base gratuita, que es lo minimo")
            lines.append("    que se le pide para justificar su complejidad. Cuando esto se")
            lines.append("    media contra la verdad anterior salia al reves por 14 puntos,")
            lines.append("    y no era pericia meteorologica: entre una noche y las 1-5")
            lines.append("    siguientes la fase lunar apenas cambia, y la verdad media la")
            lines.append("    Luna. La persistencia no predecia el tiempo, predecia el sesgo.")
        lines.append("")

    others = [entry for entry in result["models"]
              if not _is_backfill(entry["model"])
              and not entry["model"].startswith("calibrado")]
    if others:
        lines.append("  MULTIMODELO (empezo a archivarse el 27-08-2026)")
        for entry in others[:12]:
            flag = "" if entry["enough"] else "   <-- muestra insuficiente, no se rankea"
            lines.append("    %-22s %d dia(s)  n=%5d  Brier=%.4f%s"
                         % (entry["model"], entry["lead_days"], entry["n"],
                            entry["brier"], flag))
        if any(entry["model"] == f"{WEATHERNEXT_MODEL}_calibrado" for entry in others):
            lines.append("    %s_calibrado es DENTRO DE MUESTRA, como el calibrado del ECMWF,"
                         % WEATHERNEXT_MODEL)
            lines.append("    y sobre otras horas (2026, no 2,5 anios): no se compara con el de")
            lines.append("    arriba. La comparacion justa, fuera de muestra y sobre las mismas")
            lines.append("    horas, esta en el comentario de calibration.WEATHERNEXT_MODEL.")
        if not any(entry["enough"] for entry in others):
            lines.append("    Ninguno llega a %d horas: todavia NO hay marcador entre "
                         "proveedores." % MIN_SAMPLES)
            lines.append("    Cada dia que corre el scheduler suma; lo no archivado no se "
                         "recupera.")
    return "\n".join(lines)


def main() -> None:
    print(report())


if __name__ == "__main__":
    main()
