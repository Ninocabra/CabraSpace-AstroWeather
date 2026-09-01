# -*- coding: utf-8 -*-
"""Una pasada de recogida en una maquina que se destruye al terminar.

Es el mismo trabajo que hacia `scheduler.py`, sin el proceso vivo. Un runner de
GitHub Actions arranca cada 30 minutos, no recuerda nada del anterior y muere:
no puede haber un planificador con estado en memoria, asi que la pregunta deja
de ser "que hora es" y pasa a ser **"que lleva demasiado tiempo sin correr"**.

POR QUE ESO ES MEJOR Y NO SOLO DISTINTO. Un cron dice "a las 06:20" y si esa
cita no ocurre, no ocurre: el propio workflow del EPS lleva escrito que su
primera cita del 29-08-2026 no llego a existir, porque los cron de GitHub van a
una cola compartida y son best-effort. Preguntando por lo que esta VENCIDO, una
pasada perdida se recupera sola en la siguiente. El planificador se vuelve
auto-reparable en vez de puntual.

EL ESTADO VIVE EN EL ARCHIVO, no en el runner: `data/estado_colector.json`
viaja al repo del archivo como un fichero suelto mas. Si se pierde, lo peor que
pasa es que la siguiente pasada lo haga todo una vez -- repetir es barato y el
deduplicador de `queries.py` lo absorbe -- que es exactamente el lado seguro al
que hay que caerse.

ORDEN Y FALLOS. Los trabajos corren en el orden de `JOBS`, que es de
dependencia: prevision antes que prediccion, fotometro antes que suelo, y
`archivo` el ultimo porque es el que EMPUJA. Un trabajo que falla no marca su
fecha, asi que sigue vencido y se reintenta en la pasada siguiente; los demas
no se enteran.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from .queries import DATA_ROOT
from .scheduler import JOBS, TALLY, _configure_logging, guarded, logger

FICHERO_ESTADO = "estado_colector.json"

# El que empuja. Va siempre el ultimo y despues de escribir el estado, para que
# la pasada suba tambien la marca de lo que acaba de hacer.
ULTIMO = "archivo"


def ruta_estado(root: Path | None = None) -> Path:
    return (root or DATA_ROOT) / FICHERO_ESTADO


def leer_estado(root: Path | None = None) -> dict[str, dt.datetime]:
    """Cuando corrio bien cada trabajo por ultima vez. Vacio si no hay nada."""
    ruta = ruta_estado(root)
    if not ruta.exists():
        return {}
    try:
        crudo = json.loads(ruta.read_text(encoding="utf-8")).get("trabajos", {})
    except (ValueError, OSError):
        # Un estado ilegible no puede parar la recogida: se trata como si no
        # existiera y se rehace todo, que es el lado seguro.
        logger.warning("estado del colector ilegible; se rehace entero")
        return {}
    fechas = {}
    for nombre, marca in crudo.items():
        try:
            fechas[nombre] = dt.datetime.fromisoformat(marca)
        except (TypeError, ValueError):
            continue
    return fechas


def escribir_estado(estado: dict[str, dt.datetime], root: Path | None = None) -> Path:
    ruta = ruta_estado(root)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps({
        "escrito_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "trabajos": {n: f.isoformat() for n, f in sorted(estado.items())},
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    return ruta


def vencidos(ahora: dt.datetime, estado: dict[str, dt.datetime],
             only: set[str] | None = None) -> list[str]:
    """Que toca correr AHORA, en orden de dependencia.

    Un trabajo esta vencido si nunca ha corrido o si ha pasado su periodo. Los
    que declaran `desde_hora_utc` ademas esperan a esa hora: no es una
    preferencia de agenda, es que verificar la noche anterior antes de que
    termine mide otra cosa.
    """
    pendientes = []
    for nombre, trabajo in JOBS.items():
        if only and nombre not in only:
            continue
        ultimo = estado.get(nombre)
        if ultimo is not None and ahora - ultimo < trabajo.periodo:
            continue
        if trabajo.desde_hora_utc is not None:
            if ahora.hour < trabajo.desde_hora_utc:
                continue
            # Y una sola vez al dia: pasada esa hora seguiria vencido toda la
            # tarde si solo se mirara el periodo contra "hace 24 h".
            if ultimo is not None and ultimo.date() == ahora.date():
                continue
        pendientes.append(nombre)
    return pendientes


def pasada(*, ahora: dt.datetime | None = None, root: Path | None = None,
           only: set[str] | None = None, en_seco: bool = False) -> dict:
    """Una pasada completa. Devuelve que se hizo, para el log del runner."""
    ahora = ahora or dt.datetime.now(dt.timezone.utc)
    estado = leer_estado(root)
    pendientes = vencidos(ahora, estado, only)

    if en_seco:
        return {"ahora": ahora.isoformat(timespec="seconds"),
                "vencidos": pendientes, "ejecutados": [], "fallidos": []}

    ejecutados, fallidos = [], []
    for nombre in pendientes:
        if nombre == ULTIMO:
            continue
        antes = TALLY.get(nombre, {}).get("failed", 0)
        guarded(nombre, JOBS[nombre].funcion)()
        if TALLY.get(nombre, {}).get("failed", 0) > antes:
            fallidos.append(nombre)
        else:
            ejecutados.append(nombre)
            estado[nombre] = ahora

    # El estado se escribe ANTES de empujar, para que suba en la misma pasada.
    if ULTIMO in pendientes:
        estado[ULTIMO] = ahora
    escribir_estado(estado, root)

    if ULTIMO in pendientes:
        antes = TALLY.get(ULTIMO, {}).get("failed", 0)
        guarded(ULTIMO, JOBS[ULTIMO].funcion)()
        if TALLY.get(ULTIMO, {}).get("failed", 0) > antes:
            fallidos.append(ULTIMO)
        else:
            ejecutados.append(ULTIMO)

    return {"ahora": ahora.isoformat(timespec="seconds"), "vencidos": pendientes,
            "ejecutados": ejecutados, "fallidos": fallidos}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--en-seco", action="store_true",
                        help="dice que correria y no corre nada")
    parser.add_argument("--only", nargs="*",
                        help="limita a estos trabajos (siguen respetando su periodo)")
    parser.add_argument("--forzar", nargs="*",
                        help="corre estos AUNQUE no esten vencidos")
    args = parser.parse_args()

    _configure_logging(verbose=True)
    estado = leer_estado()
    if args.forzar:
        for nombre in args.forzar:
            if nombre not in JOBS:
                raise SystemExit(f"trabajo desconocido: {nombre}")
            estado.pop(nombre, None)
        escribir_estado(estado)

    only = set(args.only) if args.only else (set(args.forzar) if args.forzar else None)
    if only:
        # EL QUE EMPUJA VA SIEMPRE. Medido el 01-09-2026: un
        # `--forzar aerosol` corrio el trabajo, escribio sus 161 filas -- con
        # las 41 de especies que llevaban seis dias sin llegar -- y las tiro a
        # la basura con el runner, porque `archivo` no estaba en `only` y es el
        # unico que commitea. Una pasada que CALCULA y no PERSISTE es peor que
        # no correr: gasta minutos y deja creer que el dato esta guardado.
        only = only | {ULTIMO}
    resumen = pasada(only=only, en_seco=args.en_seco)

    logger.info("pasada %s | vencidos: %s", resumen["ahora"],
                ", ".join(resumen["vencidos"]) or "ninguno")
    if resumen["ejecutados"]:
        logger.info("hechos: %s", ", ".join(resumen["ejecutados"]))
    if resumen["fallidos"]:
        # Se informa y se sale con 0: un trabajo caido no puede pintar de rojo
        # una pasada en la que los otros quince han funcionado, y como no marca
        # su fecha se reintenta solo dentro de media hora.
        logger.error("FALLARON (se reintentan en la siguiente): %s",
                     ", ".join(resumen["fallidos"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
