"""Tests de la exportacion estatica para la web (SPEC v8 seccion 6.2).

Lo que estos tests protegen no es el formato, es la regla: **nada se publica sin
su incertidumbre**. Un JSON que sale del repo se lee en otro sitio, sin nadie
delante que sepa lo que hay detras de cada numero, asi que la procedencia y los
avisos tienen que viajar DENTRO.
"""

import datetime as dt
import json

import pytest

from astroweather import webexport


def _noche(recomendaciones, origen_seeing="ecmwf", origen_aod="cams"):
    return {
        "noche": "2026-08-28",
        "oscuridad_astronomica_h": 7.7,
        "luna_iluminacion": 1.0,
        "probabilidad_de_abrir": 0.88,
        "horas_utilizables_esperadas": 11.4,
        "mejor_ventana": None,
        "horas": [],
        "cielo": {
            "mag_cenit_mas_oscuro": 17.08,
            "seeing": {"tercil": "favorable", "rango_arcsec": [0.8, 1.3],
                       "jet_ms": 18.0, "origen": origen_seeing, "calibrado": False},
            "transparencia": {"aod": 0.053, "k_total": 0.2,
                              "veredicto": "noche transparente", "origen": origen_aod},
        },
        "recomendaciones": recomendaciones,
    }


def test_every_published_number_says_where_it_came_from(monkeypatch):
    """Un supuesto y una prevision no se publican igual, y quien lea el JSON
    no tiene forma de distinguirlos si no se lo decimos."""
    monkeypatch.setattr(webexport, "_night", lambda *a, **k: _noche([]))
    payload = webexport.build(nights=1)

    cielo = payload["noches"][0]["cielo"]
    assert cielo["seeing"]["origen"] in ("ecmwf", "supuesto")
    assert cielo["transparencia"]["origen"] in ("cams", "supuesto")
    # El seeing no esta calibrado y el JSON tiene que decirlo solo.
    assert cielo["seeing"]["calibrado"] is False


def test_an_assumed_value_raises_its_own_warning(monkeypatch):
    """Si no hay prevision y se cae al supuesto, el aviso viaja con el dato."""
    monkeypatch.setattr(webexport, "_night",
                        lambda *a, **k: _noche([], origen_seeing="supuesto",
                                               origen_aod="supuesto"))
    avisos = " ".join(webexport.build(nights=1)["confianza"]["avisos"])
    assert "viento en altura" in avisos
    assert "aerosol" in avisos


def test_the_seeing_warning_is_never_optional(monkeypatch):
    """El modelo predijo 1,21\" donde se midieron 3,16\": el aviso va siempre,
    haya o no prevision."""
    monkeypatch.setattr(webexport, "_night", lambda *a, **k: _noche([]))
    avisos = " ".join(webexport.build(nights=1)["confianza"]["avisos"])
    assert "terciles" in avisos


def test_the_confidence_block_carries_the_curve_it_used(monkeypatch):
    """Dentro de un anio hay que poder saber que curva produjo esta noche."""
    monkeypatch.setattr(webexport, "_night", lambda *a, **k: _noche([]))
    confianza = webexport.build(nights=1)["confianza"]
    if confianza["calibrado"]:
        assert confianza["modelo_calibrado"]
        assert confianza["curva_ajustada_el"]
        assert confianza["pares_de_entrenamiento"] > 0


def test_the_payload_survives_a_round_trip(monkeypatch):
    """Se lee en otro proceso, en otra maquina: si no serializa, no existe."""
    monkeypatch.setattr(webexport, "_night", lambda *a, **k: _noche(
        [{"objeto": "NGC 7000", "clase": "narrowband", "horas_si_despeja": 4.5,
          "horas_esperadas": 3.9, "veredicto": "recomendado", "porque": "x"}]))
    payload = webexport.build(nights=1)
    vuelta = json.loads(json.dumps(payload, ensure_ascii=False))
    assert vuelta["noches"][0]["recomendaciones"][0]["objeto"] == "NGC 7000"
    assert dt.datetime.fromisoformat(vuelta["generado_utc"])


def test_writing_it_creates_the_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(webexport, "_night", lambda *a, **k: _noche([]))
    destino = tmp_path / "sub" / "noche.json"
    assert webexport.write(destino, nights=1) == destino
    assert json.loads(destino.read_text(encoding="utf-8"))["noches"]
