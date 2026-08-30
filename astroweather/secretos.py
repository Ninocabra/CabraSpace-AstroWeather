# -*- coding: utf-8 -*-
"""Las credenciales, leidas de UN sitio y con un orden declarado.

Habia dos lectores de `.env` en el proyecto, uno en `fetch_aemet` y otro en
`fetch_aerosol`, cada uno con su mini-parser: el primero solo entendia lineas
que empezaran por `AEMET_API_KEY=` y el segundo partia por el primer `=`
ignorando comentarios. Dos formas de leer el mismo fichero es como empiezan las
discrepancias que este proyecto ya conoce.

Y hace falta ademas para que el colector pueda correr FUERA de este disco: en
un runner de GitHub no hay `.env` -- esta en el .gitignore, y tiene que
seguir estandolo -- sino variables de entorno puestas desde los secretos del
repositorio.

EL ORDEN ES ENTORNO PRIMERO, y no al reves, por dos razones: es lo que permite
sobreescribir una credencial en una maquina concreta sin tocar ficheros, y es
lo que hace que el runner funcione sin inventarse un `.env` falso.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

FICHERO = Path(__file__).resolve().parent.parent / ".env"


@lru_cache(maxsize=1)
def _del_fichero() -> dict[str, str]:
    """Lo que hay en `.env`, o nada si no existe. NO es un error que falte."""
    if not FICHERO.exists():
        return {}
    valores = {}
    for linea in FICHERO.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        valores[clave.strip()] = valor.strip()
    return valores


def leer(nombre: str, *, obligatorio: bool = True) -> str | None:
    """La credencial, del entorno o del `.env`, en ese orden.

    Si falta y es obligatoria se levanta con el nombre dentro: una credencial
    ausente tiene que decir CUAL, porque el sintoma aguas abajo es una fuente
    que deja de responder sin motivo aparente.
    """
    valor = os.environ.get(nombre) or _del_fichero().get(nombre)
    if valor:
        return valor
    if obligatorio:
        raise RuntimeError(
            f"falta la credencial {nombre}: ponla en el entorno o en {FICHERO.name}")
    return None
