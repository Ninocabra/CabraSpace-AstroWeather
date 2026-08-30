"""Canonical read views over the archive (SPEC v8 sections 4.1 and 4.3).

Writers append; they never update. That keeps the archive honest but means the
reader is responsible for two things:

* Deduplication. The AstroCamp scraper polls faster than the sensors publish,
  so the same reading is archived several times. The natural key is
  (instrument_id, t_utc) for measurements and (model, run_utc, valid_utc,
  member) for forecasts -- not the row itself, since provenance columns differ
  between fetches of the same value.
* Night framing. The product only ever talks about sunset-1h to sunrise+1h,
  and a "night" spans two calendar dates, so the observing night is labelled
  by the date it started.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

DATA_ROOT = Path(__file__).resolve().parent.parent / "data"


def connect(root: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open a connection with deduplicated views over the Parquet archive."""
    base = (root or DATA_ROOT).as_posix()
    connection = duckdb.connect()
    # Everything in the archive is UTC; without this DuckDB hands rows back in
    # the machine's local zone and joins between tables silently miss by hours.
    connection.execute("SET TimeZone = 'UTC'")

    connection.execute(f"""
        CREATE VIEW measurements AS
        SELECT * EXCLUDE (rank) FROM (
            SELECT *, row_number() OVER (
                PARTITION BY instrument_id, t_utc
                -- Newest pipeline first: a method change (moments -> Gaussian
                -- fit for FWHM) must not leave two measurements of the same
                -- frame competing in one median.
                ORDER BY pipeline_version DESC, retrieved_at DESC
            ) AS rank
            FROM read_parquet('{base}/measurements/**/*.parquet', union_by_name=true)
        ) WHERE rank = 1
    """)

    connection.execute(f"""
        CREATE VIEW forecasts AS
        SELECT * EXCLUDE (rank) FROM (
            SELECT *, row_number() OVER (
                PARTITION BY model, run_utc, valid_utc, member
                ORDER BY retrieved_at DESC
            ) AS rank
            FROM read_parquet('{base}/forecasts/**/*.parquet', union_by_name=true)
        ) WHERE rank = 1
    """)

    try:
        connection.execute(f"""
            CREATE VIEW daily_observations AS
            SELECT * EXCLUDE (rank) FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY station_id, date ORDER BY retrieved_at DESC
                ) AS rank
                FROM read_parquet('{base}/daily_observations/**/*.parquet',
                                  union_by_name=true)
            ) WHERE rank = 1
        """)
    except duckdb.IOException:
        pass          # no external observations archived yet

    # An observing night is labelled by the date it began, so everything from
    # dusk to dawn belongs to the same night rather than splitting at midnight.
    connection.execute("""
        CREATE VIEW nights AS
        SELECT
            CAST(CASE WHEN hour(t_utc) < 12 THEN t_utc - INTERVAL 1 DAY
                      ELSE t_utc END AS DATE) AS night,
            -- Igual que arriba: la magnitud del cielo es la del FOTOMETRO.
            -- La allsky escribe en la misma columna una magnitud instrumental
            -- con punto cero arbitrario, y mezcladas no significan nada.
            count(*) FILTER (WHERE sky_mag_raw IS NOT NULL
                             AND instrument_type = 'TESS') AS sky_samples,
            median(sky_mag_raw) FILTER (WHERE instrument_type = 'TESS')
                AS sky_mag_median,
            stddev(sky_mag_raw) FILTER (WHERE instrument_type = 'TESS')
                AS sky_mag_std,
            min(t_sky_ir) AS sky_ir_min,
            bool_or(roof_open) AS any_roof_open
        FROM measurements
        WHERE hour(t_utc) >= 21 OR hour(t_utc) <= 5
        GROUP BY night
    """)
    return connection


# Valores de respaldo, y son SUPUESTOS, no medidas. Son los que el producto
# usaba como constantes hasta el 2026-08-28, cuando se descubrio que la
# prevision de las dos cosas llevaba meses archivada y sin conectar.
FALLBACK_AOD_TOTAL = 0.07
FALLBACK_AOD_DUST = 0.01
FALLBACK_JET_MS = 25.0


def night_inputs(start, end, site_id: str = "nerpio",
                 root: Path | None = None) -> dict:
    """Aerosol y viento en altura PREVISTOS para una ventana de noche.

    Las dos cosas se pronostican y se archivan desde hace meses -- CAMS da AOD
    de futuro y el ECMWF da los niveles de presion -- y hasta hoy el producto
    usaba en su lugar dos constantes por defecto que nadie alimentaba. Peor:
    estaban elegidas para ser plausibles, asi que el fallo no se veia. La noche
    del 28-08-2026 el jet previsto era 17,8 m/s contra la constante de 25,0, y
    variaba de 24,0 a las 21:00 a 13,4 a las 06:00.

    Cada valor viene con su procedencia, porque un supuesto y una prevision no
    se publican igual.
    """
    result = {"aod_total": FALLBACK_AOD_TOTAL, "aod_dust": FALLBACK_AOD_DUST,
              "jet_speed": FALLBACK_JET_MS, "aod_source": "supuesto",
              "jet_source": "supuesto"}
    try:
        connection = connect(root)
    except Exception:
        return result                     # sin archivo, los supuestos y a decirlo

    try:
        row = connection.execute("""
            SELECT median(aod_total), median(aod_dust), count(*) FROM (
                SELECT aod_total, aod_dust FROM forecasts
                WHERE site_id = ? AND valid_utc BETWEEN ? AND ?
                  AND aod_total IS NOT NULL
                QUALIFY row_number() OVER (
                    PARTITION BY valid_utc ORDER BY run_utc DESC) = 1)
        """, [site_id, start, end]).fetchone()
        if row and row[2]:
            result["aod_total"] = float(row[0])
            result["aod_dust"] = float(row[1]) if row[1] is not None else FALLBACK_AOD_DUST
            result["aod_source"] = "cams"
    except Exception:
        pass

    try:
        # El "jet" del perfil Hufnagel-Valley es el viento de la capa del chorro,
        # o sea el MAYOR de los niveles altos, no el de uno elegido a dedo. Se
        # toma por hora y se resume con la mediana de la noche.
        row = connection.execute("""
            SELECT median(jet), count(*) FROM (
                SELECT greatest(coalesce(v200, 0), coalesce(v250, 0),
                                coalesce(v300, 0)) AS jet
                FROM forecasts
                WHERE site_id = ? AND valid_utc BETWEEN ? AND ?
                  AND (v200 IS NOT NULL OR v250 IS NOT NULL OR v300 IS NOT NULL)
                QUALIFY row_number() OVER (
                    PARTITION BY valid_utc ORDER BY run_utc DESC) = 1)
        """, [site_id, start, end]).fetchone()
        if row and row[1] and row[0]:
            result["jet_speed"] = float(row[0])
            result["jet_source"] = "ecmwf"
    except Exception:
        pass
    return result


def summary(root: Path | None = None) -> dict[str, int]:
    """Row counts after deduplication -- what the archive actually holds."""
    connection = connect(root)
    result = {}
    for view in ("measurements", "forecasts", "nights"):
        result[view] = connection.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
    try:
        result["daily_observations"] = connection.execute(
            "SELECT count(*) FROM daily_observations"
        ).fetchone()[0]
    except duckdb.CatalogException:
        pass
    return result
