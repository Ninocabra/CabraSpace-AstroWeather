"""Tests for the sky background budget (SPEC v8 section 2.2)."""

import datetime as dt

import pytest

from astroweather.skybackground import (
    epoch_floor,
    NATURAL_FLOOR_MAG, airglow_excess, budget, integration_penalty,
    zodiacal_excess,
)

NIGHT = dt.datetime(2026, 7, 15, 23, tzinfo=dt.timezone.utc)


def test_the_ecliptic_is_brighter_than_the_ecliptic_pole():
    """Measured on site: 0.27 mag of zodiacal light overhead at the ecliptic."""
    assert zodiacal_excess(0) > zodiacal_excess(45) > zodiacal_excess(89)
    assert zodiacal_excess(0) == pytest.approx(0.27, abs=0.02)


def test_the_zodiacal_term_decays_and_does_not_go_negative():
    for latitude in (0, 10, 30, 60, 90, -30):
        assert 0 <= zodiacal_excess(latitude) <= 0.4


def test_airglow_follows_solar_activity_slowly():
    """A slow term, never an hourly predictor."""
    assert airglow_excess(230) > airglow_excess(130) > airglow_excess(70)
    assert airglow_excess(130) == pytest.approx(0.0)
    assert airglow_excess(None) == 0.0
    # A whole solar cycle moves the sky by about a tenth of a magnitude.
    assert abs(airglow_excess(230) - airglow_excess(70)) < 0.25


def test_the_budget_says_where_every_magnitude_came_from():
    entry = budget([NIGHT])[0]
    # Contra el suelo DE SU PROPIO INSTANTE, que desde el 30-08-2026 no es el
    # de hoy: el suelo se remide cada dia contra el fotometro y ademas se
    # guarda su DERIVA, porque el cielo de aqui se ha oscurecido medio punto de
    # magnitud desde 2019 y aplicarle a una noche de 2019 el suelo de 2026 mete
    # 0,44 mag justo donde se deriva la verdad terreno de nubes. Sin serie
    # medida `epoch_floor(t)` devuelve el numero unico, asi que esto vale en
    # las dos situaciones.
    assert entry.natural_floor == pytest.approx(float(epoch_floor(NIGHT)), abs=1e-9)
    assert entry.zodiacal <= 0            # brightening, so it lowers the number
    assert "suelo natural" in entry.breakdown
    assert 20.5 < entry.total_mag < 22.2


def test_light_pollution_adds_in_flux_not_in_magnitudes():
    clean = budget([NIGHT])[0]
    polluted = budget([NIGHT], light_pollution_mag=22.0)[0]
    assert polluted.total_mag < clean.total_mag
    # Two comparable sources brighten by less than a full magnitude.
    assert clean.total_mag - polluted.total_mag < 1.0


def test_each_magnitude_of_sky_costs_a_factor_in_exposure():
    """The number that turns 21.0 instead of 21.9 into something actionable."""
    # Penalizacion 1 contra el suelo en uso, sea el medido o el de respaldo.
    assert integration_penalty(epoch_floor()) == pytest.approx(1.0)
    # Una magnitud por debajo del suelo EN USO cuesta un factor 2,512. Antes
    # se restaba a la constante, y eso mezclaba dos afirmaciones: la del
    # factor por magnitud, que es fisica, y la del valor del suelo, que ahora
    # se remide cada dia.
    assert integration_penalty(epoch_floor() - 1) == pytest.approx(2.512, rel=1e-3)
    assert integration_penalty(epoch_floor() - 2) == pytest.approx(6.31, rel=1e-2)


def test_the_sky_is_not_one_number_the_zenith_is_not_the_sky():
    """El suelo depende de DONDE se apunta, y hasta 2026-08-28 no lo hacia.

    La Via Lactea vale 0,39 mag en el plano y decae con escala 26,5 grados, asi
    que colapsar la boveda a su valor cenital cuesta hasta 0,405 mag sobre el
    cielo util -- contra los 0,17 mag de incertidumbre que este modelo declara.
    """
    import numpy as np

    from astroweather.skybackground import natural_background

    times = [NIGHT] * 3
    # Tres direcciones a la misma altura y azimut distinto: el suelo no puede
    # salir igual en las tres, o el termino galactico no se esta aplicando.
    values = natural_background(times, [60.0, 60.0, 60.0], [0.0, 120.0, 240.0])
    assert len(set(np.round(values, 3))) > 1
    assert all(20.5 < v < 22.2 for v in values)


def test_pointing_at_the_galactic_plane_is_brighter_than_the_pole():
    """El signo, que es lo unico que no puede estar al reves."""
    import numpy as np
    from astropy.coordinates import SkyCoord, AltAz
    import astropy.units as u
    from astropy.time import Time

    from astroweather.ephem import location
    from astroweather.site import NERPIO
    from astroweather.skybackground import natural_background

    when = Time(NIGHT)
    frame = AltAz(obstime=when, location=location(NERPIO))
    # Barremos el azimut a altura fija y cogemos el punto de |b| menor y mayor.
    azimuths = np.arange(0, 360, 5.0)
    altitudes = np.full_like(azimuths, 50.0)
    pointing = SkyCoord(AltAz(az=azimuths * u.deg, alt=altitudes * u.deg,
                              obstime=when, location=location(NERPIO)))
    b = np.abs(np.asarray(pointing.galactic.b.deg, dtype=float))
    plane, pole = int(np.argmin(b)), int(np.argmax(b))

    values = natural_background([NIGHT] * len(azimuths), altitudes, azimuths, NERPIO)
    # Magnitudes al reves: mas brillante es numero MENOR.
    assert values[plane] < values[pole]


def test_the_zenith_helpers_still_agree_with_the_general_one():
    """Refactorizar el cenit a un caso particular no puede moverlo."""
    import numpy as np

    from astroweather.skybackground import budget, natural_background

    general = natural_background([NIGHT], [90.0], [0.0])[0]
    assert general == pytest.approx(budget([NIGHT])[0].total_mag, abs=1e-9)


def test_el_suelo_de_2019_no_es_el_de_2026():
    """La deriva medida tiene que llegar al modelo, no quedarse en el fichero.

    Es la diferencia entre releer el archivo bien y releerlo con el cielo de
    hoy: sobre 15.514 horas estables, el residuo del modelo iba de -0,374 en
    2019 a +0,064 en 2026 con un suelo unico, y de -0,002 a +0,057 con la
    serie. Si esta afirmacion deja de cumplirse es que la serie se ha perdido y
    la verdad terreno vuelve a llevar dentro una rampa de siete anios.
    """
    import datetime as dt

    from astroweather.skybackground import _serie_suelo

    if not _serie_suelo():
        pytest.skip("sin serie medida en esta maquina")

    viejo = float(epoch_floor(dt.datetime(2019, 9, 1, tzinfo=dt.timezone.utc)))
    reciente = float(epoch_floor(dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)))
    assert reciente > viejo, "el cielo se ha oscurecido; el suelo tiene que subir"
    assert 0.2 < reciente - viejo < 1.0, f"deriva implausible: {reciente - viejo:+.3f} mag"
