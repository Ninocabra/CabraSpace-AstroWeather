"""El panel ingles tiene que estar EN INGLES, incluido lo que redacta el motor.

La pagina traducia sus rotulos pero el contenido de "Que hacer esta noche" viaja
ya redactado dentro del JSON, asi que hasta el 31-08-2026 la version inglesa
enseñaba consejos en castellano. Traducir aguas abajo no era posible: el texto
lleva los numeros de la noche dentro, y recomponer la frase en el navegador
seria la misma frase calculada en dos sitios con dos reglas.

Estos tests barren MUCHAS noches distintas a proposito: cada rama de cada regla
redacta su propio texto, y una rama sin traducir solo aparece la noche en que se
dispara. Un unico caso de ejemplo no habria cazado nada.
"""

import itertools

import pytest

from astroweather.advice import Conditions, evaluate


def _noches():
    """Combinaciones que recorren las ramas de las 15 reglas."""
    for luna, cielo, seeing, polvo, hum, rocio, viento, oscuras in itertools.product(
            (0.0, 0.6, 0.95), (17.5, 21.4), ("favorable", "desfavorable"),
            (False, True), (40.0, 95.0), (1.0, 12.0), (1.0, 9.0), (3.0, 8.0)):
        yield Conditions(
            moon_illumination=luna, moon_altitude_max=40.0, moon_up=luna > 0.0,
            sky_mag=cielo, seeing_tercile=seeing, seeing_range=(0.9, 1.6),
            aod=0.3 if polvo else 0.05, k_total=0.4 if polvo else 0.2,
            dust_alert=polvo, open_probability=0.4, dark_hours=oscuras,
            humidity=hum, dew_margin=rocio, wind=viento,
            planets_up=[("Marte", 45.0)])


CASOS = list(_noches())


def test_hay_bastantes_noches_que_barrer():
    """Si el barrido se queda corto, los demas tests dejan de proteger."""
    assert len(CASOS) > 200


@pytest.mark.parametrize("c", CASOS)
def test_ninguna_fila_sale_sin_ingles(c):
    salida = evaluate(c)
    for fila in salida["adquisicion"] + salida["objetos"] + salida["avisos"]:
        assert fila["etiqueta_en"], fila["clave"]
        assert fila["resumen_en"], fila["clave"]
        assert fila["detalle_en"], fila["clave"]


@pytest.mark.parametrize("c", CASOS[:64])
def test_el_ingles_no_es_el_castellano_copiado(c):
    """Una traduccion olvidada que copie el original pasaria el test de arriba."""
    salida = evaluate(c)
    for fila in salida["adquisicion"] + salida["objetos"] + salida["avisos"]:
        assert fila["detalle_en"] != fila["detalle"], fila["clave"]


@pytest.mark.parametrize("c", CASOS[:64])
def test_el_titular_tambien_viaja_en_ingles(c):
    salida = evaluate(c)
    assert salida["resumen_en"]
    assert salida["resumen_en"] != salida["resumen"]


def test_las_referencias_llevan_las_dos_versiones():
    for ref in evaluate(CASOS[0])["referencias"]:
        assert ref["tema_en"] and ref["dice_en"]
        assert ref["dice_en"] != ref["dice"]


@pytest.mark.parametrize("c", CASOS[:32])
def test_el_titular_ingles_nombra_lo_mismo_que_el_castellano(c):
    """Los dos titulares salen de los MISMOS semaforos.

    No se comparan palabras -- son idiomas distintos -- sino la estructura: si
    uno dice "hoy no" el otro tambien, porque la lista de rojos es la misma.
    """
    salida = evaluate(c)
    assert ("Hoy no:" in salida["resumen"]) == ("Not today:" in salida["resumen_en"])
