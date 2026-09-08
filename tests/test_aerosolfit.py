# -*- coding: utf-8 -*-
"""Tests del ajuste de `AEROSOL_SCALE_HEIGHT_M` (08-09-2026).

Lo que se prueba aqui es un ESTIMADOR, y un estimador roto no da error: da un
numero con toda la cara de haber medido algo. Asi que hay dos mitades:

* **Control positivo**: con datos sinteticos de altura CONOCIDA tiene que
  recuperarla -- y varias distintas, porque uno que siempre devuelva 1700
  pasaria un solo caso.
* **Las negativas, vistas en rojo**: sin especies, con colinealidad alta o con
  pocas horas tiene que NEGARSE. Son las tres situaciones reales del archivo
  hoy, y la unica proteccion contra publicar una constante inventada como si
  fuera medida.

El test de la degeneracion no prueba codigo nuestro: prueba el ALGEBRA sobre la
que descansa todo el modulo, para que quede escrita y ejecutable.
"""

import numpy as np
import pytest

from astroweather import aerosolfit
from astroweather.site import NERPIO

DH = NERPIO.delta_h          # 583 m
B_FISICA = 1.086             # mag por unidad de profundidad optica


def _sintetico(altura_verdadera, n=800, ruido=0.01, colinealidad=0.3, semilla=1):
    """Horas con una altura de escala CONOCIDA.

    `colinealidad` controla cuanto se parecen `aod_dust` y `aod_total`, que es
    lo que decide si el problema tiene solucion: es el parametro que en el
    archivo real vale 0,957 y lo hace irresoluble.
    """
    rng = np.random.default_rng(semilla)
    total = rng.uniform(0.03, 0.6, n)
    fraccion = np.clip(colinealidad + rng.uniform(-0.3, 0.3, n), 0.0, 1.0)
    polvo = total * fraccion
    f = np.exp(-DH / altura_verdadera)
    y = 21.5 + B_FISICA * (polvo + f * (total - polvo)) + rng.normal(0, ruido, n)
    return y, total, polvo


# --- control positivo -------------------------------------------------------

@pytest.mark.parametrize("verdadera", [900.0, 1700.0, 4000.0])
def test_recupera_una_altura_conocida(verdadera):
    """Tres alturas distintas: una constante devuelta a ciegas fallaria dos."""
    ajuste = aerosolfit.fit_scale_height(*_sintetico(verdadera), DH)

    assert ajuste.medido, ajuste.porque
    assert ajuste.scale_height_m == pytest.approx(verdadera, rel=0.10)


def test_el_intervalo_del_68_cubre_el_68_por_ciento():
    """La forma correcta de probar un intervalo, y la incorrecta.

    La primera version de este test exigia que UN intervalo del 68 % contuviera
    la verdad. Fallo, y con razon: un intervalo del 68 % deja fuera la verdad
    una de cada tres veces POR DEFINICION. Lo que se puede exigir es la
    cobertura sobre muchas realizaciones -- si sale del 95 %, el intervalo esta
    inflado y no sirve para decidir nada; si sale del 30 %, miente.
    """
    dentro = 0
    intentos = 60
    for semilla in range(intentos):
        ajuste = aerosolfit.fit_scale_height(
            *_sintetico(1700.0, ruido=0.05, semilla=semilla), DH, remuestreos=120)
        assert ajuste.medido, ajuste.porque
        bajo, alto = ajuste.intervalo_68
        dentro += bajo <= 1700.0 <= alto

    cobertura = dentro / intentos
    assert 0.50 <= cobertura <= 0.85, (
        "cobertura %.2f: un intervalo del 68%% que cubre eso no es del 68%%"
        % cobertura)


def test_el_intervalo_se_ensancha_con_el_ruido():
    """Un intervalo que no reacciona al ruido no es un intervalo, es adorno."""
    limpio = aerosolfit.fit_scale_height(*_sintetico(1700.0, ruido=0.005), DH)
    sucio = aerosolfit.fit_scale_height(*_sintetico(1700.0, ruido=0.05), DH)

    assert limpio.medido and sucio.medido
    ancho = lambda a: a.intervalo_68[1] - a.intervalo_68[0]
    assert ancho(sucio) > 3 * ancho(limpio)


# --- las negativas, que son el caso real ------------------------------------

def test_sin_especies_se_niega_por_construccion():
    """EL caso. Es la rama que corre hoy en `extinction.evaluate`.

    No es que salga impreciso: es que no hay respuesta. Un ajuste que devolviera
    aqui un numero estaria inventandoselo entero.
    """
    y, total, _ = _sintetico(1700.0)
    ajuste = aerosolfit.fit_scale_height(y, total, [None] * len(total), DH)

    assert not ajuste.medido
    assert "indeterminable por construccion" in ajuste.porque
    assert ajuste.scale_height_m is None


def test_la_degeneracion_es_algebraica_no_estadistica():
    """La prueba, ejecutable, de por que el test de arriba tiene que existir.

    Con solo `aod_total`, `site_aod = aod_total * f` y la pendiente absorbe f
    exactamente: el ajuste sale IDENTICO para cualquier altura de escala. Sobre
    las 3.200 horas limpias del archivo real el R2 coincide a doce decimales.
    """
    y, total, _ = _sintetico(1700.0, ruido=0.05)
    erres = []
    for altura in (500, 1000, 1700, 3000, 6000, 20000):
        x = np.exp(-DH / altura) * total
        erres.append(np.corrcoef(x, y)[0, 1] ** 2)

    assert max(erres) - min(erres) < 1e-12, (
        "si el R2 cambiara con la altura, la degeneracion no seria total y el "
        "modulo entero sobraria")


def test_colinealidad_alta_se_niega():
    """El archivo real va a r = 0,957 entre aod_dust y aod_total."""
    y, total, polvo = _sintetico(1700.0, colinealidad=0.5)
    casi_igual = total * 0.6              # r = 1 exacto
    ajuste = aerosolfit.fit_scale_height(y, total, casi_igual, DH)

    assert not ajuste.medido
    assert "colinealidad" in ajuste.porque
    assert ajuste.diagnostico["colinealidad"] > 0.9


def test_pocas_horas_se_niega_y_dice_cuantas_faltan():
    y, total, polvo = _sintetico(1700.0, n=7)
    ajuste = aerosolfit.fit_scale_height(y, total, polvo, DH)

    assert not ajuste.medido and ajuste.n == 7
    assert "7" in ajuste.porque and str(aerosolfit.N_MINIMO) in ajuste.porque


def test_un_perfil_que_crece_con_la_altura_se_niega():
    """Si el ajuste pide f > 1, el aerosol aumentaria con la altura.

    No es un valor 'raro': es imposible, y publicarlo como altura de escala
    seria darle nombre fisico a un artefacto de la regresion.
    """
    rng = np.random.default_rng(3)
    n = 400
    total = rng.uniform(0.05, 0.5, n)
    polvo = total * rng.uniform(0.1, 0.6, n)
    # El observable responde SOLO al polvo: la parte no-polvo no pinta nada,
    # asi que la regresion tira c1 hacia cero y f se va fuera de (0, 1).
    y = 21.5 - 2.0 * polvo + rng.normal(0, 0.01, n)
    ajuste = aerosolfit.fit_scale_height(y, total, polvo, DH)

    assert not ajuste.medido
    assert ajuste.scale_height_m is None


def test_el_diagnostico_trae_los_numeros_para_juzgarlo():
    """Una negativa sin numeros no se puede rebatir ni seguir."""
    y, total, polvo = _sintetico(1700.0)
    ajuste = aerosolfit.fit_scale_height(y, total, polvo, DH)

    for clave in ("colinealidad", "ruido", "senal", "senal_ruido", "n_con_especies"):
        assert clave in ajuste.diagnostico, clave
    assert ajuste.diagnostico["senal_ruido"] > 1.0, "el sintetico es facil a proposito"


def test_la_señal_ruido_del_fotometro_real_es_la_medida():
    """Con la S/N que de verdad tiene el fotometro (0,138), no sale.

    Este es el numero que convierte 'todavia no' en 'asi no': el cielo se mueve
    0,033 mag con el AOD tipico y su dispersion propia es 0,241.
    """
    y, total, polvo = _sintetico(1700.0, n=800, ruido=0.241)
    ajuste = aerosolfit.fit_scale_height(y, total, polvo, DH)

    if ajuste.medido:
        bajo, alto = ajuste.intervalo_68
        assert alto / bajo > 2.0, (
            "con el ruido real el intervalo no puede salir estrecho; si sale, "
            "el remuestreo esta subestimando la incertidumbre")
    else:
        assert ajuste.porque
