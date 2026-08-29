"""Tests de la verificacion del viento (29-08-2026).

La racha alimenta una puerta de SEGURIDAD y no tiene verdad terreno en ninguna
fuente. Lo unico medible es el viento medio, y sale corto. Lo que estos tests
protegen es que esa medida no se convierta en una correccion antes de tiempo:
una puerta de seguridad ajustada con veintinueve horas de agosto es ruido con
autoridad, y el error caeria del lado de abrir cuando no se debe.
"""

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
