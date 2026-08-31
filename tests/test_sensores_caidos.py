# -*- coding: utf-8 -*-
"""Lo que se publica cuando la fuente del sitio NO responde.

AstroCamp se cayo el 31-08-2026 a las 10:22 UTC y no volvio en toda la tarde.
Lo que el panel haga en ese caso no es un detalle de presentacion: es la
diferencia entre "no lo se" y un numero inventado, y este proyecto ya se ha
tropezado cuatro veces con esa frontera en un solo dia.

La regla: se conserva la ultima lectura, viaja CUANDO se midio, y lo que no se
pueda fechar con honestidad no se publica.
"""

import datetime as dt

import pytest

from astroweather import webexport
from astroweather.site import NERPIO


UTC = dt.timezone.utc


@pytest.fixture
def sin_sitio(monkeypatch):
    """AstroCamp no responde: `site_now.fetch` revienta."""
    def revienta(site=None):
        raise RuntimeError("ConnectTimeout")
    monkeypatch.setattr(webexport.site_now, "fetch", revienta)


def _archivo(monkeypatch, valores):
    """`valores` es {columna: (valor, hora)}."""
    monkeypatch.setattr(webexport, "_ultimo_del_archivo",
                        lambda columnas, root=None: {c: v for c, v in valores.items()
                                                     if c in columnas})


def test_sin_respuesta_los_techos_conservan_su_ultimo_estado(sin_sitio, monkeypatch):
    cuando = dt.datetime(2026, 8, 31, 10, 22, 36, tzinfo=UTC)
    _archivo(monkeypatch, {"roof_open": (False, cuando)})
    panel = webexport._sensores(NERPIO)
    assert panel["incompleto"] == "sin respuesta"
    # La CUENTA no se rescata: el archivo guarda un booleano y "0/18" de hace
    # ocho horas no se puede reconstruir sin inventarselo.
    assert panel["techos_total"] is None
    assert panel["techos_estado_archivo"] is False
    assert panel["del_archivo"]["techos"] == cuando.isoformat(timespec="seconds")


def test_si_el_sitio_responde_no_se_rescatan_los_techos(monkeypatch):
    """La guarda no puede quedarse encendida: con respuesta, manda la respuesta."""
    class Ahora:
        retrieved_at = dt.datetime(2026, 8, 31, 21, 0, tzinfo=UTC)
        t_utc = dt.datetime(2026, 8, 31, 21, 0, tzinfo=UTC)
        age_minutes = 1.0
        meteo_fresh = True
        sky_fresh = True
        roofs_open, roofs_total = 18, 18
        missing_note = None
        t_out = rh_out = dew_point = dew_margin = None
        dew_risk = "desconocido"
        wind_ms = wind_dir = pressure = sky_temp = sky_index = None
        sky_clear = safety_status = None

    monkeypatch.setattr(webexport.site_now, "fetch", lambda site=None: Ahora())
    _archivo(monkeypatch, {"roof_open": (True, dt.datetime(2026, 8, 30, tzinfo=UTC))})
    panel = webexport._sensores(NERPIO)
    assert panel["techos_total"] == 18
    assert "techos_estado_archivo" not in panel
    assert "techos" not in panel["del_archivo"]


def test_el_margen_de_rocio_no_resta_dos_horas_distintas(sin_sitio, monkeypatch):
    """El fallo real del 31-08, con sus numeros.

    La temperatura se rescataba de AEMET -- viva, 16:00 UTC -- y el punto de
    rocio de AstroCamp -- muerta desde las 10:21 --, y la resta daba 25,8 grados
    de margen mezclando dos medidas separadas casi seis horas: un numero
    plausible y sin significado, en el campo que avisa de condensacion.
    """
    _archivo(monkeypatch, {
        "t2m": (34.6, dt.datetime(2026, 8, 31, 16, 0, tzinfo=UTC)),
        "dew_point": (8.8, dt.datetime(2026, 8, 31, 10, 21, 8, tzinfo=UTC)),
    })
    panel = webexport._sensores(NERPIO)
    assert panel["temperatura"] == 34.6 and panel["punto_rocio"] == 8.8
    assert panel["margen_rocio"] is None, "no se puede restar entre dos instantes"


def test_con_las_dos_mitades_a_la_misma_hora_si_se_deriva(sin_sitio, monkeypatch):
    """Y al reves: si son del mismo momento, la resta es legitima."""
    cuando = dt.datetime(2026, 8, 31, 10, 21, 8, tzinfo=UTC)
    _archivo(monkeypatch, {"t2m": (20.0, cuando), "dew_point": (8.0, cuando)})
    panel = webexport._sensores(NERPIO)
    assert panel["margen_rocio"] == 12.0
