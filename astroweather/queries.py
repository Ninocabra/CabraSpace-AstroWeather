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
            count(*) FILTER (WHERE sky_mag_raw IS NOT NULL) AS sky_samples,
            median(sky_mag_raw) AS sky_mag_median,
            stddev(sky_mag_raw) AS sky_mag_std,
            min(t_sky_ir) AS sky_ir_min,
            bool_or(roof_open) AS any_roof_open
        FROM measurements
        WHERE hour(t_utc) >= 21 OR hour(t_utc) <= 5
        GROUP BY night
    """)
    return connection


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
