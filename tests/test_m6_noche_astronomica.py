"""M6 (26-10-2026): la ventana del veredicto y las horas esperadas, dentro de la
noche astronomica.

El fallo que tapa: la rejilla de presentacion va de ocaso-1 h a orto+1 h, pero la
curva del techo (roofcalibration.py) se ajusta SOLO con horas de Sol < -18
grados. Medido en el archivo: 13 + 26 ventanas publicadas acababan despues del
orto, la falsa alarma del 28-09 se apoyo en una ventana de 06-08 UTC, y con la
curva de M1 la noche del 26-10 daba 11,9 horas esperadas con 10,3 h de noche
astronomica.

Las pruebas usan las efemerides reales (verano e invierno) y una probabilidad
de 1,0 en toda la rejilla: es el caso que mas estira la suma, asi que si la
cota se cumple aqui se cumple con cualquier probabilidad.
"""

import datetime as dt

import pytest

from astroweather import roofcalibration
from astroweather.ephem import (ASTRONOMICAL, dark_slot_weights, night_for,
                                sun_positions)
from astroweather.gates import evaluate, opening_probability, opening_window
from astroweather.tonight import HourForecast, NightReport

UTC = dt.timezone.utc
NOCHES = {"verano": dt.datetime(2026, 6, 21, 12, tzinfo=UTC),
          "invierno": dt.datetime(2026, 12, 21, 12, tzinfo=UTC)}


def _informe(day: dt.datetime, p, con_noche: bool = True, paso_min: int = 60):
    """Un informe con la rejilla de producto (ocaso-1 h a orto+1 h, marcas en
    punto o cada `paso_min`) y `p(t)` de probabilidad utilizable en cada casilla."""
    noche = night_for(day)
    t = noche.window_start.replace(minute=0, second=0, microsecond=0)
    if t < noche.window_start:
        t += dt.timedelta(hours=1)
    horas = []
    while t <= noche.window_end:
        horas.append(HourForecast(
            t_utc=t, cloud_total=0.0, clear_probability=p(t),
            usable_probability=p(t), moon_altitude=0.0,
            safety=evaluate(t_utc=t, hours_ahead=12.0), calibrated=True))
        t += dt.timedelta(minutes=paso_min)
    return NightReport(
        night=noche.night.date(), site="prueba", hours=horas, moon_illumination=0.0,
        dark_hours=noche.dark_hours, calibration_note="prueba",
        dusk_astronomical=noche.dusk_astronomical if con_noche else None,
        dawn_astronomical=noche.dawn_astronomical if con_noche else None), noche


def test_la_constante_es_la_de_la_curva_del_techo():
    """Un solo umbral: el de las horas con las que se ajusta la curva. Si alguien
    mueve uno, este test avisa de que los dos ya no cuentan lo mismo."""
    assert roofcalibration.DARK_SUN_ALTITUDE == ASTRONOMICAL == -18.0


@pytest.mark.parametrize("estacion", NOCHES)
def test_las_casillas_que_cuentan_son_las_del_criterio_de_la_curva(estacion):
    """Mismo criterio que `roofcalibration.hourly_pairs`: Sol < -18 grados a la
    media hora de la casilla. Se compara casilla a casilla con las efemerides."""
    informe, _ = _informe(NOCHES[estacion], lambda t: 1.0)
    medias = [h.t_utc + dt.timedelta(minutes=30) for h in informe.hours]
    altitudes = [p.altitude for p in sun_positions(medias)]
    esperado = [a < roofcalibration.DARK_SUN_ALTITUDE for a in altitudes]
    assert [w > 0 for w in informe.dark_weights] == esperado
    assert any(esperado) and not all(esperado)


@pytest.mark.parametrize("estacion", NOCHES)
def test_las_horas_esperadas_no_pasan_de_la_noche_astronomica(estacion):
    informe, noche = _informe(NOCHES[estacion], lambda t: 1.0)
    assert informe.usable_hours <= noche.dark_hours + 1e-9
    # y no es una cota vacia: la suma llega a la noche menos lo que dejan fuera
    # las dos casillas de los extremos (cada una, menos de una hora)
    assert informe.usable_hours > noche.dark_hours - 2.0
    # la rejilla entera, la cuenta de antes, SI se pasaba: es el fallo
    assert informe.usable_hours_grid > noche.dark_hours


@pytest.mark.parametrize("estacion", NOCHES)
def test_la_ventana_cae_dentro_de_la_noche_astronomica(estacion):
    informe, noche = _informe(NOCHES[estacion], lambda t: 1.0)
    inicio, fin, suelo = informe.verdict_window
    assert noche.dusk_astronomical <= inicio < fin <= noche.dawn_astronomical
    assert suelo == pytest.approx(1.0)
    mejor = informe.best_window
    assert noche.dusk_astronomical <= mejor[0] < mejor[1] <= noche.dawn_astronomical


@pytest.mark.parametrize("estacion", NOCHES)
def test_el_crepusculo_bueno_no_abre_una_noche_mala(estacion):
    """La falsa alarma del 28-09: lo unico bueno de la noche caia al amanecer.
    Con la rejilla entera la ventana se apoyaba ahi; ahora no puede."""
    informe, noche = _informe(
        NOCHES[estacion],
        lambda t: 0.95 if not (noche_oscura(NOCHES[estacion], t)) else 0.05)
    assert informe.opening_probability == pytest.approx(0.05)
    assert informe.verdict_window[2] == pytest.approx(0.05)
    assert informe.best_window is None
    # el control: sin acotar, esa misma noche daba "Abierto" con 0,95
    todas = [h.usable_probability for h in informe.hours]
    assert opening_probability(todas) == pytest.approx(0.95)


def noche_oscura(day, t):
    noche = night_for(day)
    return noche.dusk_astronomical <= t + dt.timedelta(minutes=30) <= noche.dawn_astronomical


def test_contar_la_casilla_entera_por_el_punto_medio_si_se_pasaba():
    """El control positivo de la cota: con el criterio del punto medio y peso 1
    para la casilla que cruza -18 grados, una noche de 10,3 h cuenta 11 casillas.
    Por eso el peso es la fraccion oscura de la casilla."""
    dusk = dt.datetime(2026, 10, 26, 18, 20, tzinfo=UTC)
    dawn = dusk + dt.timedelta(hours=10.26)
    inicios = [dt.datetime(2026, 10, 26, 17, tzinfo=UTC) + dt.timedelta(hours=i)
               for i in range(14)]
    pesos = dark_slot_weights(inicios, 1.0, dusk, dawn)
    cuentan = [w for w in pesos if w > 0]
    assert len(cuentan) == 11 > 10.26          # lo que daria el punto medio a secas
    assert sum(pesos) <= 10.26 + 1e-9          # con el peso, nunca
    # las dos casillas de los extremos cuentan en proporcion, no enteras
    assert 0 < cuentan[0] < 1 and 0 < cuentan[-1] < 1


@pytest.mark.parametrize("minutos", range(0, 60, 7))
@pytest.mark.parametrize("duracion_h", [6.0, 10.26, 10.5, 7.4])
def test_la_suma_de_pesos_nunca_pasa_de_la_noche(minutos, duracion_h):
    dusk = dt.datetime(2026, 10, 26, 18, 0, tzinfo=UTC) + dt.timedelta(minutes=minutos)
    dawn = dusk + dt.timedelta(hours=duracion_h)
    inicios = [dt.datetime(2026, 10, 26, 16, tzinfo=UTC) + dt.timedelta(hours=i)
               for i in range(16)]
    assert sum(dark_slot_weights(inicios, 1.0, dusk, dawn)) <= duracion_h + 1e-9
    mitad = [dt.datetime(2026, 10, 26, 16, tzinfo=UTC) + dt.timedelta(minutes=30 * i)
             for i in range(32)]
    assert sum(dark_slot_weights(mitad, 0.5, dusk, dawn)) * 0.5 <= duracion_h + 1e-9


def test_sin_cruces_astronomicos_todo_cuenta_como_antes():
    """Un informe armado a mano (sin ocaso/orto astronomicos) conserva el
    comportamiento anterior: no deja la noche vacia por no saber cuando empieza."""
    informe, _ = _informe(NOCHES["verano"], lambda t: 0.5, con_noche=False)
    assert informe.dark_weights == [1.0] * len(informe.hours)
    assert informe.usable_hours == pytest.approx(informe.usable_hours_grid)
    assert informe.verdict_window[0] == informe.hours[0].t_utc


def test_la_ventana_con_pesos_conserva_los_indices_de_la_rejilla_entera():
    p = [0.9, 0.9, 0.2, 0.3, 0.8, 0.7, 0.9, 0.9]
    pesos = [0, 0, 1, 1, 1, 1, 0, 0]
    assert opening_window(p) == (0, 1, pytest.approx(0.9))
    assert opening_window(p, weights=pesos) == (4, 5, pytest.approx(0.7))
    assert opening_probability(p, weights=pesos) == pytest.approx(0.7)
    assert opening_window(p, weights=[0] * 8) is None
    assert opening_probability(p, climatology=0.4, weights=[0] * 8) == pytest.approx(0.4)
