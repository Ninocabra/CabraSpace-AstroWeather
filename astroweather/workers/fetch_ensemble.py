"""Archive the ECMWF EPS members (SPEC v8 sections 3.1 and 6.1).

This is the most time-critical worker in the system: Open-Meteo keeps the
individual ensemble members for roughly 4-5 days (verified 2026-08-27 with
past_days=35, where member25 returned nulls beyond that window). Whatever is
not archived on the day is gone for good, and with it the ability to ever
recalibrate the probabilistic forecast retrospectively.

We therefore store all 51 raw member values per (run, valid_time), not the
derived P_raw alone: the definition of "clear" may well change.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any

import requests

from ..archive import store_raw, utcnow, write_canonical
from ..invariants import check_eps_members
from ..normalize import PRECIP_CLAMPED_FLAG, clamp_small_negative
from ..site import NERPIO, Site

ENDPOINT = "https://ensemble-api.open-meteo.com/v1/ensemble"
MODEL = "ecmwf_ifs025"
VARIABLES = ("cloud_cover", "precipitation")
# Cloud cover below this counts as "clear" for P_raw. Stored alongside the raw
# members so the threshold can be revisited without losing data.
CLEAR_THRESHOLD = 20.0
NATIVE_TIMESTEP_MINUTES = 180  # EPS native step; hourly output is interpolated
LICENSE_TAG = "open-meteo-cc-by-nc"


def fetch(site: Site, *, forecast_days: int = 7) -> dict[str, Any]:
    response = requests.get(
        ENDPOINT,
        params={
            "latitude": site.latitude,
            "longitude": site.longitude,
            "elevation": site.site_elevation,
            "hourly": ",".join(VARIABLES),
            "models": MODEL,
            "wind_speed_unit": "ms",
            "timezone": "UTC",
            "forecast_days": forecast_days,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def to_rows(payload: dict[str, Any], site: Site, retrieved_at: datetime) -> list[dict]:
    hourly = payload["hourly"]
    check_eps_members(hourly.keys(), variable="cloud_cover")

    times = [
        datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        for value in hourly["time"]
    ]
    # The ensemble API does not expose the run timestamp; the first valid hour
    # is our best proxy and is stored as such.
    run_utc = times[0]

    def series(variable: str, member: int) -> list[float | None]:
        key = variable if member == 0 else f"{variable}_member{member:02d}"
        return hourly.get(key) or [None] * len(times)

    rows: list[dict] = []
    clamped = 0
    for member in range(EPS_MEMBERS := 51):
        clouds = series("cloud_cover", member)
        precip = series("precipitation", member)
        for index, valid in enumerate(times):
            cloud = clouds[index]
            if cloud is None:
                continue
            # Open-Meteo da a veces -0.1 mm en algun miembro (artefacto
            # numerico). Un negativo pequeno se recorta a 0 y se marca; uno
            # grande sigue llegando al validador de escritura.
            rain, was_clamped = clamp_small_negative(precip[index])
            clamped += was_clamped
            rows.append(
                {
                    "site_id": site.site_id,
                    "model": MODEL,
                    "run_utc": run_utc,
                    "valid_utc": valid,
                    "lead_h": int((valid - run_utc).total_seconds() // 3600),
                    "member": member,
                    "cloud_total": float(cloud),
                    "precipitation": rain,
                    "provider_model_version": MODEL,
                    "provider_grid_lat": payload.get("latitude"),
                    "provider_grid_lon": payload.get("longitude"),
                    "site_elevation": site.site_elevation,
                    "model_grid_elevation": site.model_grid_elevation,
                    "downscaling_elevation": payload.get("elevation"),
                    "native_resolution_km": 25.0,
                    "native_timestep_minutes": NATIVE_TIMESTEP_MINUTES,
                    "interpolated": True,
                    "retrieved_at": retrieved_at,
                    "license_tag": LICENSE_TAG,
                    "quality_flags": PRECIP_CLAMPED_FLAG if was_clamped else None,
                }
            )
    if clamped:
        print(f"[fetch_ensemble] {clamped} precipitation values in "
              f"[-0.5, 0) clamped to 0.0 (provider numerical artifact)")
    return rows


def p_raw(rows: list[dict]) -> dict[datetime, float]:
    """Bare ensemble fraction of clear members. NOT a calibrated probability."""
    tally: dict[datetime, list[int]] = {}
    for row in rows:
        clear = row["cloud_total"] < CLEAR_THRESHOLD
        tally.setdefault(row["valid_utc"], []).append(int(clear))
    return {valid: sum(flags) / len(flags) for valid, flags in tally.items()}


def run(site: Site = NERPIO, *, forecast_days: int = 7) -> int:
    retrieved_at = utcnow()
    payload = fetch(site, forecast_days=forecast_days)
    store_raw(payload, source="openmeteo_ensemble", site_id=site.site_id,
              retrieved_at=retrieved_at)
    rows = to_rows(payload, site, retrieved_at)
    write_canonical(rows, dataset="forecasts", site_id=site.site_id)
    probabilities = p_raw(rows)
    print(
        f"[fetch_ensemble] {len(rows)} member-hours archived, "
        f"{len(probabilities)} valid hours, "
        f"P_raw(first)={next(iter(probabilities.values())):.2f}"
    )
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    run(forecast_days=args.days)
