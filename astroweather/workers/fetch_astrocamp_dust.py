"""PM2.5 en superficie de AstroCamp, digitalizado de su grafico (SPEC v8 2.1).

Es la PRIMERA medida in situ de aerosol que tenemos. Todo lo demas -- AOD
total, AOD de polvo, polvo en superficie -- sale de CAMS, o sea de un modelo:
`site_aod()` corrige por cota con `AEROSOL_SCALE_HEIGHT_M = 1700`, un numero
que lleva desde el principio con el comentario "calibrable contra AERONET" y
que nadie ha calibrado nunca porque no habia contra que.

POR QUE SE LEE UNA IMAGEN, QUE NO ES NORMAL. AstroCamp publica ese sensor solo
como PNG. Se sondeo el servidor entero el 08-09-2026: `/img/` es el unico
listado abierto y solo tiene PNGs; `web.astrocamp.es:81` sirve un JSON estatico
de once campos sin polvo; el iframe del panel es el Grafana del TESS; y el
puerto 82 es su Flask privado con login, que no se toca. No hay CSV, ni JSON,
ni API. O se lee el dibujo o no hay dato.

LO QUE HACE QUE EL DIBUJO SE PUEDA LEER, y lo que pasa si deja de ser cierto:

* El eje Y esta FIJO en 0-98 ug/m3, con el dato entre 4 y 11. Al no
  autoescalarse, el mapa pixel->valor es constante. Si un dia AstroCamp lo
  reescala, la curva se seguiria extrayendo tan campante con los numeros
  multiplicados por cinco y NADIE SE ENTERARIA: es exactamente el modo de
  fallo silencioso que costo seis dias de aerosol sin especies en agosto. Por
  eso la escala no se supone: se mide la geometria en cada pasada y se compara
  con `REF`. Si no cuadra, esto revienta y `scheduler.guarded` lo grita.

* La curva se aisla como la mancha azul QUE TOCA EL SUELO DEL EJE. La muestra
  de color de la leyenda es otra mancha, suelta en el aire, y asi no hay que
  adivinar donde cae ni suponer que el dato es pequeno -- que es justo lo que
  deja de valer en un episodio de calima, el unico caso que importa.

* La referencia vertical son las MARCAS del eje, no las lineas de rejilla. Con
  la curva alta el relleno tapa la rejilla de debajo: medido, a 45 ug/m3
  desaparecen 4 de las 9 lineas y el guardian saltaba en falso. Las marcas
  viven fuera de la caja y el dato no las puede tapar.

* El eje X se ancla en las marcas verticales, que son horas EN PUNTO multiplo
  de 3. El `Last-Modified` del PNG solo sirve para saber DE QUE hora se trata;
  el ajuste fino sale de la rejilla. Medido: el desfase entre el borde derecho
  y el Last-Modified es de ~3,6 min, que son 2,5 pixeles.

* La leyenda es OPACA y esta arriba a la izquierda, asi que por encima de unos
  88 ug/m3 tapa la curva en su tercio de columnas. Sin tratarlo, el extractor
  devolvia 88 donde habia 95 -- un 7% de menos, en silencio, y solo en el
  episodio mas fuerte que puede pasar. Esas columnas se marcan como DESCONOCIDAS
  en vez de publicar el borde de la leyenda como si fuera el dato.

PRECISION, medida con curvas de valor conocido inyectadas en el PNG real
(`tests/test_astrocamp_dust.py`): error maximo 0,23 ug/m3 de 4 a 86 ug/m3
-- 86 es lo mas alto que se puede medir entero, porque ahi empieza la leyenda.
Lo que toca el techo del eje esta RECORTADO, no medido, y sale marcado.

Se publica media HORARIA y no la serie por pixel. El PNG da un punto cada 1,4
min, pero eso es resolucion del dibujo, no cadencia del sensor: inventaria una
frecuencia que el proveedor no ha dicho. La media horaria es ademas la que se
cruza con el AOD de CAMS, que es horario. Nada se pierde: el PNG se archiva
entero y cualquier otra agregacion se puede recalcular desde el raw.
"""

from __future__ import annotations

import argparse
import datetime as dt
from collections import defaultdict
from email.utils import parsedate_to_datetime
from pathlib import Path

import numpy as np
import requests
from PIL import Image
from scipy import ndimage

from ..archive import store_raw, utcnow, write_canonical
from ..site import NERPIO, Site

URL = "http://com1.astrocamp.es:81/img/dust.png"
SOURCE = "astrocamp_dust_png"
LICENSE_TAG = "astrocamp-public-page"
INSTRUMENT = "astrocamp_dust"

# Geometria de referencia, medida el 08-09-2026 sobre el PNG real de 1280x960.
# No es decoracion: es contra lo que se comprueba que el grafico sigue siendo
# el que se calibro.
REF = dict(n_marcas_y=9, dy=65.375, paso=10.0, alto_caja=651, ancho_caja=990,
           horas_de_ancho=24.0)
TOL_DY = 0.75
TOL_CAJA = 4
TOL_HORAS = 1.5

# Colores del grafico: la linea y su relleno.
AZUL_LINEA = np.array([0, 0, 255])
AZUL_RELLENO = np.array([101, 127, 204])
# El verde del area de datos. Sirve para localizar por descarte la leyenda,
# que es opaca y TAPA la curva por encima de ~90 ug/m3 (medido).
FONDO = np.array([204, 229, 204])

# Minimo de pixeles para dar una hora por medida. Una hora entera son ~41.
PX_MINIMOS_POR_HORA = 20


class GeometriaCambiada(RuntimeError):
    """El grafico ya no es el que se calibro: no se publica nada.

    Sube hasta `scheduler.guarded`, que registra el fallo y reintenta. Que
    tumbe el trabajo es lo que se quiere: el otro camino es publicar numeros
    mal escalados con el trabajo en verde, y eso ya nos costo seis dias.
    """


def _grupos(indices: np.ndarray) -> np.ndarray:
    """Centro de cada racha de pixeles contiguos (una marca son 2-3 pixeles)."""
    if len(indices) == 0:
        return np.array([])
    grupos, actual = [], [indices[0]]
    for valor in indices[1:]:
        if valor - actual[-1] <= 2:
            actual.append(valor)
        else:
            grupos.append(float(np.mean(actual)))
            actual = [valor]
    grupos.append(float(np.mean(actual)))
    return np.array(grupos)


def fetch(url: str = URL) -> tuple[bytes, dt.datetime]:
    """El PNG tal cual y la hora en que el servidor lo dibujo.

    El `Last-Modified` es la unica fecha que trae la imagen; sin el no se sabe
    a que dia corresponde el eje de tiempos, asi que si falta no se sigue.
    """
    respuesta = requests.get(url, timeout=45)
    respuesta.raise_for_status()
    cabecera = respuesta.headers.get("Last-Modified")
    if not cabecera:
        raise GeometriaCambiada(
            "el PNG llega sin Last-Modified y es la unica fecha que tiene: "
            "sin ella el eje de tiempos seria una invencion")
    return respuesta.content, parsedate_to_datetime(cabecera).astimezone(dt.timezone.utc)


def digitiza(png: str | Path | bytes, last_modified: dt.datetime):
    """De la imagen a (momentos, ug/m3, diagnostico). Revienta si la geometria cambio."""
    if isinstance(png, bytes):
        import io
        imagen = Image.open(io.BytesIO(png))
    else:
        imagen = Image.open(png)
    a = np.asarray(imagen.convert("RGB")).astype(int)

    # --- la caja del eje: filas y columnas casi enteras de pixel oscuro
    oscuro = a.sum(2) < 200
    alto, ancho = oscuro.shape
    xs = np.where(oscuro.sum(0) > 0.3 * alto)[0]
    ys = np.where(oscuro.sum(1) > 0.3 * ancho)[0]
    if len(xs) < 2 or len(ys) < 2:
        raise GeometriaCambiada("no se encuentra el marco del eje en la imagen")
    x0, x1, y0, y1 = xs.min() + 2, xs.max() - 1, ys.min() + 2, ys.max() - 1

    # --- las marcas del eje, que el dato no puede tapar
    marcas_y = _grupos(np.where(oscuro[:, x0 - 8:x0 - 2].mean(1) > 0.5)[0])
    marcas_x = _grupos(np.where(oscuro[y1 + 2:y1 + 8, :].mean(0) > 0.5)[0])
    marcas_y = marcas_y[(marcas_y > y0) & (marcas_y < y1 - 1)]   # la del cero cae en el marco
    marcas_x = marcas_x[(marcas_x > x0) & (marcas_x < x1)]
    if len(marcas_y) < 2 or len(marcas_x) < 2:
        raise GeometriaCambiada(
            f"marcas insuficientes: {len(marcas_y)} en Y y {len(marcas_x)} en X")
    dy = float(np.mean(np.diff(marcas_y)))

    # --- el guardian
    problemas = []
    if len(marcas_y) != REF["n_marcas_y"]:
        problemas.append(f"{len(marcas_y)} marcas en el eje Y en vez de {REF['n_marcas_y']}")
    if abs(dy - REF["dy"]) > TOL_DY:
        problemas.append(f"separacion {dy:.2f} px en vez de {REF['dy']:.2f}")
    if (abs((y1 - y0) - REF["alto_caja"]) > TOL_CAJA
            or abs((x1 - x0) - REF["ancho_caja"]) > TOL_CAJA):
        problemas.append(
            f"caja {x1 - x0}x{y1 - y0} en vez de {REF['ancho_caja']}x{REF['alto_caja']}")
    # El suelo del eje tiene que ser el cero: la marca mas baja vale `paso`,
    # asi que bajar una separacion mas debe caer en el marco. Es la unica
    # comprobacion que ata la escala al origen sin leer las etiquetas.
    cero = marcas_y[-1] + dy
    if abs(cero - y1) > 3:
        problemas.append(f"el cero cae en y={cero:.1f} y el marco esta en {y1}")
    px_h = float(np.polyfit(np.arange(len(marcas_x)) * 3.0, marcas_x, 1)[0])
    horas = (x1 - x0) / px_h
    if abs(horas - REF["horas_de_ancho"]) > TOL_HORAS:
        problemas.append(f"el grafico abarca {horas:.1f} h en vez de {REF['horas_de_ancho']:.0f}")
    if problemas:
        raise GeometriaCambiada("; ".join(problemas))

    def valor(y: float) -> float:
        return (marcas_y[-1] - y) / dy * REF["paso"] + REF["paso"]

    # --- la curva: la mancha azul que toca el suelo del eje
    azul = (np.all(np.abs(a - AZUL_LINEA) < 40, axis=2)
            | np.all(np.abs(a - AZUL_RELLENO) < 30, axis=2))
    etiquetas, _ = ndimage.label(azul)
    tocan_el_suelo = set(etiquetas[y1 - 1, x0:x1]) - {0}
    curva = np.isin(etiquetas, list(tocan_el_suelo))

    # --- la leyenda: dentro del eje, lo unico que no es fondo, rejilla ni curva
    #
    # Se localiza por descarte y no por el color de su cara: la linea azul de
    # muestra y el texto la parten en tres trozos, y quedarse con el mayor daba
    # un borde inferior 12 px demasiado alto -- justo lo que hacia que la
    # oclusion no se detectara.
    dentro = a[y0:y1, x0:x1]
    fondo = np.all(np.abs(dentro - FONDO) < 12, axis=2)
    rejilla = np.all(np.abs(dentro - 176) < 25, axis=2)
    curva_color = (np.all(np.abs(dentro - AZUL_LINEA) < 60, axis=2)
                   | np.all(np.abs(dentro - AZUL_RELLENO) < 45, axis=2))
    ajeno = ~(fondo | rejilla | curva_color)
    bloques, cuantos = ndimage.label(ajeno, structure=np.ones((3, 3)))
    leyenda = None
    if cuantos:
        tamanos = ndimage.sum(ajeno, bloques, range(1, cuantos + 1))
        mayor = int(np.argmax(tamanos)) + 1
        if tamanos[mayor - 1] > 1000:             # una caja, no un borde antialiasado
            ys_l, xs_l = np.where(bloques == mayor)
            leyenda = (xs_l.min() + x0, xs_l.max() + x0, ys_l.min() + y0, ys_l.max() + y0)

    serie = np.full(x1 - x0, np.nan)
    ocluido = np.zeros(x1 - x0, bool)
    for i in range(x1 - x0):
        encima = np.where(curva[y0:y1, x0 + i])[0]
        if not len(encima):
            continue
        cima = encima[0] + y0
        # Si la cima de la curva se apoya justo en el borde de abajo de la
        # leyenda, lo que se esta midiendo es la leyenda, no el dato: el valor
        # real es ESE O MAYOR. Publicarlo seria inventarse un techo.
        if (leyenda is not None and leyenda[0] - 1 <= x0 + i <= leyenda[1] + 1
                and 0 <= cima - leyenda[3] <= 3):
            ocluido[i] = True
            continue
        serie[i] = valor(cima)

    # --- el eje de tiempos, anclado a la marca de la derecha (hora en punto)
    aprox = last_modified - dt.timedelta(hours=(x1 - 1 - marcas_x[-1]) / px_h)
    ancla = aprox.replace(minute=0, second=0, microsecond=0)
    ancla += dt.timedelta(hours=round((aprox - ancla).total_seconds() / 3600))
    momentos = [ancla + dt.timedelta(hours=((x0 + i) - marcas_x[-1]) / px_h)
                for i in range(x1 - x0)]

    techo = valor(y0)
    diagnostico = {
        "px_h": px_h, "dy": dy, "horas": horas,
        "desfase_min": (ancla - aprox).total_seconds() / 60,
        "techo": techo,
        "px_en_el_techo": int(np.sum(np.abs(serie - techo) < REF["paso"] * 0.1)),
        "px_ocultos": int(ocluido.sum()),
        "ocluido": ocluido,
    }
    return momentos, serie, diagnostico


def to_rows(momentos, serie, diagnostico: dict, site: Site,
            retrieved_at: dt.datetime) -> list[dict]:
    """Medias horarias UTC. Una hora con pocos pixeles no es una hora medida."""
    por_hora: dict[dt.datetime, list[float]] = defaultdict(list)
    horas_ocultas: set[dt.datetime] = set()
    ocluido = diagnostico.get("ocluido")
    for indice, (cuando, valor) in enumerate(zip(momentos, serie)):
        hora = cuando.replace(minute=0, second=0, microsecond=0)
        if ocluido is not None and ocluido[indice]:
            horas_ocultas.add(hora)
        if np.isfinite(valor):
            por_hora[hora].append(float(valor))

    techo = diagnostico["techo"]
    filas = []
    for hora in sorted(por_hora):
        muestras = por_hora[hora]
        if len(muestras) < PX_MINIMOS_POR_HORA:
            continue                     # hora a medias en el borde del grafico
        banderas = [f"digitalizado_de_png;px={len(muestras)}",
                    f"paso_eje={REF['paso']:g}"]
        # Lo que toca el techo es una cota inferior, no una medida. Se publica
        # marcado en vez de callarlo: silenciar justo el episodio grande seria
        # perder el unico dato que importa.
        if any(abs(v - techo) < REF["paso"] * 0.1 for v in muestras):
            banderas.append("recortado_por_el_techo_del_eje")
        # La media de esta hora se ha calculado SIN las columnas que la leyenda
        # tapa, y esas eran las altas: el numero es un minimo.
        if hora in horas_ocultas:
            banderas.append("parcialmente_oculto_por_la_leyenda")
        filas.append({
            "site_id": site.site_id,
            "t_utc": hora,
            "pm2_5": float(np.mean(muestras)),
            "instrument_type": "AirQuality",
            "instrument_id": INSTRUMENT,
            "source": SOURCE,
            "quality_flags": ";".join(banderas),
            "retrieved_at": retrieved_at,
        })
    return filas


def run(site: Site = NERPIO, *, root: Path | None = None) -> dict:
    retrieved_at = utcnow()
    png, dibujado = fetch()
    store_raw(png, source=SOURCE, site_id=site.site_id,
              retrieved_at=retrieved_at, suffix="png", root=root)
    momentos, serie, diagnostico = digitiza(png, dibujado)
    filas = to_rows(momentos, serie, diagnostico, site, retrieved_at)
    write_canonical(filas, dataset="measurements", site_id=site.site_id, root=root)

    if diagnostico["px_ocultos"]:
        print(f"::warning::[polvo] la leyenda tapa {diagnostico['px_ocultos']} "
              f"columnas: el dato subio por encima de ~88 ug/m3")
    recortadas = sum(1 for f in filas
                     if "recortado_por_el_techo_del_eje" in f["quality_flags"])
    if recortadas:
        print(f"::warning::[polvo] {recortadas} horas tocan el techo del eje "
              f"({diagnostico['techo']:.0f} ug/m3): son cotas inferiores")
    return {
        "filas": len(filas),
        "dibujado": dibujado.isoformat(),
        "desde": filas[0]["t_utc"].isoformat() if filas else None,
        "hasta": filas[-1]["t_utc"].isoformat() if filas else None,
        "pm2_5_ultimo": filas[-1]["pm2_5"] if filas else None,
        "recortadas": recortadas,
    }


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    resumen = run()
    print(f"[polvo] {resumen['filas']} horas de PM2.5 "
          f"{resumen['desde']} -> {resumen['hasta']}; "
          f"ultima {resumen['pm2_5_ultimo']:.2f} ug/m3"
          if resumen["filas"] else "[polvo] sin horas completas en el grafico")


if __name__ == "__main__":
    main()
