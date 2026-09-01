# -*- coding: utf-8 -*-
"""La extincion de CADA HORA llega a la fisica, y no la mediana de la noche.

Hasta el 01-09-2026 `evaluate_night` calculaba UNA transparencia con la mediana
de la noche y la usaba en los tres sitios donde entra la extincion: los dos
caminos de luz de la Luna, la perdida por masa de aire y el rendimiento. Los
tres son exponenciales en k, asi que colapsar a la mediana no es "aproximado":
por Jensen no da lo mismo ni de lejos. Medido con una rampa de AOD 0,05 -> 0,35
de la misma mediana que un 0,20 plano, 35 objetos del catalogo se mueven una
mediana del 3,1 % y hasta un 19 %, EN LAS DOS DIRECCIONES -- ganan los que
tienen su ventana en la parte limpia y pierden los que la tienen en la sucia.
Esa es exactamente la informacion que la mediana borraba.

El dato existia desde el 31-08: `queries.night_series` lo publicaba y `_hour` lo
sacaba a la web hora a hora. Lo que seguia comiendose la mediana era el modelo.
"""

import datetime as dt
import statistics

import pytest

from astroweather.ephem import night_for
from astroweather.hourly import at_times
from astroweather.seeing import integrate
from astroweather.targets import CATALOGUE, evaluate_night

UTC = dt.timezone.utc
NEW_MOON = dt.datetime(2026, 9, 11, tzinfo=UTC)


@pytest.fixture(scope="module")
def noche():
    times = night_for(NEW_MOON).hours(60)
    return times, len(times)


def _serie(times, valores):
    return {t: {"aod_total": v, "aod_dust": None, "jet_speed": None}
            for t, v in zip(times, valores)}


def _correr(times, serie=None, aod_total=0.20):
    return {r.target.name: r.q_conditional for r in evaluate_night(
        CATALOGUE, times, usable_probability=[0.8] * len(times),
        turbulence=integrate(20.0), aod_total=aod_total, aod_dust=0.02,
        serie=serie)}


# --------------------------------------------------------------------------
# El ayudante: interpolar, sostener los bordes y no inventarse zonas horarias
# --------------------------------------------------------------------------

def test_interpola_linealmente_entre_muestras_horarias():
    """A mitad de camino entre dos muestras, el valor de en medio.

    La rejilla de la noche va a diez minutos dentro de la oscuridad y las
    muestras del archivo son horarias: sin interpolar, cada cambio de hora mete
    un escalon que el aerosol no tiene.
    """
    base = dt.datetime(2026, 9, 1, 22, tzinfo=UTC)
    serie = _serie([base, base + dt.timedelta(hours=1)], [0.10, 0.20])
    medio = base + dt.timedelta(minutes=30)
    assert at_times(serie, "aod_total", [medio])[0] == pytest.approx(0.15)


def test_fuera_del_tramo_se_sostiene_el_extremo_y_no_se_extrapola():
    """Prolongar la recta se va a AOD negativo, que no significa nada."""
    base = dt.datetime(2026, 9, 1, 22, tzinfo=UTC)
    serie = _serie([base, base + dt.timedelta(hours=1)], [0.30, 0.10])
    antes = base - dt.timedelta(hours=5)
    despues = base + dt.timedelta(hours=5)
    assert at_times(serie, "aod_total", [antes, despues]) == [0.30, 0.10]


def test_lo_ingenuo_se_lee_como_utc_y_no_como_hora_local():
    """`datetime.timestamp()` sobre un naive asume la zona de la maquina.

    En Nerpio son dos horas en verano: sobre una serie horaria, dos muestras
    enteras de deslizamiento, y en silencio.
    """
    base = dt.datetime(2026, 9, 1, 22, tzinfo=UTC)
    serie = _serie([base, base + dt.timedelta(hours=1)], [0.10, 0.20])
    ingenuo = dt.datetime(2026, 9, 1, 22, 30)
    assert at_times(serie, "aod_total", [ingenuo])[0] == pytest.approx(0.15)


def test_sin_muestras_del_campo_devuelve_la_mediana_declarada():
    """La serie ANADE resolucion; cuando no la hay, se publica lo de siempre."""
    base = dt.datetime(2026, 9, 1, 22, tzinfo=UTC)
    serie = _serie([base], [0.10])                 # trae aod_total, no aod_dust
    assert at_times(serie, "aod_dust", [base], fallback=0.03) == [0.03]
    assert at_times({}, "aod_total", [base], fallback=0.07) == [0.07]


# --------------------------------------------------------------------------
# El modelo: que la k horaria LLEGUE, y que su ausencia no cambie nada
# --------------------------------------------------------------------------

def test_sin_serie_sale_exactamente_lo_de_antes(noche):
    """La red de seguridad: sin serie, el informe es el que era.

    Vale para las noches viejas del archivo y para cualquier fallo de la
    consulta. Si esto se rompe, el cambio ha dejado de ser una mejora de
    resolucion y se ha convertido en otra cosa.
    """
    times, _ = noche
    assert _correr(times, serie=None) == _correr(times, serie={})


def test_una_serie_constante_reproduce_la_mediana(noche):
    """Sin variacion que aportar, la maquinaria nueva no puede mover nada.

    Separa el efecto FISICO (la k se mueve) del efecto de haber metido tuberia
    nueva: si una serie plana a 0,20 no diera lo mismo que un 0,20 plano, lo que
    estariamos midiendo seria la tuberia.
    """
    times, n = noche
    plano = _correr(times, serie=None)
    constante = _correr(times, serie=_serie(times, [0.20] * n))
    for nombre, q in plano.items():
        assert constante[nombre] == pytest.approx(q, rel=1e-12), nombre


def test_la_misma_mediana_repartida_distinto_NO_da_lo_mismo(noche):
    """El test que el codigo viejo no podia pasar.

    Una rampa 0,05 -> 0,35 tiene mediana 0,20 exacta, asi que el modelo antiguo
    la veia igual que un 0,20 plano: las dos noches puntuaban identico. Y no son
    iguales -- la extincion entra exponenciada tres veces -- ni lo son en una
    sola direccion: unos objetos suben y otros bajan segun donde caiga su
    ventana. Que haya de los DOS signos es la prueba de que esto es informacion
    y no un sesgo global.
    """
    times, n = noche
    rampa = [0.05 + 0.30 * i / (n - 1) for i in range(n)]
    assert statistics.median(rampa) == pytest.approx(0.20)

    plano = _correr(times, serie=None)
    variable = _correr(times, serie=_serie(times, rampa))

    cambios = [(variable[k] - plano[k]) / plano[k]
               for k, q in plano.items() if q > 0.01]
    assert len(cambios) > 20, "el catalogo tiene que dar suficientes objetos"
    assert max(abs(c) for c in cambios) > 0.05, "el efecto no puede ser ruido"
    assert any(c > 0 for c in cambios) and any(c < 0 for c in cambios), (
        "unos objetos ganan y otros pierden: eso es lo que la mediana borraba")


def test_la_serie_llega_desde_el_producto_hasta_la_fisica(noche):
    """De punta a punta: ensuciar UNA hora mueve esa hora y solo esa.

    Sube el AOD en una sola hora del centro de la noche y comprueba que las
    trazas anteriores no se enteran. Es la comprobacion de que la serie se
    evalua DONDE se le pregunta y no se promedia por el camino.

    La hora se coge del CENTRO y no del final a proposito: la ultima muestra de
    la rejilla cae en el amanecer, con el cielo ya a 5 mag, donde todo rinde
    cero y ningun aerosol puede cambiar nada. Un test que ensuciase esa hora
    pasaria a verde el dia que la serie dejara de llegar.
    """
    times, n = noche
    medio = n // 2

    def correr(serie):
        return {r.target.name: [h.get("yield") for h in r.trace]
                for r in evaluate_night(
                    CATALOGUE, times, usable_probability=[0.8] * n,
                    turbulence=integrate(20.0), aod_total=0.05, aod_dust=0.02,
                    serie=serie)}

    serie = _serie(times, [0.05] * n)
    limpia = correr(serie)
    serie[times[medio]]["aod_total"] = 0.60
    sucia = correr(serie)

    movidos = [nombre for nombre in limpia if limpia[nombre] != sucia[nombre]]
    assert movidos, "ensuciar una hora de la noche tiene que mover algo"
    for nombre in movidos:
        assert limpia[nombre][:medio] == sucia[nombre][:medio], (
            "%s: las horas anteriores no han cambiado de aerosol" % nombre)
        assert limpia[nombre][medio] != sucia[nombre][medio], (
            "%s: la hora ensuciada tiene que ser la que se mueve" % nombre)
