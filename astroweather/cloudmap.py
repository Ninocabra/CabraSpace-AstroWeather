"""El campo de nubes previsto, en coordenadas de MAPA (no de boveda).

`dome.py` ya trae una rejilla de nubes, pero es POLAR -- anillos y acimutes
alrededor del sitio -- porque su pregunta es "en que direccion del cielo cae
esa nube". Aqui la pregunta es otra: "por donde viene", y eso se contesta sobre
un mapa. Misma fuente y mismo modelo; geometria distinta, asi que rejilla
distinta. Fundirlas habria obligado a una de las dos a fingir.

QUE PUBLICA Y QUE NO. Publica la PREVISION del ECMWF sobre una caja de unos
900 x 550 km centrada en Nerpio, hora a hora, con la nubosidad total y las tres
capas. No publica imagenes ni nada de terceros: la pagina pinta este campo
encima del satelite de EUMETSAT, que se baja ella.

TRES DECISIONES QUE NO SON DE ESTILO:

* **La rejilla se ancla EN EL SITIO**, no en una caja redonda de coordenadas.
  Asi Nerpio cae exactamente en una celda -- la del centro -- y el valor que
  se pinta sobre el observatorio es un numero del modelo, no una interpolacion
  de cuatro vecinos. Es la misma celda cuya serie se puede comparar luego con
  el fotometro, que es medio proyecto.

* **Open-Meteo cobra por PUNTO.** Medido el 21-09-2026, a base de que nos
  contestara un 429: cada coordenada de la lista cuenta como una llamada, y
  hay tope por minuto ademas del diario. 465 puntos por pasada y cuatro
  pasadas al dia son ~1.900 llamadas diarias. Por eso esto NO lo puede hacer
  el navegador de quien visita la pagina: una tarde con visitas nos deja sin
  servicio. Y por eso `descargar` cuenta lo que gasta y lo devuelve: el dia
  que nos corten, queremos saber en que lo gastamos, no adivinarlo.

* **El campo viaja codificado, un caracter por celda.** En JSON normal, 465
  celdas x 49 horas x 4 campos son unos 300 kB por pasada, y esto se
  reescribe en un repo PUBLICO cuatro veces al dia: 1,2 MB diarios de
  historial para una cosa que se mira en pantalla. Un caracter por celda lo
  deja en ~90 kB. El precio es cuantizar al 2 %, sobre una magnitud cuyo error
  de modelo es del orden del 20 %: se tira precision que no existia.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import time

import requests

from .site import NERPIO, Site

MODELO = "ecmwf_ifs025"
API = "https://api.open-meteo.com/v1/forecast"

# Paso de la rejilla, en grados. El nativo del modelo es 0,25 y esto es mas
# grueso A PROPOSITO: a 0,25 esta caja serian ~1.600 puntos, y Open-Meteo cobra
# por punto.
#
# 0,5 Y NO 0,35, Y NO CUESTA RESOLUCION EN PANTALLA. Lo que se ve de gruesa una
# celda no lo decide su tamanio en kilometros, lo decide su tamanio en PIXELES:
# son 31 columnas repartidas en los ~920 px del mapa, o sea 30 px por celda,
# valga la celda 39 km o 55. Pasando de 0,35 a 0,5 con las mismas 31 columnas
# el mapa ensena un 43 % mas de terreno -- 1.313 x 779 km en vez de 919 x 545 --
# por el MISMO numero de puntos y con el mismo aspecto. Lo unico que se pierde
# es detalle que el modelo no tenia: su resolucion efectiva ya era mas gruesa
# que su rejilla.
#
# Si algun dia hace falta de verdad mas detalle, el camino esta medido: ECMWF
# open data publica `tcc` a 0,25 en GRIB y se puede pedir el campo suelto con
# un Range HTTP (571 kB, 0,6 s), a cambio de `eccodes` y de perder las capas y
# los pasos horarios.
PASO = 0.5
# Columnas y filas a cada lado del sitio. 15 x 0,5 son 7,5 grados de longitud
# (~656 km aqui) y 7 x 0,5 son 3,5 de latitud (~390 km). La caja entera mide
# 1.313 x 779 km, proporcion 1,685, que es la del marco de la web: asi el
# recorte del satelite y el campo se piden tal cual y no se estira ninguno.
SEMI_X, SEMI_Y = 15, 7

# Horas publicadas, contando la actual. Dos dias, que es lo que cubren las dos
# noches de `noche.json`: el mapa y el panel hablan de la misma ventana.
HORAS = 49

CAMPOS = {
    "total": "cloud_cover",
    "bajas": "cloud_cover_low",
    "medias": "cloud_cover_mid",
    "altas": "cloud_cover_high",
}

# El viento, para las flechas del mapa. Tres decisiones aqui:
#
# 1. SE PUBLICAN COMPONENTES u/v, NO velocidad y direccion. Parece un capricho
#    y es la diferencia entre funcionar y no: la direccion es un angulo, y
#    promediar angulos esta mal. Entre 350 y 10 grados la media aritmetica da
#    180 -- exactamente el viento contrario -- y eso pasaria en cada paso
#    intermedio de la animacion y en cada celda entre dos vecinas. Con u y v no
#    hay nada que arreglar: se interpolan como numeros y ya.
#
# 2. VAN EN UNA REJILLA MAS GRUESA, una celda de cada dos. El viento es mucho
#    mas liso que la nube y ademas las flechas se dibujan espaciadas: publicar
#    los 465 puntos seria mandar 68 kB para tirar tres cuartas partes. Asi son
#    16x8 = 128 puntos y ~19 kB.
#
# 3. La RACHA aparte de la velocidad media, porque es la racha la que cierra el
#    techo. Las flechas apuntan con el viento medio y se pintan de color con la
#    racha, contra los umbrales de `gates.py` -- los mismos del veredicto, que
#    viajan dentro del fichero para que no puedan discrepar.
VIENTO = {"velocidad": "wind_speed_10m", "direccion": "wind_direction_10m",
          "racha": "wind_gusts_10m"}
VIENTO_SALTO = 2
# u y v en m/s, de -25 a +25 en pasos de 1: 51 niveles, que es justo lo que
# cabe en el alfabeto de arriba. La racha, de 0 a 50 en pasos de 1.
VIENTO_OFFSET = 25.0

# Puntos por peticion. El tope real es la longitud de la URL: con 779 puntos
# (8.455 caracteres) el servidor devuelve 414, y con 390 (4.296) no. 230 deja
# margen de sobra y parte la caja en dos.
LOTE = 230
# Respiro entre lotes. No es cortesia decorativa: el tope por minuto existe y
# nos lo hemos comido probando.
PAUSA_S = 1.0

# La codificacion. Valor en % -> se redondea a pasos de 2 -> 0..50 -> se suma
# BASE. El 51 queda reservado para "el proveedor no mando valor", que NO es lo
# mismo que cero nubes y no se puede pintar igual.
#
# BASE = 35 ('#') Y NO 48, y la diferencia importa: empezando en 48 los codigos
# llegan hasta el 99 y por el camino esta el 92, que es la BARRA INVERTIDA.
# JSON obliga a escaparla, asi que esa celda ocuparia dos caracteres en vez de
# uno -- adios a la cuenta de tamano que justifica todo esto -- y cualquier
# lector ingenuo que no desescape leeria el campo corrido a partir de ahi.
# Empezando en 35 los 52 codigos caben entre '#' y 'V' sin tocar ni la comilla
# (34) ni la barra (92). Lo encontro el test, no yo.
BASE = 35
PASO_CUANT = 2
MAX_NIVEL = 50
SIN_DATO = chr(BASE + MAX_NIVEL + 1)


def rejilla(site: Site = NERPIO) -> dict:
    """La caja, anclada en el sitio.

    Devuelve tambien donde cae el sitio dentro de ella, porque quien pinta el
    mapa necesita saberlo y calcularlo otra vez al otro lado seria la clase de
    duplicado que acaba discrepando.
    """
    lons = [site.longitude + i * PASO for i in range(-SEMI_X, SEMI_X + 1)]
    lats = [site.latitude + j * PASO for j in range(-SEMI_Y, SEMI_Y + 1)]
    return {
        "paso": PASO,
        "lon0": round(lons[0], 5), "lat0": round(lats[0], 5),
        "nx": len(lons), "ny": len(lats),
        # Fila a fila de SUR a NORTE y de OESTE a ESTE: indice = j * nx + i.
        # Se deja escrito porque es lo unico que hay que saber para leer esto,
        # y adivinarlo mal produce un mapa espejado que parece plausible.
        "orden": "j*nx+i, j de sur a norte, i de oeste a este",
        "ix": SEMI_X, "iy": SEMI_Y,
    }


def puntos(site: Site = NERPIO) -> list[tuple[float, float]]:
    """Las coordenadas de la rejilla, en el orden que declara `rejilla`."""
    r = rejilla(site)
    return [(round(r["lat0"] + j * PASO, 5), round(r["lon0"] + i * PASO, 5))
            for j in range(r["ny"]) for i in range(r["nx"])]


def codificar(valores) -> str:
    """Un caracter por celda."""
    salida = []
    for v in valores:
        if v is None:
            salida.append(SIN_DATO)
            continue
        nivel = int(round(max(0.0, min(100.0, float(v))) / PASO_CUANT))
        salida.append(chr(BASE + min(nivel, MAX_NIVEL)))
    return "".join(salida)


def codificar_niveles(niveles) -> str:
    """Cuando el valor NO es un porcentaje. Recibe ya el nivel entero."""
    return "".join(SIN_DATO if n is None
                   else chr(BASE + max(0, min(MAX_NIVEL, int(n))))
                   for n in niveles)


def _uv(velocidad, direccion):
    """De velocidad y direccion METEOROLOGICA a componentes.

    En meteorologia la direccion es DE DONDE viene el viento, no hacia donde
    va: 0 grados es viento del norte, que sopla hacia el sur. El seno y el
    coseno llevan el signo cambiado por eso, y no es un despiste.
    """
    if velocidad is None or direccion is None:
        return None, None
    rad = math.radians(float(direccion))
    return (-velocidad * math.sin(rad), -velocidad * math.cos(rad))


def montar_viento(respuestas: list, desde: int, cuantas: int,
                  site: Site = NERPIO) -> dict:
    """Las flechas: u, v y racha, en la rejilla gruesa."""
    r = rejilla(site)
    # EL SUBMUESTREO EMPIEZA EN EL SITIO, no en el cero. La celda de Nerpio es
    # la 15 de 31, impar, asi que tomando una de cada dos desde el 0 el
    # observatorio se quedaba JUSTO ENTRE dos flechas -- se vio comparando la
    # celda central con una consulta de un punto y saliendo 2,2 m/s contra 0,1,
    # que era el viento de 55 km al nordeste. Arrancando en `ix % salto` la
    # rejilla gruesa contiene el sitio por construccion, igual que la fina.
    ies = list(range(r["ix"] % VIENTO_SALTO, r["nx"], VIENTO_SALTO))
    jotas = list(range(r["iy"] % VIENTO_SALTO, r["ny"], VIENTO_SALTO))
    indices = [j * r["nx"] + i for j in jotas for i in ies]

    us, vs, rachas = [], [], []
    for k in range(desde, desde + cuantas):
        fila_u, fila_v, fila_r = [], [], []
        for idx in indices:
            h = respuestas[idx]["hourly"]
            u, v = _uv(h[VIENTO["velocidad"]][k], h[VIENTO["direccion"]][k])
            fila_u.append(None if u is None else round(u + VIENTO_OFFSET))
            fila_v.append(None if v is None else round(v + VIENTO_OFFSET))
            racha = h[VIENTO["racha"]][k]
            fila_r.append(None if racha is None else round(float(racha)))
        us.append(codificar_niveles(fila_u))
        vs.append(codificar_niveles(fila_v))
        rachas.append(codificar_niveles(fila_r))

    from .gates import GUST_DEGRADED, GUST_CLOSE
    return {
        "nx": len(ies), "ny": len(jotas), "salto": VIENTO_SALTO,
        "i0": ies[0], "j0": jotas[0],
        # Donde cae el sitio DENTRO de la rejilla gruesa, para que la pagina no
        # tenga que volver a deducirlo.
        "ix": ies.index(r["ix"]), "iy": jotas.index(r["iy"]),
        "offset": VIENTO_OFFSET,
        # Los umbrales viajan DENTRO. Si la pagina se los inventara, el mapa
        # podria pintar una flecha tranquila sobre una hora que el veredicto
        # da por cerrada, y no habria forma de enterarse.
        "umbrales": {"degradado": GUST_DEGRADED, "cierre": GUST_CLOSE},
        "como": "u = (codigo - base) - offset, en m/s; racha = codigo - base, "
                "en m/s; u positiva hacia el este, v hacia el norte",
        "campos": {"u": us, "v": vs, "racha": rachas},
    }


def decodificar(texto: str) -> list:
    """La vuelta, para los tests y para quien lea esto desde Python.

    La pagina hace lo mismo en una linea de JavaScript; existe aqui para poder
    comprobar el viaje de ida y vuelta sin salir del motor.
    """
    salida = []
    for c in texto:
        nivel = ord(c) - BASE
        salida.append(None if nivel > MAX_NIVEL else nivel * PASO_CUANT)
    return salida


def _horas_publicadas(marcas: list[str], ahora: dt.datetime) -> tuple[int, int]:
    """Desde que indice y cuantas. La primera hora es la ACTUAL, no la de
    medianoche: Open-Meteo devuelve el dia entero desde las 00:00 UTC, y
    publicar las horas que ya han pasado seria engordar el fichero con pasado
    que ademas contradice al satelite, que ese si es medido."""
    corte = ahora.replace(minute=0, second=0, microsecond=0)
    for k, marca in enumerate(marcas):
        cuando = dt.datetime.fromisoformat(marca).replace(tzinfo=dt.timezone.utc)
        if cuando >= corte:
            return k, min(HORAS, len(marcas) - k)
    return 0, min(HORAS, len(marcas))


def montar(respuestas: list, site: Site = NERPIO,
           ahora: dt.datetime | None = None) -> dict:
    """De lo que devuelve el proveedor al fichero que publica la web.

    `respuestas` llega ya concatenada y EN ORDEN: el proveedor contesta a una
    lista de coordenadas con una lista del mismo largo y en la misma posicion,
    asi que el orden es el contrato. Si algun dia dejara de serlo, el test del
    orden es el que se pondria rojo.
    """
    ahora = ahora or dt.datetime.now(dt.timezone.utc)
    r = rejilla(site)
    esperados = r["nx"] * r["ny"]
    if len(respuestas) != esperados:
        raise ValueError(f"{len(respuestas)} puntos y la rejilla pide {esperados}")

    marcas = respuestas[0]["hourly"]["time"]
    desde, cuantas = _horas_publicadas(marcas, ahora)
    horas = [marcas[k] + "Z" for k in range(desde, desde + cuantas)]

    campos = {}
    for nombre, variable in CAMPOS.items():
        serie = []
        for k in range(desde, desde + cuantas):
            serie.append(codificar(p["hourly"][variable][k] for p in respuestas))
        campos[nombre] = serie

    return {
        "generado_utc": ahora.isoformat(timespec="seconds"),
        "modelo": MODELO,
        "sitio": {"site_id": site.site_id,
                  "lat": site.latitude, "lon": site.longitude},
        "rejilla": r,
        "horas": horas,
        "codificacion": {
            "base": BASE, "paso": PASO_CUANT, "max": MAX_NIVEL,
            "sin_dato": SIN_DATO,
            "como": "porcentaje = (codigo - base) * paso; el codigo de "
                    "`sin_dato` significa que el proveedor no mando valor, "
                    "que NO es cero nubes",
        },
        "campos": campos,
        "viento": montar_viento(respuestas, desde, cuantas, site),
    }


# Reintentos ante un 503. No es paranoia: el 21-09-2026, pidiendo el ultimo
# lote, el proveedor contesto 503 y se llevo por delante la pasada entera. Una
# recogida que corre cada seis horas no puede perderse una pasada porque el
# otro lado tosiera un segundo; y los 429 NO se reintentan, porque ahi el
# problema es que estamos gastando de mas y repetir lo empeora.
REINTENTOS = 3
ESPERA_REINTENTO_S = 5.0


def _con_reintento(pedir, params):
    ultimo = None
    for intento in range(REINTENTOS):
        respuesta = pedir(API, params=params, timeout=120)
        if respuesta.status_code != 503:
            return respuesta
        ultimo = respuesta
        if intento < REINTENTOS - 1:
            time.sleep(ESPERA_REINTENTO_S * (intento + 1))
    return ultimo


def descargar(site: Site = NERPIO, *, sesion=None) -> tuple[list, dict]:
    """Los puntos, por lotes, contando lo que cuesta.

    Devuelve tambien el gasto. No es telemetria por gusto: el proveedor cobra
    por punto y tiene tope por minuto y por dia, asi que lo unico que separa
    "funciona" de "un dia deja de funcionar" es mirar el consumo antes de que
    nos corten.
    """
    coords = puntos(site)
    pedir = sesion.get if sesion else requests.get
    trozos: list = []
    gasto = {"puntos": len(coords), "lotes": 0, "segundos": 0.0}
    for inicio in range(0, len(coords), LOTE):
        sub = coords[inicio:inicio + LOTE]
        if gasto["lotes"]:
            time.sleep(PAUSA_S)
        t0 = time.monotonic()
        respuesta = _con_reintento(pedir, {
            "latitude": ",".join(f"{p[0]:.4f}" for p in sub),
            "longitude": ",".join(f"{p[1]:.4f}" for p in sub),
            "hourly": ",".join(list(CAMPOS.values()) + list(VIENTO.values())),
            "models": MODELO, "forecast_days": 3, "timezone": "UTC",
            # EN METROS POR SEGUNDO, Y NO ES UN DETALLE. Open-Meteo devuelve el
            # viento en km/h si no se le dice otra cosa, y los umbrales del
            # techo -- los de `gates.py`, que viajan dentro de este fichero --
            # estan en m/s. Sin esta linea, un viento flojo de 20 km/h se
            # codificaria como 20 m/s y el mapa pintaria galerna permanente
            # sobre un veredicto que dice "viento flojo". Se me colo al
            # escribirlo y salto al comparar con una consulta de un punto.
            "wind_speed_unit": "ms",
        })
        gasto["segundos"] += time.monotonic() - t0
        gasto["lotes"] += 1
        if respuesta.status_code == 429:
            raise RuntimeError(
                f"Open-Meteo nos ha cortado tras {gasto['lotes']} lotes y "
                f"{inicio + len(sub)} puntos: cobra por PUNTO, no por peticion")
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
        trozos.extend(cuerpo if isinstance(cuerpo, list) else [cuerpo])
    gasto["segundos"] = round(gasto["segundos"], 2)
    return trozos, gasto


def build(site: Site = NERPIO) -> tuple[dict, dict]:
    respuestas, gasto = descargar(site)
    return montar(respuestas, site), gasto


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salida", default=None,
                        help="escribe el JSON aqui en vez de resumirlo")
    args = parser.parse_args()
    payload, gasto = build()
    if args.salida:
        with open(args.salida, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
    r = payload["rejilla"]
    print(f"[cloudmap] {r['nx']}x{r['ny']} celdas de {PASO} grados, "
          f"{len(payload['horas'])} horas desde {payload['horas'][0]} "
          f"| {gasto['puntos']} puntos en {gasto['lotes']} lotes, "
          f"{gasto['segundos']} s "
          f"| {len(json.dumps(payload)) // 1024} kB")


if __name__ == "__main__":
    main()
