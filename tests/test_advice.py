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
        assert len(salida["adquisicion"]) == 9      # + fotometria, 26-09-2026
        assert len(salida["objetos"]) == 9      # + transitos y variables, 26-09-2026


def test_infrared_is_green_exactly_when_there_is_something_to_get_through():
    """El IR gana atravesando: Luna, calima o mal seeing. Sin nada de eso su
    ventaja desaparece, y decir que siempre es buena idea seria vender humo."""
    from astroweather.advice import Conditions, evaluate, VERDE, AMBAR

    def luz(**kw):
        salida = evaluate(Conditions(**kw))
        return next(f for f in salida["adquisicion"] if f["clave"] == "ir")["luz"]

    assert luz(**LLENA) == VERDE                                   # Luna alta
    assert luz(**{**NUEVA, "dust_alert": True, "aod": 0.31}) == VERDE   # calima
    assert luz(**NUEVA) == AMBAR                                   # nada que atravesar


def test_the_limiting_factor_names_a_tile_of_the_summary():
    """La casilla que la pagina pinta en rojo sale de aqui, para que no pueda
    discrepar del semaforo."""
    from astroweather.advice import Conditions, evaluate

    assert evaluate(Conditions(**LLENA))["limitante"] == "luna"
    assert evaluate(Conditions(**NUEVA))["limitante"] is None
    assert evaluate(Conditions(**{**NUEVA, "dust_alert": True,
                                 "aod": 0.31}))["limitante"] == "transparencia"
    assert evaluate(Conditions(**{**NUEVA, "seeing_tercile": "desfavorable"}
                               ))["limitante"] == "seeing"


def test_the_headline_lists_every_green_not_a_sample():
    """Si el resumen no es exhaustivo no es un resumen, es una muestra: obliga
    a abrir las dos filas para saber si te has perdido algo."""
    from astroweather.advice import Conditions, evaluate, VERDE

    salida = evaluate(Conditions(**LLENA))
    verdes = [f["etiqueta"] for f in salida["adquisicion"] + salida["objetos"]
              if f["luz"] == VERDE]

    for etiqueta in verdes:
        assert etiqueta.lower() in salida["resumen"].lower(), etiqueta


def _fila(clave, **kw):
    salida = evaluate(Conditions(**kw))
    return next(f for f in salida["adquisicion"] + salida["objetos"]
                if f["clave"] == clave)


def test_photometry_asks_for_a_stable_sky_not_a_dark_one():
    """La Luna llena tumba la banda ancha pero NO la fotometria de estrellas
    brillantes: se mide contra comparativas del mismo campo. Lo que si la
    tumba son las nubes a ratos, que no atenuan igual a todas."""
    assert _fila("rgb", **LLENA)["luz"] == ROJO
    assert _fila("fotometria", **LLENA)["luz"] == AMBAR
    assert _fila("fotometria", **NUEVA)["luz"] == VERDE
    assert _fila("fotometria", **{**NUEVA, "open_probability": 0.4})["luz"] == ROJO


def test_dust_allows_differential_but_not_absolute_photometry():
    fila = _fila("fotometria", **{**NUEVA, "dust_alert": True, "k_total": 0.40})
    assert fila["luz"] == AMBAR
    assert "absoluta" in fila["resumen"]


def test_poor_seeing_warns_about_scintillation_on_bright_stars():
    fila = _fila("fotometria", **{**NUEVA, "seeing_tercile": "desfavorable"})
    assert fila["luz"] == AMBAR
    assert "centelleo" in fila["resumen"]


def test_a_transit_needs_it_whole():
    """Un hueco en la entrada o la salida deja el ajuste sin atar; una noche
    corta puede no tener sitio para tránsito y linea base."""
    assert _fila("transitos", **NUEVA)["luz"] == VERDE
    assert _fila("transitos", **{**NUEVA, "open_probability": 0.4})["luz"] == ROJO
    assert _fila("transitos", **{**NUEVA, "open_probability": 0.55})["luz"] == AMBAR
    assert _fila("transitos", **{**NUEVA, "dark_hours": 3.5})["luz"] == AMBAR
    # La Luna no cambia el brillo de una anfitriona brillante.
    assert _fila("transitos", **LLENA)["luz"] == VERDE


def test_slow_variables_survive_a_broken_night_and_fast_ones_do_not():
    fila = _fila("variables", **{**NUEVA, "open_probability": 0.4})
    assert fila["luz"] == AMBAR
    assert "lentas" in fila["resumen"]
    assert _fila("variables", **NUEVA)["luz"] == VERDE
    assert _fila("variables", **LLENA)["resumen"] == "las débiles no"
