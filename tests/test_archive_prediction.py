"""Tests de archivar NUESTRA propia prevision (SPEC v8 seccion 6.2).

El hueco que tapa: `p_calibrated` existe en el esquema desde el principio y
estaba vacia. El marcador recalculaba la probabilidad desde el `cloud_total`
guardado, asi que cambiar el calibrador cambiaba retroactivamente lo que
dijimos que dijimos.
"""

import datetime as dt

import pytest

from astroweather.gates import evaluate
from astroweather.tonight import HourForecast, NightReport
from astroweather.workers import archive_prediction


def _report(hours, now):
    """Un informe de mentira, para no depender del archivo."""
    return NightReport(
        night=now.date(), site="prueba",
        hours=[
            HourForecast(
                t_utc=now + dt.timedelta(hours=delta),
                cloud_total=cloud,
                clear_probability=probability,
                usable_probability=probability,
                moon_altitude=0.0,
                safety=evaluate(t_utc=now + dt.timedelta(hours=delta),
                                hours_ahead=max(0.0, delta)),
                calibrated=True,
            )
            for delta, cloud, probability in hours
        ],
        moon_illumination=0.5, dark_hours=8.0, calibration_note="prueba")


def test_an_hour_already_past_is_not_a_prediction(monkeypatch):
    """La ventana abre una hora antes del ocaso, asi que al correr de noche
    siempre hay horas por detras. Archivarlas seria guardar retrospectiva en el
    registro que existe justo para auditar lo que se dijo ANTES."""
    now = dt.datetime(2026, 8, 28, 20, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(archive_prediction, "utcnow", lambda: now)
    monkeypatch.setattr(archive_prediction, "report",
                        lambda *a, **k: _report(
                            [(-2, 10.0, 0.9), (-1, 10.0, 0.9),
                             (0, 20.0, 0.8), (3, 40.0, 0.6)], now))

    rows = archive_prediction.rows_for(now)
    assert [r["lead_h"] for r in rows] == [0, 3]


def test_the_raw_and_the_calibrated_are_both_kept(monkeypatch):
    """Si solo se guarda una, no se puede medir lo que anade calibrar."""
    now = dt.datetime(2026, 8, 28, 20, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(archive_prediction, "utcnow", lambda: now)
    monkeypatch.setattr(archive_prediction, "report",
                        lambda *a, **k: _report([(2, 40.0, 0.71)], now))

    row = archive_prediction.rows_for(now)[0]
    assert row["cloud_total"] == 40.0
    assert row["p_raw"] == pytest.approx(0.60)      # 1 - 40/100, sin curva
    assert row["p_calibrated"] == pytest.approx(0.71)
    assert row["p_raw"] != row["p_calibrated"]


def test_every_row_says_which_calibrator_produced_it(monkeypatch):
    """Dentro de un anio, una prediccion mala hay que poder atribuirla: al
    modelo, a la curva o al cielo. Sin esta marca no se distinguen."""
    now = dt.datetime(2026, 8, 28, 20, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(archive_prediction, "utcnow", lambda: now)
    monkeypatch.setattr(archive_prediction, "report",
                        lambda *a, **k: _report([(1, 0.0, 0.88)], now))

    version = archive_prediction.rows_for(now)[0]["provider_model_version"]
    assert version.startswith("astroweather/")
    # O el modelo con el que se calibro, o el aviso de que no hay curva.
    assert "@" in version or version.endswith("sin-calibrar")


def test_it_is_archived_as_one_more_provider(monkeypatch):
    """Para que el marcador pueda puntuarlo con la misma vara que a ECMWF."""
    now = dt.datetime(2026, 8, 28, 20, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(archive_prediction, "utcnow", lambda: now)
    monkeypatch.setattr(archive_prediction, "report",
                        lambda *a, **k: _report([(1, 0.0, 0.88)], now))

    row = archive_prediction.rows_for(now)[0]
    assert row["model"] == archive_prediction.MODEL
    assert row["member"] is None          # determinista, no un miembro del EPS
    assert row["run_utc"] == now

    from astroweather.schema import FORECAST_SCHEMA
    assert not set(row) - set(FORECAST_SCHEMA.names)
