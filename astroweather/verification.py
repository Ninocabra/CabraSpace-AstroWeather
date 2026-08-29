"""Forecast verification against ground truth (SPEC v8 section 4.3).

The rule this module exists to enforce: every claim the system makes is scored
against two baselines that cost nothing to beat if the forecast is any good --
persistence ("tonight like last night") and climatology ("what August usually
does here"). A model that cannot beat them is not adding information, and the
panel says so out loud.

What can be verified today, with the archive as it stands:

* Clouds: NOT HERE ANY MORE, see `scoreboard.py`. This module scored them
  against `measurements.clear_flag`, and that column dies in 2021 -- the TESS
  cloud index needs tamb and tsky, and AstroCamp's station has published a
  literal zero for both ever since. Against forecasts that start in 2024 the
  join left 51 rows out of 22000, all from two days of a different sensor, and
  the panel printed Brier=0.000 as if it were perfect skill. The section now
  refuses to score on that little and points at the scoreboard, which uses the
  photometer itself as truth (6822 hours of overlap instead of 51).
* Surface temperature, against the AEMET station 11.5 km away, which is
  external ground truth and labelled as such.

Seeing is deliberately absent: without the FWHM pipeline there is nothing to
verify it against, and inventing a proxy would be worse than the gap.
"""

from __future__ import annotations

from datetime import date

from .queries import connect

CLEAR_THRESHOLD = 20.0        # cloud cover below this counts as clear
# A Brier over a handful of hours from one clear spell is not a score. This is
# the guard that stops the panel from announcing skill it does not have.
MIN_CLOUD_PAIRS = 200


def cloud_scores(start: date | None = None, root=None,
                 min_pairs: int = MIN_CLOUD_PAIRS) -> list[dict]:
    """Brier score per model and lead time against the observatory's verdict.

    Lower is better; 0.25 is what a constant 50 % guess scores. `min_pairs`
    exists so a unit test can score a ten-hour fixture without lowering the bar
    for the panel, which needs MIN_CLOUD_PAIRS before it says anything.
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
        HAVING count(*) >= {min_pairs}
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


# Pares minimos antes de dejar que la medida del viento corrija nada. Es el
# mismo criterio que MIN_CLOUD_PAIRS y por la misma razon: una correccion de
# SEGURIDAD sacada de una veintena de horas es ruido con autoridad.
MIN_WIND_PAIRS = 200


def wind_scores(root=None) -> list[dict]:
    """Sesgo del viento MEDIO previsto contra la estacion del sitio.

    La racha no tiene verdad terreno en ninguna fuente -- ni AstroCamp, que
    publica viento medio, ni las tres estaciones AEMET cercanas, donde `vv`,
    `vmax` y `dv` vienen vacios (verificado el 29-08-2026 sobre el endpoint
    horario). Y la racha es la que alimenta la puerta de seguridad.

    Lo que si se puede medir es el viento medio, y lo que sale importa: el
    modelo se queda CORTO. Con n=29 el sesgo es -1,80 m/s sobre una media
    medida de 3,99 -- prevee el 55 % del viento real. Si la racha comparte ese
    sesgo, la puerta avisa tarde, que es el peor sentido en el que puede
    fallar una puerta de seguridad.
    """
    connection = connect(root)
    try:
        rows = connection.execute("""
            WITH medido AS (
                SELECT site_id, date_trunc('hour', t_utc) AS hora,
                       median(wind) AS viento
                FROM measurements
                WHERE wind IS NOT NULL AND source = 'astrocamp_meteo'
                GROUP BY 1, 2
            ),
            previsto AS (
                SELECT site_id, valid_utc AS hora, model,
                       median(wind10m) AS viento
                FROM forecasts WHERE wind10m IS NOT NULL AND member IS NULL
                GROUP BY 1, 2, 3
            )
            SELECT p.model, count(*) AS n,
                   avg(p.viento - m.viento) AS bias,
                   sqrt(avg(pow(p.viento - m.viento, 2))) AS rmse,
                   avg(m.viento) AS observado,
                   avg(p.viento) AS previsto
            FROM previsto p JOIN medido m USING (site_id, hora)
            GROUP BY p.model ORDER BY n DESC
        """).fetchall()
    except Exception:
        return []
    return [{"model": r[0], "n": r[1], "bias": r[2], "rmse": r[3],
             "observed": r[4], "forecast": r[5],
             # Cuanto del viento real llega a prever. >1 es que se pasa.
             "ratio": (r[5] / r[4]) if r[4] else None,
             "trustworthy": r[1] >= MIN_WIND_PAIRS}
            for r in rows]


def wind_correction(root=None, model: str | None = None) -> dict:
    """El factor por el que multiplicar la racha prevista, si se puede.

    Devuelve `factor=1.0` mientras no haya pares suficientes, y dice por que.
    No se fuerza: una correccion sacada de 29 horas de agosto no sabe nada del
    viento de enero, y esto decide si se abre un techo.
    """
    puntuaciones = [s for s in wind_scores(root)
                    if model is None or s["model"] == model]
    if not puntuaciones:
        return {"factor": 1.0, "n": 0, "applied": False,
                "why": "sin pares de viento previsto contra medido"}
    mejor = max(puntuaciones, key=lambda s: s["n"])
    if not mejor["trustworthy"] or not mejor["ratio"]:
        return {"factor": 1.0, "n": mejor["n"], "applied": False,
                "ratio": mejor["ratio"], "bias": mejor["bias"],
                "why": (f"solo {mejor['n']} pares, hacen falta {MIN_WIND_PAIRS}: "
                        f"se mide un sesgo de {mejor['bias']:+.2f} m/s pero NO se "
                        f"corrige, y la puerta de viento queda sin calibrar")}
    return {"factor": 1.0 / mejor["ratio"], "n": mejor["n"], "applied": True,
            "ratio": mejor["ratio"], "bias": mejor["bias"],
            "why": (f"medido sobre {mejor['n']} horas: el modelo prevee el "
                    f"{mejor['ratio']*100:.0f} % del viento medio real")}


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
        lines.append(f"    NO se puntua: clear_flag muere en 2021 (el sensor de AstroCamp")
        lines.append(f"    publica tamb=tsky=0) y las predicciones empiezan en 2024, asi que")
        lines.append(f"    no hay ni {MIN_CLOUD_PAIRS} pares. El marcador bueno usa el propio")
        lines.append(f"    fotometro como verdad: python -m astroweather.scoreboard")

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
