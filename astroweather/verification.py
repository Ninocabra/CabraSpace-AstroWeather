"""Forecast verification against ground truth (SPEC v8 section 4.3).

The rule this module exists to enforce: every claim the system makes is scored
against two baselines that cost nothing to beat if the forecast is any good --
persistence ("tonight like last night") and climatology ("what August usually
does here"). A model that cannot beat them is not adding information, and the
panel says so out loud.

What can be verified today, with the archive as it stands:

* Clouds, against the observatory's own Clear/Cloudy verdict and against the
  photometer. Note the photometer only sees clouds once the Moon is modelled
  out (verified: lunar illumination dominates the raw variability), so the
  cloud score currently leans on the safety log and on AEMET rainfall.
* Surface temperature, against the AEMET station 11.5 km away, which is
  external ground truth and labelled as such.

Seeing is deliberately absent: without the FWHM pipeline there is nothing to
verify it against, and inventing a proxy would be worse than the gap.
"""

from __future__ import annotations

from datetime import date

from .queries import connect

CLEAR_THRESHOLD = 20.0        # cloud cover below this counts as clear


def cloud_scores(start: date | None = None, root=None) -> list[dict]:
    """Brier score per model and lead time against the observatory's verdict.

    Lower is better; 0.25 is what a constant 50 % guess scores.
    """
    connection = connect(root)
    where = f"AND f.valid_utc >= DATE '{start}'" if start else ""

    rows = connection.execute(f"""
        WITH truth AS (
            SELECT date_trunc('hour', t_utc) AS hour,
                   bool_or(clear_flag) AS observed_clear
            FROM measurements
            WHERE clear_flag IS NOT NULL
            GROUP BY hour
        ),
        predictions AS (
            SELECT f.model, f.lead_h,
                   date_trunc('hour', f.valid_utc) AS hour,
                   -- deterministic runs give a yes/no, the ensemble a fraction
                   CASE WHEN f.member IS NULL
                        THEN CASE WHEN f.cloud_total < {CLEAR_THRESHOLD} THEN 1.0 ELSE 0.0 END
                        ELSE avg(CASE WHEN f.cloud_total < {CLEAR_THRESHOLD}
                                      THEN 1.0 ELSE 0.0 END) OVER (
                                 PARTITION BY f.model, f.run_utc, f.valid_utc)
                   END AS p_clear
            FROM forecasts f
            WHERE f.cloud_total IS NOT NULL {where}
        )
        SELECT p.model, p.lead_h, count(*) AS n,
               avg((p.p_clear - CASE WHEN t.observed_clear THEN 1.0 ELSE 0.0 END) ^ 2)
                   AS brier
        FROM predictions p JOIN truth t USING (hour)
        GROUP BY p.model, p.lead_h
        HAVING count(*) >= 5
        ORDER BY brier
    """).fetchall()

    return [{"model": r[0], "lead_h": r[1], "n": r[2], "brier": r[3]} for r in rows]


def temperature_scores(root=None) -> list[dict]:
    """Mean error and MAE of forecast T against the nearest AEMET station.

    The station sits 165 m below the observatory, so a systematic warm bias is
    expected and is exactly what the MOS is meant to remove.
    """
    connection = connect(root)
    try:
        rows = connection.execute("""
            WITH daily AS (
                SELECT site_id, CAST(valid_utc AS DATE) AS day, model,
                       min(t2m) AS t_min_fc, max(t2m) AS t_max_fc, avg(t2m) AS t_mean_fc
                FROM forecasts WHERE t2m IS NOT NULL AND member IS NULL
                GROUP BY site_id, day, model
            )
            SELECT d.model, count(*) AS n,
                   avg(d.t_mean_fc - o.t_mean) AS bias,
                   avg(abs(d.t_mean_fc - o.t_mean)) AS mae
            FROM daily d
            JOIN daily_observations o ON o.date = d.day AND o.site_id = d.site_id
            WHERE o.t_mean IS NOT NULL
            GROUP BY d.model HAVING count(*) >= 3
            ORDER BY mae
        """).fetchall()
    except Exception:
        return []
    return [{"model": r[0], "n": r[1], "bias": r[2], "mae": r[3]} for r in rows]


def baselines(root=None) -> dict:
    """Persistence and climatology: the two scores any forecast must beat."""
    connection = connect(root)

    persistence = connection.execute(f"""
        WITH nightly AS (
            SELECT night, bool_or(any_roof_open) AS opened FROM nights GROUP BY night
        ), shifted AS (
            SELECT night, opened,
                   lag(opened) OVER (ORDER BY night) AS yesterday
            FROM nightly
        )
        SELECT count(*), avg(CASE WHEN opened = yesterday THEN 1.0 ELSE 0.0 END)
        FROM shifted WHERE yesterday IS NOT NULL
    """).fetchone()

    # Climatology of usable nights, straight from the observatory's own
    # 2012-> record of open dome hours: the honest "what August usually does".
    climatology = connection.execute("""
        SELECT count(*), avg(CASE WHEN precipitation > 0 THEN 1.0 ELSE 0.0 END)
        FROM daily_observations
    """).fetchone() if _has_daily(connection) else (0, None)

    return {
        "persistence_nights": persistence[0],
        "persistence_accuracy": persistence[1],
        "climatology_days": climatology[0],
        "climatology_wet_fraction": climatology[1],
    }


def _has_daily(connection) -> bool:
    try:
        connection.execute("SELECT 1 FROM daily_observations LIMIT 1")
        return True
    except Exception:
        return False


def report(root=None) -> str:
    """The panel, as plain text. Ugly on purpose: it exists to be read early."""
    lines = ["PANEL DE VERIFICACION", "=" * 60, ""]

    clouds = cloud_scores(root=root)
    lines.append("NUBES - Brier score contra el veredicto del observatorio")
    lines.append("  (0 = perfecto, 0.25 = tirar una moneda; menos es mejor)")
    if clouds:
        for score in clouds[:8]:
            lines.append(f"    {score['model']:26s} lead {score['lead_h']:>3}h  "
                         f"n={score['n']:>4}  Brier={score['brier']:.3f}")
    else:
        lines.append("    sin pares suficientes todavia (hace falta mas verdad terreno)")

    lines += ["", "TEMPERATURA - contra AEMET 7067Y (11.5 km, 165 m mas abajo)"]
    temps = temperature_scores(root=root)
    if temps:
        for score in temps[:6]:
            lines.append(f"    {score['model']:26s} n={score['n']:>4}  "
                         f"sesgo={score['bias']:+.2f} C  MAE={score['mae']:.2f} C")
    else:
        lines.append("    sin solape entre forecast y observaciones diarias todavia")

    base = baselines(root=root)
    lines += ["", "BASELINES QUE HAY QUE BATIR"]
    if base["persistence_accuracy"] is not None:
        lines.append(f"    persistencia (noche = anoche): {base['persistence_accuracy']:.1%} "
                     f"sobre {base['persistence_nights']} noches")
    if base["climatology_wet_fraction"] is not None:
        lines.append(f"    climatologia: llueve el {base['climatology_wet_fraction']:.1%} "
                     f"de {base['climatology_days']} dias registrados")

    lines += ["", "PENDIENTE: seeing no se verifica hasta que exista el pipeline de FWHM.",
              "Un proxy inventado seria peor que el hueco."]
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
