# -*- coding: utf-8 -*-
"""Lo que el codigo importa tiene que estar declarado para el runner.

EL CASO (27-08 -> 01-09-2026, seis dias). `fetch_aerosol.dust_rows` importa
`netCDF4` para leer el NetCDF de CAMS, y `netCDF4` no estaba en
`requirements-colector.txt`. En el escritorio estaba instalado, asi que ahi
funcionaba; en el runner no, y el `except Exception` del worker convertia el
`ModuleNotFoundError` en una linea de log. Resultado: el trabajo `aerosol` se
marcaba OK todos los dias, escribia sus 120 horas de AOD total y perdia en
silencio las 41 de especies. Seis dias sin polvo, con la web publicando
`dust_alert: false` en noches con calima de verdad.

Lo que lo hizo invisible no fue el `except` -- eso es una decision defendible
para una fuente que se cae -- sino que NADIE COMPROBABA que el entorno del
runner supiera ejecutar el codigo que se le manda. Este test lo comprueba.

POR QUE NO BASTA CON MIRAR LOS IMPORTS DE CABECERA: los dos casos interesantes
son de dentro de una funcion. `netCDF4` se importa dentro de `dust_rows`, que el
colector SI llama, y `apscheduler` dentro del `main()` del planificador vivo,
que NO llama nunca. Estaticamente no se distinguen, asi que lo que este test
exige es que cada modulo de terceros este DECLARADO o EXCLUIDO A PROPOSITO, y
que la exclusion este escrita donde se pueda leer.
"""

import ast
import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
REQUISITOS = RAIZ / "requirements-colector.txt"
CODIGO = RAIZ / "astroweather"

# Fuera del colector A PROPOSITO, con el motivo. Cada uno se comprueba abajo
# contra la cabecera del fichero de requisitos, para que no se pueda excluir
# algo aqui y dejar el fichero diciendo otra cosa.
FUERA = {
    "apscheduler": "el planificador de proceso vivo; el colector no planifica",
    "photutils": "solo el pipeline de FWHM, que corre a mano sobre FITS en disco",
    "google": "google-cloud-bigquery, solo fetch_weathernext; el scheduler no lo "
              "llama hasta que Google apruebe el acceso (pedido el 07-09-2026)",
}


# El modulo que se importa y la distribucion que se instala no siempre se
# llaman igual. Sin esto, `pillow` declarado y `PIL` importado parecen dos
# cosas distintas y el test acusa a un huerfano que no lo es.
ALIAS = {"pil": "pillow"}


def _declarados() -> set[str]:
    """Los nombres de distribucion del fichero de requisitos, normalizados."""
    nombres = set()
    for linea in REQUISITOS.read_text(encoding="utf-8").splitlines():
        linea = linea.split("#")[0].strip()
        if not linea:
            continue
        nombre = re.split(r"[~=<>!\[]", linea)[0].strip()
        if nombre:
            nombres.add(nombre.lower().replace("-", "_"))
    return nombres


def _distribucion(modulo: str) -> str:
    """El nombre con el que se instala el modulo, normalizado."""
    normal = modulo.lower().replace("-", "_")
    return ALIAS.get(normal, normal)


def _importados() -> dict[str, set[str]]:
    """{modulo de terceros: ficheros que lo importan}, de TODO astroweather/.

    Incluye los imports de dentro de funciones, que son justo los dos casos que
    importan aqui.
    """
    salida: dict[str, set[str]] = {}
    for fichero in sorted(CODIGO.rglob("*.py")):
        arbol = ast.parse(fichero.read_text(encoding="utf-8"), str(fichero))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                raices = [alias.name.split(".")[0] for alias in nodo.names]
            elif isinstance(nodo, ast.ImportFrom):
                # `level > 0` es un import relativo: codigo propio.
                raices = ([nodo.module.split(".")[0]]
                          if nodo.module and not nodo.level else [])
            else:
                continue
            for raiz in raices:
                if raiz in sys.stdlib_module_names or raiz == "astroweather":
                    continue
                salida.setdefault(raiz, set()).add(
                    str(fichero.relative_to(RAIZ)).replace("\\", "/"))
    return salida


def test_todo_lo_que_importa_el_codigo_esta_declarado_o_excluido():
    """El test que habria ahorrado seis dias de aerosol sin especies."""
    declarados = _declarados()
    huerfanos = {
        modulo: sorted(ficheros)
        for modulo, ficheros in _importados().items()
        if _distribucion(modulo) not in declarados and modulo not in FUERA
    }
    assert not huerfanos, (
        "modulos que el codigo importa y el runner no tiene forma de tener:\n"
        + "\n".join("  %s <- %s" % (m, ", ".join(f))
                    for m, f in sorted(huerfanos.items()))
        + "\nDeclaralo en requirements-colector.txt, o excluyelo a proposito "
          "en FUERA de este test explicando por que.")


def test_netcdf4_esta_declarado():
    """El caso concreto, clavado, para que no vuelva a caerse solo.

    Vale que lo cubre el test de arriba; esto nombra el fallo para que quien
    lea la lista de tests sepa que paso el 27-08-2026.
    """
    assert "netcdf4" in _declarados(), (
        "sin netCDF4 el runner no puede leer el NetCDF de CAMS y el aerosol "
        "se queda sin especies, en silencio y con el trabajo en verde")


@pytest.mark.parametrize("modulo", sorted(FUERA))
def test_lo_excluido_esta_explicado_en_el_fichero(modulo):
    """Una exclusion que no se explica donde se mira es una trampa futura."""
    cabecera = REQUISITOS.read_text(encoding="utf-8")
    assert modulo in cabecera, (
        "%s se excluye en el test pero el fichero de requisitos no dice por "
        "que; el proximo que lo lea no sabra si falta o sobra" % modulo)


def test_lo_declarado_se_puede_importar_de_verdad():
    """Declarar no es instalar: se comprueba que el nombre existe.

    Si un dia se declara mal el nombre de la distribucion (`netcdf4` en vez de
    `netCDF4`, por ejemplo) el fichero pasaria el test de arriba y el runner
    seguiria sin el modulo.
    """
    import importlib
    for modulo in _importados():
        if modulo in FUERA:
            continue
        importlib.import_module(modulo)
