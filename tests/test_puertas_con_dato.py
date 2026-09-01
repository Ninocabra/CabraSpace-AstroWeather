# -*- coding: utf-8 -*-
"""Las puertas de seguridad tienen que RECIBIR dato, no solo existir.

El 28-08-2026 la consulta de la noche se filtró al modelo con el que se entrenó
el calibrador — correcto para la nube, porque la curva es de esa malla — pero
ese modelo no publica racha, ni CAPE, ni probabilidad de precipitación: las
publica otro. Desde ese día `gates.evaluate` recibía None en las tres y las tres
devolvían "ok" sin mirar nada. Cuatro días publicando "abrible" sin haber
comprobado nada, y con 948 tests en verde: ninguno miraba si el dato LLEGA.

El segundo camino por el que se perdía: al mismo `valid_utc` escriben la pasada
de superficie completa (una vez al día) y una de solo nubes (en cada recogida).
Coger "la fila más reciente" se quedaba con la de solo nubes.
"""

import datetime as dt

import pytest

from astroweather import tonight
from astroweather.site import NERPIO


UTC = dt.timezone.utc
INICIO = dt.datetime(2026, 9, 1, 20, tzinfo=UTC)
FIN = dt.datetime(2026, 9, 2, 4, tzinfo=UTC)
HORA = dt.datetime(2026, 9, 1, 22, tzinfo=UTC)


class _Cursor:
    def __init__(self, filas):
        self._filas = filas

    def fetchall(self):
        return self._filas


class _Conexion:
    """Responde por el contenido de la consulta, no por el orden.

    Así el test no se rompe si mañana se reordenan las consultas dentro de la
    función, que es una refactorización legítima y no un fallo.
    """

    def __init__(self, nubes, seguridad):
        self.nubes, self.seguridad = nubes, seguridad

    def execute(self, sql, params=None):
        return _Cursor(self.seguridad if "gust10m" in sql else self.nubes)


@pytest.fixture
def archivo(monkeypatch):
    """Nube del modelo calibrado; racha y CAPE de OTRO modelo, como en la vida."""
    nubes = [(HORA, 5.0, 12.0)]                       # valid, cloud_total, lead_h
    seguridad = [(HORA, 1.2, 9.0, 0.0, 5.0, 2500.0, "ecmwf_ifs")]
    monkeypatch.setattr(tonight, "connect",
                        lambda root=None: _Conexion(nubes, seguridad))


def test_la_racha_llega_aunque_venga_de_otro_modelo(archivo):
    filas = tonight._forecast_rows(NERPIO, INICIO, FIN, None, "ecmwf_ifs025")
    assert len(filas) == 1
    valid, nube, viento, racha, precip, pprec, cape, lead = filas[0]
    assert nube == 5.0, "la nube sigue saliendo del modelo calibrado"
    assert racha == 9.0, "la racha tiene que llegar aunque la publique otro modelo"
    assert cape == 2500.0
    assert pprec == 5.0


def test_la_puerta_de_viento_deja_de_estar_ciega(archivo):
    """Con 9,0 m/s la puerta tiene que MIRAR, no encogerse de hombros."""
    from astroweather.gates import GUST_DEGRADED, evaluate
    filas = tonight._forecast_rows(NERPIO, INICIO, FIN, None, "ecmwf_ifs025")
    racha = filas[0][3]
    assert racha >= GUST_DEGRADED
    veredicto = evaluate(t_utc=HORA, hours_ahead=5.0, gust=racha)
    viento = [r for r in veredicto.risks if r.name == "viento"][0]
    assert viento.level != "ok", "una racha por encima del umbral no puede ser ok"
    assert "sin dato" not in viento.reason


def test_sin_ninguna_fila_de_seguridad_la_hora_sigue_saliendo(monkeypatch):
    """Degradar, no reventar: si no hay dato de seguridad, la noche se publica.

    Lo que NO puede pasar es que la falta de racha tumbe el informe entero: la
    nube sigue siendo útil y la ausencia se trata en las puertas.
    """
    nubes = [(HORA, 5.0, 12.0)]
    monkeypatch.setattr(tonight, "connect", lambda root=None: _Conexion(nubes, []))
    filas = tonight._forecast_rows(NERPIO, INICIO, FIN, None, "ecmwf_ifs025")
    assert len(filas) == 1
    assert filas[0][1] == 5.0
    assert filas[0][3] is None


def test_se_puede_declarar_de_donde_sale_la_racha(archivo):
    """Si la racha viene de otra malla, hay que poder DECIRLO."""
    assert tonight.safety_source(NERPIO, INICIO, FIN) == ["ecmwf_ifs"]
