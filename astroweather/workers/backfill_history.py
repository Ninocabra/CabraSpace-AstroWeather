"""Deterministic backfill by lead time (SPEC v8 sections 3.1 and 4.2).

The Previous Runs API answers the question the MOS needs: for a given hour,
what did the run from N days earlier predict? Verified to reach back at least
2.5 years for ecmwf_ifs025 -- so the bias correction starts with history
instead of waiting months for our own archive to fill.

Two things it is NOT:

* It is not the Historical Forecast API. That one stitches together the short
  leads of successive runs into a continuous "best estimate" series, which is
  useful but is not what any run actually published for +48 or +72 h.
* It is not probabilistic. Ensemble members are kept upstream for about four
  days, so the calibration of P_raw can only ever grow from our own archive.

The exact issue hour of each previous run is not published, so rows are stored
with the lead expressed in days and the run timestamp derived from it. That
approximation is recorded in quality_flags rather than hidden.
"""

from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta, timezone

import requests

from ..archive import store_raw, utcnow, write_canonical
from ..invariants import check_wind_units
from ..site import NERPIO, Site

ENDPOINT = "https://previous-runs-api.open-meteo.com/v1/forecast"
MODEL = "ecmwf_ifs025"
LICENSE_TAG = "open-meteo-cc-by-nc"
LEAD_DAYS = (1, 2, 3, 4, 5)

# Variables worth carrying back: the cloud gate and the surface fields that the
# AEMET station and our own sensors can verify.
VARIABLES = {
    "cloud_cover": "cloud_total",
    "cloud_cover_low": "cloud_low",
    "cloud_cover_mid": "cloud_mid",
    "cloud_cover_high": "cloud_high",
    "temperature_2m": "t2m",
    "relative_humidity_2m": "rh2m",
    "wind_speed_10m": "wind10m",
    "precipitation": "precipitation",
}


def _hourly_names() -> list[str]:
    names = []
    for variable in VARIABLES:
        names.append(variable)
        names.extend(f"{variable}_previous_day{day}" for day in LEAD_DAYS)
    return names


def fetch(site: Site, start: date, end: date) -> dict:
    response = requests.get(
        ENDPOINT,
        params={
            "latitude": site.latitude,
            "longitude": site.longitude,
            "elevation": site.site_elevation,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": ",".join(_hourly_names()),
            "models": MODEL,
            "wind_speed_unit": "ms",
            "timezone": "UTC",
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def to_rows(payload: dict, site: Site, retrieved_at: datetime) -> list[dict]:
    hourly = payload["hourly"]
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in hourly["time"]]

    rows: list[dict] = []
    for lead_days in LEAD_DAYS:
        by_time: dict[datetime, dict] = {}
        for variable, column in VARIABLES.items():
            series = hourly.get(f"{variable}_previous_day{lead_days}")
            if series is None:
                continue
            if column == "wind10m":
                check_wind_units(series, label=f"prev{lead_days}:{column}")
            for index, valid in enumerate(times):
                value = series[index]
                if value is not None:
                    by_time.setdefault(valid, {})[column] = float(value)

        for valid, values in by_time.items():
            # The provider does not publish the issue hour of each previous
            # run; 00Z of the day N days back is the closest honest guess.
            run_utc = (valid - timedelta(days=lead_days)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            rows.append({
                "site_id": site.site_id,
                "model": f"{MODEL}_prev{lead_days}d",
                "run_utc": run_utc,
                "valid_utc": valid,
                "lead_h": int((valid - run_utc).total_seconds() // 3600),
                "member": None,
                **values,
                "provider_model_version": MODEL,
                "provider_grid_lat": payload.get("latitude"),
                "provider_grid_lon": payload.get("longitude"),
                "site_elevation": site.site_elevation,
                "model_grid_elevation": site.model_grid_elevation,
                "downscaling_elevation": payload.get("elevation"),
                "native_resolution_km": 25.0,
                "native_timestep_minutes": 60,
                "interpolated": False,
                "retrieved_at": retrieved_at,
                "license_tag": LICENSE_TAG,
                "quality_flags": f"previous_runs;lead_days={lead_days};run_hour_assumed_00z",
            })
    return rows


def _months(start: date, end: date):
    cursor = start.replace(day=1)
    while cursor < end:
        following = (cursor + timedelta(days=32)).replace(day=1)
        yield cursor, min(following - timedelta(days=1), end)
        cursor = following


def run(site: Site = NERPIO, *, start: date, end: date | None = None,
        pause: float = 1.5) -> int:
    end = end or datetime.now(timezone.utc).date() - timedelta(days=1)
    total = 0

    for window_start, window_end in _months(start, end):
        retrieved_at = utcnow()
        try:
            payload = fetch(site, window_start, window_end)
        except requests.HTTPError as error:
            print(f"[backfill] {window_start:%Y-%m}: {error.response.status_code}, salto")
            time.sleep(pause)
            continue

        store_raw(payload, source="openmeteo_previous_runs", site_id=site.site_id,
                  retrieved_at=retrieved_at)
        rows = to_rows(payload, site, retrieved_at)
        write_canonical(rows, dataset="forecasts", site_id=site.site_id)
        total += len(rows)
        print(f"[backfill] {window_start:%Y-%m}: {len(rows):>5} filas "
              f"({len(LEAD_DAYS)} leads)")
        time.sleep(pause)      # shared 10k/day quota with every other endpoint

    print(f"[backfill] total {total} filas historicas")
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2024-01-01", help="YYYY-MM-DD")
    parser.add_argument("--end", help="YYYY-MM-DD")
    args = parser.parse_args()
    run(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end) if args.end else None,
    )
