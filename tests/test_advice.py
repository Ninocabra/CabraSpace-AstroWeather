# -*- coding: utf-8 -*-
"""Tests del semaforo de la noche (29-08-2026).

Lo que protegen es que el semaforo SEPA CONTRADECIRSE segun la noche: si con
Luna llena y con Luna nueva pintara lo mismo, seria decoracion. Y que la
contradiccion sea la correcta -- el error tipico de la banda estrecha es dar por
hecho que el OIII hereda el indulto del Halfa, y aqui no lo hereda.
"""

import pytest

from astroweather.advice import (
    AMBAR, ROJO, VERDE, Conditions, evaluate,
)

LLENA = dict(moon_illumination=0.97, moon_up=True, sky_mag=17.1,
             seeing_tercile="favorable", seeing_range=(0.7, 1.3),
             k_total=0.20, dark_hours=7.8, open_probability=0.88)
NUEVA = dict(moon_illumination=0.02, moon_up=False, sky_mag=21.7,
             seeing_tercile="normal", seeing_range=(1.2, 1.9),
             k_total=0.16, dark_hours=8.0, open_probability=0.90)


def _luces(**kwargs):
    salida = evaluate(Conditions(**kwargs))
    return {f["clave"]: f["luz"] for f in salida["adquisicion"] + salida["objetos"]}


def test_a_full_moon_hands_the_night_to_halpha_and_takes_it_from_broadband():
    luces = _luces(**LLENA)

    assert luces["rgb"] == ROJO
    assert luces["luminancia"] == ROJO
    assert luces["ha"] == VERDE
    assert luces["ifn"] == ROJO


def test_oiii_does_not_inherit_the_pardon_of_halpha():
    """El error tipico de la banda estrecha, y la razon de que el OIII tenga
    fila propia: cae en el azul, donde Rayleigh va como lambda^-4."""
    luces = _luces(**LLENA)

    assert luces["ha"] == VERDE
    assert luces["oiii"] == ROJO
    # Y el dual, que es medio y medio, queda en ambar por eso mismo.
    assert luces["dual"] == AMBAR


def test_a_new_moon_says_the_opposite():
    luces = _luces(**NUEVA)

    assert luces["rgb"] == VERDE
    assert luces["luminancia"] == VERDE
    assert luces["oiii"] == VERDE
    assert luces["galaxias"] == VERDE


def test_a_full_moon_that_never_rises_is_not_a_problem():
    """La fase sola mandaba a rojo noches limpias: lo que molesta es la Luna
    ARRIBA, y el semaforo tiene que saber la diferencia."""
    arriba = _luces(**{**LLENA, "moon_up": True, "sky_mag": 17.1})
    abajo = _luces(**{**LLENA, "moon_up": False, "sky_mag": 21.6})

    assert arriba["rgb"] == ROJO
    assert abajo["rgb"] == VERDE


def test_dust_hits_the_blue_channel_and_spares_halpha():
    luces = _luces(moon_illumination=0.1, moon_up=True, sky_mag=21.0,
                   dust_alert=True, aod=0.31, k_total=0.34,
                   seeing_tercile="normal", seeing_range=(1.2, 1.9))

    assert luces["oiii"] == ROJO
    assert luces["ha"] == AMBAR       # resta, pero pasa
    assert luces["sii"] == AMBAR


def test_the_seeing_decides_the_planets_and_the_moon_does_not():
    malo = _luces(**{**LLENA, "seeing_tercile": "desfavorable",
                     "seeing_range": (2.4, 3.9)})
    bueno = _luces(**LLENA)

    assert malo["planetaria"] == ROJO
    # Con Luna llena y seeing bueno la planetaria sigue verde: no vive del fondo.
    assert bueno["planetaria"] == VERDE


def test_clusters_are_stars_not_a_surface():
    """`SKY_GATE` mide cuanto fondo aguanta una SUPERFICIE, y un globular
    brillante se saca con Luna llena. Pasarlos por esa puerta los pintaba en
    rojo, y eso es falso."""
    luces = _luces(**LLENA)

    assert luces["cumulos"] == AMBAR
    assert luces["galaxias"] == ROJO      # esa si es superficie


def test_emission_is_amber_because_the_two_filters_disagree():
    """La fila que cruza las dos: en Halfa verde y en OIII rojo, asi que la
    familia entera queda en 'si, pero solo por un canal'."""
    salida = evaluate(Conditions(**LLENA))
    emision = next(f for f in salida["objetos"] if f["clave"] == "emision")

    assert emision["luz"] == AMBAR
    assert "Hα" in emision["resumen"]


def test_every_light_carries_its_numbers():
    """Un veredicto sin su cuenta no se puede discutir."""
    salida = evaluate(Conditions(**LLENA))
    for fila in salida["adquisicion"] + salida["objetos"]:
        assert fila["luz"] in (VERDE, AMBAR, ROJO)
        assert fila["resumen"] and fila["detalle"]
        assert any(c.isdigit() for c in fila["detalle"]), fila["clave"]
        assert "\n" not in fila["detalle"], "el detalle va en una linea"


def test_the_equipment_warnings_only_appear_when_measured():
    """Sin sensores no se inventan avisos; con ellos, salen con su numero."""
    sin = evaluate(Conditions(**LLENA))["avisos"]
    con = evaluate(Conditions(**{**LLENA, "dew_margin": 1.2, "wind": 9.0}))["avisos"]

    assert sin == []
    claves = {a["clave"]: a["luz"] for a in con}
    assert claves["rocio"] == ROJO
    assert claves["viento"] == ROJO


def test_the_headline_names_what_to_do_and_what_not_to():
    resumen = evaluate(Conditions(**LLENA))["resumen"]

    assert "Hα" in resumen
    assert "RGB" in resumen           # sin destrozar las siglas


def test_every_row_is_always_present():
    """Ensenar solo lo que va mal es mas corto y menos util: quien mira esto
    esta decidiendo un plan y necesita ver tambien lo que si puede hacer."""
    for condiciones in (LLENA, NUEVA):
        salida = evaluate(Conditions(**condiciones))
        assert len(salida["adquisicion"]) == 7
        assert len(salida["objetos"]) == 6
