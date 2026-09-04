"""La curva P(techo abierto | nube prevista), ajustada contra el techo REAL.

Lo que protegen estos tests es la procedencia de cada parametro: el cruce sale
de los meses, la pendiente de las horas, y al llegar a las 60 noches el ajuste
cambia de metodo solo. Y el control positivo de siempre: la curva del
fotometro, puesta sobre los mismos meses, tiene que salir optimista, que es
el defecto que motivo esta curva (2026-09-04).
"""

import datetime as dt

import numpy as np
import pytest

from astroweather import roofcalibration as R
from astroweather.calibration import Calibrator

UTC = dt.timezone.utc


def _months(crossing=0.30, n=31, seed=3):
    """Meses sinteticos cuyo techo abre exactamente cuando la nube < cruce."""
    rng = np.random.default_rng(seed)
    pairs = []
    for i in range(n):
        # Meses distintos entre si: veranos casi limpios e inviernos cerrados,
        # que es lo que hace que la climatologia constante tenga algo que perder.
        clouds = rng.beta(rng.uniform(0.3, 1.5), rng.uniform(0.3, 1.5), 300)
        pairs.append(R.MonthPair(2024 + i // 12, i % 12 + 1,
                                 float((clouds < crossing).mean()), clouds))
    return pairs


def _hours(nights, slope=-6.0, crossing=0.30, seed=5, per_night=10):
    """Horas sinteticas con techo Bernoulli segun una logistica conocida."""
    rng = np.random.default_rng(seed)
    pairs = []
    for n in range(nights):
        night = dt.datetime(2026, 9, 1, 18, tzinfo=UTC) + dt.timedelta(days=n)
        for h in range(per_night):
            cloud = float(rng.uniform(0, 100))
            p = 1 / (1 + np.exp(-slope * (cloud / 100 - crossing)))
            pairs.append((night + dt.timedelta(hours=h), cloud,
                          1.0 if rng.random() < p else 0.0))
    return pairs


def test_el_cruce_sale_de_los_meses():
    assert R.crossing_from_months(_months(0.30)) == pytest.approx(0.30, abs=0.02)
    assert R.crossing_from_months(_months(0.55)) == pytest.approx(0.55, abs=0.02)


def test_la_pendiente_sale_de_las_horas_y_trae_su_intervalo():
    slope, low, high = R.slope_from_hours(_hours(40), crossing=0.30, rounds=60)
    assert -9.0 < slope < -3.5
    assert low <= slope <= high
    # Menos noches, intervalo mas ancho: la incertidumbre se MIDE, no se declara.
    _, low_few, high_few = R.slope_from_hours(_hours(6), crossing=0.30, rounds=60)
    assert (high_few - low_few) > (high - low)


def test_con_pocas_noches_el_cruce_es_mensual_y_se_declara():
    model = R.fit(_months(), _hours(6), rounds=40)
    assert model.method == "cruce mensual + pendiente horaria"
    assert model.truth == "techo"
    assert model.crossing == pytest.approx(0.30, abs=0.02)
    assert model.nights == 6
    assert model.nights_missing == R.NIGHTS_FOR_HOURLY_REFIT - 6
    # El cruce se respeta exactamente: P = 0,5 en la nube del cruce.
    assert model.probability(model.crossing * 100) == pytest.approx(0.5, abs=1e-6)
    assert model.probability(0) > 0.5 > model.probability(100)


def test_la_nota_de_las_60_noches_cambia_el_metodo_sola():
    """El dia que haya 60 noches nadie tiene que acordarse: fit() lo decide."""
    model = R.fit(_months(), _hours(60), rounds=40)
    assert model.method == "horario"
    assert model.nights_missing == 0
    # Y el ajuste horario recupera la curva con la que se generaron las horas.
    assert model.crossing == pytest.approx(0.30, abs=0.08)
    assert -9.0 < model.cloud_weight < -3.5


def test_el_umbral_de_noches_es_un_parametro_y_no_un_numero_suelto():
    assert R.fit(_months(), _hours(10), nights_target=10, rounds=20).method == "horario"
    assert R.fit(_months(), _hours(10), nights_target=11, rounds=20).method != "horario"


def test_la_curva_del_techo_mejora_a_la_climatologia_mensual():
    model = R.fit(_months(), _hours(20), rounds=40)
    assert model.beats_climatology
    assert model.monthly_mae < model.monthly_mae_climatology
    assert model.held_out_mae is not None


def test_control_positivo_la_curva_del_fotometro_sale_optimista():
    """La curva del fotometro (cloud_calibration.json del 30-08-2026), sobre
    meses cuyo techo abre solo con nube < 30 %, promete MAS de lo que se abre.
    Es el defecto medido sobre 31 meses reales (+26 puntos) y lo que motiva
    esta curva; si este test dejara de fallar en rojo, no estaria midiendo."""
    photometer = Calibrator(intercept=2.443, cloud_weight=-2.125, lead_weight=0.0,
                            trained_on=33799, brier=0.136, brier_climatology=0.149,
                            base_rate=0.82, fitted_at="2026-08-30T17:54:56+00:00")
    months = _months()
    promised = np.mean([np.mean([photometer.probability(c * 100) for c in m.clouds])
                        for m in months])
    opened = np.mean([m.open_fraction for m in months])
    assert promised - opened > 0.15


def test_sin_datos_suficientes_no_se_inventa_curva():
    with pytest.raises(ValueError):
        R.fit(_months(n=5), _hours(6), rounds=5)
    with pytest.raises(ValueError):
        R.fit(_months(), _hours(2), rounds=5)


def test_guardar_y_cargar_conserva_la_procedencia(tmp_path):
    model = R.fit(_months(), _hours(8), rounds=20)
    path = model.save(tmp_path / "roof_calibration.json")
    back = R.RoofCalibrator.load(path)
    assert back.method == model.method
    assert back.crossing == model.crossing
    assert back.probability(40) == pytest.approx(model.probability(40))
    assert R.RoofCalibrator.load(tmp_path / "missing.json") is None


def test_load_calibrator_prefiere_el_techo_y_cae_al_fotometro(tmp_path):
    assert R.load_calibrator(tmp_path) is None
    Calibrator(intercept=2.4, cloud_weight=-2.1, lead_weight=0.0, trained_on=100,
               brier=0.1, brier_climatology=0.2, base_rate=0.8,
               fitted_at="x").save(tmp_path / "cloud_calibration.json")
    assert getattr(R.load_calibrator(tmp_path), "truth", "fotometro") == "fotometro"
    R.fit(_months(), _hours(8), rounds=20).save(tmp_path / "roof_calibration.json")
    assert R.load_calibrator(tmp_path).truth == "techo"
