"""La allsky de AstroCamp: la referencia independiente que faltaba.

Verificado el 29-08-2026 con permiso de AstroCamp. ZWO ASI462MC con indi-allsky
en `com2.astrocamp.es:85`, y el endpoint que sirve la serie es

    /indi-allsky/js/charts?camera_id=1&limit_s=<segundos>&night=0

que devuelve `sqm`, `stars`, `exp`, `temp`, `detection` y nueve `custom_*`, con
una muestra **cada 15 segundos**.

LO QUE PUEDE Y LO QUE NO, medido antes de escribir una linea de analisis:

* **No hay historico.** El endpoint tope en **4.612 puntos, 19,2 h**: pedir 72 h
  o 720 h devuelve exactamente los mismos 4.612. Asi que la allsky **no puede
  responder a la deriva del suelo de 2019-2026** (SPEC 2.2 bis) mirando hacia
  atras. Solo puede empezar una serie independiente HOY. Para lo retrospectivo
  hace falta la base de datos de indi-allsky del servidor, que si la tiene: la
  pregunta concreta a AstroCamp es un volcado de la tabla de imagenes con sus
  marcas de tiempo, no "si guardan historico".

* **El `sqm` NO esta en mag/arcsec2.** Son cuentas: entre 2,4e6 y 4,1e6 a
  mediodia. Es el brillo medio de una region de la imagen en ADU. Aqui se
  guarda como magnitud instrumental, `-2,5 log10(ADU)`, que es una
  transformacion monotona y por tanto no inventa ni pierde nada, con el punto
  cero declarado ARBITRARIO en `quality_flags`. Compararla con el TESS exige
  ajustar ese punto cero, y eso es justo el trabajo que hace falta: dos
  instrumentos independientes que deriven distinto separan el instrumento del
  cielo, que es la pregunta abierta.

* **Las marcas de tiempo vienen sin fecha**: `x` es `"HH:MM:SS"` y nada mas. Se
  reconstruyen hacia atras desde el momento de la descarga, retrocediendo un
  dia cada vez que el reloj da la vuelta. Por eso el raw no necesita envoltorio:
  el nombre del fichero del archivo YA lleva `retrieved_at`, y sin el estos
  datos no significan nada.

* De dia `stars` y `exp` valen cero, que es correcto y no es un fallo: no hay
  estrellas que contar con el Sol arriba.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import ssl
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from ..archive import store_raw, utcnow, write_canonical
from ..site import NERPIO, Site

BASE = "https://com2.astrocamp.es:85"
PATH = "/indi-allsky/js/charts"
SOURCE = "astrocamp_allsky"
LICENSE_TAG = "astrocamp-permiso-2026-08-29"
INSTRUMENT = "indi-allsky-cam1"

# Tope real del endpoint, medido: 4.612 puntos a 15 s. Se piden 5 h y se
# consulta cada 4, para que haya una hora de solape y ninguna caida corta deje
# un hueco. Las filas repetidas las quita la vista deduplicada.
DEFAULT_WINDOW_S = 5 * 3600
MEASURED_CAP_POINTS = 4612
CADENCE_S = 15

# El servidor responde con un certificado que no valida contra la cadena
# publica. Es el equipo del sitio en una IP conocida y el dato no es secreto:
# se acepta a proposito y se deja escrito, en vez de que parezca un descuido.
_CONTEXT = ssl.create_default_context()
_CONTEXT.check_hostname = False
_CONTEXT.verify_mode = ssl.CERT_NONE


def fetch(window_s: int = DEFAULT_WINDOW_S, camera_id: int = 1) -> dict:
    """La respuesta del proveedor, tal cual."""
    url = f"{BASE}{PATH}?" + urllib.parse.urlencode(
        {"camera_id": camera_id, "limit_s": window_s, "night": 0})
    request = urllib.request.Request(
        url, headers={"User-Agent": "astroweather/1.0 (+https://cabraspace.com)"})
    with urllib.request.urlopen(request, timeout=60, context=_CONTEXT) as response:
        return json.loads(response.read())


def _timestamps(labels: list[str], retrieved_at: dt.datetime,
                site: Site = NERPIO) -> list[dt.datetime]:
    """Reconstruye la fecha que el proveedor no manda.

    Los puntos llegan del mas viejo al mas nuevo y el ultimo es de hace
    segundos, asi que se ancla ahi y se retrocede: cada vez que la hora sube al
    ir hacia atras, es que se ha cruzado la medianoche.
    """
    if not labels:
        return []
    zona = ZoneInfo(site.timezone)
    local = retrieved_at.astimezone(zona)
    dia = local.date()
    salida: list[dt.datetime] = []
    anterior: dt.time | None = None
    for etiqueta in reversed(labels):
        try:
            hora = dt.time.fromisoformat(etiqueta)
        except ValueError:
            salida.append(None)
            continue
        if anterior is not None and hora > anterior:
            dia -= dt.timedelta(days=1)
        anterior = hora
        salida.append(dt.datetime.combine(dia, hora, tzinfo=zona)
                      .astimezone(dt.timezone.utc).replace(microsecond=0))
    return list(reversed(salida))


def to_rows(payload: dict, retrieved_at: dt.datetime,
            site: Site = NERPIO) -> list[dict]:
    """De las series del panel a filas de `measurements`."""
    series = payload.get("chart_data") or {}
    sqm = [p for p in series.get("sqm", []) if isinstance(p, dict)]
    if not sqm:
        return []
    momentos = _timestamps([p.get("x") for p in sqm], retrieved_at, site)
    estrellas = {p.get("x"): p.get("y") for p in series.get("stars", [])
                 if isinstance(p, dict)}

    filas = []
    for punto, cuando in zip(sqm, momentos):
        adu = punto.get("y")
        if cuando is None or not isinstance(adu, (int, float)) or adu <= 0:
            continue
        conteo = estrellas.get(punto.get("x"))
        filas.append({
            "site_id": site.site_id,
            "t_utc": cuando,
            # Magnitud INSTRUMENTAL: monotona en el ADU y con punto cero
            # arbitrario, que es justo lo que dice `quality_flags`. Convertirla
            # a mag/arcsec2 sin cruzarla antes con el TESS seria inventarse el
            # punto cero, y el punto cero es el objeto del estudio.
            "sky_mag_raw": -2.5 * math.log10(float(adu)),
            "instrument_type": "ALLSKY",
            "instrument_id": INSTRUMENT,
            "bandpass_id": "asi462mc-sin-filtro",
            "star_count": int(conteo) if isinstance(conteo, (int, float)) else None,
            "source": SOURCE,
            "quality_flags": "zero_point_arbitrario;mag_instrumental_desde_adu",
            "retrieved_at": retrieved_at,
        })
    return filas


def run(site: Site = NERPIO, *, window_s: int = DEFAULT_WINDOW_S,
        root: Path | None = None) -> dict:
    retrieved_at = utcnow()
    payload = fetch(window_s)
    store_raw(payload, source=SOURCE, site_id=site.site_id,
              retrieved_at=retrieved_at, root=root)
    filas = to_rows(payload, retrieved_at, site)
    write_canonical(filas, dataset="measurements", site_id=site.site_id, root=root)
    con_estrellas = sum(1 for f in filas if f["star_count"])
    return {"filas": len(filas), "con_estrellas": con_estrellas,
            "desde": filas[0]["t_utc"].isoformat() if filas else None,
            "hasta": filas[-1]["t_utc"].isoformat() if filas else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-s", type=int, default=DEFAULT_WINDOW_S,
                        help=f"tope medido del proveedor: {MEASURED_CAP_POINTS} puntos, 19,2 h")
    args = parser.parse_args()
    resumen = run(window_s=args.window_s)
    print(f"[allsky] {resumen['filas']} filas "
          f"({resumen['con_estrellas']} con estrellas contadas) "
          f"{resumen['desde']} -> {resumen['hasta']}")


if __name__ == "__main__":
    main()
