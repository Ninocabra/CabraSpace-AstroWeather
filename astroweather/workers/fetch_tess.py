"""TESS photometer ingestion, live and historical (SPEC v8 sections 3.1 and 4.1).

The STARS4ALL photometer at AstroCamp (stars289) is the ground truth this
project leans on: sky brightness AND an IR sky temperature that works as a
cloud detector, once a minute, since 2019 (~450k samples/year, verified).

Two things this module refuses to do:

* Call the reading "SQM". TESS and SQM have different spectral responses, so
  the raw value is stored with its instrument and bandpass, and any SQM-scale
  equivalence has to be an explicit, versioned calibration.
* Trust a live-looking value. Stations emit mag=0 during daylight and some emit
  a stuck 13.25 forever, so every reading passes the plausibility filter and
  the sky magnitude is nulled -- not the row dropped -- when it fails, because
  the IR cloud signal in the same row is still good.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import requests

from ..archive import store_raw, utcnow, write_canonical
from ..invariants import is_plausible_sky_mag
from ..site import NERPIO, Site

GRAFANA_URL = "https://tess.dashboards.stars4all.eu/api/tsdb/query"
DATASOURCE_ID = 2          # shared InfluxDB behind every stars* dashboard
MEASUREMENT = "mqtt_consumer"
FIELDS = ("mag", "tamb", "tsky")
DEFAULT_STATION = "stars289"
HISTORY_START = datetime(2019, 1, 1, tzinfo=timezone.utc)
LICENSE_TAG = "stars4all-public-dashboard"
# The vendor's own cloud index, as used by the official STARS4ALL panels:
# 100 - 3*(tamb - tsky); below 40 counts as clear.
CLEAR_INDEX_THRESHOLD = 40.0


def query(sql: str, *, since_days: int = 3000, timeout: int = 180) -> list[dict[str, Any]]:
    response = requests.post(
        GRAFANA_URL,
        json={
            "from": f"now-{since_days}d",
            "to": "now",
            "queries": [
                {
                    "refId": "A",
                    "datasourceId": DATASOURCE_ID,
                    "rawQuery": True,
                    "query": sql,
                    "resultFormat": "time_series",
                }
            ],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["results"]["A"].get("series") or []


def _window_sql(station: str, start: datetime, end: datetime) -> str:
    fields = ",".join(f'"{name}"' for name in FIELDS)
    return (
        f'SELECT {fields} FROM "{MEASUREMENT}" '
        f"WHERE (\"name\"='{station}') "
        f"AND time >= '{start:%Y-%m-%dT%H:%M:%SZ}' AND time < '{end:%Y-%m-%dT%H:%M:%SZ}'"
    )


def _to_rows(series: list[dict], site: Site, station: str, retrieved_at: datetime) -> list[dict]:
    """Zip the per-field series (they share an index) into canonical rows."""
    by_field = {s["name"].split(".")[-1]: s["points"] for s in series}
    if "mag" not in by_field:
        return []

    rows: list[dict] = []
    for index, (magnitude, epoch) in enumerate(by_field["mag"]):
        ambient = by_field.get("tamb", [])[index][0] if "tamb" in by_field else None
        sky = by_field.get("tsky", [])[index][0] if "tsky" in by_field else None

        # Not every TESS reports temperatures. stars289 (AstroCamp) publishes a
        # literal 0 for both tamb and tsky (verified 2026-08-27; stars1/4/1000
        # do report real values), so its cloud index would be a constant 100 --
        # "overcast" forever. Treat the double zero as missing: at AstroCamp the
        # cloud signal comes from the CloudWatchers in cloud.html, not from here.
        if ambient == 0 and sky == 0:
            ambient = sky = None

        clear_flag = None
        cloud_index = None
        if ambient is not None and sky is not None:
            cloud_index = 100.0 - 3.0 * (ambient - sky)
            clear_flag = cloud_index < CLEAR_INDEX_THRESHOLD

        rows.append(
            {
                "site_id": site.site_id,
                "t_utc": datetime.fromtimestamp(epoch, timezone.utc),
                # Nulled when implausible (daylight zeros, stuck sensors); the
                # row survives because its cloud signal is still meaningful.
                "sky_mag_raw": float(magnitude) if is_plausible_sky_mag(magnitude) else None,
                "instrument_type": "TESS",
                "instrument_id": station,
                "bandpass_id": "TESS-W",
                "t_sky_ir": float(sky) if sky is not None else None,
                # Semantics matter: this is the raw MQTT tsky field, not a
                # broadband radiative temperature (SPEC v8 section 2.6).
                "t_sky_ir_semantics": "tess_mqtt_tsky_raw",
                "t2m": float(ambient) if ambient is not None else None,
                "clear_flag": clear_flag,
                "cloud_frac": cloud_index,
                "source": "stars4all_tess",
                "retrieved_at": retrieved_at,
            }
        )
    return rows


def run_live(site: Site = NERPIO, *, station: str = DEFAULT_STATION,
             minutos: int = 90) -> dict:
    """La ultima hora y media del fotometro, para tenerlo AHORA y no ayer.

    El relleno por meses es para construir el archivo; esto es otra cosa. Hasta
    hoy la lectura mas reciente que teniamos era de mas de un dia antes, asi
    que el producto publicaba un fondo de cielo PREDICHO al lado de un
    fotometro que estaba midiendo el de verdad y al que no le preguntabamos.

    Mismo camino que el resto -- raw archivado, filas canonicas, filtro de
    plausibilidad -- porque una lectura en vivo no merece menos disciplina que
    una historica: precisamente en vivo es cuando llegan los ceros de dia y los
    13,25 clavados.
    """
    fin = utcnow()
    inicio = fin - timedelta(minutes=minutos)
    retrieved_at = fin
    series = query(_window_sql(station, inicio, fin), since_days=2, timeout=45)
    if not series:
        return {"filas": 0, "ultima": None, "mag": None}
    store_raw(series, source=f"tess_{station}_live", site_id=site.site_id,
              retrieved_at=retrieved_at)
    filas = _to_rows(series, site, station, retrieved_at)
    write_canonical(filas, dataset="measurements", site_id=site.site_id)
    validas = [f for f in filas if f["sky_mag_raw"] is not None]
    ultima = max(validas, key=lambda f: f["t_utc"]) if validas else None
    return {"filas": len(filas), "validas": len(validas),
            "ultima": ultima["t_utc"].isoformat() if ultima else None,
            "mag": round(float(ultima["sky_mag_raw"]), 2) if ultima else None}


def _months(start: datetime, end: datetime) -> Iterator[tuple[datetime, datetime]]:
    cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cursor < end:
        following = (cursor + timedelta(days=32)).replace(day=1)
        yield cursor, min(following, end)
        cursor = following


def _already_done(site: Site, month: datetime, root: Path | None = None) -> bool:
    from ..archive import DATA_ROOT

    base = root or DATA_ROOT
    directory = (
        base / "measurements" / f"site={site.site_id}"
        / f"year={month:%Y}" / f"month={month:%m}"
    )
    return directory.exists() and any(directory.glob("*.parquet"))


def run(
    site: Site = NERPIO,
    *,
    station: str = DEFAULT_STATION,
    start: datetime | None = None,
    end: datetime | None = None,
    resume: bool = True,
    pause: float = 1.0,
) -> int:
    """Backfill (or top up) the photometer series, one month per request."""
    start = start or HISTORY_START
    end = end or utcnow()
    total = 0

    for month_start, month_end in _months(start, end):
        if resume and _already_done(site, month_start):
            print(f"[fetch_tess] {month_start:%Y-%m} ya archivado, salto")
            continue

        retrieved_at = utcnow()
        series = query(_window_sql(station, month_start, month_end))
        if not series:
            print(f"[fetch_tess] {month_start:%Y-%m} sin datos")
            continue

        store_raw(series, source=f"tess_{station}", site_id=site.site_id,
                  retrieved_at=retrieved_at)
        rows = _to_rows(series, site, station, retrieved_at)
        write_canonical(rows, dataset="measurements", site_id=site.site_id)
        valid = sum(1 for row in rows if row["sky_mag_raw"] is not None)
        total += len(rows)
        print(f"[fetch_tess] {month_start:%Y-%m}: {len(rows):>6} muestras "
              f"({valid} con mag valida)")
        time.sleep(pause)   # be a good citizen with a community service

    print(f"[fetch_tess] total {total} muestras archivadas")
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", default=DEFAULT_STATION)
    parser.add_argument("--live", action="store_true",
                        help="solo la ultima hora y media, para el panel")
    parser.add_argument("--start", help="YYYY-MM (por defecto 2019-01)")
    parser.add_argument("--months", type=int, help="solo N meses desde --start")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    begin = (
        datetime.strptime(args.start, "%Y-%m").replace(tzinfo=timezone.utc)
        if args.start else HISTORY_START
    )
    finish = None
    if args.months:
        finish = begin
        for _ in range(args.months):
            finish = (finish + timedelta(days=32)).replace(day=1)

    run(station=args.station, start=begin, end=finish, resume=not args.no_resume)
