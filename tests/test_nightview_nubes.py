"""La rejilla de nubes que NO llega tiene que notarse.

Esto no protege un formato, protege la regla de siempre del proyecto: la
ausencia de dato no se pinta como dato. La noche del 31-08-2026 se publico con
los 59 marcos de la cupula a `clouds: []` mientras la prevision horaria daba
100 % de cobertura al amanecer, y la web dibujo un cielo limpio toda la noche.
El fallo de red era transitorio; lo que lo hizo invisible fue un `except
Exception` que dejaba una lista vacia detras, indistinguible de "no hay nubes".
"""

import pytest

import astroweather.nightview as nightview


def _sin_rejilla(monkeypatch):
    def revienta(site):
        raise RuntimeError("Open-Meteo no responde")
    monkeypatch.setattr(nightview, "fetch_cloud_grid", revienta)


def test_la_ausencia_de_rejilla_viaja_en_el_json(monkeypatch):
    _sin_rejilla(monkeypatch)
    salida = nightview.build()
    assert salida["clouds_available"] is False
    assert salida["clouds_error"]                     # y con el motivo dentro


def test_sin_rejilla_el_techo_no_se_inventa_con_ceros(monkeypatch):
    """Lo peor del fallo antiguo no era la cupula, era ESTO.

    El calculo de apertura hacia `max(nubes, default=0.0)` por marco: sin
    rejilla contaba la noche como despejada y publicaba una probabilidad alta
    construida sobre ceros inventados. Desde el 18-09-2026 el techo ya no sale
    de la rejilla sino del veredicto; sin rejilla tiene que seguir diciendo lo
    que dice el veredicto, no un "despejado" por defecto.
    """
    _sin_rejilla(monkeypatch)
    _veredicto_falso(monkeypatch, 0.1)
    techo = nightview.build()["roof"]
    assert techo["verdict"] == "Cerrado"
    assert techo["probability"] == 0.1


def test_con_rejilla_la_bandera_dice_que_si(monkeypatch):
    """La guarda no puede quedarse encendida para siempre: con rejilla, pasa.

    Con rejilla FALSA y no con la de verdad: un test que pide la rejilla real
    para comprobar que se detectan los fallos de red se cae precisamente cuando
    hay un fallo de red, y entonces no distingues el bug de la meteorologia de
    Open-Meteo. La red se prueba a mano; esto prueba la bandera.
    """
    horas = ["2026-08-31T%02d:00" % h for h in range(24)]
    hourly = {"time": horas,
              "cloud_cover_low": [0] * 24, "cloud_cover_mid": [0] * 24,
              "cloud_cover_high": [0] * 24, "relative_humidity_2m": [50] * 24,
              "dew_point_2m": [5] * 24, "temperature_2m": [15] * 24,
              "wind_speed_10m": [2] * 24}
    monkeypatch.setattr(nightview, "fetch_cloud_grid",
                        lambda site: ([{"az": 0.0, "alt": 45.0}], [{"hourly": hourly}]))
    monkeypatch.setattr(nightview, "project_clouds", lambda p, pay, i: [])
    salida = nightview.build()
    assert salida["clouds_available"] is True
    assert salida["clouds_error"] is None


# --- el techo de la cupula es el del veredicto (18-09-2026) -----------------

import datetime as dt
from types import SimpleNamespace

import astroweather.tonight as tonight


_DIA = dt.datetime(2026, 9, 18, 18, 0, tzinfo=dt.timezone.utc)


def _veredicto_falso(monkeypatch, p_hora: float):
    """Un informe como el de `tonight.report` con la misma probabilidad cada
    hora: la ventana de dos horas y la suma salen de ahi, como en el real."""
    inicio = dt.datetime(2026, 9, 18, 20, 0, tzinfo=dt.timezone.utc)
    horas = [SimpleNamespace(t_utc=inicio + dt.timedelta(hours=i),
                             usable_probability=p_hora) for i in range(8)]
    informe = SimpleNamespace(
        hours=horas, opening_probability=p_hora, usable_hours=p_hora * 8,
        verdict_window=(horas[0].t_utc, horas[2].t_utc, p_hora))
    monkeypatch.setattr(tonight, "report", lambda day, site: informe)
    return informe


def test_la_cupula_dice_cerrado_cuando_el_veredicto_dice_cerrado(monkeypatch):
    """El desacuerdo que habia: la rejilla sin nubes y la curva del fotometro
    daban "muy probable" con el factor humano encima, mientras el veredicto
    publicado -- curva del techo -- decia Cerrado. Ahora la cupula no calcula:
    lee el veredicto, asi que una rejilla limpia no puede abrir el techo."""
    horas = ["2026-09-18T%02d:00" % h for h in range(24)]
    hourly = {"time": horas,
              "cloud_cover_low": [0] * 24, "cloud_cover_mid": [0] * 24,
              "cloud_cover_high": [0] * 24, "relative_humidity_2m": [50] * 24,
              "dew_point_2m": [5] * 24, "temperature_2m": [15] * 24,
              "wind_speed_10m": [2] * 24}
    monkeypatch.setattr(nightview, "fetch_cloud_grid",
                        lambda site: ([{"az": 0.0, "alt": 45.0}], [{"hourly": hourly}]))
    monkeypatch.setattr(nightview, "project_clouds", lambda p, pay, i: [])
    _veredicto_falso(monkeypatch, 0.2)
    techo = nightview.build()["roof"]
    assert techo["verdict"] == "Cerrado"
    assert techo["probability"] == 0.2
    assert techo["source"] == "veredicto"


def test_el_umbral_es_el_del_veredicto_publicado(monkeypatch):
    from astroweather.webexport import UMBRAL_ABRIR
    _veredicto_falso(monkeypatch, UMBRAL_ABRIR)
    assert nightview._techo_del_veredicto(_DIA, nightview.NERPIO)["verdict"] == "Abierto"
    _veredicto_falso(monkeypatch, UMBRAL_ABRIR - 0.01)
    assert nightview._techo_del_veredicto(_DIA, nightview.NERPIO)["verdict"] == "Cerrado"


def test_sin_climatologia_no_se_pintan_horas_tipicas_a_cero(monkeypatch):
    import astroweather.roof as roof
    _veredicto_falso(monkeypatch, 0.8)
    monkeypatch.setattr(roof, "load_climatology", lambda path=None: None)
    techo = nightview._techo_del_veredicto(_DIA, nightview.NERPIO)
    assert techo["typical_hours"] is None and techo["range_hours"] is None
    assert techo["expected_hours"] == pytest.approx(6.4)
