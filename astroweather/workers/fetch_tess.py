"""TESS photometer ingestion, live and historical (SPEC v8 sections 3.1 and 4.1).

The STARS4ALL photometer at AstroCamp (stars289) is the ground truth this
project leans on: sky brightness AND an IR sky temperature that works as a
cloud detector, once a minute, since 2019 (~450k samples/year, verified).

Two things this module refuses to do:

* Call the reading "SQM". TESS and SQM have different spectral responses, so
  the raw value is stored with its instrument and bandpass, and any SQM-scale
  equivalence has to be an explicit, versioned calibration.
* Trust a live-looking value. Stations emit mag=0 during daylight and some emit
  a stuck 13.25 forever, so every reading passes the plausibility filter and
  the sky magnitude is nulled -- not the row dropped -- when it fails, because
  the IR cloud signal in the same row is still good.
"""

from __future__ import annotations

import argparse
import time
import duckdb

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import requests

from ..archive import store_raw, utcnow, write_canonical
from ..invariants import is_plausible_sky_mag
from ..site import NERPIO, Site

GRAFANA_URL = "https://tess.dashboards.stars4all.eu/api/tsdb/query"
DATASOURCE_ID = 2          # shared InfluxDB behind every stars* dashboard
MEASUREMENT = "mqtt_consumer"
FIELDS = ("mag", "tamb", "tsky")
DEFAULT_STATION = "stars289"
HISTORY_START = datetime(2019, 1, 1, tzinfo=timezone.utc)
LICENSE_TAG = "stars4all-public-dashboard"
# The vendor's own cloud index, as used by the official STARS4ALL panels:
# 100 - 3*(tamb - tsky); below 40 counts as clear.
CLEAR_INDEX_THRESHOLD = 40.0


def query(sql: str, *, since_days: int = 3000, timeout: int = 180) -> list[dict[str, Any]]:
    response = requests.post(
        GRAFANA_URL,
        json={
            "from": f"now-{since_days}d",
            "to": "now",
            "queries": [
                {
                    "refId": "A",
                    "datasourceId": DATASOURCE_ID,
                    "rawQuery": True,
                    "query": sql,
                    "resultFormat": "time_series",
                }
            ],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["results"]["A"].get("series") or []


def _window_sql(station: str, start: datetime, end: datetime) -> str:
    fields = ",".join(f'"{name}"' for name in FIELDS)
    return (
        f'SELECT {fields} FROM "{MEASUREMENT}" '
        f"WHERE (\"name\"='{station}') "
        f"AND time >= '{start:%Y-%m-%dT%H:%M:%SZ}' AND time < '{end:%Y-%m-%dT%H:%M:%SZ}'"
    )


def _to_rows(series: list[dict], site: Site, station: str, retrieved_at: datetime) -> list[dict]:
    """Zip the per-field series (they share an index) into canonical rows."""
    by_field = {s["name"].split(".")[-1]: s["points"] for s in series}
    if "mag" not in by_field:
        return []

    rows: list[dict] = []
    for index, (magnitude, epoch) in enumerate(by_field["mag"]):
        ambient = by_field.get("tamb", [])[index][0] if "tamb" in by_field else None
        sky = by_field.get("tsky", [])[index][0] if "tsky" in by_field else None

        # Not every TESS reports temperatures. stars289 (AstroCamp) publishes a
        # literal 0 for both tamb and tsky (verified 2026-08-27; stars1/4/1000
        # do report real values), so its cloud index would be a constant 100 --
        # "overcast" forever. Treat the double zero as missing: at AstroCamp the
        # cloud signal comes from the CloudWatchers in cloud.html, not from here.
        if ambient == 0 and sky == 0:
            ambient = sky = None

        clear_flag = None
        cloud_index = None
        if ambient is not None and sky is not None:
            cloud_index = 100.0 - 3.0 * (ambient - sky)
            clear_flag = cloud_index < CLEAR_INDEX_THRESHOLD

        rows.append(
            {
                "site_id": site.site_id,
                "t_utc": datetime.fromtimestamp(epoch, timezone.utc),
                # Nulled when implausible (daylight zeros, stuck sensors); the
                # row survives because its cloud signal is still meaningful.
                "sky_mag_raw": float(magnitude) if is_plausible_sky_mag(magnitude) else None,
                "instrument_type": "TESS",
                "instrument_id": station,
                "bandpass_id": "TESS-W",
                "t_sky_ir": float(sky) if sky is not None else None,
                # Semantics matter: this is the raw MQTT tsky field, not a
                # broadband radiative temperature (SPEC v8 section 2.6).
                "t_sky_ir_semantics": "tess_mqtt_tsky_raw",
                "t2m": float(ambient) if ambient is not None else None,
                "clear_flag": clear_flag,
                "cloud_frac": cloud_index,
                "source": "stars4all_tess",
                "retrieved_at": retrieved_at,
            }
        )
    return rows


def run_live(site: Site = NERPIO, *, station: str = DEFAULT_STATION,
             minutos: int = 90) -> dict:
    """La ultima hora y media del fotometro, para tenerlo AHORA y no ayer.

    El relleno por meses es para construir el archivo; esto es otra cosa. Hasta
    hoy la lectura mas reciente que teniamos era de mas de un dia antes, asi
    que el producto publicaba un fondo de cielo PREDICHO al lado de un
    fotometro que estaba midiendo el de verdad y al que no le preguntabamos.

    Mismo camino que el resto -- raw archivado, filas canonicas, filtro de
    plausibilidad -- porque una lectura en vivo no merece menos disciplina que
    una historica: precisamente en vivo es cuando llegan los ceros de dia y los
    13,25 clavados.
    """
    fin = utcnow()
    inicio = fin - timedelta(minutes=minutos)
    retrieved_at = fin
    series = query(_window_sql(station, inicio, fin), since_days=2, timeout=45)
    if not series:
        return {"filas": 0, "ultima": None, "mag": None}
    store_raw(series, source=f"tess_{station}_live", site_id=site.site_id,
              retrieved_at=retrieved_at)
    filas = _to_rows(series, site, station, retrieved_at)
    write_canonical(filas, dataset="measurements", site_id=site.site_id)
    validas = [f for f in filas if f["sky_mag_raw"] is not None]
    ultima = max(validas, key=lambda f: f["t_utc"]) if validas else None
    return {"filas": len(filas), "validas": len(validas),
            "ultima": ultima["t_utc"].isoformat() if ultima else None,
            "mag": round(float(ultima["sky_mag_raw"]), 2) if ultima else None}


def _months(start: datetime, end: datetime) -> Iterator[tuple[datetime, datetime]]:
    """Trozos de como mucho un mes, empezando en `start` DE VERDAD.

    Redondear el primer trozo al dia 1 del mes esta bien para el relleno
    historico y es un desastre para ponerse al dia: `top_up` pedia dos horas de
    hueco y se traia el mes entero, 42.000 filas repetidas que el deduplicador
    esconde y el archivo paga.
    """
    cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cursor < end:
        following = (cursor + timedelta(days=32)).replace(day=1)
        yield max(cursor, start), min(following, end)
        cursor = following


def _already_done(site: Site, month: datetime, root: Path | None = None) -> bool:
    """Si ese mes ya tiene fichero. Sirve para el relleno HISTORICO y solo ahi.

    Un mes con algun parquet dentro no es un mes completo, y el mes EN CURSO
    nunca lo es: por eso `top_up` existe y `run(resume=True)` no se usa para
    ponerse al dia.
    """
    from ..archive import DATA_ROOT

    base = root or DATA_ROOT
    directory = (
        base / "measurements" / f"site={site.site_id}"
        / f"year={month:%Y}" / f"month={month:%m}"
    )
    return directory.exists() and any(directory.glob("*.parquet"))


def ultima_archivada(station: str = DEFAULT_STATION,
                     root: Path | None = None) -> datetime | None:
    """La medida mas reciente que ya esta en el archivo, o None si no hay nada."""
    from ..queries import connect

    try:
        fila = connect(root).execute(
            "SELECT max(t_utc) FROM measurements WHERE instrument_id = ?",
            [station]).fetchone()
    except (duckdb.Error, OSError):
        # La primera pasada de una maquina nueva no tiene archivo que consultar
        # y eso NO es un fallo. Cualquier otra cosa -- un esquema que cambio,
        # un bug aqui -- sube a `guarded` en vez de disfrazarse de "no hay
        # medidas todavia", que es indistinguible y nos costo seis dias en el
        # aerosol.
        return None
    if not fila or fila[0] is None:
        return None
    marca = fila[0]
    return marca if marca.tzinfo else marca.replace(tzinfo=timezone.utc)


# Cuanto mira `top_up` hacia atras, en dias enteros. Cuatro, porque lo peor
# que tiene que aguantar es un fin de semana con el colector caido: de la
# ultima pasada buena del viernes por la manana a la primera del lunes por la
# manana van ~72 h, y `tess` corre cada 24 h, asi que el lunes puede pasar
# hasta un dia mas tarde. 4 d = 96 h cubre eso con margen. Mas no compra nada:
# un hueco mas viejo que la ventana se pide igual, desde la ultima archivada.
VENTANA_DIAS = 4
# Un hueco es mas de este tiempo sin muestra. La cadencia es de una por minuto,
# asi que 5 min no confunde el jitter del MQTT con un agujero.
HUECO_MINIMO = timedelta(minutes=5)
# Dos huecos separados por menos que esto se piden juntos: la fila que se
# repite en medio cuesta menos que otra peticion al servicio comunitario.
FUSIONAR_HUECOS = timedelta(hours=1)


def instantes_archivados(station: str, desde: datetime, hasta: datetime,
                         root: Path | None = None) -> list[datetime]:
    """Las marcas de tiempo que ya hay en el archivo entre `desde` y `hasta`."""
    from ..queries import connect

    try:
        filas = connect(root).execute(
            "SELECT DISTINCT t_utc FROM measurements "
            "WHERE instrument_id = ? AND t_utc >= ? AND t_utc < ? ORDER BY t_utc",
            [station, desde, hasta]).fetchall()
    except (duckdb.Error, OSError):
        # Mismo criterio que `ultima_archivada`: sin archivo no hay nada dentro.
        return []
    return [t if t.tzinfo else t.replace(tzinfo=timezone.utc) for (t,) in filas]


def huecos(instantes: list[datetime], desde: datetime, hasta: datetime, *,
           minimo: timedelta = HUECO_MINIMO,
           fusionar: timedelta = FUSIONAR_HUECOS) -> list[tuple[datetime, datetime]]:
    """Los tramos [inicio, fin) de la ventana donde el archivo no tiene muestras.

    Los bordes son EXCLUSIVOS de lo que ya hay: el tramo empieza un segundo
    despues de la ultima muestra antes del hueco y acaba en la primera
    despues, y `_window_sql` pide `>= inicio AND < fin`. Asi rellenar un hueco
    no vuelve a escribir las filas que lo rodean.
    """
    tramos: list[tuple[datetime, datetime]] = []
    anterior = None   # None: el hueco de cabeza empieza en `desde`, incluido
    for instante in [*instantes, hasta]:
        inicio = desde if anterior is None else anterior + timedelta(seconds=1)
        referencia = desde if anterior is None else anterior
        if instante - referencia > minimo and instante > inicio:
            tramos.append((inicio, instante))
        anterior = instante

    fusionados: list[tuple[datetime, datetime]] = []
    for inicio, fin in tramos:
        if fusionados and inicio - fusionados[-1][1] < fusionar:
            fusionados[-1] = (fusionados[-1][0], fin)
        else:
            fusionados.append((inicio, fin))
    return fusionados


def top_up(site: Site = NERPIO, *, station: str = DEFAULT_STATION,
           root: Path | None = None, dias: int = VENTANA_DIAS,
           pause: float = 1.0) -> int:
    """Pedir los HUECOS de los ultimos `dias` dias enteros, no solo la cola.

    Hasta el 26-10-2026 se pedia desde la ULTIMA medida archivada hasta ahora.
    Parecia lo mismo y no lo era: `tess_vivo` escribe la ultima hora y media
    cada pocos minutos, asi que la ultima archivada siempre era de hace un
    rato y las horas que quedaban ENTRE dos pasadas del vivo (el PC apagado,
    el runner que no arranca) no se pedian nunca. El retroanalisis lo midio:
    en octubre de 2026 solo 103 de 245 horas oscuras tenian fotometro, y
    STARS4ALL las servia todas.

    Por que huecos y no la ventana entera: pedir 4 dias enteros cada pasada
    reescribe ~5.700 filas repetidas diarias (4 x 1.440), el mismo pecado, en
    pequenio, que el mes entero que se abandono (ver `_job_tess`). Mirando
    antes que hay en el archivo, una pasada sin huecos no escribe NADA, y la
    vista deduplicada queda para lo que es: el solape de los bordes.

    Las horas que STARS4ALL tampoco tiene (dia, estacion caida) se vuelven a
    pedir en cada pasada mientras sigan en la ventana y vuelven vacias, sin
    escribir. Son pocas peticiones -- los huecos cercanos se piden juntos -- y
    es el precio de no confundir "aun no lo tengo" con "no existe".
    """
    ultima = ultima_archivada(station, root)
    if ultima is None:
        # Archivo vacio: esto no es ponerse al dia, es el relleno historico.
        return run(site, station=station, root=root, pause=pause)

    hasta = utcnow()
    desde = (hasta - timedelta(days=dias)).replace(hour=0, minute=0, second=0)
    # Un apagon mas largo que la ventana no se queda a medias: si la ultima
    # medida es anterior, la ventana empieza en ella.
    desde = min(desde, ultima)

    tramos = huecos(instantes_archivados(station, desde, hasta, root), desde, hasta)
    if not tramos:
        print(f"[fetch_tess] sin huecos desde {desde:%Y-%m-%d %H:%M} UTC")
        return 0

    horas = sum((fin - inicio).total_seconds() for inicio, fin in tramos) / 3600
    print(f"[fetch_tess] {len(tramos)} hueco(s) desde {desde:%Y-%m-%d %H:%M} UTC "
          f"({horas:.1f} h)")
    total = 0
    for inicio, fin in tramos:
        total += run(site, station=station, start=inicio, end=fin,
                     resume=False, root=root, pause=pause)
    return total


def run(
    site: Site = NERPIO,
    *,
    station: str = DEFAULT_STATION,
    start: datetime | None = None,
    end: datetime | None = None,
    resume: bool = True,
    root: Path | None = None,
    pause: float = 1.0,
) -> int:
    """Relleno HISTORICO, un mes por peticion.

    Para ponerse al dia se usa `top_up`, que pide solo los huecos: `resume` aqui
    salta meses que ya tienen fichero, y el mes en curso siempre lo tiene.
    """
    start = start or HISTORY_START
    end = end or utcnow()
    total = 0

    for month_start, month_end in _months(start, end):
        if resume and _already_done(site, month_start, root):
            print(f"[fetch_tess] {month_start:%Y-%m} ya archivado, salto")
            continue

        retrieved_at = utcnow()
        series = query(_window_sql(station, month_start, month_end))
        if not series:
            print(f"[fetch_tess] {month_start:%Y-%m} sin datos")
            continue

        store_raw(series, source=f"tess_{station}", site_id=site.site_id,
                  retrieved_at=retrieved_at, root=root)
        rows = _to_rows(series, site, station, retrieved_at)
        write_canonical(rows, dataset="measurements", site_id=site.site_id, root=root)
        valid = sum(1 for row in rows if row["sky_mag_raw"] is not None)
        total += len(rows)
        print(f"[fetch_tess] {month_start:%Y-%m}: {len(rows):>6} muestras "
              f"({valid} con mag valida)")
        time.sleep(pause)   # be a good citizen with a community service

    print(f"[fetch_tess] total {total} muestras archivadas")
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", default=DEFAULT_STATION)
    parser.add_argument("--live", action="store_true",
                        help="solo la ultima hora y media, para el panel")
    parser.add_argument("--start", help="YYYY-MM (por defecto 2019-01)")
    parser.add_argument("--months", type=int, help="solo N meses desde --start")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    begin = (
        datetime.strptime(args.start, "%Y-%m").replace(tzinfo=timezone.utc)
        if args.start else HISTORY_START
    )
    finish = None
    if args.months:
        finish = begin
        for _ in range(args.months):
            finish = (finish + timedelta(days=32)).replace(day=1)

    run(station=args.station, start=begin, end=finish, resume=not args.no_resume)
