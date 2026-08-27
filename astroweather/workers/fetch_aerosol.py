"""Aerosol ingestion: total AOD hourly, dust AOD once a day (SPEC v8 2.1/3.1).

Two sources with different roles, because no single free feed gives both
species resolution and low latency:

* Open-Meteo Air Quality: total AOD at 550 nm, synchronous, hourly. Its `dust`
  field is a SURFACE concentration in ug/m3 -- archived as such, never used as
  a dust AOD. A Saharan layer at 3 km leaves the ground clean, which is exactly
  the case the correction exists for.
* CAMS ADS: the real dust AOD, but queued (minutes to the best part of an
  hour), so it runs once a day off the critical path. Its 120 h forecast
  covers the whole horizon from a single request.

Both feed the site correction of SPEC v8 section 2.1, where the dust term is
left uncorrected (elevated layer) and the rest is scaled by the site's 583 m
above the model topography.
"""

from __future__ import annotations

import argparse
import math
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from ..archive import DATA_ROOT, store_raw, utcnow, write_canonical
from ..site import NERPIO, Site

AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
CAMS_DATASET = "cams-global-atmospheric-composition-forecasts"
AEROSOL_SCALE_HEIGHT_M = 1700.0     # H_a, calibrable against AERONET
# Above this dust optical depth the night is no longer good for faint targets.
DUST_ALERT_THRESHOLD = 0.15
LICENSE_OPENMETEO = "open-meteo-cc-by-nc"
LICENSE_CAMS = "copernicus-cams"


def site_aod(aod_total: float | None, aod_dust: float | None, delta_h: float) -> float | None:
    """Aerosol optical depth seen from the site, corrected by species.

    Dust travels in elevated layers over Iberia (1.5-5 km), so at 1650 m the
    observatory sits inside or below it and that term is not scaled. Everything
    else concentrates in the boundary layer and is reduced by the height of the
    site above the model topography. Applying the barometric correction to the
    total would claim a clean sky in the middle of a dust episode.
    """
    if aod_total is None:
        return None
    factor = math.exp(-delta_h / AEROSOL_SCALE_HEIGHT_M)
    if aod_dust is None:
        return aod_total * factor       # no species split: degrade, and say so
    return aod_dust + max(0.0, aod_total - aod_dust) * factor


def fetch_air_quality(site: Site, *, days: int = 5,
                      start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """Forecast by default; a date range when backfilling history.

    The same endpoint serves the past, which is what lets the aerosol series be
    reconstructed as far back as the photometer archive goes.
    """
    params = {
        "latitude": site.latitude,
        "longitude": site.longitude,
        "hourly": "aerosol_optical_depth,dust,pm10,pm2_5",
        "timezone": "UTC",
    }
    if start and end:
        params.update({"start_date": start, "end_date": end})
    else:
        params["forecast_days"] = days
    response = requests.get(AIR_QUALITY_URL, params=params, timeout=90)
    response.raise_for_status()
    return response.json()


def air_quality_rows(payload: dict, site: Site, retrieved_at: datetime) -> list[dict]:
    hourly = payload["hourly"]
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in hourly["time"]]
    run_utc = times[0]
    rows = []
    for index, valid in enumerate(times):
        aod = hourly["aerosol_optical_depth"][index]
        if aod is None:
            continue
        rows.append({
            "site_id": site.site_id,
            "model": "cams_openmeteo",
            "run_utc": run_utc,
            "valid_utc": valid,
            "lead_h": int((valid - run_utc).total_seconds() // 3600),
            "member": None,
            "aod_total": float(aod),
            # Surface concentration in ug/m3. Kept under its own name so it can
            # never be mistaken for a dust optical depth.
            "dust_surface": hourly["dust"][index],
            "pm10": hourly["pm10"][index],
            "provider_model_version": "cams_via_openmeteo",
            "provider_grid_lat": payload.get("latitude"),
            "provider_grid_lon": payload.get("longitude"),
            "site_elevation": site.site_elevation,
            "model_grid_elevation": site.model_grid_elevation,
            # CAMS Europe is ~11 km, CAMS global ~45 km: different domains.
            "native_resolution_km": 11.0,
            "native_timestep_minutes": 60,
            "interpolated": False,
            "retrieved_at": retrieved_at,
            "license_tag": LICENSE_OPENMETEO,
            "quality_flags": "aod_total_only;no_species",
        })
    return rows


def _ads_client():
    import cdsapi

    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    env = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return cdsapi.Client(url=env["ADS_URL"], key=env["ADS_KEY"], quiet=True)


def fetch_dust(site: Site, *, day: str | None = None, root: Path | None = None) -> Path:
    """Daily dust AOD pull from CAMS. Queued upstream: never on the hot path."""
    day = day or (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    base = root or DATA_ROOT
    target = base / "raw" / f"site={site.site_id}" / "source=cams_dust"
    target.mkdir(parents=True, exist_ok=True)
    archive = target / f"dust_{day}.zip"

    _ads_client().retrieve(
        CAMS_DATASET,
        {
            "variable": [
                "total_aerosol_optical_depth_550nm",
                "dust_aerosol_optical_depth_550nm",
            ],
            "date": f"{day}/{day}",
            "time": "00:00",
            "leadtime_hour": [str(h) for h in range(0, 121, 3)],
            "type": "forecast",
            # A small box around the site keeps the request light.
            "area": [site.latitude + 0.5, site.longitude - 0.5,
                     site.latitude - 0.5, site.longitude + 0.5],
            "data_format": "netcdf_zip",
        },
        str(archive),
    )
    if not zipfile.is_zipfile(archive):
        raise RuntimeError(f"CAMS returned something that is not a zip: {archive}")
    return archive


def dust_rows(archive: Path, site: Site, retrieved_at: datetime) -> list[dict]:
    """Read the CAMS NetCDF and keep the grid point nearest the observatory.

    The file carries `aod550` (total) and `duaod550` (dust) every 3 h out to
    120 h, which is why one request a day covers the whole horizon.
    """
    import netCDF4
    import numpy as np

    with zipfile.ZipFile(archive) as bundle:
        name = next(n for n in bundle.namelist() if n.endswith(".nc"))
        payload = bundle.read(name)

    dataset = netCDF4.Dataset("cams", memory=payload)
    lats = dataset.variables["latitude"][:]
    lons = dataset.variables["longitude"][:]
    # CAMS publishes longitudes in 0-360 in some products.
    lon_site = site.longitude % 360 if lons.max() > 180 else site.longitude
    row = int(np.argmin(np.abs(lats - site.latitude)))
    col = int(np.argmin(np.abs(lons - lon_site)))

    reference = datetime.fromtimestamp(
        int(dataset.variables["forecast_reference_time"][0]), timezone.utc)
    periods = dataset.variables["forecast_period"][:]
    total = dataset.variables["aod550"][:, 0, row, col]
    dust = dataset.variables["duaod550"][:, 0, row, col]

    rows = []
    for index, lead in enumerate(periods):
        valid = reference + timedelta(hours=float(lead))
        rows.append({
            "site_id": site.site_id,
            "model": "cams_ads",
            "run_utc": reference,
            "valid_utc": valid,
            "lead_h": int(lead),
            "member": None,
            "aod_total": float(total[index]),
            "aod_dust": float(dust[index]),
            "provider_model_version": CAMS_DATASET,
            "provider_grid_lat": float(lats[row]),
            "provider_grid_lon": float(lons[col]),
            "site_elevation": site.site_elevation,
            "model_grid_elevation": site.model_grid_elevation,
            "native_resolution_km": 40.0,
            "native_timestep_minutes": 180,
            "interpolated": False,
            "retrieved_at": retrieved_at,
            "license_tag": LICENSE_CAMS,
            "quality_flags": "species_resolved",
        })
    return rows


def run(site: Site = NERPIO, *, days: int = 5, with_dust: bool = False) -> int:
    retrieved_at = utcnow()
    payload = fetch_air_quality(site, days=days)
    store_raw(payload, source="openmeteo_airquality", site_id=site.site_id,
              retrieved_at=retrieved_at)
    rows = air_quality_rows(payload, site, retrieved_at)
    write_canonical(rows, dataset="forecasts", site_id=site.site_id)

    first = rows[0] if rows else {}
    corrected = site_aod(first.get("aod_total"), None, site.delta_h)
    print(f"[fetch_aerosol] {len(rows)} horas de AOD total; "
          f"AOD ahora {first.get('aod_total')} -> {corrected:.3f} corregido a cota"
          if rows else "[fetch_aerosol] sin datos")

    if with_dust:
        try:
            archive = fetch_dust(site)
            dust = dust_rows(archive, site, retrieved_at)
            write_canonical(dust, dataset="forecasts", site_id=site.site_id)
            peak_row = max(dust, key=lambda r: r["aod_dust"])
            peak = peak_row["aod_dust"]
            # dust[0] is the analysis time of the run, not the present moment.
            analysis = site_aod(dust[0]["aod_total"], dust[0]["aod_dust"], site.delta_h)
            print(f"[fetch_aerosol] CAMS: {len(dust)} pasos con especies; "
                  f"AOD_dust maximo {peak:.3f} el {peak_row['valid_utc']:%d-%m %HZ}"
                  + (" ALERTA CALIMA" if peak > DUST_ALERT_THRESHOLD else "")
                  + f"; AOD del sitio en el analisis ({dust[0]['valid_utc']:%d-%m %HZ}) "
                  f"{analysis:.3f}")
            rows += dust
        except Exception as error:      # queue down, licence revoked, etc.
            print(f"[fetch_aerosol] CAMS dust NO disponible: {type(error).__name__}: "
                  f"{str(error)[:160]}")
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--dust", action="store_true", help="pide tambien CAMS (encolado)")
    args = parser.parse_args()
    run(days=args.days, with_dust=args.dust)
