"""Guarda los JPEG nocturnos de la allsky de AstroCamp para medir k (SPEC v8 1.1).

Corre desatendido toda la noche y se para solo al amanecer. Existe porque
`k_extinction` sigue vacia y la unica fuente posible en Nerpio es esta camara:
la fotometria estelar a varias masas de aire necesita fotogramas, y los de esta
noche no se pueden recuperar manana.

QUE NO HACE. No mide nada ni escribe en el archivo. Solo baja ficheros a una
carpeta local y anota lo que baja. El analisis viene despues, sobre lo guardado,
y por eso aqui no se decide nada que no se pueda rehacer.

TRES COSAS QUE UN BUCLE INGENUO HACE MAL:

* **Fechar por el reloj de casa.** La imagen trae `Last-Modified`, que es cuando
  la camara la tomo; el momento de la descarga es otra cosa y puede ir minutos
  por detras. La misma trampa que en el PNG del polvo: la hora del dato la pone
  el dato.
* **Guardar la misma imagen veinte veces.** `latest.jpg` solo cambia cuando hay
  toma nueva. Se deduplica por hash del contenido, que ademas detecta gratis que
  la camara se ha quedado congelada.
* **Morirse a la primera de cambio.** Un fallo de red a las 3 de la manana no
  puede llevarse la noche entera por delante: se registra y se sigue.

CUANTO OCUPA: ~230 kB por imagen. A una cada 3 minutos desde el ocaso, unas
200 imagenes y **~45 MB por noche**. Va a una carpeta local, NUNCA al archivo:
el repo de datos no es sitio para esto.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
import urllib3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astroweather.ephem import sun_positions           # noqa: E402
from astroweather.site import NERPIO                   # noqa: E402

URL = "https://com2.astrocamp.es:85/indi-allsky/images/latest.jpg"
DESTINO = Path(__file__).resolve().parent.parent / "capturas" / "allsky"
# -12 grados es el crepusculo nautico. No se espera a los -18 a proposito: las
# estrellas brillantes ya se miden antes, y son las que mas masa de aire barren
# al principio y al final de la noche, que es justo lo que el ajuste necesita.
SOL_MAXIMO = -12.0
INTERVALO_S = 180
ESPERA_DE_DIA_S = 600
# El certificado del servidor no valida contra la cadena publica. Es el equipo
# del sitio en una IP conocida y la imagen no es secreta: se acepta a proposito
# y se deja escrito, igual que en `fetch_allsky`.
VERIFICAR_TLS = False
# El aviso de urllib3 va callado A PROPOSITO y solo aqui: si se dejara, cada
# descarga de la noche escupiria el mismo parrafo y el log dejaria de leerse --
# que es la forma habitual de que un aviso de verdad pase inadvertido.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def altura_del_sol(cuando: dt.datetime) -> float:
    return sun_positions([cuando], NERPIO)[0].altitude


def descarga(sesion: requests.Session) -> tuple[bytes, dt.datetime] | None:
    """El JPEG y la hora en que la camara lo tomo. None si la red falla."""
    try:
        r = sesion.get(URL, timeout=45, verify=VERIFICAR_TLS)
        r.raise_for_status()
    except requests.exceptions.RequestException as error:
        print(f"  red: {type(error).__name__}: {str(error)[:120]}", flush=True)
        return None
    cabecera = r.headers.get("Last-Modified")
    tomada = (parsedate_to_datetime(cabecera).astimezone(dt.timezone.utc)
              if cabecera else dt.datetime.now(dt.timezone.utc))
    return r.content, tomada


def guarda(contenido: bytes, tomada: dt.datetime, destino: Path,
           vistos: set[str]) -> Path | None:
    digest = hashlib.sha256(contenido).hexdigest()
    if digest in vistos:
        return None                       # misma imagen: la camara no ha disparado
    vistos.add(digest)
    destino.mkdir(parents=True, exist_ok=True)
    ruta = destino / f"allsky_{tomada:%Y%m%dT%H%M%SZ}_{digest[:8]}.jpg"
    ruta.write_bytes(contenido)
    with (destino / "manifiesto.jsonl").open("a", encoding="utf-8") as mano:
        mano.write(json.dumps({"fichero": ruta.name,
                               "tomada_utc": tomada.isoformat(),
                               "bajada_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                               "bytes": len(contenido), "sha256": digest}) + "\n")
    return ruta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destino", type=Path, default=DESTINO)
    parser.add_argument("--intervalo", type=int, default=INTERVALO_S)
    parser.add_argument("--sol-maximo", type=float, default=SOL_MAXIMO)
    parser.add_argument("--una", action="store_true",
                        help="baja UNA imagen ahora, sea la hora que sea, y sale")
    parser.add_argument("--log", type=Path,
                        help="anade la salida a este fichero (para la tarea programada)")
    args = parser.parse_args()

    if args.log:
        # La tarea programada corre con `pythonw`, que no tiene consola: sin
        # esto cada `print` se pierde en silencio y una noche fallida es
        # indistinguible de una que ni arranco. Exit 0 y sin log es justo el
        # modo de fallo que no queremos.
        args.log.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = args.log.open("a", encoding="utf-8", buffering=1)
        print(f"--- {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M}Z arranque", flush=True)

    sesion = requests.Session()
    vistos: set[str] = set()
    # Si la carpeta ya tiene capturas, sus hashes cuentan: relanzar el guion a
    # mitad de noche no debe duplicar lo que ya esta en disco.
    for previa in args.destino.glob("allsky_*.jpg"):
        vistos.add(hashlib.sha256(previa.read_bytes()).hexdigest())
    if vistos:
        print(f"[allsky] {len(vistos)} capturas previas en {args.destino}", flush=True)

    if args.una:
        traido = descarga(sesion)
        if traido is None:
            raise SystemExit("no se pudo descargar")
        ruta = guarda(*traido, args.destino, vistos)
        print(f"[allsky] {'guardada ' + ruta.name if ruta else 'ya la teniamos'}; "
              f"Sol a {altura_del_sol(dt.datetime.now(dt.timezone.utc)):+.1f} grados")
        return

    print(f"[allsky] capturando en {args.destino} cada {args.intervalo} s "
          f"mientras el Sol este por debajo de {args.sol_maximo:+.0f} grados. "
          f"Ctrl-C para parar.", flush=True)

    empezo = False
    guardadas = 0
    while True:
        ahora = dt.datetime.now(dt.timezone.utc)
        sol = altura_del_sol(ahora)
        if sol > args.sol_maximo:
            if empezo:
                # Amanecio: la noche esta entera y el guion ha terminado su
                # trabajo. Salir es lo correcto -- dejarlo vivo todo el dia
                # solo sirve para que nadie sepa si sigue funcionando.
                print(f"[allsky] amanece (Sol {sol:+.1f}). {guardadas} imagenes "
                      f"en {args.destino}", flush=True)
                return
            print(f"  {ahora:%H:%M}Z Sol {sol:+.1f}, aun de dia; espero", flush=True)
            time.sleep(ESPERA_DE_DIA_S)
            continue

        empezo = True
        traido = descarga(sesion)
        if traido is not None:
            ruta = guarda(*traido, args.destino, vistos)
            if ruta:
                guardadas += 1
                print(f"  {ahora:%H:%M}Z Sol {sol:+5.1f}  {ruta.name} "
                      f"({guardadas} guardadas)", flush=True)
        time.sleep(args.intervalo)


if __name__ == "__main__":
    main()
