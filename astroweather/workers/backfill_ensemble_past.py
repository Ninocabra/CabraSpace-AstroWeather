"""Rescue the EPS members of the hours already gone, via `past_days`.

WHY THIS EXISTS. `archivo-eps.yml` failed from 2026-11-11 to 2026-11-14 (a
-0.1 mm precipitation artifact, fixed in `normalize.py`). The runs that were
missed cannot be fetched again: the Ensemble API has no run selector (probed
2026-11-14: `run=<any 00Z/12Z of the last 5 days, even today's 00Z>` answers
400 "The requested model run is not available"; `start_date`/`end_date` are
mutually exclusive with `forecast_days`). What the API does keep is
`past_days` (maximum 93), and for the last ~4-5 days it still serves the 51
members; before that every member is null (measured 2026-11-14: the first hour
with members is 2026-11-10T00:00, everything older is null).

WHAT COMES BACK IS NOT A RUN. For each past valid hour the API returns one
value per member from "the latest run that covers that hour" -- a stitched
series of successive runs' short leads. It is not what any run published at
+24 h or +72 h, and it differs a lot from the archived runs (measured against
the 2026-11-10T23:43Z run: over the common hours, the median of the largest
member-to-member cloud difference is 49 points).
So the rows must never enter lead-time statistics or the calibration of P_raw
by run. How they are kept apart (every point is checked by the tests):

* `model = ecmwf_ifs025_pastdays`, not `ecmwf_ifs025`. Every consumer that
  reads the EPS (`asof.raw_ensemble_asof`, `hindcast`, `roofcalibration`,
  `aerosolfit`, `calibration`) filters `model = 'ecmwf_ifs025'` exactly or
  `starts_with(model, 'ecmwf_ifs025_prev')`; none matches this name.
* `lead_h = NULL`: the lead of each hour is unknown and not recoverable.
* `run_utc` = the real request time truncated to the hour, NOT a guessed
  initialisation. It is later than `valid_utc` for every stored row, so a
  `run_utc <= valid_utc` filter excludes them and a naive `valid - run`
  gives a negative number instead of a plausible-looking lead.
* `retrieved_at` = the real request time (it is also what `asof` uses as the
  availability instant).
* `quality_flags = 'backfill_past_days;lead_unknown;stitched_latest_run'`
  (plus `precip_clamped_to_zero` when it applies).
* Only PAST hours are written (valid < request hour): the future hours are the
  ordinary latest run, which `fetch_ensemble` archives with its real label.

The raw response lives under its own source, `openmeteo_ensemble_past_days`.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ..archive import store_raw, utcnow, write_canonical
from ..site import NERPIO, Site
from . import fetch_ensemble as eps

RAW_SOURCE = "openmeteo_ensemble_past_days"
BACKFILL_MODEL = f"{eps.MODEL}_pastdays"
BACKFILL_FLAGS = "backfill_past_days;lead_unknown;stitched_latest_run"
MAX_PAST_DAYS = 93          # measured 2026-11-14: 94+ answers 400 "Allowed range 0 to 93"


def fetch(site: Site, *, past_days: int = MAX_PAST_DAYS, forecast_days: int = 7) -> dict[str, Any]:
    response = requests.get(
        eps.ENDPOINT,
        params={
            "latitude": site.latitude,
            "longitude": site.longitude,
            "elevation": site.site_elevation,
            "hourly": ",".join(eps.VARIABLES),
            "models": eps.MODEL,
            "wind_speed_unit": "ms",
            "timezone": "UTC",
            "forecast_days": forecast_days,
            "past_days": past_days,
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def to_backfill_rows(payload: dict[str, Any], site: Site, retrieved_at: datetime) -> list[dict]:
    """Rows of the past hours, marked as reconstructed (see the module docstring).

    Reuses `fetch_ensemble.to_rows` so the member parsing, the clamp of tiny
    negative precipitation and the EPS-member invariant are the same code.
    Hours where the members are null come out already skipped there.
    """
    run_utc = retrieved_at.replace(minute=0, second=0, microsecond=0)
    rows = []
    for row in eps.to_rows(payload, site, retrieved_at):
        if row["valid_utc"] >= run_utc:
            continue
        flags = BACKFILL_FLAGS
        if row["quality_flags"]:
            flags = f"{flags};{row['quality_flags']}"
        row.update(model=BACKFILL_MODEL, run_utc=run_utc, lead_h=None,
                   quality_flags=flags)
        rows.append(row)
    return rows


def _retrieved_from_name(path: Path) -> datetime:
    match = re.match(r"(\d{8}T\d{6})Z_", path.name)
    if not match:
        raise ValueError(f"cannot read the request time from {path.name}")
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def run(site: Site = NERPIO, *, past_days: int = MAX_PAST_DAYS,
        from_raw: Path | None = None, root: Path | None = None) -> int:
    """Fetch (or re-read a saved raw), keep the raw, write the flagged rows."""
    if from_raw is not None:
        with gzip.open(from_raw, "rb") as handle:
            payload = json.loads(handle.read())
        retrieved_at = _retrieved_from_name(from_raw)
    else:
        retrieved_at = utcnow()
        payload = fetch(site, past_days=past_days)
        # The raw first: whatever fails later, the response is not lost again.
        store_raw(payload, source=RAW_SOURCE, site_id=site.site_id,
                  retrieved_at=retrieved_at, root=root)
    rows = to_backfill_rows(payload, site, retrieved_at)
    write_canonical(rows, dataset="forecasts", site_id=site.site_id, root=root)
    hours = sorted({row["valid_utc"] for row in rows})
    print(f"[backfill_ensemble_past] {len(rows)} member-hours as {BACKFILL_MODEL}, "
          f"{len(hours)} valid hours"
          + (f" ({hours[0]:%Y-%m-%dT%H}Z .. {hours[-1]:%Y-%m-%dT%H}Z)" if hours else ""))
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--past-days", type=int, default=MAX_PAST_DAYS)
    parser.add_argument("--from-raw", type=Path,
                        help="rebuild the rows from an already saved raw file")
    args = parser.parse_args()
    run(past_days=args.past_days, from_raw=args.from_raw)
