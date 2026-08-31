"""Seeing y transparencia POR HORA (SPEC v8 2.1/3.1).

Hasta el 31-08-2026 las dos salian por noche: un tercil y un AOD, la mediana de
la ventana entera. Y las dos se mueven dentro de la misma noche. Medido sobre el
archivo esa noche: el AOD va de 0,085 a las 18:00 a 0,192 al amanecer -- MAS DEL
DOBLE -- mientras la mediana publicada decia 0,19 y la pagina ponia "noche
transparente" para una noche que se enturbia segun avanza.
"""

import datetime as dt
import types

import astroweather.queries as queries
from astroweather.site import NERPIO
from astroweather.webexport import _hour


UTC = dt.timezone.utc


class _Cursor:
    def __init__(self, filas):
        self._filas = filas

    def fetchall(self):
        return self._filas


class _Conexion:
    """Devuelve una tanda por consulta, en el orden en que se piden."""

    def __init__(self, tandas):
        self._tandas = list(tandas)

    def execute(self, sql, params=None):
        return _Cursor(self._tandas.pop(0) if self._tandas else [])


def test_el_aerosol_y_el_jet_se_juntan_aunque_vengan_en_filas_distintas(monkeypatch):
    """El fallo que costo la primera version de esto.

    El aerosol y los niveles de presion NO viajan en la misma fila del archivo.
    Pedirlos con UNA consulta y quedarse con la ultima pasada por `valid_utc`
    devuelve la fila de uno con las columnas del otro en blanco, y la serie sale
    entera a None sin que nada falle.
    """
    hora = dt.datetime(2026, 8, 31, 22, 0, tzinfo=UTC)
    monkeypatch.setattr(queries, "connect",
                        lambda root: _Conexion([[(hora, 0.18, 0.01)],
                                                [(hora, 21.0)]]))
    serie = queries.night_series(hora, hora)
    assert serie[hora]["aod_total"] == 0.18
    assert serie[hora]["jet_speed"] == 21.0


def test_sin_archivo_la_serie_sale_vacia_y_no_revienta(monkeypatch):
    def revienta(root):
        raise RuntimeError("sin archivo")
    monkeypatch.setattr(queries, "connect", revienta)
    assert queries.night_series(dt.datetime.now(UTC), dt.datetime.now(UTC)) == {}


def _hora_falsa(cuando):
    return types.SimpleNamespace(
        t_utc=cuando, cloud_total=0.0, clear_probability=0.9,
        usable_probability=0.9, limiting="-",
        safety=types.SimpleNamespace(safe_to_open=True, summary="abrible"))


def test_sin_serie_la_hora_sale_como_salia():
    """Esto ANIADE resolucion, no la sustituye: sin serie, nada cambia."""
    salida = _hour(_hora_falsa(dt.datetime(2026, 8, 31, 22, 0, tzinfo=UTC)))
    assert "seeing_arcsec" not in salida and "aod" not in salida
    assert salida["p_utilizable"] == 0.9


def test_con_serie_la_hora_lleva_su_seeing_y_su_transparencia():
    cuando = dt.datetime(2026, 8, 31, 22, 0, tzinfo=UTC)
    serie = {cuando: {"aod_total": 0.18, "aod_dust": 0.01, "jet_speed": 21.0}}
    salida = _hour(_hora_falsa(cuando), serie, NERPIO)
    assert salida["seeing_arcsec"] > 0
    assert salida["seeing_tercil"] in ("favorable", "normal", "desfavorable")
    assert salida["aod"] > 0 and salida["k_total"] > 0


def test_la_hora_se_empareja_en_punto_aunque_la_marca_traiga_minutos():
    """La rejilla del recomendador no cae en punto; el proveedor si."""
    cuando = dt.datetime(2026, 8, 31, 22, 37, 41, tzinfo=UTC)
    serie = {cuando.replace(minute=0, second=0, microsecond=0):
             {"aod_total": 0.18, "aod_dust": 0.01, "jet_speed": 21.0}}
    assert "seeing_arcsec" in _hour(_hora_falsa(cuando), serie, NERPIO)
