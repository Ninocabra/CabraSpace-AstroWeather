"""Ingestion worker for Google DeepMind WeatherNext 3 via Earth Engine.

The ensemble is represented as six canonical models: the mean
(`weathernext3`) and five quantiles (`weathernext3_pXX`).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
import pathlib
import re
from typing import Any

from ..archive import store_raw, utcnow, write_canonical
from ..invariants import check_wind_units
from ..site import NERPIO, Site

ENDPOINT_KIND = "earthengine"
COLLECTION_ID = (
    "projects/gcp-public-data-weathernext/assets/"
    "weathernext_3_0_0_0p1deg"
)
NATIVE_CRS = "EPSG:4326"
NATIVE_TRANSFORM = (0.1, 0.0, -180.05, 0.0, -0.1, 90.05)

LICENSE_TAG = "weathernext3-gdm-experimental"
MODEL = "weathernext3"
RESOLUTION_KM = 10.0
STATS = ("mean", "p10", "p25", "p50", "p75", "p90")
PROVIDER_MODEL_VERSION = "weathernext_3_0_0_0p1deg"

VARIABLES = (
    "total_cloud_cover",
    "low_cloud_cover",
    "medium_cloud_cover",
    "high_cloud_cover",
    "temperature_2m",
    "dewpoint_temperature_2m",
    "wind_speed_10m",
    "u_component_of_wind_10m",
    "v_component_of_wind_10m",
    "total_precipitation_1hr",
    "mean_sea_level_pressure",
)
BANDS = tuple(
    f"{variable}_{stat}"
    for variable in VARIABLES
    for stat in STATS
)

# Decimals kept in the canonical row, the precision the other providers
# already publish. The model hands out float noise (a cloud cover of
# 0.0772 %) that zstd cannot compress: rounded, a run shrinks 42 % and a month
# bundle 85 %, and the exam AUC moves in the fourth decimal (17-09-2026). The
# raw keeps every digit.
DECIMALS = {
    "cloud_total": 1, "cloud_low": 1, "cloud_mid": 1, "cloud_high": 1,
    "t2m": 1, "td2m": 1, "wind10m": 1, "pressure_msl": 1, "precipitation": 2,
}

MAGNUS_A = 17.62
MAGNUS_B = 243.12

_IMAGE_ID_RE = re.compile(
    r"(?:.*/)?(?P<start>\d{12})_(?P<end>\d{12})\Z"
)


def derive_rh2m(t_c: float, td_c: float) -> float:
    """Derive 2 m relative humidity from Celsius temperature and dew point."""
    gamma_td = (MAGNUS_A * td_c) / (MAGNUS_B + td_c)
    gamma_t = (MAGNUS_A * t_c) / (MAGNUS_B + t_c)
    rh = 100.0 * math.exp(gamma_td - gamma_t)
    return round(max(0.0, min(100.0, rh)), 2)


def derive_wind_direction(u: float, v: float) -> float:
    """Return meteorological wind direction: where the wind comes from."""
    dir_deg = math.degrees(math.atan2(-u, -v)) % 360.0
    return round(dir_deg, 2)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_z(value: datetime) -> str:
    return _utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _stable_floor(value: float) -> int:
    """Floor a grid quotient after removing representation noise at integers."""
    nearest = round(value)
    if math.isclose(value, nearest, rel_tol=0.0, abs_tol=1e-10):
        value = float(nearest)
    return math.floor(value)


def native_cell(lat: float, lon: float) -> tuple[float, float]:
    """Return the native WeatherNext cell centre containing a coordinate."""
    lat = float(lat)
    lon = float(lon)

    if not math.isfinite(lat) or not math.isfinite(lon):
        raise ValueError("lat/lon deben ser finitos")
    if not -90.0 <= lat <= 90.0:
        raise ValueError("lat fuera de [-90, 90]")

    lon = ((lon + 180.0) % 360.0) - 180.0

    i = _stable_floor((lon + 180.05) / 0.1)
    j = _stable_floor((90.05 - lat) / 0.1)

    # The half-cell interval [179.95, 180) belongs to the dateline cell,
    # represented by column zero in this 3600-column raster.
    i %= 3600

    centre_lat = 90.0 - 0.1 * j
    centre_lon = -180.0 + 0.1 * i
    return round(centre_lat, 6), round(centre_lon, 6)


def parse_image_id(image_id: str) -> tuple[datetime, datetime]:
    """Parse `<initialization>_<valid>` image IDs, optionally path-prefixed."""
    match = _IMAGE_ID_RE.fullmatch(str(image_id))
    if match is None:
        raise ValueError(f"id de imagen WeatherNext inválido: {image_id!r}")

    try:
        start = datetime.strptime(
            match.group("start"), "%Y%m%d%H%M"
        ).replace(tzinfo=timezone.utc)
        end = datetime.strptime(
            match.group("end"), "%Y%m%d%H%M"
        ).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(
            f"fecha inválida en id WeatherNext: {image_id!r}"
        ) from exc

    if end <= start:
        raise ValueError(
            f"el fin no es posterior al inicio en id WeatherNext: {image_id!r}"
        )
    return start, end


def records_from_region(
    table: list[list],
    site: Site,
    expected_init: datetime | None = None,
) -> list[dict]:
    """Convert an Earth Engine getRegion table into `_rows` records."""
    if not table:
        return []

    header = table[0]
    if list(header[:4]) != ["id", "longitude", "latitude", "time"]:
        raise ValueError(
            "cabecera getRegion inválida: se esperaba "
            "id, longitude, latitude, time"
        )
    if len(table) == 1:
        return []

    if len(set(header)) != len(header):
        raise ValueError("cabecera getRegion con columnas duplicadas")

    expected = _utc(expected_init) if expected_init is not None else None
    expected_lat, expected_lon = native_cell(site.latitude, site.longitude)

    common_init: datetime | None = None
    seen_ids: set[str] = set()
    forecast: list[dict] = []
    grid_lat: float | None = None
    grid_lon: float | None = None

    for row_number, raw_row in enumerate(table[1:], start=1):
        if len(raw_row) != len(header):
            raise ValueError(
                f"fila getRegion {row_number} tiene longitud incorrecta"
            )

        values = dict(zip(header, raw_row))
        image_id = str(values["id"])
        if image_id in seen_ids:
            raise ValueError(f"id repetido en getRegion: {image_id}")
        seen_ids.add(image_id)

        start, end = parse_image_id(image_id)

        if common_init is None:
            common_init = start
        elif start != common_init:
            raise ValueError("filas con distinta init")

        if expected is not None and start != expected:
            raise ValueError(
                f"init {start.isoformat()} distinta de expected_init "
                f"{expected.isoformat()}"
            )

        raw_time = values["time"]
        try:
            region_init = datetime.fromtimestamp(
                float(raw_time) / 1000.0,
                tz=timezone.utc,
            )
        except (TypeError, ValueError, OSError) as exc:
            raise ValueError(
                f"columna time inválida para init en fila {row_number}"
            ) from exc

        if region_init != start:
            raise ValueError(
                f"columna time no coincide con init en fila {row_number}"
            )

        lon = float(values["longitude"])
        lat = float(values["latitude"])
        if not (
            math.isclose(lat, expected_lat, rel_tol=0.0, abs_tol=1e-6)
            and math.isclose(lon, expected_lon, rel_tol=0.0, abs_tol=1e-6)
        ):
            raise ValueError(
                f"pixel inesperado en fila {row_number}: "
                f"({lat}, {lon}), esperado ({expected_lat}, {expected_lon})"
            )

        # Provenance carries the exact cell centre, not the float noise of
        # the server (38.199999999999996).
        grid_lat, grid_lon = expected_lat, expected_lon

        lead_seconds = (end - start).total_seconds()
        if lead_seconds <= 0 or lead_seconds % 3600 != 0:
            raise ValueError(
                f"lead no entero en horas para imagen {image_id}"
            )
        hours = int(lead_seconds // 3600)
        if hours < 1:
            raise ValueError(f"lead menor que uno para imagen {image_id}")

        item: dict[str, Any] = {
            "time": end,
            "hours": hours,
        }
        for band in header[4:]:
            value = values[band]
            if value is not None:
                item[str(band)] = float(value)
        forecast.append(item)

    forecast.sort(key=lambda item: item["hours"])

    assert common_init is not None
    assert grid_lat is not None
    assert grid_lon is not None
    return [
        {
            "init_time": common_init,
            "provider_grid_lat": grid_lat,
            "provider_grid_lon": grid_lon,
            "forecast": forecast,
        }
    ]


def select_complete_init(
    counts: dict[str, int],
    hours: int,
    allowed_hours: tuple[int, ...] | None = None,
) -> datetime | None:
    """Select the most recent initialization containing exactly `hours` images.

    `allowed_hours` keeps the live job on the same runs the exam scored (00Z and
    12Z). 06Z and 18Z are just as long, but scoring what is not archived the
    same way is how a comparison stops comparing.
    """
    if not 1 <= hours <= 360:
        raise ValueError("hours debe estar entre 1 y 360")

    complete: list[datetime] = []
    for raw_start, raw_count in counts.items():
        if int(raw_count) != hours:
            continue
        try:
            start = datetime.fromisoformat(
                str(raw_start).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                f"start_time inválido en histograma: {raw_start!r}"
            ) from exc
        start = _utc(start)
        if allowed_hours is not None and start.hour not in allowed_hours:
            continue
        complete.append(start)

    return max(complete) if complete else None


# Where the credentials come from, in order. A service account is what the
# ephemeral runner would need, but on 17-09-2026 Google had granted WeatherNext
# to a personal user account only: `astroweather-runner@astroweather-cabra` signs in
# and then gets "asset not found (does not exist or caller does not have
# access)". Access for it is REQUESTED (weathernext@google.com, 17-09-2026);
# until it arrives this job runs on the desktop with the user credentials that
# `earthengine authenticate` left behind, and the runner skips it.
SERVICE_ACCOUNT_ENV = "EE_SERVICE_ACCOUNT_KEY"
USER_CREDENTIALS = pathlib.Path.home() / ".config" / "earthengine" / "credentials"


def credentials_source() -> str | None:
    """'service_account', 'user' or None, without touching the network."""
    if os.getenv(SERVICE_ACCOUNT_ENV):
        return "service_account"
    if USER_CREDENTIALS.is_file():
        return "user"
    return None


def initialize(ee, project: str) -> str:
    """Start Earth Engine with whatever credentials this machine has."""
    source = credentials_source()
    if source is None:
        raise RuntimeError(
            "sin credenciales de Earth Engine: define "
            f"{SERVICE_ACCOUNT_ENV} (JSON de la cuenta de servicio o ruta al "
            "fichero) o ejecuta `earthengine authenticate` en esta maquina")

    if source == "service_account":
        raw = os.environ[SERVICE_ACCOUNT_ENV]
        key_file = raw if os.path.isfile(raw) else None
        info = json.loads(pathlib.Path(raw).read_text(encoding="utf-8")
                          if key_file else raw)
        email = info["client_email"]
        credentials = (ee.ServiceAccountCredentials(email, key_file) if key_file
                       else ee.ServiceAccountCredentials(email, key_data=raw))
        ee.Initialize(credentials, project=project)
    else:
        ee.Initialize(project=project)
    return source


def fetch_region(
    ee,
    site: Site,
    init_time: datetime,
    hours: int,
) -> list[list]:
    """Fetch one native WeatherNext pixel for one initialization."""
    collection = (
        ee.ImageCollection(COLLECTION_ID)
        .filter(ee.Filter.eq("start_time", _iso_z(init_time)))
        .filter(ee.Filter.lte("forecast_hour", hours))
        .select(list(BANDS))
    )
    return collection.getRegion(
        geometry=ee.Geometry.Point([site.longitude, site.latitude]),
        crs=NATIVE_CRS,
        crsTransform=list(NATIVE_TRANSFORM),
    ).getInfo()


def latest_complete_init(
    ee,
    hours: int,
    now: datetime,
    lookback_h: int = 30,
    allowed_hours: tuple[int, ...] | None = None,
) -> datetime | None:
    """Find the latest fully ingested initialization in the lookback window."""
    if not 1 <= hours <= 360:
        raise ValueError("hours debe estar entre 1 y 360")
    if lookback_h < 1:
        raise ValueError("lookback_h debe ser positivo")

    now = _utc(now)
    counts = (
        ee.ImageCollection(COLLECTION_ID)
        .filterDate(
            _iso_z(now - timedelta(hours=lookback_h)),
            _iso_z(now + timedelta(hours=1)),
        )
        .filter(ee.Filter.lte("forecast_hour", hours))
        .aggregate_histogram("start_time")
        .getInfo()
    )
    return select_complete_init(counts or {}, hours, allowed_hours)


def _rows(
    records: list[dict],
    site: Site,
    retrieved_at: datetime,
) -> list[dict]:
    """Convert provider records to canonical forecast rows."""
    rows: list[dict] = []

    for record in records:
        raw_init = record.get("init_time")
        if raw_init is None:
            continue
        if isinstance(raw_init, str):
            init_time = datetime.fromisoformat(
                raw_init.replace("Z", "+00:00")
            )
        else:
            init_time = raw_init
        init_time = _utc(init_time)

        grid_lat = record.get("provider_grid_lat")
        grid_lon = record.get("provider_grid_lon")
        if grid_lat is None or grid_lon is None:
            geography = record.get("geography")
            if isinstance(geography, str):
                match = re.search(
                    r"POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)\s*\)",
                    geography,
                    re.IGNORECASE,
                )
                if match:
                    grid_lon = float(match.group(1))
                    grid_lat = float(match.group(2))

        forecast = record.get("forecast") or []

        for stat in STATS:
            model = MODEL if stat == "mean" else f"{MODEL}_{stat}"

            for item in forecast:
                raw_valid = item.get("time")
                if raw_valid is None:
                    continue
                if isinstance(raw_valid, str):
                    valid_utc = datetime.fromisoformat(
                        raw_valid.replace("Z", "+00:00")
                    )
                else:
                    valid_utc = raw_valid
                valid_utc = _utc(valid_utc)

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

                c_total = item.get(f"total_cloud_cover_{stat}")
                if c_total is not None:
                    row["cloud_total"] = round(float(c_total) * 100.0, DECIMALS["cloud_total"])

                c_low = item.get(f"low_cloud_cover_{stat}")
                if c_low is not None:
                    row["cloud_low"] = round(float(c_low) * 100.0, DECIMALS["cloud_low"])

                c_mid = item.get(f"medium_cloud_cover_{stat}")
                if c_mid is not None:
                    row["cloud_mid"] = round(float(c_mid) * 100.0, DECIMALS["cloud_mid"])

                c_high = item.get(f"high_cloud_cover_{stat}")
                if c_high is not None:
                    row["cloud_high"] = round(float(c_high) * 100.0, DECIMALS["cloud_high"])

                t_k = item.get(f"temperature_2m_{stat}")
                t2m = None
                if t_k is not None:
                    t2m = float(t_k) - 273.15
                    row["t2m"] = round(t2m, DECIMALS["t2m"])

                td_k = item.get(f"dewpoint_temperature_2m_{stat}")
                td2m = None
                if td_k is not None:
                    td2m = float(td_k) - 273.15
                    row["td2m"] = round(td2m, DECIMALS["td2m"])

                if stat == "mean" and t2m is not None and td2m is not None:
                    row["rh2m"] = derive_rh2m(t2m, td2m)

                w_speed = item.get(f"wind_speed_10m_{stat}")
                if w_speed is not None:
                    wind10m = round(float(w_speed), DECIMALS["wind10m"])
                    check_wind_units([wind10m], label=f"{model}:wind10m")
                    row["wind10m"] = wind10m

                u_w = item.get(f"u_component_of_wind_10m_{stat}")
                v_w = item.get(f"v_component_of_wind_10m_{stat}")
                if stat == "mean" and u_w is not None and v_w is not None:
                    row["wind_dir10m"] = derive_wind_direction(
                        float(u_w), float(v_w)
                    )

                precip_m = item.get(f"total_precipitation_1hr_{stat}")
                if precip_m is not None:
                    row["precipitation"] = round(
                        float(precip_m) * 1000.0, DECIMALS["precipitation"]
                    )

                p_pa = item.get(f"mean_sea_level_pressure_{stat}")
                if p_pa is not None:
                    row["pressure_msl"] = round(float(p_pa) / 100.0, DECIMALS["pressure_msl"])

                row.update(
                    {
                        "provider_model_version": PROVIDER_MODEL_VERSION,
                        "provider_grid_lat": grid_lat,
                        "provider_grid_lon": grid_lon,
                        "site_elevation": float(site.site_elevation),
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
    """Recursively convert datetimes to ISO strings for raw storage."""
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
    project: str | None = None,
    ee_module=None,
    allowed_hours: tuple[int, ...] | None = None,
) -> dict[str, int]:
    """Fetch, archive and write one complete WeatherNext initialization."""
    project = project or os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError(
            "Faltan variables de entorno requeridas para WeatherNext.\n"
            "Configura GOOGLE_CLOUD_PROJECT=<tu_proyecto_gcp>"
        )

    if not 1 <= hours <= 360:
        raise ValueError("hours debe estar entre 1 y 360")

    if ee_module is None:
        import ee as ee_module

    source = initialize(ee_module, project)

    selected_init = _utc(init_time) if init_time is not None else None
    if selected_init is None:
        selected_init = latest_complete_init(
            ee_module,
            hours,
            utcnow(),
            allowed_hours=allowed_hours,
        )
        if selected_init is None:
            print("[fetch_weathernext] sin inicializacion completa")
            return {}

    table = fetch_region(
        ee_module,
        site,
        selected_init,
        hours,
    )
    records = records_from_region(
        table,
        site,
        expected_init=selected_init,
    )
    if not records:
        print("[fetch_weathernext] sin inicializacion completa")
        return {}

    forecast = records[0].get("forecast") or []
    if len(forecast) != hours:
        raise ValueError(
            f"inicializacion incompleta: esperadas {hours} horas, "
            f"recibidas {len(forecast)}"
        )

    retrieved_at = utcnow()
    raw_payload = {
        "collection": COLLECTION_ID,
        "init_time": _iso_z(selected_init),
        "hours": hours,
        "crs": NATIVE_CRS,
        "crs_transform": list(NATIVE_TRANSFORM),
        "region": table,
    }
    store_raw(
        _to_json_serializable(raw_payload),
        source="weathernext_ee",
        site_id=site.site_id,
        retrieved_at=retrieved_at,
    )

    canonical_rows = _rows(records, site, retrieved_at)
    write_canonical(
        canonical_rows,
        dataset="forecasts",
        site_id=site.site_id,
        tag=MODEL,          # archivepush bundles these by closed month
    )

    counts: dict[str, int] = {}
    for row in canonical_rows:
        model = row["model"]
        counts[model] = counts.get(model, 0) + 1

    print(
        f"[fetch_weathernext] {source} {selected_init:%Y-%m-%dT%HZ} "
        + ", ".join(
            f"{model}={count}"
            for model, count in sorted(counts.items())
        )
    )
    return counts


def _parse_cli_init(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _utc(parsed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hours",
        type=int,
        default=48,
        help="Horas de predicción a consultar",
    )
    parser.add_argument(
        "--init-time",
        type=str,
        default=None,
        help="Timestamp ISO de inicialización; sin zona se interpreta como UTC",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Proyecto de Google Cloud para Earth Engine",
    )
    args = parser.parse_args()
    run(
        hours=args.hours,
        init_time=_parse_cli_init(args.init_time),
        project=args.project,
    )
