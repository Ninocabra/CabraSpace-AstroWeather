"""La curva propia de WeatherNext 3: de donde salen sus pares y a quien se aplica.

Lo que se fija aqui es lo que, mal hecho, no da error: contar dos veces la hora
que cubren las pasadas de 00Z y 12Z, colar percentiles o el dia 0 en el
entrenamiento, pisar la curva del ECMWF que lee el producto, o aplicar una curva
a un modelo que no es el suyo.
"""

import datetime as dt

import numpy as np
import pytest

from astroweather import calibration as C
from astroweather import scoreboard as S
from astroweather.archive import write_canonical

NOCHE = dt.datetime(2026, 3, 10, 1, 0, tzinfo=dt.timezone.utc)
HORA = NOCHE.replace(tzinfo=None)


def _fila(model, lead_h, cloud, member=None, valid=NOCHE):
    return {"site_id": "nerpio", "model": model, "member": member,
            "run_utc": valid - dt.timedelta(hours=lead_h), "valid_utc": valid,
            "lead_h": lead_h, "cloud_total": cloud}


def _archivo(tmp_path, filas):
    write_canonical(filas, dataset="forecasts", site_id="nerpio", root=tmp_path)
    # `connect()` monta tambien la vista de medidas y falla si no existe: una
    # fila cualquiera basta (la verdad se inyecta aparte en cada test).
    write_canonical([{"site_id": "nerpio", "t_utc": NOCHE, "sky_mag_raw": 21.6,
                      "instrument_type": "TESS", "instrument_id": "stars289",
                      "bandpass_id": "tess-w"}],
                    dataset="measurements", site_id="nerpio", root=tmp_path)
    return tmp_path


def test_las_dos_pasadas_de_la_misma_hora_son_un_par_y_no_dos(tmp_path):
    """00Z y 12Z validas para la misma hora y el mismo dia de alcance son dos
    opiniones sobre UNA hora: si se contaran por separado, las horas que caen
    en las dos pasadas pesarian el doble en el ajuste."""
    raiz = _archivo(tmp_path, [_fila("weathernext3", 25, 20.0),
                               _fila("weathernext3", 37, 60.0)])
    pares = C.training_pairs_weathernext({HORA: True}, raiz)
    assert pares == [(40.0, 1.0, True)]


def test_solo_entra_la_media_y_en_los_alcances_validados(tmp_path):
    raiz = _archivo(tmp_path, [
        _fila("weathernext3", 5, 10.0),            # dia 0: sin ECMWF, no validado
        _fila("weathernext3", 150, 10.0),          # dia 6: solo 00Z, no validado
        _fila("weathernext3_p90", 50, 90.0),       # percentil, no es la media
        _fila("ecmwf_ifs025_prev2d", 0, 90.0),     # otro modelo
        _fila("weathernext3", 50, 30.0),           # el unico que vale
    ])
    pares = C.training_pairs_weathernext({HORA: False}, raiz)
    assert pares == [(30.0, 2.0, False)]


def test_una_hora_sin_verdad_no_entra(tmp_path):
    raiz = _archivo(tmp_path, [_fila("weathernext3", 50, 30.0)])
    otra = HORA + dt.timedelta(hours=1)
    assert C.training_pairs_weathernext({otra: True}, raiz) == []


def test_entrenar_weathernext_no_pisa_la_curva_del_ecmwf(tmp_path, monkeypatch):
    """El producto y el runner leen `cloud_calibration.json`. La curva de
    WeatherNext va a su propio fichero y declara con que modelo se entreno."""
    ecmwf = C.Calibrator(intercept=1.0, cloud_weight=-2.0, lead_weight=0.0,
                         trained_on=500, brier=0.1, brier_climatology=0.2,
                         base_rate=0.7, fitted_at="x")
    ecmwf.save(C.model_path(root=tmp_path))
    antes = (tmp_path / "cloud_calibration.json").read_bytes()

    rng = np.random.default_rng(5)
    nubes = rng.uniform(0, 100, 600)
    sinteticos = [(float(n), float(rng.integers(1, 6)),
                   bool(rng.random() < 1 / (1 + np.exp(-(3 - 4 * n / 100)))))
                  for n in nubes]
    monkeypatch.setattr(C, "observed_clear_hours", lambda since, root: {})
    monkeypatch.setattr(C, "training_pairs_weathernext", lambda truth, root: sinteticos)

    curva = C.train(root=tmp_path, model=C.WEATHERNEXT_MODEL)

    assert (tmp_path / "cloud_calibration.json").read_bytes() == antes
    guardada = C.Calibrator.load(tmp_path / "cloud_calibration_weathernext3.json")
    assert guardada.trained_on_model == "weathernext3"
    assert guardada.cloud_weight == pytest.approx(curva.cloud_weight)
    assert guardada.cloud_weight < 0


def test_un_modelo_sin_curva_propia_no_se_entrena():
    with pytest.raises(ValueError):
        C.train(model="icon_seamless")


def _marcador(tmp_path, monkeypatch, curva_wn):
    """El marcador sobre 30 noches sinteticas, con la verdad inyectada."""
    filas, verdad = [], {}
    rng = np.random.default_rng(9)
    for dia in range(30):
        valida = NOCHE + dt.timedelta(days=dia)
        nube = float(rng.uniform(0, 100))
        verdad[valida.replace(tzinfo=None)] = bool(nube < 50)
        filas.append(_fila("weathernext3", 50, nube, valid=valida))
    _archivo(tmp_path, filas)
    if curva_wn is not None:
        curva_wn.save(C.model_path(C.WEATHERNEXT_MODEL, tmp_path))
    monkeypatch.setattr(S, "observed_clear_hours", lambda since, root: verdad)
    return {e["model"] for e in S.cloud_scores(root=tmp_path)["models"]}


def _curva(modelo):
    return C.Calibrator(intercept=2.0, cloud_weight=-4.0, lead_weight=0.0,
                        trained_on=500, brier=0.1, brier_climatology=0.2,
                        base_rate=0.7, fitted_at="x", trained_on_model=modelo)


def test_el_marcador_puntua_weathernext_calibrado_con_su_curva(tmp_path, monkeypatch):
    modelos = _marcador(tmp_path, monkeypatch, _curva("weathernext3"))
    assert "weathernext3_calibrado" in modelos
    # Y no se mete en el bloque del ECMWF, que agrupa lo que empieza por
    # "calibrado".
    assert not any(m.startswith("calibrado") for m in modelos)


def test_una_curva_de_otro_modelo_no_se_aplica_a_weathernext(tmp_path, monkeypatch):
    """Si el fichero de WeatherNext trajera la curva del ECMWF (una copia mal
    hecha), aplicarla seria corregir unos numeros con la medida de otros."""
    assert "weathernext3_calibrado" not in _marcador(
        tmp_path, monkeypatch, _curva("ecmwf_ifs025"))


def test_sin_curva_el_marcador_sigue_sin_ella(tmp_path, monkeypatch):
    modelos = _marcador(tmp_path, monkeypatch, None)
    assert "weathernext3" in modelos
    assert "weathernext3_calibrado" not in modelos
