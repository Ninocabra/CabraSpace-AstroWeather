"""Tests de la verificacion del viento (29-08-2026).

La racha alimenta una puerta de SEGURIDAD y no tiene verdad terreno en ninguna
fuente. Lo unico medible es el viento medio, y sale corto. Lo que estos tests
protegen es que esa medida no se convierta en una correccion antes de tiempo:
una puerta de seguridad ajustada con veintinueve horas de agosto es ruido con
autoridad, y el error caeria del lado de abrir cuando no se debe.
"""

import datetime as dt

import pytest

from astroweather import verification


def _puntuacion(n, ratio=0.55, bias=-1.8):
    return [{"model": "ecmwf_ifs", "n": n, "bias": bias, "rmse": 2.1,
             "observed": 3.99, "forecast": 3.99 * ratio, "ratio": ratio,
             "trustworthy": n >= verification.MIN_WIND_PAIRS}]


def test_a_handful_of_hours_measures_but_does_not_correct(monkeypatch):
    monkeypatch.setattr(verification, "wind_scores", lambda *a, **k: _puntuacion(29))
    resultado = verification.wind_correction()

    assert resultado["factor"] == 1.0
    assert resultado["applied"] is False
    assert "29" in resultado["why"] and str(verification.MIN_WIND_PAIRS) in resultado["why"]
    # Pero el sesgo medido no se esconde: se mide y se dice.
    assert resultado["bias"] == -1.8


def test_with_enough_pairs_the_correction_lifts_the_forecast(monkeypatch):
    """El modelo se queda corto, asi que el factor tiene que ser > 1: corregir
    hacia abajo una puerta de seguridad seria el fallo que se intenta evitar."""
    monkeypatch.setattr(verification, "wind_scores",
                        lambda *a, **k: _puntuacion(verification.MIN_WIND_PAIRS))
    resultado = verification.wind_correction()

    assert resultado["applied"] is True
    assert resultado["factor"] > 1.0
    assert resultado["factor"] == 1.0 / 0.55


def test_no_pairs_at_all_is_not_a_crash(monkeypatch):
    monkeypatch.setattr(verification, "wind_scores", lambda *a, **k: [])
    resultado = verification.wind_correction()

    assert resultado["factor"] == 1.0
    assert resultado["applied"] is False


def test_the_uncalibrated_gate_says_so_in_the_published_json(monkeypatch):
    """Nada se publica sin su incertidumbre, y menos una puerta de seguridad
    que se ha medido optimista."""
    from astroweather import webexport

    monkeypatch.setattr(webexport, "wind_correction",
                        lambda *a, **k: {"factor": 1.0, "n": 29, "applied": False,
                                         "ratio": 0.55, "bias": -1.8, "why": "pocos pares"})
    monkeypatch.setattr(webexport, "_night", lambda *a, **k: None)
    avisos = webexport.build(nights=1)["confianza"]["avisos"]

    assert any("viento" in a.lower() and "optimista" in a.lower() for a in avisos)


def test_la_correccion_medida_llega_a_la_puerta(monkeypatch):
    """Se medía desde el 29-08-2026 y la puerta seguia con la racha CRUDA.

    El modelo prevé el 55 % del viento medio real en este sitio -- terreno de
    montania contra una celda de 25 km -- y eso se publicaba como aviso al pie
    del panel mientras la puerta de seguridad decidia con el numero corto. Un
    aviso no cierra un techo.
    """
    from astroweather import tonight

    vistos = []
    real = tonight.evaluate

    def espia(*, t_utc, hours_ahead, gust, **resto):
        vistos.append(gust)
        return real(t_utc=t_utc, hours_ahead=hours_ahead, gust=gust, **resto)

    monkeypatch.setattr(tonight, "evaluate", espia)
    monkeypatch.setattr(tonight, "wind_correction",
                        lambda root=None: {"factor": 2.0, "applied": True})

    fila = (dt.datetime(2026, 9, 1, 22, tzinfo=dt.timezone.utc), 10.0, 3.0, 6.0,
            0.0, 0.0, 0.0, 6, "ecmwf_ifs")
    monkeypatch.setattr(tonight, "_forecast_rows", lambda *a, **k: [fila])
    tonight.report(dt.datetime(2026, 9, 1, 12, tzinfo=dt.timezone.utc))

    assert vistos, "la puerta no llego a evaluarse"
    assert vistos[0] == pytest.approx(12.0), (
        f"la racha llego a la puerta como {vistos[0]} en vez de corregida")


def test_sin_pares_suficientes_no_se_corrige_nada(monkeypatch):
    """Una correccion sacada de 48 horas de agosto no sabe del viento de enero.

    Mientras `wind_correction` no la de por fiable, el factor es 1,0 y la
    puerta ve exactamente lo que preve el modelo: peor, pero declarado.
    """
    from astroweather import tonight

    vistos = []
    monkeypatch.setattr(tonight, "evaluate",
                        lambda *, t_utc, hours_ahead, gust, **r:
                        (vistos.append(gust) or tonight.SafetyVerdict(t_utc, [])))
    monkeypatch.setattr(tonight, "wind_correction",
                        lambda root=None: {"factor": 1.0, "applied": False})
    fila = (dt.datetime(2026, 9, 1, 22, tzinfo=dt.timezone.utc), 10.0, 3.0, 6.0,
            0.0, 0.0, 0.0, 6, "ecmwf_ifs")
    monkeypatch.setattr(tonight, "_forecast_rows", lambda *a, **k: [fila])
    tonight.report(dt.datetime(2026, 9, 1, 12, tzinfo=dt.timezone.utc))
    assert vistos[0] == pytest.approx(6.0)
