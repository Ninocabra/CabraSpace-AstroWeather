"""AstroCamp on-site sensors, scraped from cloud.html (SPEC v8 3.1 note 7).

This page is the only local cloud signal we have: the TESS photometer at the
site reports zeros for its temperature channels, so the CloudWatchers here are
the IR sky sensors the dew and cloud models need.

Three things the page does that a naive parser gets wrong:

* It lists dead sensors next to live ones, each with its own timestamp. One
  CloudWatcher has been frozen since July and a weather station since 2025, so
  rows are selected by timestamp freshness -- never by position in the table.
* Wind is published in km/h while every formula in the spec is in m/s.
* It carries a safety log with the observatory's own Clear/Cloudy verdicts,
  which is operational ground truth we would otherwise have to infer.

It is plain HTTP on a non-standard port with no SLA, so every parse step
degrades to "missing" instead of raising.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
import requests

from ..archive import DATA_ROOT, store_raw, utcnow, write_canonical
from ..invariants import is_fresh
from ..site import NERPIO, Site

URL = "http://com1.astrocamp.es:81/cloud.html"
LICENSE_TAG = "astrocamp-public-page"
KMH_TO_MS = 1 / 3.6
# The page timestamps are UTC, not local time: its own "UT" ephemeris field
# matches them to the second (verified 2026-08-27). Reading them as Europe/Madrid
# shifted every sensor two hours into the past and made live units look stale.


def _cells(row: str) -> list[str]:
    raw = re.findall(r"<t[dh][^>]*>(.*?)(?=<t[dh]|</tr|$)", row, re.S)
    return [re.sub(r"<[^>]+>", " ", html.unescape(cell)).strip() for cell in raw]


def _tables(page: str) -> list[str]:
    return re.findall(r"<table[^>]*>.*?</table>", page, re.S)


def _rows(table: str) -> list[list[str]]:
    found = re.findall(r"<tr[^>]*>.*?(?=<tr|</table)", table, re.S)
    return [cells for cells in (_cells(row) for row in found) if cells]


def _timestamp(text: str) -> datetime | None:
    try:
        naive = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return None
    return naive.replace(tzinfo=timezone.utc)


def _number(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse(page: str) -> dict:
    """Pull the structured content out of the page, tolerating missing tables."""
    result: dict = {
        "cloudwatchers": [],
        "meteo": [],
        "roofs": {},
        "safety_log": [],
        "open_hours": {},
        # La franja de estado de arriba de la pagina, que no se miraba. Trae
        # DOS veredictos de cielo independientes -- Sky (3) y Sky (4) -- y el
        # estado de red electrica e internet, que es justo lo que el gate
        # `hardware_ok` esperaba y nadie le daba.
        "status": {},
    }

    for table in _tables(page):
        rows = _rows(table)
        if not rows:
            continue
        # Tables may open with a stray empty row or a title row, so the header
        # is located by its signature instead of by position.
        def _is_header(row: list[str]) -> bool:
            return (
                (row and row[0].startswith("Humidity ("))
                or row[:3] == ["Cloud", "Date time", "SkyTemp"]
                or row[:3] == ["Meteo", "Date time", "Temp Out"]
                or row[:2] == ["Date time", "AstroCamp Safety log"]
                or (row and row[0].startswith("S1R"))
                or (row and row[0].startswith("Night"))
            )

        head_index = next((i for i, row in enumerate(rows) if _is_header(row)), None)
        if head_index is None:
            continue
        header, rows = rows[head_index], rows[head_index:]

        if header and header[0].startswith("Humidity ("):
            # Pares nombre/valor: "Sky (4)", "Cloudy", "Mains (1)", "Mains OK"...
            for name, state in zip(header[::2], header[1::2]):
                result["status"][name.strip()] = state.strip()

        elif header[:3] == ["Cloud", "Date time", "SkyTemp"]:
            for row in rows[1:]:
                if len(row) < 5:
                    continue
                result["cloudwatchers"].append({
                    "id": "cloudwatcher" + row[0],
                    "t_utc": _timestamp(row[1]),
                    "sky_temp": _number(row[2]),
                    "ambient": _number(row[3]),
                    "sky_index": _number(row[4]),
                })

        elif header[:3] == ["Meteo", "Date time", "Temp Out"]:
            for row in rows[1:]:
                if len(row) < 10:
                    continue
                wind_kmh = _number(row[5])
                result["meteo"].append({
                    "id": "meteo" + row[0],
                    "t_utc": _timestamp(row[1]),
                    "t_out": _number(row[2]),
                    "rh_out": _number(row[3]),
                    "dew_point": _number(row[4]),
                    # km/h on the page, m/s everywhere in this codebase.
                    "wind_ms": wind_kmh * KMH_TO_MS if wind_kmh is not None else None,
                    "wind_dir": _number(row[6]),
                    "pressure": _number(row[9]),
                    "rain": _number(row[10]) if len(row) > 10 else None,
                })

        elif header and header[0].startswith("S1R"):
            for row in rows:
                for name, state in zip(row[::2], row[1::2]):
                    if state in ("Open", "Closed"):
                        result["roofs"][name] = state == "Open"

        elif header[:2] == ["Date time", "AstroCamp Safety log"]:
            for row in rows[1:]:
                if len(row) < 2 or not _timestamp(row[0]):
                    continue
                result["safety_log"].append({
                    "t_utc": _timestamp(row[0]),
                    "message": row[1],
                })

        elif header and header[0].startswith("Night"):
            for row in rows[1:]:
                if len(row) >= 14 and row[0].isdigit():
                    months = [int(v) if v.isdigit() else None for v in row[1:13]]
                    result["open_hours"][row[0]] = {
                        "months": months,
                        "total": int(row[13]) if row[13].isdigit() else None,
                    }
    return result


def _sensor_verdict(status: dict, sensor_id: str) -> bool | None:
    """El veredicto DE ESE sensor, de la franja de estado.

    Hasta el 2026-08-28 se estampaba en todas las filas un unico veredicto
    sacado del log de seguridad, y no cuadraba con el propio sensor: decia
    despejado en 71 de 181 lecturas mientras el IR marcaba -28 C, que es cielo
    claro sin discusion. La franja publica un Clear/Cloudy POR sensor -- Sky (3)
    y Sky (4) son dos aparatos distintos -- y eso es lo que hay que guardar.
    """
    numero = "".join(c for c in sensor_id if c.isdigit())
    estado = status.get(f"Sky ({numero})")
    if estado == "Clear":
        return True
    if estado == "Cloudy":
        return False
    return None


def _sky_verdict(safety_log: list[dict]) -> bool | None:
    """The observatory's own latest Clear/Cloudy call, if it published one."""
    for entry in safety_log:
        message = entry["message"]
        if "is Clear" in message:
            return True
        if "is Cloudy" in message:
            return False
    return None


def to_rows(parsed: dict, site: Site, retrieved_at: datetime) -> list[dict]:
    rows: list[dict] = []
    verdict = _sky_verdict(parsed["safety_log"])

    for sensor in parsed["cloudwatchers"]:
        if not is_fresh(sensor["t_utc"]):
            continue                      # frozen units sit next to live ones
        rows.append({
            "site_id": site.site_id,
            "t_utc": sensor["t_utc"],
            "t_sky_ir": sensor["sky_temp"],
            "sky_index": sensor["sky_index"],
            # Vendor semantics: an AAG-style IR sky temperature, not a
            # broadband radiative temperature (SPEC v8 section 2.6).
            "t_sky_ir_semantics": "aag_cloudwatcher_skytemp",
            # OJO: es el sensor `ambient` del AAG, que va en la caja y de dia se
            # calienta al sol -- medido, hasta +18 C sobre la estacion. De NOCHE
            # coinciden en 0,02 C, asi que el indice de nubes no esta sesgado,
            # pero quien promedie `t2m` sin filtrar por instrument_type mezcla
            # dos aparatos distintos.
            "t2m": sensor["ambient"],
            "clear_flag": _sensor_verdict(parsed["status"], sensor["id"]) or verdict,
            "instrument_type": "CloudWatcher",
            "instrument_id": sensor["id"],
            "source": "astrocamp_cloudwatcher",
            "retrieved_at": retrieved_at,
        })

    for station in parsed["meteo"]:
        if not is_fresh(station["t_utc"]):
            continue
        rows.append({
            "site_id": site.site_id,
            "t_utc": station["t_utc"],
            "t2m": station["t_out"],
            "rh": station["rh_out"],
            "dew_point": station["dew_point"],
            "wind": station["wind_ms"],
            "station_pressure": station["pressure"],
            "instrument_type": "WeatherStation",
            "instrument_id": station["id"],
            "source": "astrocamp_meteo",
            "retrieved_at": retrieved_at,
        })

    if parsed["roofs"]:
        open_count = sum(parsed["roofs"].values())
        rows.append({
            "site_id": site.site_id,
            "t_utc": retrieved_at,
            "roof_open": open_count > 0,
            # Nobody observing is not the same as bad weather: this is the
            # censoring flag the learning loops need (SPEC v8 section 4.4).
            "censored": open_count == 0,
            "censor_reason": None if open_count else "all_roofs_closed",
            "instrument_type": "RoofStatus",
            "instrument_id": "astrocamp_roofs",
            "source": "astrocamp_roofs",
            # Al recuento de techos se le suma el estado de hardware de la
            # franja de arriba: el gate `hardware_ok` existia sin nadie que lo
            # alimentara, y la pagina publica red, internet y humedad.
            "quality_flags": ";".join(
                ["open=%d/%d" % (open_count, len(parsed["roofs"]))]
                + [f"{k}={v}" for k, v in sorted(parsed["status"].items())
                   if k.startswith(("Mains", "Internet", "Humidity"))]),
            "retrieved_at": retrieved_at,
        })
    return rows


def save_open_hours(parsed: dict, site: Site, root: Path | None = None) -> Path | None:
    """The 2012-> monthly open-hours table: the site's own climatology baseline."""
    if not parsed["open_hours"]:
        return None
    base = root or DATA_ROOT
    base.mkdir(parents=True, exist_ok=True)
    path = base / ("open_hours_" + site.site_id + ".json")
    path.write_text(json.dumps(parsed["open_hours"], indent=2), encoding="utf-8")
    return path


def run(site: Site = NERPIO) -> int:
    retrieved_at = utcnow()
    response = requests.get(URL, timeout=45)
    response.raise_for_status()
    store_raw(response.text, source="astrocamp_cloudhtml", site_id=site.site_id,
              retrieved_at=retrieved_at, suffix="html")

    parsed = parse(response.text)
    rows = to_rows(parsed, site, retrieved_at)
    write_canonical(rows, dataset="measurements", site_id=site.site_id)
    save_open_hours(parsed, site)

    live_cw = sum(1 for row in rows if row.get("instrument_type") == "CloudWatcher")
    live_meteo = sum(1 for row in rows if row.get("instrument_type") == "WeatherStation")
    open_roofs = sum(parsed["roofs"].values())
    print(
        "[scrape_astrocamp] CloudWatchers vivos %d/%d, estaciones %d/%d, "
        "techos abiertos %d/%d, cielo=%s"
        % (live_cw, len(parsed["cloudwatchers"]), live_meteo, len(parsed["meteo"]),
           open_roofs, len(parsed["roofs"]), _sky_verdict(parsed["safety_log"]))
    )
    return len(rows)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    run()
