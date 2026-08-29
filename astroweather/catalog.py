# -*- coding: utf-8 -*-
"""El catalogo grande: NGC, IC y Messier, clasificados para el recomendador.

Hasta hoy el recomendador trabajaba sobre una lista de 44 objetos escrita a
mano. Eso vale para responder "que hago esta noche" con un puñado de clasicos y
no vale para "y si quiero apuntar a NGC 6543". Ahora hay 12.098 objetos.

DE DONDE SALE. De OpenNGC (Mattia Verga), que es la base NGC/IC con licencia
amistosa -- CC-BY-SA-4.0 -- y que trae lo que hace falta para clasificar:
magnitud, BRILLO SUPERFICIAL, tamaño mayor y tipo. La atribucion y la licencia
viajan con el fichero derivado, aqui y en la web.

LO QUE NO SE PUEDE HACER, y por eso el criterio de abajo esta escrito con
cuidado: `difficulty` y `alpha` son JUICIOS sobre el objeto, y los 44 de la
lista curada estaban puestos a mano precisamente porque un catalogo no los
trae. Con 12.098 no hay forma de curarlos uno a uno, asi que se DERIVAN de tipo
y brillo superficial con reglas explicitas. Es peor que curarlos y mucho mejor
que no tenerlos, y se declara: cada objeto dice si su clase es `curada` o
`derivada`, y los 44 de siempre siguen mandando sobre lo derivado.

La regla clave es que **el brillo superficial manda sobre la magnitud**. Una
galaxia de magnitud 9 y 20 arcmin de diametro es mucho mas dificil que una de
magnitud 11 y 2 arcmin: la primera reparte su luz sobre cien veces mas cielo.
Es el error mas comun al elegir objetivo con un catalogo delante.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .targets import CATALOGUE, Target

FICHERO = Path(__file__).resolve().parent / "catalogo_ngc.json"
LICENCIA = "OpenNGC (Mattia Verga), CC-BY-SA-4.0"

# Brillo superficial, en mag/arcsec2, a partir del cual una superficie
# extendida entra en las clases exigentes. Los cortes son los mismos que
# `targets.SKY_GATE` usa para el cielo, corridos: un objeto solo destaca si su
# brillo esta por encima del fondo, asi que un objeto de SB 24 necesita un
# cielo mejor que uno de SB 21.
SB_BAJO_CONTRASTE = 23.0
SB_EXTREMO = 24.5

# Tamaño mayor, en arcmin, por debajo del cual un objeto es "compacto": lo que
# lo limita deja de ser el fondo y pasa a ser el seeing.
COMPACTO_ARCMIN = 2.0

# Tipos de OpenNGC. El mapa es explicito a proposito: un `.get(tipo, algo)`
# silencioso convertiria un tipo nuevo en una recomendacion inventada.
NARROWBAND = {"HII", "EmN", "Cl+N", "SNR", "Neb"}
GALAXIAS = {"G", "GPair", "GTrpl", "GGroup"}
CUMULOS = {"OCl", "GCl", "*Ass"}
REFLEXION = {"RfN"}
PLANETARIAS = {"PN"}

ESPANOL = {
    "G": "galaxia", "GPair": "par de galaxias", "GTrpl": "trio de galaxias",
    "GGroup": "grupo de galaxias", "PN": "nebulosa planetaria",
    "OCl": "cumulo abierto", "GCl": "cumulo globular",
    "Cl+N": "cumulo con nebulosa", "HII": "region HII", "EmN": "nebulosa de emision",
    "Neb": "nebulosa", "RfN": "nebulosa de reflexion", "SNR": "resto de supernova",
    "*Ass": "asociacion estelar",
}


def _clase(objeto: dict) -> tuple[str, float]:
    """Clase de dificultad y exponente de morfologia, derivados del catalogo.

    El orden importa: primero lo que decide la FISICA del objeto (emite en
    lineas o no), y solo despues el brillo superficial, que es lo que decide
    cuanto fondo aguanta.
    """
    tipo = objeto["t"]
    tam = objeto.get("maj")
    sb = objeto.get("sb")

    if tipo in NARROWBAND:
        # Emiten en lineas: el filtro estrecho les cambia la vida, y por eso su
        # clase no depende del brillo superficial como las demas.
        return "narrowband", 0.0

    if tipo in PLANETARIAS:
        # Las pequeñas viven del seeing; las grandes son emision pura.
        if tam is not None and tam < COMPACTO_ARCMIN:
            return "compacto_resolucion", 1.0
        return "narrowband", 0.5

    if tipo in REFLEXION:
        # Luz de estrellas reflejada en polvo: banda ancha obligatoria, y por
        # eso la Luna les hace todo el daño.
        return "bajo_contraste", 0.5

    if tipo in CUMULOS:
        return ("compacto_resolucion", 1.0) if (tam is not None and tam < COMPACTO_ARCMIN) \
            else ("normal", 0.5)

    if tipo in GALAXIAS:
        if sb is None:
            # Sin brillo superficial no se puede juzgar, y suponer "normal"
            # seria recomendar a ciegas. Se cae del lado prudente.
            return "bajo_contraste", 0.5
        if sb >= SB_EXTREMO:
            return "extremo_contraste", 0.5
        if sb >= SB_BAJO_CONTRASTE:
            return "bajo_contraste", 0.5
        return "normal", 0.5

    return "normal", 0.5


def _nombre(objeto: dict) -> str:
    """Como se llama en la pantalla: Messier manda, luego el nombre comun."""
    partes = []
    if "m" in objeto:
        partes.append(f"M{objeto['m']}")
    partes.append(objeto["n"].replace("NGC", "NGC ").replace("IC", "IC ").strip())
    etiqueta = " / ".join(dict.fromkeys(partes))
    if objeto.get("c"):
        etiqueta += f" ({objeto['c'].split(',')[0].strip()})"
    return etiqueta


def _alias(objeto: dict) -> tuple[str, ...]:
    """Todo por lo que alguien podria buscarlo."""
    alias = [objeto["n"]]
    if "m" in objeto:
        alias += [f"M{objeto['m']}", f"M {objeto['m']}", f"Messier {objeto['m']}"]
    if objeto.get("ic"):
        alias.append(f"IC {objeto['ic']}")
    if objeto.get("c"):
        alias += [c.strip() for c in objeto["c"].split(",")]
    # "NGC0224" y "NGC 224" son el mismo objeto para quien escribe.
    crudo = objeto["n"]
    if crudo.startswith(("NGC", "IC")):
        prefijo = "NGC" if crudo.startswith("NGC") else "IC"
        numero = crudo[len(prefijo):].lstrip("0") or "0"
        alias += [f"{prefijo} {numero}", f"{prefijo}{numero}"]
    return tuple(dict.fromkeys(a for a in alias if a))


def designacion(texto: str) -> str | None:
    """La designacion de catalogo, normalizada: "IC 1805", "IC1805" y
    "IC 1805 (Corazon)" son el mismo objeto para quien escribe y tienen que
    serlo tambien para el cruce.

    Sin esto, el objeto curado a mano y el mismo objeto venido de OpenNGC
    entraban los dos en el ranking, uno al lado del otro con las mismas horas.
    """
    import re
    m = re.match(r"\s*(M|NGC|IC)\s*0*(\d+)", texto.upper())
    return f"{m.group(1)}{int(m.group(2))}" if m else None


@lru_cache(maxsize=1)
def _crudo() -> list[dict]:
    if not FICHERO.exists():
        return []
    return json.loads(FICHERO.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def catalogo_completo() -> tuple[Target, ...]:
    """Los 12.098, como objetivos que el recomendador puede puntuar.

    Los 44 curados a mano MANDAN: si un objeto del catalogo grande coincide con
    uno de ellos por alias, se queda el curado, porque su clase la puso una
    persona mirando el objeto y no una regla.
    """
    curados = {a.lower() for t in CATALOGUE for a in (t.name,) + t.aliases}
    # Y por designacion normalizada, que es como se cuelan los duplicados: el
    # curado "IC 1805 (Corazon)" no lleva "IC 1805" entre sus alias.
    disenados = {d for t in CATALOGUE
                 for d in (designacion(a) for a in (t.name,) + t.aliases) if d}
    salida = list(CATALOGUE)
    for objeto in _crudo():
        alias = _alias(objeto)
        if any(a.lower() in curados for a in alias):
            continue
        if any(designacion(a) in disenados for a in alias if designacion(a)):
            continue
        dificultad, alpha = _clase(objeto)
        salida.append(Target(
            name=_nombre(objeto),
            ra_deg=objeto["ra"], dec_deg=objeto["dec"],
            kind=ESPANOL.get(objeto["t"], objeto["t"]),
            alpha=alpha, difficulty=dificultad,
            note="", aliases=alias))
    return tuple(salida)


def candidatos(*, magnitud_maxima: float = 12.0) -> tuple[Target, ...]:
    """Lo que vale la pena PUNTUAR esta noche, antes de mirar el cielo.

    El corte por magnitud no es una opinion sobre que merece la pena fotografiar
    -- hay gente cazando galaxias de 15 -- es lo que un recomendador puede
    ordenar con sentido: por debajo, la magnitud del catalogo tiene tanta
    incertidumbre que el ranking dejaria de significar nada. Los 44 curados
    entran siempre, tengan la magnitud que tengan.
    """
    brillo = {}
    for objeto in _crudo():
        if "v" in objeto:
            for alias in _alias(objeto):
                brillo[alias.lower()] = objeto["v"]
    curados = {t.name for t in CATALOGUE}
    salida = []
    for objetivo in catalogo_completo():
        if objetivo.name in curados:
            salida.append(objetivo)
            continue
        v = next((brillo[a.lower()] for a in (objetivo.name,) + objetivo.aliases
                  if a.lower() in brillo), None)
        if v is not None and v <= magnitud_maxima:
            salida.append(objetivo)
    return tuple(salida)
