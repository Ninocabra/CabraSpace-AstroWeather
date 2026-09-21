"""Tests de la allsky de AstroCamp (29-08-2026).

Lo que hay que proteger aqui no es el parseo, es lo que el proveedor NO manda:
las series vienen etiquetadas `"HH:MM:SS"`, sin fecha y sin zona. Si la
reconstruccion se equivoca al cruzar la medianoche, la serie queda desplazada un
dia entero y nadie se entera hasta que alguien intente cruzarla con el TESS.
"""

import datetime as dt
import math

import pytest

from astroweather.workers import fetch_allsky


def _payload(sqm, stars=None):
    datos = {"sqm": [{"x": x, "y": y} for x, y in sqm]}
    if stars is not None:
        datos["stars"] = [{"x": x, "y": y} for x, y in stars]
    return {"chart_data": datos, "message": ""}


def test_midnight_does_not_move_the_series_a_day():
    """El unico sitio donde la reconstruccion se puede romper.

    Los puntos llegan de mas viejo a mas nuevo; anclando en la descarga y yendo
    hacia atras, que la hora SUBA significa que se ha cruzado la medianoche.
    """
    descarga = dt.datetime(2026, 8, 29, 0, 30, tzinfo=dt.timezone.utc)  # 02:30 local
    momentos = fetch_allsky._timestamps(
        ["23:58:00", "23:59:00", "00:00:00", "01:00:00", "02:29:00"], descarga)

    assert [m.isoformat() for m in momentos] == [
        "2026-08-28T21:58:00+00:00",
        "2026-08-28T21:59:00+00:00",
        "2026-08-28T22:00:00+00:00",
        "2026-08-28T23:00:00+00:00",
        "2026-08-29T00:29:00+00:00",
    ]
    assert momentos == sorted(momentos), "la serie tiene que quedar creciente"


def test_a_window_inside_one_day_stays_in_that_day():
    descarga = dt.datetime(2026, 8, 29, 10, 4, tzinfo=dt.timezone.utc)   # 12:04 local
    momentos = fetch_allsky._timestamps(["11:00:00", "11:30:00", "12:00:00"], descarga)

    assert all(m.date() == dt.date(2026, 8, 29) for m in momentos)
    assert momentos[0].hour == 9                       # 11:00 local = 09:00 UTC


def test_the_adu_becomes_an_instrumental_magnitude_and_says_so():
    """El `sqm` del panel son cuentas, no mag/arcsec2. Publicarlo como si lo
    fuera seria inventarse el punto cero, que es justo lo que se investiga."""
    descarga = dt.datetime(2026, 8, 29, 10, 0, tzinfo=dt.timezone.utc)
    filas = fetch_allsky.to_rows(_payload([("11:59:45", 3230223.884)]), descarga)

    assert len(filas) == 1
    fila = filas[0]
    assert fila["sky_mag_raw"] == pytest.approx(-2.5 * math.log10(3230223.884))
    assert "zero_point_arbitrario" in fila["quality_flags"]
    assert fila["instrument_type"] == "ALLSKY"
    assert fila["instrument_id"] != "stars289", "no confundirla con el fotometro"


def test_a_dead_reading_is_dropped_not_stored_as_zero():
    """Un cero en el ADU no es un cielo infinitamente oscuro, es un hueco."""
    descarga = dt.datetime(2026, 8, 29, 10, 0, tzinfo=dt.timezone.utc)
    filas = fetch_allsky.to_rows(
        _payload([("11:58:00", 0.0), ("11:59:00", None), ("11:59:30", 2500000.0)]),
        descarga)

    assert len(filas) == 1


def test_the_star_count_travels_with_its_reading():
    """Es verdad de nubes independiente del modelo y del fotometro."""
    descarga = dt.datetime(2026, 8, 29, 22, 0, tzinfo=dt.timezone.utc)
    filas = fetch_allsky.to_rows(
        _payload([("23:59:00", 120000.0), ("23:59:30", 118000.0)],
                 stars=[("23:59:00", 412), ("23:59:30", 0)]),
        descarga)

    assert [f["star_count"] for f in filas] == [412, 0]


def test_the_measured_ceiling_is_written_down_not_guessed():
    """4.612 puntos y 19,2 h: medido pidiendo 6 h, 24 h, 72 h y 720 h. La
    ventana por defecto tiene que caber con solape dentro de ese tope."""
    assert fetch_allsky.MEASURED_CAP_POINTS == 4612
    assert (fetch_allsky.DEFAULT_WINDOW_S / fetch_allsky.CADENCE_S
            < fetch_allsky.MEASURED_CAP_POINTS)

# ------------------------------------------- la hora del fotograma (21-09-2026)

class _Respuesta:
    def __init__(self, cabecera):
        self.headers = {"Last-Modified": cabecera} if cabecera else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _con_cabecera(monkeypatch, cabecera):
    monkeypatch.setattr(fetch_allsky.urllib.request, "urlopen",
                        lambda *a, **k: _Respuesta(cabecera))


def test_la_hora_del_fotograma_sale_de_la_camara_y_en_utc(monkeypatch):
    """La unica razon de que esto exista: el proxy por el que la web ve la
    imagen sella con la hora en que la BAJO el, no con la de la exposicion."""
    _con_cabecera(monkeypatch, "Mon, 21 Sep 2026 16:46:57 GMT")

    cuando = fetch_allsky.frame_utc()

    assert cuando.isoformat() == "2026-09-21T16:46:57+00:00"
    assert cuando.tzinfo is not None, "sin zona no se puede comparar con nada"


def test_una_camara_que_no_contesta_no_inventa_una_hora(monkeypatch):
    """Callar es correcto; una hora inventada convierte una camara congelada en
    una camara que parece al dia."""
    def _revienta(*a, **k):
        raise OSError("sin ruta al host")
    monkeypatch.setattr(fetch_allsky.urllib.request, "urlopen", _revienta)

    assert fetch_allsky.frame_utc() is None


def test_sin_cabecera_tampoco(monkeypatch):
    _con_cabecera(monkeypatch, None)
    assert fetch_allsky.frame_utc() is None


def test_una_cabecera_ilegible_no_tumba_la_recogida(monkeypatch):
    _con_cabecera(monkeypatch, "ayer por la tarde")
    assert fetch_allsky.frame_utc() is None
