# -*- coding: utf-8 -*-
"""El jet de CADA HORA llega al modelo, y no la mediana de la noche.

Mismo patron que la k del 01-09-2026 y misma frontera: el dato ya viajaba a la
web hora a hora -- `webexport._hour` publica `seeing_arcsec` y `seeing_tercil`
desde el 31-08 -- y lo que seguia comiendose la mediana era el MODELO.

QUE SE MUEVE, MEDIDO. El jet cambia dentro de la noche mas que el aerosol: en
las ocho ultimas noches de Nerpio (27-08 a 03-09-2026) va de su minimo a su
maximo por un factor de 1,12 a 2,29, y cuatro de las ocho pasan de 1,5. Sobre
la noche real del 01-09 (9,9 -> 21,2 m/s) el cambio mueve 14 de los 44 objetos
una mediana del -2,9 % y hasta un 5,7 %, en las dos direcciones. Con una rampa
5 -> 40 m/s de la MISMA mediana que un plano de 22,5 -- indistinguibles para el
codigo viejo -- se mueven 20 de los 35 puntuables, de -12,6 % (M78) a +9,3 %
(M22).

Y LO QUE NO SE MUEVE, QUE IMPORTA IGUAL. Doblar el jet no dobla el seeing: en
el perfil Hufnagel-Valley el termino del chorro es cuadratico en la velocidad
pero vive a 10 km, y aqui manda la capa limite. Un factor 2,2 en el jet son un
11 % en el seeing. Y solo lo notan los objetos COMPACTOS, porque la nitidez
entra como `(FWHM_ref/FWHM)^(2 alpha)` y con alpha = 0 vale 1 pase lo que pase.
"""

import datetime as dt

import pytest

from astroweather.ephem import night_for
from astroweather.seeing import integrate
from astroweather.targets import CATALOGUE, evaluate_night, _turbulencia_por_hora

UTC = dt.timezone.utc
NEW_MOON = dt.datetime(2026, 9, 11, tzinfo=UTC)

JET_PLANO = 22.5           # la mediana exacta de la rampa de abajo
JET_RAMPA = (5.0, 40.0)


@pytest.fixture(scope="module")
def noche():
    return night_for(NEW_MOON).hours(60)


def _serie(times, jets):
    return {t: {"aod_total": 0.20, "aod_dust": 0.02, "jet_speed": j}
            for t, j in zip(times, jets)}


def _rampa(times):
    bajo, alto = JET_RAMPA
    n = len(times) - 1
    return [bajo + (alto - bajo) * i / n for i in range(len(times))]


def _correr(times, serie):
    return {r.target.name: r for r in evaluate_night(
        CATALOGUE, times, usable_probability=[0.8] * len(times),
        turbulence=integrate(JET_PLANO), aod_total=0.20, aod_dust=0.02,
        serie=serie)}


# --------------------------------------------------------------------------
# El ayudante: sin serie, IDENTICO; con serie, una turbulencia por hora
# --------------------------------------------------------------------------

def test_sin_serie_devuelve_el_mismo_objeto_y_no_uno_recalculado(noche):
    """No "parecido": el MISMO objeto, para que el camino viejo no cambie.

    Recalcular `integrate(turbulence.jet_speed)` daria un valor igual salvo
    ruido de coma flotante, y ese ruido es justo lo que convierte un cambio
    inerte en un diff imposible de leer cuando falta el archivo.
    """
    turbulence = integrate(JET_PLANO)
    salida = _turbulencia_por_hora(None, noche, turbulence)
    assert len(salida) == len(noche)
    assert all(t is turbulence for t in salida)


def test_serie_sin_muestras_de_jet_tambien_devuelve_la_de_la_noche(noche):
    """Hay serie de aerosol pero no de jet: eso NO es un cero, es "no se"."""
    turbulence = integrate(JET_PLANO)
    serie = _serie(noche, [None] * len(noche))
    assert all(t is turbulence
               for t in _turbulencia_por_hora(serie, noche, turbulence))


def test_sin_turbulencia_de_noche_no_se_inventa_ninguna(noche):
    """`turbulence=None` es un caso vivo: `evaluate_night` lo admite."""
    assert _turbulencia_por_hora(_serie(noche, _rampa(noche)), noche, None) \
        == [None] * len(noche)


def test_cada_hora_recibe_el_jet_de_su_hora(noche):
    """La rampa entra: la primera hora sopla flojo y la ultima fuerte."""
    salida = _turbulencia_por_hora(_serie(noche, _rampa(noche)), noche,
                                   integrate(JET_PLANO))
    assert salida[0].jet_speed == pytest.approx(JET_RAMPA[0], abs=0.05)
    assert salida[-1].jet_speed == pytest.approx(JET_RAMPA[1], abs=0.05)
    assert salida[0].seeing_arcsec < salida[-1].seeing_arcsec


def test_hereda_la_amplitud_de_capa_limite_y_no_vuelve_al_defecto(noche):
    """Una calibracion local del sitio no se pierde por pasar a horario.

    `boundary_amplitude` es el otro parametro del perfil. Si el dia que se
    calibre contra el FWHM medido en Nerpio esta funcion volviera al valor por
    defecto, el seeing horario saldria de OTRA fisica que el de la noche y
    nadie lo veria: los dos numeros siguen siendo plausibles.
    """
    ajustada = integrate(JET_PLANO, boundary_amplitude=7e-14)
    salida = _turbulencia_por_hora(_serie(noche, _rampa(noche)), noche, ajustada)
    assert {t.boundary_amplitude for t in salida} == {7e-14}


# --------------------------------------------------------------------------
# El efecto en el producto: la mediana borraba QUE HORAS son las buenas
# --------------------------------------------------------------------------

def test_una_rampa_de_la_misma_mediana_mueve_el_catalogo(noche):
    """CONTROL POSITIVO: sin el cambio esto sale a cero y el test cae.

    Una rampa 5 -> 40 m/s tiene la MISMA mediana que un plano de 22,5, asi que
    el codigo que colapsaba a la mediana no podia distinguirlas. Si este test
    pasa con todo a cero es que el jet horario no esta llegando.
    """
    plano = _correr(noche, _serie(noche, [None] * len(noche)))
    rampa = _correr(noche, _serie(noche, _rampa(noche)))
    difs = [100 * (rampa[n].q_conditional - plano[n].q_conditional)
            / plano[n].q_conditional
            for n in plano if plano[n].q_conditional > 0.01]
    movidos = [d for d in difs if abs(d) > 1e-9]
    assert len(movidos) >= 15, "la rampa no llega al modelo"
    assert max(abs(d) for d in movidos) > 5.0


def test_se_mueve_en_LAS_DOS_direcciones(noche):
    """Ganan los que apuntan cuando el chorro afloja y pierden los otros.

    Es la comprobacion que separa "llevar mas informacion" de "meter un
    sesgo": un cambio que solo empujara hacia arriba seria un factor
    disfrazado.
    """
    plano = _correr(noche, _serie(noche, [None] * len(noche)))
    rampa = _correr(noche, _serie(noche, _rampa(noche)))
    difs = [rampa[n].q_conditional - plano[n].q_conditional
            for n in plano if plano[n].q_conditional > 0.01]
    assert max(difs) > 0 and min(difs) < 0


def test_los_objetos_EXTENSOS_no_se_enteran(noche):
    """alpha = 0 y la nitidez vale 1: el seeing no les entra por ningun sitio.

    Si un dia esto empieza a fallar, no es este test: es que alguien ha metido
    el seeing en un camino que no pasa por `alpha`.
    """
    plano = _correr(noche, _serie(noche, [None] * len(noche)))
    rampa = _correr(noche, _serie(noche, _rampa(noche)))
    extensos = [n for n in plano if plano[n].target.alpha == 0
                and plano[n].q_conditional > 0.01]
    assert extensos, "el catalogo ya no tiene objetos extensos que puntuen"
    for nombre in extensos:
        assert rampa[nombre].q_conditional == pytest.approx(
            plano[nombre].q_conditional, rel=1e-12)


def test_doblar_el_jet_NO_dobla_el_seeing():
    """La escala del efecto, escrita para que nadie la exagere.

    Con la capa limite mandando en el r0 del sitio, un factor 2,2 en el jet es
    un 11 % en el seeing. Este test existe para que el dia que alguien lea
    "ahora el jet es horario" no espere ver la noche partida en dos.
    """
    flojo, fuerte = integrate(9.9), integrate(22.1)
    razon = fuerte.seeing_arcsec / flojo.seeing_arcsec
    assert 1.05 < razon < 1.20, f"el seeing se movio x{razon:.2f}, revisa el perfil"


def test_el_veredicto_de_la_noche_sigue_siendo_la_mediana(noche):
    """"Seeing normal esta noche" habla de la NOCHE, no de las 03:40.

    Igual que la calima: la fisica se come la serie, el titular se queda con el
    resumen. Si el veredicto empezara a seguir la hora, cambiaria de palabra a
    lo largo de la noche sin que nadie lo hubiera decidido.
    """
    rampa = _correr(noche, _serie(noche, _rampa(noche)))
    razones = {r.reason for r in rampa.values() if "seeing" in r.reason}
    assert len(razones) <= 1
