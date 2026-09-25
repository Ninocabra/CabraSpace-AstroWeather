"""WeatherNext en el producto, decidido el 25-09-2026.

Nubes de WeatherNext con SU curva para las noches despues de hoy (validado a
1-5 dias); esta noche sigue el ECMWF. Su lluvia entra en las puertas de
seguridad cuando su pasada es la mas reciente. Y sus terminos de uso mandan en
lo que se publica: lo derivado si, el dato crudo no, y la cita siempre.
"""

import datetime as dt
import json
import re

import pytest

from astroweather import gates, tonight, webexport
from astroweather.archive import write_canonical
from astroweather.calibration import Calibrator
from astroweather.ephem import night_for
from astroweather.site import NERPIO

UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
ECMWF = Calibrator(intercept=2.0, cloud_weight=-4.0, lead_weight=0.0, trained_on=100,
                   brier=0.1, brier_climatology=0.2, base_rate=0.5,
                   fitted_at="2026-09-18", trained_on_model="ecmwf_ifs025")
WN = Calibrator(intercept=3.748, cloud_weight=-3.549, lead_weight=-0.0592,
                trained_on=6346, brier=0.1146, brier_climatology=0.141,
                base_rate=0.83, fitted_at="2026-09-18", trained_on_model="weathernext3")


def _horas(day):
    night = night_for(day, NERPIO)
    t = night.window_start.replace(minute=0, second=0, microsecond=0)
    while t <= night.window_end:
        yield t
        t += dt.timedelta(hours=1)


def _archivo(tmp_path, wn_rain=0.0):
    ecmwf_run = NOW.replace(hour=0) - dt.timedelta(days=1)
    wn_run = NOW.replace(hour=0)        # mas reciente que la del ECMWF
    filas = []
    for day in (NOW, NOW + dt.timedelta(days=1)):
        for t in _horas(day):
            filas.append({"site_id": "nerpio", "model": "ecmwf_ifs025", "member": None,
                          "run_utc": ecmwf_run, "valid_utc": t,
                          "lead_h": int((t - ecmwf_run).total_seconds() // 3600),
                          "cloud_total": 80.0})
            filas.append({"site_id": "nerpio", "model": "ecmwf_ifs", "member": None,
                          "run_utc": ecmwf_run, "valid_utc": t, "gust10m": 3.0,
                          "precipitation": 0.0, "prob_precip": 0.0, "cape": 0.0,
                          "wind10m": 2.0})
            filas.append({"site_id": "nerpio", "model": "weathernext3", "member": None,
                          "run_utc": wn_run, "valid_utc": t,
                          "lead_h": int((t - wn_run).total_seconds() // 3600),
                          "cloud_total": 10.0, "precipitation": wn_rain,
                          "wind10m": 2.0})
    write_canonical(filas, dataset="forecasts", site_id="nerpio", root=tmp_path)
    write_canonical([{"site_id": "nerpio", "t_utc": ecmwf_run, "sky_mag_raw": 21.0,
                      "instrument_type": "TESS", "instrument_id": "stars289",
                      "bandpass_id": "tess-w"}],
                    dataset="measurements", site_id="nerpio", root=tmp_path)
    (tmp_path / "cloud_calibration_weathernext3.json").write_text(
        json.dumps(WN.__dict__), encoding="utf-8")
    return tmp_path


@pytest.fixture
def raiz(tmp_path, monkeypatch):
    monkeypatch.setattr(tonight, "load_calibrator", lambda: ECMWF)
    return _archivo(tmp_path)


def test_manana_sale_de_weathernext_con_su_curva(raiz):
    informe = tonight.report(NOW + dt.timedelta(days=1), NERPIO, raiz)
    assert informe.hours
    assert informe.cloud_model == "weathernext3"
    assert informe.cloud_truth == "fotometro"
    hora = informe.hours[0]
    lead = (hora.t_utc - NOW.replace(hour=0)).total_seconds() / 3600
    assert hora.clear_probability == pytest.approx(WN.probability(10.0, lead))


def test_esta_noche_sigue_con_el_ecmwf(raiz):
    informe = tonight.report(NOW, NERPIO, raiz)
    assert informe.hours
    assert informe.cloud_model == "ecmwf_ifs025"
    assert {h.cloud_total for h in informe.hours} == {80.0}


def test_sin_curva_de_weathernext_manana_sigue_con_el_ecmwf(raiz):
    (raiz / "cloud_calibration_weathernext3.json").unlink()
    informe = tonight.report(NOW + dt.timedelta(days=1), NERPIO, raiz)
    assert informe.cloud_model == "ecmwf_ifs025"


def test_la_nube_cruda_de_weathernext_no_se_publica(raiz):
    manana = tonight.report(NOW + dt.timedelta(days=1), NERPIO, raiz)
    hoy = tonight.report(NOW, NERPIO, raiz)
    publicada = webexport._hour(manana.hours[0])
    assert publicada["nubes_previstas"] is None
    assert publicada["p_despejado"] is not None
    assert webexport._hour(hoy.hours[0])["nubes_previstas"] == 80.0


def test_la_lluvia_de_weathernext_bloquea_pero_sin_su_numero(tmp_path, monkeypatch):
    monkeypatch.setattr(tonight, "load_calibrator", lambda: ECMWF)
    raiz = _archivo(tmp_path, wn_rain=0.7)
    informe = tonight.report(NOW, NERPIO, raiz)
    hora = informe.hours[0]
    assert not hora.safety.safe_to_open
    assert "WeatherNext" in hora.safety.summary
    assert not re.search(r"\d", hora.safety.summary)
    assert "weathernext3" in tonight.safety_source(
        NERPIO, informe.hours[0].t_utc, informe.hours[-1].t_utc, raiz)


def test_la_lluvia_del_ecmwf_sigue_diciendo_sus_mm():
    riesgo = gates._rain_risk(0.7, None, None, 3.0, "ecmwf_ifs")
    assert riesgo.reason == "precipitación prevista 0.7 mm"


def test_la_cita_es_la_que_piden_los_terminos():
    cita = webexport._atribucion()[0]["cita"]
    assert "Google Earth Engine" in cita
    assert ("© 2024-6 Google LLC, whose machine learning models were used to "
            "create the experimental data made available under the following "
            "licence terms https://storage.googleapis.com/weathernext-public/"
            "terms-of-use.pdf. This data is intended for experimental modelling "
            "only and is not intended, validated, or approved for real world use."
            ) in cita
