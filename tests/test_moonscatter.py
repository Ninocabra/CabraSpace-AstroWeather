"""Tests de la ley de dispersion lunar medida en el sitio (moonscatter.py).

No comprueban que el ajuste sea bueno -- eso lo dice el archivo, no un test --
sino que el modulo sigue diciendo lo que se midio, que no se separa de la
funcion publicada donde no debe, y que las tres propiedades que decidieron el
diagnostico siguen ahi.
"""

from datetime import datetime, timezone

import pytest

from astroweather import moonscatter
from astroweather.moonsky import moon_brightness, scattering as mauna_kea
from astroweather.skymodel import predict


def test_the_published_function_is_copied_exactly():
    """El suelo tiene que ser la funcion publicada, no una version que derivo.

    `moonscatter` la duplica porque `moonsky` la importa a el y no puede haber
    ciclo. Este test es lo unico que impide que las dos copias se separen.
    """
    for separation in (10, 25, 45, 70, 90, 120, 179):
        assert moonscatter._mauna_kea(separation) == pytest.approx(
            mauna_kea(separation), rel=1e-12)


def test_this_site_scatters_more_than_mauna_kea_where_it_was_measured():
    """1650 m con polvo sahariano contra 4200 m sobre la inversion.

    Entre 12 y 80 grados, que es el rango ajustado, la ley del sitio esta por
    encima; el cociente crece hacia angulos pequenos, que es la forma que se
    midio (5,5x con la Luna alta, 1,2x con la Luna baja).
    """
    ratios = [moonscatter.scattering(s) / mauna_kea(s) for s in (15, 40, 78)]
    assert all(r > 1.0 for r in ratios)
    assert ratios[0] > ratios[1] > ratios[2]


def test_the_law_never_falls_below_the_published_one():
    """Fuera del rango medido manda el suelo, o a 150 grados daria 37x MENOS.

    Este sitio tiene mas aire y mas polvo encima que Mauna Kea: no puede
    dispersar menos, y una exponencial extrapolada si lo haria.
    """
    for separation in (85, 100, 130, 179):
        assert moonscatter.scattering(separation) >= mauna_kea(separation)


def test_the_scattering_law_falls_monotonically():
    values = [moonscatter.scattering(s) for s in range(12, 81, 4)]
    assert values == sorted(values, reverse=True)


def test_the_beam_crosses_the_horizon_without_a_step():
    """El resultado que decidio el diagnostico: no hay escalon en el horizonte.

    Luz parasita implica corte seco cuando la Tierra tapa el haz. Lo medido es
    una caida continua, con la Luna ya invisible dando todavia una fraccion
    apreciable de lo que da recien salida.
    """
    below = moonscatter.beam_transmission(-1.0)
    above = moonscatter.beam_transmission(1.0)
    assert 0.0 < below < above < 1.0
    assert below / above > 0.3          # medido: 0,130 / 0,304 = 0,43


def test_the_beam_dies_where_the_air_column_is_in_shadow():
    assert moonscatter.beam_transmission(-6.0) == 0.0
    assert moonscatter.beam_transmission(-20.0) == 0.0
    assert moonscatter.beam_transmission(30.0) == 1.0


def test_the_transmission_only_ever_grows_with_altitude():
    altitudes = [a / 2 for a in range(-14, 44)]
    values = [moonscatter.beam_transmission(a) for a in altitudes]
    assert values == sorted(values)


def test_the_shadow_geometry_is_what_the_decay_was_checked_against():
    """La cuenta que sostiene la interpretacion, no el modelo.

    A 2 grados de depresion la sombra sube unos 4 km, que es la escala a la que
    se midio que decae el exceso.
    """
    assert moonscatter.shadow_height_km(0.0) == 0.0
    assert moonscatter.shadow_height_km(2.0) == pytest.approx(3.9, abs=0.2)
    assert moonscatter.shadow_height_km(6.0) == pytest.approx(35.1, abs=1.0)


def test_the_fitted_range_is_declared_and_not_hidden():
    assert moonscatter.is_extrapolated(5.0)
    assert not moonscatter.is_extrapolated(45.0)
    assert moonscatter.is_extrapolated(120.0)


def test_a_high_full_moon_now_matches_what_the_photometer_reads():
    """El numero que el modelo anterior fallaba por 1,8 mag.

    Medido en el archivo sobre horas planas con la Luna entre 40 y 60 grados y
    fase menor de 30: mediana 17,53 mag/arcsec2, donde el modelo de Mauna Kea
    predecia 18,57.
    """
    sky = moon_brightness(phase_angle_deg=15, separation_deg=40,
                          moon_altitude_deg=50, target_altitude_deg=90)
    from astroweather.moonsky import mag_to_nl, nl_to_mag
    total = nl_to_mag(mag_to_nl(21.71) + sky.physical_nl)
    assert 17.0 < total < 18.0


def test_the_clear_sky_model_uses_the_site_law_end_to_end():
    """Si alguien vuelve a enchufar la de Mauna Kea, esto se entera."""
    full_moon_high = datetime(2026, 8, 27, 23, tzinfo=timezone.utc)
    prediction = predict([full_moon_high])[0]
    assert prediction.moon_altitude > 30.0
    assert prediction.predicted_mag < 18.0      # con la de Mauna Kea salia 19,2
