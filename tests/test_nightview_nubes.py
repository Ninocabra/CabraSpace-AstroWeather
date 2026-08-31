"""La rejilla de nubes que NO llega tiene que notarse.

Esto no protege un formato, protege la regla de siempre del proyecto: la
ausencia de dato no se pinta como dato. La noche del 31-08-2026 se publico con
los 59 marcos de la cupula a `clouds: []` mientras la prevision horaria daba
100 % de cobertura al amanecer, y la web dibujo un cielo limpio toda la noche.
El fallo de red era transitorio; lo que lo hizo invisible fue un `except
Exception` que dejaba una lista vacia detras, indistinguible de "no hay nubes".
"""

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


def test_sin_rejilla_no_se_estima_probabilidad_de_abrir(monkeypatch):
    """Lo peor del fallo antiguo no era la cupula, era ESTO.

    El calculo de apertura hace `max(nubes, default=0.0)` por marco: sin
    rejilla contaba la noche como despejada y publicaba una probabilidad alta
    construida sobre ceros inventados. Callar es correcto; estimar, no.
    """
    _sin_rejilla(monkeypatch)
    assert nightview.build()["roof"] is None


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
