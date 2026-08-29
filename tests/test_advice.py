"""Tests de la base de consejos de adquisicion (29-08-2026).

Un consejo es una regla con su condicion en numeros, no una frase suelta. Lo
que estos tests protegen es que las reglas SEPAN CONTRADECIRSE segun la noche:
si con Luna llena y con Luna nueva dijeran lo mismo, la base seria decoracion.
"""

from astroweather.advice import Conditions, RULES, SEVERITIES, evaluate


def _consejos(**kwargs):
    return {c["clave"]: c for c in evaluate(Conditions(**kwargs))}


def test_a_full_moon_kills_broadband_and_hands_the_night_to_halpha():
    """La inversion que hace util a toda la base."""
    salida = _consejos(moon_illumination=0.97, sky_mag=17.1)

    assert salida["rgb_luna_alta"]["severidad"] == "evitar"
    assert salida["halfa_luna"]["severidad"] == "aprovecha"
    assert salida["ifn_luna"]["severidad"] == "evitar"
    # Y el OIII NO hereda el indulto del Halfa, que es el error tipico.
    assert salida["oiii_luna"]["severidad"] == "evitar"


def test_a_new_moon_says_the_opposite():
    salida = _consejos(moon_illumination=0.02, sky_mag=21.7, dark_hours=7.5)

    assert "luna_nueva" in salida
    assert "rgb_luna_alta" not in salida
    assert "ifn_luna" not in salida
    assert "halfa_luna" not in salida        # el Halfa no gana nada sin Luna


def test_the_moon_does_not_touch_the_planets():
    """Una base que solo supiera decir 'no' con Luna seria media base."""
    salida = _consejos(moon_illumination=0.97, sky_mag=17.1)

    assert salida["planetaria_luna"]["severidad"] == "aprovecha"


def test_dust_hits_oiii_and_spares_halpha():
    salida = _consejos(dust_alert=True, aod=0.31, k_total=0.34)

    assert salida["calima_oiii"]["severidad"] == "evitar"
    assert "calima_rgb" in salida
    assert not any(c["tecnica"] == "halfa" and c["severidad"] == "evitar"
                   for c in salida.values())


def test_bad_seeing_sinks_the_planets_and_leaves_the_nebulae_alone():
    salida = _consejos(seeing_tercile="desfavorable", seeing_range=(2.4, 3.9))

    assert salida["seeing_malo_planetaria"]["severidad"] == "evitar"
    assert "seeing_bueno" not in salida
    # Y dice a donde ir, no solo de donde huir.
    assert "no se entera del seeing" in salida["seeing_malo_compacto"]["texto"]


def test_every_advice_carries_the_numbers_that_fired_it():
    """Un consejo sin su porque no se puede discutir, y si no se puede
    discutir no vale nada."""
    condiciones = Conditions(moon_illumination=0.97, sky_mag=17.06,
                             seeing_tercile="desfavorable", seeing_range=(2.4, 3.9),
                             dust_alert=True, aod=0.31, k_total=0.34,
                             open_probability=0.42, dark_hours=4.2,
                             humidity=91.0, dew_margin=1.4, wind=7.0)
    consejos = evaluate(condiciones)

    assert consejos, "una noche asi de mala tiene que decir algo"
    for consejo in consejos:
        assert "{" not in consejo["texto"], "queda un hueco sin rellenar"
        assert consejo["severidad"] in SEVERITIES
        assert any(caracter.isdigit() for caracter in consejo["texto"]), (
            f"{consejo['clave']} no ensena ni un numero")


def test_a_night_with_nothing_wrong_does_not_invent_problems():
    """Callarse es una respuesta. Si no hay Luna, ni calima, ni nada medido,
    lo unico que se puede decir es que aproveches la oscuridad."""
    consejos = evaluate(Conditions(moon_illumination=0.02, dark_hours=8.0))

    assert [c["clave"] for c in consejos] == ["luna_nueva"]


def test_the_worst_news_comes_first():
    """El orden no es cosmetico: lo que hay que EVITAR cambia el plan, y lo
    que hay que aprovechar solo lo mejora."""
    consejos = evaluate(Conditions(moon_illumination=0.97, sky_mag=17.1,
                                   open_probability=0.9))
    severidades = [c["severidad"] for c in consejos]

    assert severidades[0] == "evitar"
    assert severidades == sorted(severidades,
                                 key=lambda s: {"evitar": 0, "aprovecha": 1,
                                                "cuidado": 2}[s])


def test_no_rule_is_unreachable():
    """Una regla que no puede dispararse nunca es codigo muerto que parece
    conocimiento."""
    extremas = [
        Conditions(moon_illumination=0.97, sky_mag=17.0, seeing_tercile="desfavorable",
                   seeing_range=(2.4, 3.9), dust_alert=True, aod=0.31, k_total=0.34,
                   open_probability=0.3, dark_hours=4.0, humidity=92.0,
                   dew_margin=1.0, wind=8.0),
        Conditions(moon_illumination=0.01, sky_mag=21.7, seeing_tercile="favorable",
                   seeing_range=(0.7, 1.2), k_total=0.15, open_probability=0.95,
                   dark_hours=8.0, humidity=35.0, dew_margin=12.0, wind=1.0),
        Conditions(moon_illumination=0.65, sky_mag=19.5, seeing_tercile="normal",
                   seeing_range=(1.2, 1.9), open_probability=0.7, dark_hours=6.0),
    ]
    disparadas = {c["clave"] for condiciones in extremas
                  for c in evaluate(condiciones)}

    assert {r.key for r in RULES} == disparadas
