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


# --- M7 (verificacion 26-10-2026, seccion 10): la fila dice que curva la produjo

class _PhotometerOnDisk:
    """La curva del fotometro que hay en disco. Hasta el 27-10 la fila
    archivada llevaba SIEMPRE esta version, aunque el numero saliera de la
    curva del techo o de la de WeatherNext."""
    trained_on_model = "ecmwf_ifs025"
    fitted_at = "2026-08-30T17:54:00+00:00"


def _report_with_curve(now, truth, model, fitted_at):
    night_report = _report([(2, 30.0, 0.66)], now)
    night_report.cloud_truth = truth
    night_report.curve_model = model
    night_report.curve_fitted_at = fitted_at
    return night_report


@pytest.mark.parametrize("truth, model, fitted_at", [
    ("techo", "ecmwf_ifs025", "2026-10-25T05:01:00+00:00"),
    ("fotometro", "weathernext3", "2026-09-18T07:28:00+00:00"),
])
def test_the_row_names_the_curve_that_produced_the_number(monkeypatch, truth,
                                                          model, fitted_at):
    """Verdad, modelo y `fitted_at` de la curva USADA, no los de la del
    fotometro: sin esto el retroanalisis del 26-10 tuvo que adivinar el regimen
    de cada fila comparando numeros (`hindcast.Curves.classify`)."""
    now = dt.datetime(2026, 10, 27, 15, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(archive_prediction, "utcnow", lambda: now)
    monkeypatch.setattr(archive_prediction, "report",
                        lambda *a, **k: _report_with_curve(now, truth, model, fitted_at))
    from astroweather.calibration import Calibrator
    monkeypatch.setattr(Calibrator, "load",
                        classmethod(lambda cls, *a, **k: _PhotometerOnDisk()))

    row = archive_prediction.rows_for(now)[0]
    assert row["curve_truth"] == truth
    assert row["curve_model"] == model
    assert row["curve_fitted_at"] == fitted_at
    assert row["provider_model_version"] == f"astroweather/{model}@{fitted_at}"
    assert _PhotometerOnDisk.fitted_at not in row["provider_model_version"]


def test_an_uncalibrated_night_says_so(monkeypatch):
    now = dt.datetime(2026, 10, 27, 15, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(archive_prediction, "utcnow", lambda: now)
    monkeypatch.setattr(archive_prediction, "report",
                        lambda *a, **k: _report_with_curve(now, None, None, None))
    row = archive_prediction.rows_for(now)[0]
    assert row["provider_model_version"] == "astroweather/sin-calibrar"
    assert row["curve_truth"] is None and row["curve_fitted_at"] is None


def test_report_carries_the_curve_it_used(monkeypatch):
    """`tonight.report` es quien elige la curva: es quien tiene que decirla."""
    from astroweather import tonight

    class Roof:
        trained_on_model = "ecmwf_ifs025"
        fitted_at = "2026-10-25T05:01:00+00:00"
        truth = "techo"
        months, nights, brier, brier_climatology = 30, 60, 0.1, 0.2

        def probability(self, cloud, lead=24.0):
            return 0.5

    monkeypatch.setattr(tonight, "load_calibrator", lambda *a, **k: Roof())
    monkeypatch.setattr(tonight, "_weathernext_calibrator", lambda *a, **k: None)
    monkeypatch.setattr(tonight, "_forecast_rows", lambda *a, **k: [])
    monkeypatch.setattr(tonight, "wind_correction", lambda *a, **k: {})
    night_report = tonight.report(dt.datetime(2026, 10, 27, 15, tzinfo=dt.timezone.utc))
    assert night_report.cloud_truth == "techo"
    assert night_report.curve_model == "ecmwf_ifs025"
    assert night_report.curve_fitted_at == "2026-10-25T05:01:00+00:00"


def test_old_and_new_rows_read_together(tmp_path):
    """Compatibilidad: lo archivado antes del 27-10 no tiene las columnas
    nuevas. Leido junto con lo nuevo, lo viejo sale nulo y nada rompe; y un
    archivo donde NINGUN fichero las tiene todavia tambien se lee."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from astroweather.archive import write_canonical
    from astroweather.hindcast import Data
    from astroweather.queries import connect

    run = dt.datetime(2026, 10, 1, 15, tzinfo=dt.timezone.utc)
    old = {"site_id": "nerpio", "model": "astroweather", "run_utc": run,
           "valid_utc": run + dt.timedelta(hours=6), "lead_h": 6, "member": None,
           "cloud_total": 10.0, "p_raw": 0.9, "p_calibrated": 0.8,
           "provider_model_version": "astroweather/ecmwf_ifs025@2026-08-30T17:54:00",
           "retrieved_at": run}
    directory = tmp_path / "forecasts" / "site=nerpio" / "year=2026" / "month=10"
    directory.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([old]), directory / "viejo.parquet")
    # `connect()` monta tambien la vista de medidas y sin ella falla.
    write_canonical([{"site_id": "nerpio", "t_utc": run, "sky_mag_raw": 21.6,
                      "instrument_type": "TESS", "instrument_id": "stars289",
                      "bandpass_id": "tess-w"}],
                    dataset="measurements", site_id="nerpio", root=tmp_path)

    # Solo lo viejo: la columna no existe en ningun fichero.
    assert Data(tmp_path, tmp_path).ours()[0]["truth"] is None

    new = dict(old, run_utc=run + dt.timedelta(days=1),
               valid_utc=run + dt.timedelta(days=1, hours=6),
               retrieved_at=run + dt.timedelta(days=1),
               curve_truth="techo", curve_model="ecmwf_ifs025",
               curve_fitted_at="2026-10-01T05:00:00+00:00")
    write_canonical([new], dataset="forecasts", site_id="nerpio", root=tmp_path)

    rows = connect(tmp_path).execute(
        "SELECT curve_truth FROM forecasts WHERE model = 'astroweather' "
        "ORDER BY run_utc").fetchall()
    assert [r[0] for r in rows] == [None, "techo"]
    ours = sorted(Data(tmp_path, tmp_path).ours(), key=lambda r: r["run"])
    assert [r["truth"] for r in ours] == [None, "techo"]
    assert ours[1]["fitted_at"] == "2026-10-01T05:00:00+00:00"


def test_classify_trusts_the_label_when_there_is_one():
    """Con la etiqueta, el regimen se LEE; sin ella (filas viejas) se sigue
    reconstruyendo como el 26-10."""
    from astroweather.hindcast import Curves

    curves = Curves.__new__(Curves)
    curves.photo, curves.roof, curves.wn = {}, [], None
    run = dt.datetime(2026, 10, 27, 15, tzinfo=dt.timezone.utc)
    base = {"run": run, "cloud": 30.0, "lead": 6, "p": 0.66, "version": "x"}
    assert curves.classify(dict(base, truth="techo", model="ecmwf_ifs025")) == ("techo", 0.0)
    assert curves.classify(dict(base, truth="fotometro", model="weathernext3")) == (
        "weathernext", 0.0)
    assert curves.classify(dict(base, truth=None, model=None))[0] == "desconocido"
