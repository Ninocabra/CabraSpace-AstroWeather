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
            "mag_cenit_mas_oscuro": 20.19,
            "mag_cenit_tipico": 17.63,
            "seeing": {"tercil": "favorable", "rango_arcsec": [0.8, 1.3],
                       "jet_ms": 18.0, "origen": origen_seeing, "calibrado": False},
            "transparencia": {"aod": 0.053, "k_total": 0.2,
                              "veredicto": "noche transparente", "origen": origen_aod,
                              "calima": False, "especies": True},
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


def test_lo_mas_oscuro_es_lo_mas_oscuro_y_no_lo_contrario():
    """En mag/arcsec2 MAS GRANDE ES MAS OSCURO, y el campo se llama "mas oscuro".

    Hasta el 30-08-2026 salia de un `min()`, o sea que publicaba el instante mas
    BRILLANTE de la noche: el panel decia 17,05 cuando el cielo de esa noche
    llegaba a 20,19 y el fotometro habia medido 20,30 la anterior con MAS Luna.
    Parecia una contradiccion entre el modelo y la medida y era un signo.
    `nightview.py` calculaba lo mismo con `max()` desde siempre.

    Y el tipico tiene que quedar por DEBAJO del mas oscuro (mas brillante), o
    los dos numeros se han intercambiado.
    """
    import statistics

    from astroweather.ephem import night_for
    from astroweather.site import NERPIO
    from astroweather.skymodel import predict

    night = night_for(dt.datetime(2026, 8, 30, tzinfo=dt.timezone.utc), NERPIO)
    times = night.adaptive_hours(10, 30, NERPIO)
    de_noche = [s.predicted_mag for s in predict(times, NERPIO) if s.is_night]
    assert de_noche

    publicado = webexport._night(
        dt.datetime(2026, 8, 30, tzinfo=dt.timezone.utc), NERPIO)
    if publicado is None:
        pytest.skip("sin datos de esa noche en el archivo local")

    cielo = publicado["cielo"]
    assert cielo["mag_cenit_mas_oscuro"] == pytest.approx(max(de_noche), abs=0.01)
    assert cielo["mag_cenit_tipico"] == pytest.approx(
        statistics.median(de_noche), abs=0.01)
    assert cielo["mag_cenit_tipico"] <= cielo["mag_cenit_mas_oscuro"]


def test_la_calima_nunca_viaja_sin_decir_si_la_hemos_medido():
    """`calima: false` a solas es indistinguible de "no tenemos el dato".

    Y no es teorico: del 27-08 al 01-09-2026 el producto publico seis dias
    seguidos que no habia calima porque `netCDF4` no estaba declarado en el
    runner y las especies de CAMS no llegaban al archivo. La noche del 01-09
    tenia polvo por encima del umbral en TODAS sus horas.

    La regla de este fichero -- nada se publica sin su incertidumbre -- aplicada
    al par: si sale una, sale la otra.
    """
    from astroweather.site import NERPIO

    publicado = webexport._night(
        dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc), NERPIO)
    if publicado is None:
        pytest.skip("sin datos de esa noche en el archivo local")

    transp = publicado["cielo"]["transparencia"]
    assert ("calima" in transp) == ("especies" in transp), (
        "una sin la otra deja al lector sin saber si el false es una medida o "
        "un hueco")
    assert isinstance(transp["especies"], bool)
    if transp["calima"]:
        assert transp["especies"] is True, (
            "no se puede avisar de calima sin tener el desglose que la define")


# --- la curva del techo y la ventana del veredicto (2026-09-04) ---------------

from astroweather.roofcalibration import (METHOD_HOURLY, METHOD_MONTHLY,  # noqa: E402
                                          NIGHTS_FOR_HOURLY_REFIT, RoofCalibrator)


# El umbral de noches ya no es 60 (26-10-2026, M1: 20, con la evaluacion
# prequencial en roofcalibration.py): estos tests lo leen del modulo en vez de
# fijar la cifra, porque lo que protegen es que el JSON lo publique.
def _curva_techo(nights):
    return RoofCalibrator(intercept=1.0, cloud_weight=-3.3, trained_on=nights * 9,
                          brier=0.02, brier_climatology=0.04, base_rate=0.55,
                          fitted_at="2026-09-05T00:00:00+00:00", crossing=0.30,
                          slope_low=-7.5, slope_high=-0.3, months=31, nights=nights,
                          monthly_mae=0.123, monthly_mae_climatology=0.181,
                          method=(METHOD_HOURLY if nights >= NIGHTS_FOR_HOURLY_REFIT
                                  else METHOD_MONTHLY))


def test_el_veredicto_lleva_su_ventana_y_la_regla_la_nombra():
    inicio = dt.datetime(2026, 9, 5, 2, tzinfo=dt.timezone.utc)
    fin = inicio + dt.timedelta(hours=1)
    veredicto = webexport._veredicto_abrir(0.66, (inicio, fin, 0.66))
    assert veredicto["abierto"] is True
    assert veredicto["ventana"] == {"desde": "2026-09-05T02:00+00:00",
                                    "hasta": "2026-09-05T03:00+00:00"}
    assert "dos horas" in veredicto["regla"]
    assert webexport._veredicto_abrir(0.30, None)["abierto"] is False
    assert webexport._veredicto_abrir(0.30, None)["ventana"] is None


def test_la_confianza_dice_contra_que_se_calibro(monkeypatch):
    monkeypatch.setattr(webexport, "_night", lambda *a, **k: _noche([]))
    monkeypatch.setattr(webexport, "load_calibrator", lambda *a, **k: _curva_techo(6))
    confianza = webexport.build(nights=1)["confianza"]
    assert confianza["verdad_terreno"] == "techo"
    curva = confianza["curva_techo"]
    assert curva["cruce_nube"] == 0.3
    assert curva["noches"] == 6 and curva["noches_objetivo"] == NIGHTS_FOR_HOURLY_REFIT
    assert curva["pendiente_p10_p90"] == [-7.5, -0.3]


def test_la_nota_de_las_60_noches_viaja_en_el_json(monkeypatch):
    """Faltan N noches, y el dia que no falten el aviso cambia a 'comprobar'."""
    monkeypatch.setattr(webexport, "_night", lambda *a, **k: _noche([]))
    monkeypatch.setattr(webexport, "load_calibrator", lambda *a, **k: _curva_techo(6))
    avisos = " ".join(webexport.build(nights=1)["confianza"]["avisos"])
    assert f"faltan {NIGHTS_FOR_HOURLY_REFIT - 6} noches" in avisos
    monkeypatch.setattr(webexport, "load_calibrator",
                        lambda *a, **k: _curva_techo(NIGHTS_FOR_HOURLY_REFIT))
    avisos = " ".join(webexport.build(nights=1)["confianza"]["avisos"])
    assert "ya se ajusta por horas" in avisos
    assert "faltan" not in avisos
    # Y ya no manda comprobar el cruce contra el 30 % mensual, que era el techo.
    assert "30 % mensual" not in avisos


def test_con_la_curva_del_fotometro_se_avisa_de_que_no_es_el_techo(monkeypatch):
    from astroweather.calibration import Calibrator
    fotometro = Calibrator(intercept=2.4, cloud_weight=-2.1, lead_weight=0.0,
                           trained_on=100, brier=0.1, brier_climatology=0.2,
                           base_rate=0.8, fitted_at="x")
    monkeypatch.setattr(webexport, "_night", lambda *a, **k: _noche([]))
    monkeypatch.setattr(webexport, "load_calibrator", lambda *a, **k: fotometro)
    payload = webexport.build(nights=1)
    assert payload["confianza"]["verdad_terreno"] == "fotometro"
    assert payload["confianza"]["curva_techo"] is None
    assert "no contra el techo real" in " ".join(payload["confianza"]["avisos"])
