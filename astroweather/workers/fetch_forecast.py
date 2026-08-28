"""Deterministic forecast ingestion, hybrid ECMWF (SPEC v8 section 3.1).

Two calls, because no single Open-Meteo model serves everything we need
(verified 2026-08-27):

* `ecmwf_ifs` (~9 km) gives surface, clouds and boundary layer height -- the
  0.25 deg model returns null for BLH -- but has NO pressure levels.
* `ecmwf_ifs025` (+ ICON, GFS, Meteo-France) gives the pressure-level profiles
  the seeing and Richardson code needs.

Both calls carry `&elevation=1650` so surface variables are downscaled to the
observatory instead of to the provider's DEM cell, and `&wind_speed_unit=ms`
so the numbers match every formula in the spec.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any

import requests

from ..archive import store_raw, utcnow, write_canonical
from ..invariants import check_elevation_request, check_wind_units
from ..site import NERPIO, Site

ENDPOINT = "https://api.open-meteo.com/v1/forecast"
LICENSE_TAG = "open-meteo-cc-by-nc"

SURFACE_MODEL = "ecmwf_ifs"
SURFACE_VARS = (
    "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high", "cloud_cover",
    "temperature_2m", "relative_humidity_2m", "dew_point_2m",
    "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
    "precipitation_probability", "precipitation", "cape",
    "boundary_layer_height", "surface_pressure", "pressure_msl", "snow_depth",
)

PROFILE_MODELS = ("ecmwf_ifs025", "icon_seamless", "gfs_seamless", "meteofrance_seamless")
PROFILE_VARS = (
    # cloud_cover (total) va aqui a proposito desde el 2026-08-28: el
    # calibrador se entreno con el retroanalisis de ecmwf_ifs025 y el producto
    # venia consumiendo el de ecmwf_ifs, que es otro modelo. Sin esta variable,
    # el modelo con el que se calibro no sirve nubes en vivo y la unica salida
    # era aplicar la curva a numeros que no son suyos.
    "cloud_cover",
    "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
    "temperature_850hPa", "temperature_700hPa", "temperature_500hPa",
    "wind_speed_500hPa", "wind_speed_300hPa", "wind_speed_250hPa", "wind_speed_200hPa",
    "wind_direction_700hPa", "wind_direction_500hPa",
    "geopotential_height_700hPa", "geopotential_height_500hPa", "geopotential_height_300hPa",
    "relative_humidity_250hPa",
)

# Open-Meteo variable -> canonical column
COLUMNS = {
    "cloud_cover_low": "cloud_low", "cloud_cover_mid": "cloud_mid",
    "cloud_cover_high": "cloud_high", "cloud_cover": "cloud_total",
    "temperature_2m": "t2m", "relative_humidity_2m": "rh2m", "dew_point_2m": "td2m",
    "wind_speed_10m": "wind10m", "wind_gusts_10m": "gust10m",
    "wind_direction_10m": "wind_dir10m",
    "precipitation_probability": "prob_precip", "precipitation": "precipitation",
    "cape": "cape", "boundary_layer_height": "blh",
    "surface_pressure": "surface_pressure", "pressure_msl": "pressure_msl",
    "snow_depth": "snow_depth",
    "temperature_850hPa": "t850", "temperature_700hPa": "t700", "temperature_500hPa": "t500",
    "wind_speed_500hPa": "v500", "wind_speed_300hPa": "v300",
    "wind_speed_250hPa": "v250", "wind_speed_200hPa": "v200",
    "wind_direction_700hPa": "dir700", "wind_direction_500hPa": "dir500",
    "geopotential_height_700hPa": "gph700", "geopotential_height_500hPa": "gph500",
    "geopotential_height_300hPa": "gph300", "relative_humidity_250hPa": "rh250",
}

RESOLUTION_KM = {
    "ecmwf_ifs": 9.0, "ecmwf_ifs025": 25.0, "icon_seamless": 11.0,
    "gfs_seamless": 25.0, "meteofrance_seamless": 5.0,
}


def _request(site: Site, variables, models, days: int) -> dict[str, Any]:
    params = {
        "latitude": site.latitude,
        "longitude": site.longitude,
        "elevation": site.site_elevation,   # invariant 2
        "hourly": ",".join(variables),
        "models": ",".join(models),
        "wind_speed_unit": "ms",            # invariant 1
        "timezone": "UTC",
        "forecast_days": days,
    }
    response = requests.get(ENDPOINT, params=params, timeout=90)
    check_elevation_request(response.url)
    response.raise_for_status()
    return response.json()


def _rows(payload: dict, site: Site, models, retrieved_at: datetime) -> list[dict]:
    hourly = payload["hourly"]
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in hourly["time"]]
    run_utc = times[0]
    single = len(models) == 1
    rows: list[dict] = []

    for model in models:
        row_by_time: dict[datetime, dict] = {}
        for variable, column in COLUMNS.items():
            key = variable if single else f"{variable}_{model}"
            series = hourly.get(key)
            if series is None:
                continue
            if column in ("wind10m", "gust10m", "v500", "v300", "v250", "v200"):
                check_wind_units(series, label=f"{model}:{column}")
            for index, valid in enumerate(times):
                value = series[index]
                if value is None:
                    continue
                row_by_time.setdefault(valid, {})[column] = float(value)

        for valid, values in row_by_time.items():
            rows.append(
                {
                    "site_id": site.site_id,
                    "model": model,
                    "run_utc": run_utc,
                    "valid_utc": valid,
                    "lead_h": int((valid - run_utc).total_seconds() // 3600),
                    "member": None,
                    **values,
                    "provider_model_version": model,
                    "provider_grid_lat": payload.get("latitude"),
                    "provider_grid_lon": payload.get("longitude"),
                    "site_elevation": site.site_elevation,
                    "model_grid_elevation": site.model_grid_elevation,
                    "downscaling_elevation": payload.get("elevation"),
                    "native_resolution_km": RESOLUTION_KM.get(model),
                    "native_timestep_minutes": 60,
                    "interpolated": False,
                    "retrieved_at": retrieved_at,
                    "license_tag": LICENSE_TAG,
                }
            )
    return rows


def run(site: Site = NERPIO, *, days: int = 7) -> dict[str, int]:
    retrieved_at = utcnow()
    counts: dict[str, int] = {}

    surface = _request(site, SURFACE_VARS, [SURFACE_MODEL], days)
    store_raw(surface, source="openmeteo_surface", site_id=site.site_id,
              retrieved_at=retrieved_at)
    surface_rows = _rows(surface, site, [SURFACE_MODEL], retrieved_at)
    write_canonical(surface_rows, dataset="forecasts", site_id=site.site_id)
    counts[SURFACE_MODEL] = len(surface_rows)

    profiles = _request(site, PROFILE_VARS, PROFILE_MODELS, days)
    store_raw(profiles, source="openmeteo_profiles", site_id=site.site_id,
              retrieved_at=retrieved_at)
    profile_rows = _rows(profiles, site, PROFILE_MODELS, retrieved_at)
    write_canonical(profile_rows, dataset="forecasts", site_id=site.site_id)
    for model in PROFILE_MODELS:
        counts[model] = sum(1 for row in profile_rows if row["model"] == model)

    print("[fetch_forecast] " + ", ".join(f"{m}={n}" for m, n in counts.items()))
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    run(days=args.days)
