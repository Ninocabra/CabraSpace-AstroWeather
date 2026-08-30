"""AEMET daily observations from the nearest comparable station (SPEC v8 3.1/15).

Station 7067Y "Canadas del Nerpio" sits 11.5 km from AstroCamp at 1485 m --
165 m below the observatory but far closer in altitude than anything else
around. It is external ground truth, not site data, and every row says so.

What this gives us, verified 2026-08-27:

* daily T (mean/min/max with the hours of the extremes), RH (mean/min/max),
  precipitation and its peak intensity, from 2019 onwards (2010 and 2015 come
  back empty: the station simply did not exist).
* Hourly TOO, desde el 29-08-2026 (`run_hourly`). El endpoint
  `observacion/convencional` solo sirve las ultimas ~12 h -- medido: 11
  registros de 00:00 a 10:00 a las 11:57 UTC -- asi que no da historico, pero
  sondeandolo tres veces al dia la serie horaria se construye HACIA ADELANTE
  con solape. Nadie lo estaba sondeando: cada dia que pasaba era una serie
  horaria que no se podia recuperar despues.
* NOT wind, en NINGUNA de las tres. Verificado el 29-08-2026 sobre el endpoint
  horario: los campos con valor son `ta`, `hr`, `prec` (mas `tamax`/`tamin`) y
  `vv`, `vmax` y `dv` no vienen. Importa mas de lo que parece: el `gust`
  alimenta una puerta de SEGURIDAD y esto cierra la ultima esperanza de verdad
  terreno externa para el viento. La unica que queda es la estacion de
  AstroCamp, que publica viento medio y no rachas.

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
from ..secretos import leer as leer_secreto
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
    """A 3-month token, never committed. Environment first, then .env."""
    return leer_secreto("AEMET_API_KEY")


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


def _to_hourly_rows(payload: list[dict], site: Site, station_id: str,
                    retrieved_at: datetime):
    """De la observacion convencional a filas de `measurements`.

    Son de una estacion EXTERNA, a 11,5 km y 165 m por debajo del observatorio,
    y la fila lo dice en `quality_flags`: confundirla con dato del sitio seria
    exactamente el error que este proyecto lleva dos dias persiguiendo.
    """
    meta = STATIONS.get(station_id, {})
    rows = []
    for entry in payload:
        marca = entry.get("fint")
        if not marca:
            continue
        cuando = datetime.strptime(marca, "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc)
        rows.append({
            "site_id": site.site_id,
            "t_utc": cuando.replace(microsecond=0),
            "t2m": _number(entry.get("ta")),
            "rh": _number(entry.get("hr")),
            "station_pressure": _number(entry.get("pres")),
            "wind": _number(entry.get("vv")),      # vacio en las tres estaciones
            "gust": _number(entry.get("vmax")),    # idem
            "source": f"aemet_{station_id}_horario",
            "quality_flags": (f"external_station;{meta.get('distance_km')}km;"
                              f"{meta.get('elevation')}m;hourly_observation"),
            "retrieved_at": retrieved_at,
        })
    return rows


def run_hourly(site: Site = NERPIO, *, stations: tuple[str, ...] | None = None,
               pause: float = 2.0) -> int:
    """Las ultimas ~12 h de observacion, de todas las estaciones cercanas.

    No hay historico que pedir: lo que no se sondee hoy, no existe manana. Por
    eso esto corre tres veces al dia y no dos -- la ventana es de 12 h y con dos
    pasadas cualquier retraso abre un hueco irrecuperable.
    """
    key = _api_key()
    total = 0
    for station in (stations or tuple(STATIONS)):
        retrieved_at = utcnow()
        payload, note = _get(f"/observacion/convencional/datos/estacion/{station}", key)
        if not payload:
            print(f"[fetch_aemet horario] {station}: sin datos ({note})")
            time.sleep(pause)
            continue
        store_raw(payload, source=f"aemet_{station}_horario", site_id=site.site_id,
                  retrieved_at=retrieved_at)
        rows = _to_hourly_rows(payload, site, station, retrieved_at)
        write_canonical(rows, dataset="measurements", site_id=site.site_id)
        total += len(rows)
        print(f"[fetch_aemet horario] {station}: {len(rows):>3} horas "
              f"({rows[0]['t_utc']:%H:%M} -> {rows[-1]['t_utc']:%H:%M} UTC)"
              if rows else f"[fetch_aemet horario] {station}: 0 horas")
        time.sleep(pause)
    return total


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
    parser.add_argument("--hourly", action="store_true",
                        help="las ultimas ~12 h de observacion de las tres estaciones")
    args = parser.parse_args()
    if args.hourly:
        run_hourly()
    else:
        run(
            station=args.station,
            start=date.fromisoformat(args.start) if args.start else None,
        )
