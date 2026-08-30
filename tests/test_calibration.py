"""Tests for the cloud probability calibrator (SPEC v8 sections 1.1 and 4.3)."""

import numpy as np
import pytest

from astroweather.calibration import Calibrator, fit, reliability


def _synthetic(n=4000, seed=7):
    """A forecast with real skill: clearer sky, higher chance of a clear hour."""
    rng = np.random.default_rng(seed)
    clouds = rng.uniform(0, 100, n)
    leads = rng.integers(1, 6, n).astype(float)
    truth_probability = 1 / (1 + np.exp(-(2.5 - 4.0 * clouds / 100)))
    observed = rng.random(n) < truth_probability
    return list(zip(clouds, leads, observed))


def test_more_cloud_means_less_chance_of_a_clear_hour():
    model = fit(_synthetic())
    assert model.cloud_weight < 0
    assert model.probability(10) > model.probability(90)


def test_a_skilful_forecast_beats_climatology():
    model = fit(_synthetic())
    assert model.beats_climatology
    assert model.brier < model.brier_climatology


def test_a_useless_forecast_does_not_beat_climatology():
    """The panel has to be able to say 'this adds nothing'."""
    rng = np.random.default_rng(3)
    noise = [(rng.uniform(0, 100), 1.0, bool(rng.random() < 0.64)) for _ in range(4000)]
    model = fit(noise)
    # With no signal the fit collapses onto the base rate; it cannot do better.
    assert model.brier >= model.brier_climatology - 0.002
    assert model.probability(10) == pytest.approx(model.probability(90), abs=0.05)


def test_probabilities_stay_within_range():
    model = fit(_synthetic())
    for cloud in (-10, 0, 50, 100, 150):
        assert 0.0 <= model.probability(cloud) <= 1.0


def test_reliability_curve_tracks_the_diagonal_for_a_good_model():
    """When it says 70 %, it should happen about 70 % of the time."""
    pairs = _synthetic(n=8000)
    curve = reliability(pairs, fit(pairs), bins=6)
    assert curve
    for predicted, observed, count in curve:
        assert abs(predicted - observed) < 0.08, (predicted, observed, count)


def test_calibration_refuses_to_fit_on_a_handful_of_nights():
    with pytest.raises(ValueError, match="insufficient data"):
        fit([(50.0, 1.0, True)] * 10)


def test_the_model_survives_a_round_trip(tmp_path):
    model = fit(_synthetic())
    path = model.save(tmp_path / "cal.json")
    restored = Calibrator.load(path)
    assert restored.probability(30) == pytest.approx(model.probability(30))
    assert Calibrator.load(tmp_path / "missing.json") is None


def test_la_verdad_del_cielo_solo_la_da_el_fotometro(tmp_path):
    """La allsky escribe en la MISMA columna que el fotometro.

    `fetch_allsky` guarda -2.5*log10(ADU) en `sky_mag_raw`: una magnitud
    instrumental, monotona en el ADU y con punto cero ARBITRARIO, y su propio
    comentario lo dice. Son -16 donde el cielo son 21. Hasta el 30-08-2026
    `observed_clear_hours` hacia `median(sky_mag_raw)` sin mirar el instrumento,
    asi que en cuanto el fotometro faltaba un rato -- que pasa cada noche, con
    el scheduler en un escritorio que se duerme -- la mediana del cubo era la de
    la allsky y salian residuos de -34 mag contra el modelo. `skybackground` ya
    filtraba por instrumento; estas dos consultas no.
    """
    import datetime as dt

    from astroweather.archive import write_canonical
    from astroweather.calibration import observed_clear_hours

    # Noche cerrada de invierno, con la Luna muy por debajo del horizonte.
    base = dt.datetime(2026, 1, 15, 2, 0, tzinfo=dt.timezone.utc)
    filas = []
    for minuto in range(0, 60, 2):
        cuando = base + dt.timedelta(minutes=minuto)
        filas.append({"site_id": "nerpio", "t_utc": cuando, "sky_mag_raw": 21.6,
                      "instrument_type": "TESS", "instrument_id": "stars289",
                      "bandpass_id": "tess-w"})
        filas.append({"site_id": "nerpio", "t_utc": cuando, "sky_mag_raw": -16.3,
                      "instrument_type": "ALLSKY", "instrument_id": "indi-allsky-cam1",
                      "bandpass_id": "asi462mc-sin-filtro"})
    write_canonical(filas, dataset="measurements", site_id="nerpio", root=tmp_path)
    # `connect()` monta tambien la vista de previsiones y falla si el dataset no
    # existe: una fila cualquiera basta para que el archivo de prueba sea legible.
    write_canonical([{"site_id": "nerpio", "model": "prev", "run_utc": base,
                      "valid_utc": base, "lead_h": 0, "cloud_total": 0.0}],
                    dataset="forecasts", site_id="nerpio", root=tmp_path)

    verdad = observed_clear_hours(since="2026-01-01", root=tmp_path)
    assert verdad, "la hora del fotometro tiene que llegar a la verdad terreno"
    # 21.6 contra el modelo es un cielo limpio; -16.3 no es un cielo, es un ADU.
    # Si la allsky entrara en la mediana, la hora saldria "no despejada".
    assert all(verdad.values()), (
        "una hora de fotometro limpio se ha etiquetado como nublada: la "
        "magnitud instrumental de la allsky se ha colado en la mediana")
