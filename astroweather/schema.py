"""Canonical storage schema (SPEC v8 section 4.1).

Two levels, as decided in v6 after the EAV design was dropped:

* RAW      - the provider response, compressed and versioned, never rewritten.
* CANONICAL- one wide, typed row per (site, model, run, valid_time, member).

The provenance columns are not decoration: they are what lets us answer, two
years from now, whether a bad forecast came from the provider, our physics or a
broken sensor.
"""

from __future__ import annotations

import pyarrow as pa

# --- provenance -------------------------------------------------------------
# Attached to every canonical forecast row (SPEC v8 section 3.2, invariant 5).
PROVENANCE_FIELDS = [
    pa.field("provider_model_version", pa.string()),
    pa.field("provider_grid_lat", pa.float64()),
    pa.field("provider_grid_lon", pa.float64()),
    # The three elevations that must never be conflated (SPEC v8 section 0).
    pa.field("site_elevation", pa.float32()),
    pa.field("model_grid_elevation", pa.float32()),
    pa.field("downscaling_elevation", pa.float32()),
    pa.field("native_resolution_km", pa.float32()),
    pa.field("native_timestep_minutes", pa.int16()),
    pa.field("interpolated", pa.bool_()),
    pa.field("retrieved_at", pa.timestamp("s", tz="UTC")),
    pa.field("license_tag", pa.string()),
    pa.field("quality_flags", pa.string()),
    pa.field("pipeline_version", pa.string()),
]

# --- forecasts --------------------------------------------------------------
FORECAST_KEYS = [
    pa.field("site_id", pa.string()),
    pa.field("model", pa.string()),
    pa.field("run_utc", pa.timestamp("s", tz="UTC")),
    pa.field("valid_utc", pa.timestamp("s", tz="UTC")),
    pa.field("lead_h", pa.int16()),
    # NULL for deterministic runs; 0..50 for EPS members. Members are archived
    # raw on every download: upstream keeps them ~4-5 days only.
    pa.field("member", pa.int16()),
]

FORECAST_VALUES = [
    # surface
    pa.field("cloud_low", pa.float32()),
    pa.field("cloud_mid", pa.float32()),
    pa.field("cloud_high", pa.float32()),
    pa.field("cloud_total", pa.float32()),
    pa.field("t2m", pa.float32()),
    pa.field("rh2m", pa.float32()),
    pa.field("td2m", pa.float32()),
    pa.field("wind10m", pa.float32()),      # m/s always (invariant 1)
    pa.field("gust10m", pa.float32()),      # m/s
    pa.field("wind_dir10m", pa.float32()),
    pa.field("prob_precip", pa.float32()),
    pa.field("precipitation", pa.float32()),
    pa.field("cape", pa.float32()),
    pa.field("blh", pa.float32()),
    pa.field("surface_pressure", pa.float32()),
    pa.field("pressure_msl", pa.float32()),
    pa.field("snow_depth", pa.float32()),
    # pressure levels (seeing / Richardson)
    pa.field("t850", pa.float32()),
    pa.field("t700", pa.float32()),
    pa.field("t500", pa.float32()),
    pa.field("v500", pa.float32()),
    pa.field("v300", pa.float32()),
    pa.field("v250", pa.float32()),
    pa.field("v200", pa.float32()),
    pa.field("dir700", pa.float32()),
    pa.field("dir500", pa.float32()),
    pa.field("gph700", pa.float32()),
    pa.field("gph500", pa.float32()),
    pa.field("gph300", pa.float32()),
    pa.field("rh250", pa.float32()),        # contrail proxy
    # aerosol / water vapour
    pa.field("aod_total", pa.float32()),
    pa.field("aod_dust", pa.float32()),
    pa.field("dust_surface", pa.float32()),  # ug/m3 - NOT an AOD
    pa.field("pm10", pa.float32()),
    pa.field("pwv", pa.float32()),
    # our own derived predictions, versioned via pipeline_version
    pa.field("fwhm_est", pa.float32()),
    pa.field("mag_sky_est", pa.float32()),
    pa.field("p_raw", pa.float32()),
    pa.field("p_calibrated", pa.float32()),
]

FORECAST_SCHEMA = pa.schema(FORECAST_KEYS + FORECAST_VALUES + PROVENANCE_FIELDS)

# --- measurements (ground truth) -------------------------------------------
MEASUREMENT_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string()),
        pa.field("t_utc", pa.timestamp("s", tz="UTC")),
        # seeing: setup_id is mandatory, an optical train change must never
        # look like an atmospheric change (SPEC v8 section 4.1).
        pa.field("fwhm_zenith_500nm", pa.float32()),
        pa.field("setup_id", pa.string()),
        pa.field("fwhm_eccentricity", pa.float32()),
        # sky background: raw reading lives in the instrument's own band.
        pa.field("sky_mag_raw", pa.float32()),
        pa.field("instrument_type", pa.string()),     # 'TESS' | 'SQM' | ...
        pa.field("instrument_id", pa.string()),       # 'stars289'
        pa.field("bandpass_id", pa.string()),
        pa.field("sky_mag_calibrated", pa.float32()),
        # clouds / transparency
        pa.field("clear_flag", pa.bool_()),
        pa.field("cloud_frac", pa.float32()),
        pa.field("k_extinction", pa.float32()),
        # IR sky sensor: store WHICH field it is, semantics are not obvious
        # (raw IR, Ts-Ta, or the vendor's corrected sky temperature).
        pa.field("t_sky_ir", pa.float32()),
        pa.field("t_sky_ir_semantics", pa.string()),
        # local weather station
        pa.field("t2m", pa.float32()),
        pa.field("rh", pa.float32()),
        pa.field("wind", pa.float32()),
        pa.field("gust", pa.float32()),
        pa.field("station_pressure", pa.float32()),
        # censoring: roofs tell us whether anyone was observing at all
        pa.field("roof_open", pa.bool_()),
        pa.field("censored", pa.bool_()),
        pa.field("censor_reason", pa.string()),
        pa.field("source", pa.string()),
        pa.field("quality_flags", pa.string()),
        pa.field("retrieved_at", pa.timestamp("s", tz="UTC")),
        pa.field("pipeline_version", pa.string()),
    ]
)

SCHEMAS = {"forecasts": FORECAST_SCHEMA, "measurements": MEASUREMENT_SCHEMA}
