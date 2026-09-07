"""Ingestion worker for Google DeepMind WeatherNext 3 via BigQuery.

Table: weathernext_3_0_0_0p1deg (0.1 degree gridded forecast).
Ensemble: 64 members summarized into mean and 5 quantiles (p10, p25, p50, p75, p90).
Architectural model layout: 6 models (mean as 'weathernext3', quantiles as 'weathernext3_pXX')
with member=None to conform to canonical schema and allow individual scoring.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import math
import os
import re
from typing import Any

from ..archive import store_raw, utcnow, write_canonical
from ..invariants import check_wind_units
from ..site import NERPIO, Site

ENDPOINT_KIND = "bigquery"
LICENSE_TAG = "weathernext3-gdm-experimental"   # el forecast del futuro NO es CC BY
MODEL = "weathernext3"
RESOLUTION_KM = 10.0                           # 0.1 deg, NO 5 km (eso es solo T/Td de estación)
STATS = ("mean", "p10", "p25", "p50", "p75", "p90")

PROVIDER_MODEL_VERSION = "weathernext_3_0_0_0p1deg"

# Magnus-Sonntag constants (Sonntag 1990) for saturation vapor pressure over water
# E(T) = 6.112 * exp(17.62 * T / (243.12 + T)) [hPa, T in deg C]
# RH = 100 * E(Td) / E(T) = 100 * exp(17.62 * Td / (243.12 + Td) - 17.62 * T / (243.12 + T))
MAGNUS_A = 17.62
MAGNUS_B = 243.12


def derive_rh2m(t_c: float, td_c: float) -> float:
    """Derive 2m relative humidity (%) from temperature and dew point in Celsius.

    Uses Magnus-Sonntag formulation (Sonntag 1990):
        RH = 100 * exp(a * Td / (b + Td) - a * T / (b + T))
    with a = 17.62, b = 243.12 deg C. Clamped to [0.0, 100.0] %.
    """
    gamma_td = (MAGNUS_A * td_c) / (MAGNUS_B + td_c)
    gamma_t = (MAGNUS_A * t_c) / (MAGNUS_B + t_c)
    rh = 100.0 * math.exp(gamma_td - gamma_t)
    return round(max(0.0, min(100.0, rh)), 2)


def derive_wind_direction(u: float, v: float) -> float:
    """Derive 10m wind direction in degrees (0..360) from zonal (u) and meridional (v) components.

    Meteorological convention: direction FROM which the wind is blowing.
    u: eastward wind (blowing towards East).
    v: northward wind (blowing towards North).
    Vector of origin is (-u, -v).
    Angle clockwise from North: atan2(-u, -v) converted to degrees modulo 360.
    """
    dir_deg = math.degrees(math.atan2(-u, -v)) % 360.0
    return round(dir_deg, 2)


def _extract_centroid(geography: Any) -> tuple[float | None, float | None]:
    """Extract (latitude, longitude) centroid from BigQuery geography field.

    Accepts WKT (POINT(lon lat)), GeoJSON dict or string, or dict with lat/lon.
    """
    if geography is None:
        return None, None

    if isinstance(geography, dict):
        if "latitude" in geography and "longitude" in geography:
            return float(geography["latitude"]), float(geography["longitude"])
        if geography.get("type") == "Point" and "coordinates" in geography:
            lon, lat = geography["coordinates"][:2]
            return float(lat), float(lon)

    if isinstance(geography, str):
        geo_str = geography.strip()
        if geo_str.startswith("{"):
            try:
                import json
                parsed = json.loads(geo_str)
                return _extract_centroid(parsed)
            except Exception:
                pass
        match = re.search(r"POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)\s*\)", geo_str, re.IGNORECASE)
        if match:
            lon, lat = float(match.group(1)), float(match.group(2))
            return lat, lon

    return None, None


def build_query(table: str, site: Site, init_time: datetime | None, max_hours: int) -> tuple[str, dict]:
    """SQL parametrizado + parámetros. Filtra por init_time (partición) y por
    ST_CONTAINS(geography_polygon, ST_GEOGPOINT(lon, lat)). Si init_time es None,
    usa la inicialización MÁS RECIENTE disponible para esa celda.

    Coste y particionamiento:
    La tabla WeatherNext 3 está particionada por `init_time`. Filtrar por `init_time`
    en la cláusula WHERE antes de cualquier otra condición es indispensable para que
    BigQuery realice partition pruning y evite un escaneo completo (full table scan)
    de todo el histórico de previsiones mundiales, cuyo coste monetario sería severo.
    """
    table_ref = table if table.startswith("`") and table.endswith("`") else f"`{table}`"

    params: dict[str, Any] = {
        "lon": site.longitude,
        "lat": site.latitude,
        "max_hours": max_hours,
    }

    if init_time is not None:
        init_filter = "init_time = @init_time"
        params["init_time"] = init_time
    else:
        init_filter = f"init_time = (SELECT MAX(init_time) FROM {table_ref})"

    query = f"""
SELECT
    init_time,
    geography,
    geography_polygon,
    ARRAY(
        SELECT AS STRUCT *
        FROM UNNEST(forecast)
        WHERE hours <= @max_hours
        ORDER BY hours
    ) AS forecast
FROM {table_ref}
WHERE {init_filter}
  AND ST_CONTAINS(geography_polygon, ST_GEOGPOINT(@lon, @lat))
""".strip()

    return query, params


def _rows(records: list[dict], site: Site, retrieved_at: datetime) -> list[dict]:
    """records = filas de BigQuery ya convertidas a dict (una por init_time+celda,
    con `forecast` como lista de dicts). Devuelve filas CANÓNICAS.
    """
    rows: list[dict] = []

    for record in records:
        raw_init = record.get("init_time")
        if raw_init is None:
            continue
        if isinstance(raw_init, str):
            init_time = datetime.fromisoformat(raw_init.replace("Z", "+00:00"))
        else:
            init_time = raw_init
        if init_time.tzinfo is None:
            init_time = init_time.replace(tzinfo=timezone.utc)
        else:
            init_time = init_time.astimezone(timezone.utc)

        grid_lat, grid_lon = _extract_centroid(record.get("geography"))
        if grid_lat is None and "provider_grid_lat" in record:
            grid_lat = record["provider_grid_lat"]
        if grid_lon is None and "provider_grid_lon" in record:
            grid_lon = record["provider_grid_lon"]

        forecast = record.get("forecast") or []

        for stat in STATS:
            model = MODEL if stat == "mean" else f"{MODEL}_{stat}"

            for item in forecast:
                raw_valid = item.get("time")
                if raw_valid is None:
                    continue
                if isinstance(raw_valid, str):
                    valid_utc = datetime.fromisoformat(raw_valid.replace("Z", "+00:00"))
                else:
                    valid_utc = raw_valid
                if valid_utc.tzinfo is None:
                    valid_utc = valid_utc.replace(tzinfo=timezone.utc)
                else:
                    valid_utc = valid_utc.astimezone(timezone.utc)

                lead_h = int(item["hours"])
                diff_seconds = (valid_utc - init_time).total_seconds()
                if diff_seconds != lead_h * 3600:
                    raise ValueError(
                        f"Incoherent lead_h: forecast.hours={lead_h} != "
                        f"(valid_utc {valid_utc} - run_utc {init_time}) "
                        f"({diff_seconds / 3600} h)"
                    )

                row: dict[str, Any] = {
                    "site_id": site.site_id,
                    "model": model,
                    "run_utc": init_time,
                    "valid_utc": valid_utc,
                    "lead_h": lead_h,
                    "member": None,
                }

                # Cloud covers (fraction 0..1 -> %)
                c_total = item.get(f"total_cloud_cover_{stat}")
                if c_total is not None:
                    row["cloud_total"] = round(float(c_total) * 100.0, 4)

                c_low = item.get(f"low_cloud_cover_{stat}")
                if c_low is not None:
                    row["cloud_low"] = round(float(c_low) * 100.0, 4)

                c_mid = item.get(f"medium_cloud_cover_{stat}")
                if c_mid is not None:
                    row["cloud_mid"] = round(float(c_mid) * 100.0, 4)

                c_high = item.get(f"high_cloud_cover_{stat}")
                if c_high is not None:
                    row["cloud_high"] = round(float(c_high) * 100.0, 4)

                # Temperatures (Kelvin -> Celsius)
                t_k = item.get(f"temperature_2m_{stat}")
                t2m = None
                if t_k is not None:
                    t2m = round(float(t_k) - 273.15, 4)
                    row["t2m"] = t2m

                td_k = item.get(f"dewpoint_temperature_2m_{stat}")
                td2m = None
                if td_k is not None:
                    td2m = round(float(td_k) - 273.15, 4)
                    row["td2m"] = td2m

                # Relative humidity derived via Magnus from t2m and td2m. Only
                # for the mean: the p10 of T paired with the p10 of Td is not
                # the p10 of RH (a quantile of a function is not the function
                # of the quantiles), so the quantile models leave it null.
                if stat == "mean" and t2m is not None and td2m is not None:
                    row["rh2m"] = derive_rh2m(t2m, td2m)

                # Wind speed (m/s) and check invariant
                w_speed = item.get(f"wind_speed_10m_{stat}")
                if w_speed is not None:
                    wind10m = round(float(w_speed), 4)
                    check_wind_units([wind10m], label=f"{model}:wind10m")
                    row["wind10m"] = wind10m

                # Wind direction derived from u and v, mean only: the p10 of
                # u and the p10 of v are two marginal numbers, not a vector,
                # and their atan2 points nowhere physical.
                u_w = item.get(f"u_component_of_wind_10m_{stat}")
                v_w = item.get(f"v_component_of_wind_10m_{stat}")
                if stat == "mean" and u_w is not None and v_w is not None:
                    row["wind_dir10m"] = derive_wind_direction(float(u_w), float(v_w))

                # Precipitation (m -> mm)
                precip_m = item.get(f"total_precipitation_1hr_{stat}")
                if precip_m is not None:
                    row["precipitation"] = round(float(precip_m) * 1000.0, 4)

                # Pressure at MSL (Pa -> hPa)
                p_pa = item.get(f"mean_sea_level_pressure_{stat}")
                if p_pa is not None:
                    row["pressure_msl"] = round(float(p_pa) / 100.0, 4)

                # Provenance
                row.update(
                    {
                        "provider_model_version": PROVIDER_MODEL_VERSION,
                        "provider_grid_lat": grid_lat,
                        "provider_grid_lon": grid_lon,
                        "site_elevation": float(site.site_elevation),
                        # `site.model_grid_elevation` is the Open-Meteo 9 km
                        # cell, verified against THAT API (site.py). WeatherNext
                        # publishes no cell elevation, and borrowing another
                        # provider's number would be provenance that lies.
                        "model_grid_elevation": None,
                        "downscaling_elevation": None,
                        "native_resolution_km": float(RESOLUTION_KM),
                        "native_timestep_minutes": 60,
                        "interpolated": False,
                        "retrieved_at": retrieved_at,
                        "license_tag": LICENSE_TAG,
                    }
                )

                rows.append(row)

    return rows


def _to_json_serializable(obj: Any) -> Any:
    """Recursively convert datetimes to ISO format strings for raw JSON storage."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _to_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json_serializable(v) for v in obj]
    return obj


def run(
    site: Site = NERPIO,
    *,
    hours: int = 48,
    init_time: datetime | None = None,
    table: str | None = None,
    project: str | None = None,
) -> dict[str, int]:
    """Consulta, archiva raw, escribe canónico, imprime '[fetch_weathernext] ...'."""
    project = project or os.getenv("GOOGLE_CLOUD_PROJECT")
    table = table or os.getenv("WEATHERNEXT_BQ_TABLE")

    if not project or not table:
        raise RuntimeError(
            "Faltan variables de entorno requeridas para WeatherNext BigQuery.\n"
            "Configura en tu fichero .env:\n"
            "  GOOGLE_CLOUD_PROJECT=<tu_proyecto_gcp>\n"
            "  WEATHERNEXT_BQ_TABLE=<proyecto.dataset.tabla>"
        )

    # Dynamic import so module and tests can be imported without google-cloud-bigquery
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    query_sql, params = build_query(table, site, init_time, hours)

    query_params = []
    for k, v in params.items():
        if isinstance(v, datetime):
            query_params.append(bigquery.ScalarQueryParameter(k, "TIMESTAMP", v))
        elif isinstance(v, float):
            query_params.append(bigquery.ScalarQueryParameter(k, "FLOAT64", v))
        elif isinstance(v, int):
            query_params.append(bigquery.ScalarQueryParameter(k, "INT64", v))
        elif isinstance(v, str):
            query_params.append(bigquery.ScalarQueryParameter(k, "STRING", v))

    job_config = bigquery.QueryJobConfig(query_parameters=query_params)
    job = client.query(query_sql, job_config=job_config)
    results = job.result()

    records: list[dict] = []
    for row in results:
        rec = dict(row.items()) if hasattr(row, "items") else dict(row)
        if "forecast" in rec and rec["forecast"]:
            rec["forecast"] = [
                dict(f.items()) if hasattr(f, "items") else dict(f)
                for f in rec["forecast"]
            ]
        records.append(rec)

    retrieved_at = utcnow()
    raw_payload = _to_json_serializable(records)
    store_raw(raw_payload, source="weathernext_bq", site_id=site.site_id, retrieved_at=retrieved_at)

    canonical_rows = _rows(records, site, retrieved_at)
    write_canonical(canonical_rows, dataset="forecasts", site_id=site.site_id)

    counts: dict[str, int] = {}
    for r in canonical_rows:
        m = r["model"]
        counts[m] = counts.get(m, 0) + 1

    print("[fetch_weathernext] " + ", ".join(f"{m}={n}" for m, n in sorted(counts.items())))
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=48, help="Horas de predicción a consultar")
    parser.add_argument("--init-time", type=str, default=None, help="Timestamp ISO de inicialización")
    parser.add_argument("--table", type=str, default=None, help="Tabla de BigQuery")
    parser.add_argument("--project", type=str, default=None, help="Proyecto de GCP")
    args = parser.parse_args()

    parsed_init = datetime.fromisoformat(args.init_time) if args.init_time else None
    run(
        hours=args.hours,
        init_time=parsed_init,
        table=args.table,
        project=args.project,
    )

