# -*- coding: utf-8 -*-
"""El techo se mira deprisa cuando se decide, y se sigue mirando despues.

LA PREGUNTA DEL PRODUCTO es si AstroCamp abre, y se responde en una franja
estrecha alrededor del ocaso astronomico. Medido el 01-09-2026: el ocaso cayo a
las 20:11Z y el archivo tiene lecturas de techo a las 19:01, 19:31, 20:01 y
20:31. Media hora de resolucion para cubrir el unico momento que importa; si
hubiera abierto a las 20:05 no lo habriamos sabido hasta las 20:31.

EL FALLO QUE ESTE FICHERO EXISTE PARA EVITAR no es el de la cadencia, que vive
en el reloj de fuera, sino uno mas tonto y mas caro: `sensores` tenia periodo de
10 minutos para estar siempre vencido con un reloj de 15. Subir el reloj a 5 sin
tocar ese 10 lo convierte en TAPON -- la mitad de las pasadas encuentran el
trabajo no vencido -- y se pagaria el doble de runners para el mismo muestreo de
10 minutos. La cadencia del reloj y el periodo del trabajo tienen que bajar
juntos.
"""

import datetime as dt

import pytest

from astroweather import colector, scheduler

UTC = dt.timezone.utc
# Ocaso astronomico real de Nerpio esa noche: 20:11Z. La ventana va de 19:11 a
# 21:11 -- una hora antes y dos de largo.
OCASO = dt.datetime(2026, 9, 1, 20, 11, tzinfo=UTC)
DENTRO = dt.datetime(2026, 9, 1, 19, 30, tzinfo=UTC)
FUERA = dt.datetime(2026, 9, 1, 18, 0, tzinfo=UTC)


@pytest.fixture
def techo_cerrado(monkeypatch):
    monkeypatch.setattr(scheduler, "techo_abierto", lambda root=None: False)


@pytest.fixture
def techo_ya_abierto(monkeypatch):
    monkeypatch.setattr(scheduler, "techo_abierto", lambda root=None: True)


def test_en_la_ventana_y_cerrado_se_mira_deprisa(techo_cerrado):
    assert scheduler.ritmo_de_techos(DENTRO) == scheduler.RITMO_TECHOS_APERTURA


def test_fuera_de_la_ventana_se_vuelve_al_ritmo_de_vigilancia(techo_cerrado):
    assert scheduler.ritmo_de_techos(FUERA) == scheduler.JOBS_PERIODO_SENSORES


def test_en_cuanto_abre_se_deja_de_correr(techo_ya_abierto):
    """La ventana termina cuando abre, aunque queden noventa minutos de ella.

    Es lo que se pidio: "hasta que este en open o pasen 2 horas".
    """
    assert scheduler.ritmo_de_techos(DENTRO) == scheduler.JOBS_PERIODO_SENSORES


def test_si_vuelve_a_cerrarse_dentro_de_la_ventana_se_acelera_otra_vez(techo_cerrado):
    """Un techo que abre y cierra a los diez minutos vuelve a ser noticia.

    La condicion se evalua en CADA pasada contra el estado de ese momento, no
    una vez al principio de la noche. Un cierre por rachas es exactamente el
    tipo de cosa que la pagina existe para contar.
    """
    assert scheduler.ritmo_de_techos(DENTRO) == scheduler.RITMO_TECHOS_APERTURA


def test_sin_dato_de_techo_se_mira_deprisa_y_no_al_reves(monkeypatch):
    """None no es "cerrado" ni es "abierto": es el caso que MAS mirar necesita.

    Tratar la ausencia como "ya esta abierto" seria apagar el muestreo justo
    cuando no sabemos nada, que es el fallo de la casa una vez mas.
    """
    monkeypatch.setattr(scheduler, "techo_abierto", lambda root=None: None)
    assert scheduler.ritmo_de_techos(DENTRO) == scheduler.RITMO_TECHOS_APERTURA


def test_la_ventana_empieza_una_hora_antes_y_dura_dos(techo_cerrado):
    """Los bordes, que es donde se equivoca uno."""
    inicio = OCASO - scheduler.VENTANA_TECHOS_ANTES
    fin = inicio + scheduler.VENTANA_TECHOS_LARGO
    un_minuto = dt.timedelta(minutes=1)

    assert scheduler.ritmo_de_techos(inicio + un_minuto) == scheduler.RITMO_TECHOS_APERTURA
    assert scheduler.ritmo_de_techos(fin - un_minuto) == scheduler.RITMO_TECHOS_APERTURA
    assert scheduler.ritmo_de_techos(inicio - un_minuto) == scheduler.JOBS_PERIODO_SENSORES
    assert scheduler.ritmo_de_techos(fin + un_minuto) == scheduler.JOBS_PERIODO_SENSORES


def test_la_vigilancia_no_se_apaga_al_acabar_la_noche(techo_ya_abierto):
    """Un techo abierto se puede cerrar, y enterarse tarde es el mismo fallo.

    A las 02:00 el trabajo sigue teniendo un periodo corto -- mas corto que
    cualquier reloj que vayamos a poner -- para que ninguna pasada de madrugada
    se quede sin raspar.
    """
    madrugada = dt.datetime(2026, 9, 2, 2, tzinfo=UTC)
    assert scheduler.ritmo_de_techos(madrugada) == scheduler.JOBS_PERIODO_SENSORES
    assert scheduler.JOBS_PERIODO_SENSORES < dt.timedelta(minutes=15)


# --------------------------------------------------------------------------
# El punto de integracion: que `vencidos` USE el ritmo y no el periodo fijo
# --------------------------------------------------------------------------

def test_con_reloj_de_cinco_minutos_el_trabajo_esta_vencido(techo_cerrado):
    """EL TEST QUE JUSTIFICA TODO ESTO.

    Cuatro minutos despues de la ultima pasada, dentro de la ventana. Con el
    periodo fijo de 10 el trabajo NO estaria vencido y el runner arrancaria para
    no raspar: cadencia real de 10 minutos pagando el doble.
    """
    estado = {"sensores": DENTRO - dt.timedelta(minutes=4)}
    assert "sensores" in colector.vencidos(DENTRO, estado, only={"sensores"})


def test_fuera_de_la_ventana_cuatro_minutos_no_bastan(techo_cerrado):
    """Y fuera de ella no se rasca de mas: la fuente no cambia tan deprisa."""
    estado = {"sensores": FUERA - dt.timedelta(minutes=4)}
    assert colector.vencidos(FUERA, estado, only={"sensores"}) == []


def test_los_demas_trabajos_no_se_enteran(techo_cerrado):
    """El ritmo dinamico es de `sensores`; nadie mas cambia de comportamiento."""
    for nombre, trabajo in scheduler.JOBS.items():
        if nombre != "sensores":
            assert trabajo.ritmo is None, nombre
