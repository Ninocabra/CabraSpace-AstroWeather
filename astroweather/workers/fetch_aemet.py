"""AEMET daily observations from the nearest comparable station (SPEC v8 3.1/15).

Station 7067Y "Canadas del Nerpio" sits 11.5 km from AstroCamp at 1485 m --
165 m below the observatory but far closer in altitude than anything else
around. It is external ground truth, not site data, and every row says so.

What this gives us, verified 2026-08-27:

* daily T (mean/min/max with the hours of the extremes), RH (mean/min/max),
  precipitation and its peak intensity, from 2019 onwards (2010 and 2015 come
  back empty: the station simply did not exist).
* NOT hourly. AEMET's `observacion/convencional` endpoint only serves the last
  ~12 hours, so the hourly MOS leans on the TESS archive instead.
* NOT wind or sunshine for this particular station (both come back null).

Its real value is verification: was that night actually rainy, and does the
NWP surface temperature carry a bias at this altitude.
"""

from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from ..archive import store_raw, utcnow, write_canonical
from ..site import NERPIO, Site

BASE = "https://opendata.aemet.es/opendata/api"
LICENSE_TAG = "aemet-opendata-reuse"
HISTORY_START = date(2019, 1, 1)

# Nearest usable stations, ordered by distance (measured 2026-08-27).
STATIONS = {
    "7067Y": {"name": "Canadas del Nerpio", "elevation": 1485.0, "distance_km": 11.5},
    "7072Y": {"name": "Sege", "elevation": 960.0, "distance_km": 14.6},
    "7066Y": {"name": "Embalse de la Fuensanta", "elevation": 680.0, "distance_km": 27.0},
}
DEFAULT_STATION = "7067Y"


def _api_key() -> str:
    """Read the key from .env; it is a 3-month token, never committed."""
    env = Path(__file__).resolve().parent.parent.parent / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("AEMET_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("AEMET_API_KEY missing from .env")


def _get(path: str, key: str):
    """AEMET answers in two steps: a envelope with a temporary `datos` URL."""
    envelope = requests.get(f"{BASE}{path}", params={"api_key": key}, timeout=60).json()
    if envelope.get("estado") != 200:
        return None, envelope.get("descripcion", "")
    payload = requests.get(envelope["datos"], timeout=90)
    payload.encoding = "latin-1"      # AEMET does not serve UTF-8
    return payload.json(), ""


def _number(value) -> float | None:
    """AEMET writes decimals with a comma, and uses 'Ip' for trace rainfall."""
    if value in (None, "", "Ip"):
        return 0.0 if value == "Ip" else None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _to_rows(payload: list[dict], site: Site, station_id: str, retrieved_at: datetime):
    meta = STATIONS.get(station_id, {})
    rows = []
    for entry in payload:
        rows.append(
            {
                "site_id": site.site_id,
                "date": date.fromisoformat(entry["fecha"]),
                "station_id": station_id,
                "station_name": meta.get("name", entry.get("nombre")),
                "station_elevation": meta.get("elevation", _number(entry.get("altitud"))),
                "station_distance_km": meta.get("distance_km"),
                "t_mean": _number(entry.get("tmed")),
                "t_min": _number(entry.get("tmin")),
                "t_max": _number(entry.get("tmax")),
                "hour_t_min": entry.get("horatmin"),
                "hour_t_max": entry.get("horatmax"),
                "rh_mean": _number(entry.get("hrMedia")),
                "rh_min": _number(entry.get("hrMin")),
                "rh_max": _number(entry.get("hrMax")),
                "precipitation": _number(entry.get("prec")),
                "precipitation_max_intensity": _number(entry.get("pintMax")),
                "wind_mean": _number(entry.get("velmedia")),
                "source": "aemet_opendata",
                "license_tag": LICENSE_TAG,
                # The row is external ground truth: never mistake it for the site.
                "quality_flags": f"external_station;{meta.get('distance_km')}km;daily_aggregate",
                "retrieved_at": retrieved_at,
            }
        )
    return rows


def _chunks(start: date, end: date, months: int = 6):
    """AEMET caps the span per request, so walk it in half-year windows."""
    cursor = start
    while cursor < end:
        following = cursor
        for _ in range(months):
            following = (following.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield cursor, min(following - timedelta(days=1), end)
        cursor = following


def run(site: Site = NERPIO, *, station: str = DEFAULT_STATION,
        start: date | None = None, end: date | None = None, pause: float = 2.0) -> int:
    key = _api_key()
    start = start or HISTORY_START
    end = end or datetime.now(timezone.utc).date()
    total = 0

    for window_start, window_end in _chunks(start, end):
        path = (
            f"/valores/climatologicos/diarios/datos"
            f"/fechaini/{window_start}T00:00:00UTC"
            f"/fechafin/{window_end}T23:59:59UTC/estacion/{station}"
        )
        retrieved_at = utcnow()
        payload, note = _get(path, key)
        if not payload:
            print(f"[fetch_aemet] {window_start}..{window_end}: sin datos ({note})")
            time.sleep(pause)
            continue

        store_raw(payload, source=f"aemet_{station}", site_id=site.site_id,
                  retrieved_at=retrieved_at)
        rows = _to_rows(payload, site, station, retrieved_at)
        write_canonical(rows, dataset="daily_observations", site_id=site.site_id)
        total += len(rows)
        print(f"[fetch_aemet] {window_start}..{window_end}: {len(rows):>4} dias")
        time.sleep(pause)      # AEMET throttles aggressively

    print(f"[fetch_aemet] total {total} dias archivados de {station}")
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", default=DEFAULT_STATION)
    parser.add_argument("--start", help="YYYY-MM-DD (por defecto 2019-01-01)")
    args = parser.parse_args()
    run(
        station=args.station,
        start=date.fromisoformat(args.start) if args.start else None,
    )
