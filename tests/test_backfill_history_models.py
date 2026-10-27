"""The backfill by lead time for any model, and the fence around ECMWF's curve.

Two things are pinned here. The worker's ECMWF rows must come out exactly as
they did before `--model` existed: 2.5 years of archive and the calibration
rest on them. And once ICON, GFS and Meteo-France have their own `_prevNd`
rows, nothing that belongs to ECMWF may pick them up: until 2026-09-18 the
calibration trained on `model LIKE '%prev%'` and the scoreboard applied ECMWF's
curve to anything with "prev" in its name. That fails without an error -- the
curve just drifts towards a blend of four models -- so it is fenced by tests.
"""

import datetime as dt

import numpy as np
import pytest

from astroweather import calibration as C
from astroweather import scoreboard as S
from astroweather.archive import write_canonical
from astroweather.site import NERPIO
from astroweather.workers import backfill_history as B

RETRIEVED = dt.datetime(2026, 9, 18, 12, 0, tzinfo=dt.timezone.utc)
NOCHE = dt.datetime(2026, 3, 10, 1, 0, tzinfo=dt.timezone.utc)
HORA = NOCHE.replace(tzinfo=None)


def _payload(leads_with_data=(1, 2, 3)):
    """Two hours, cloud and wind only, the way the API answers: a lead the
    model does not publish comes back as a column of nulls."""
    hourly = {"time": ["2026-03-10T00:00", "2026-03-10T01:00"]}
    for day in B.LEAD_DAYS:
        if day in leads_with_data:
            hourly[f"cloud_cover_previous_day{day}"] = [10.0 * day, None]
            hourly[f"wind_speed_10m_previous_day{day}"] = [3.0, 4.0]
        else:
            hourly[f"cloud_cover_previous_day{day}"] = [None, None]
            hourly[f"wind_speed_10m_previous_day{day}"] = [None, None]
        # low/mid/high come back null for every model (probed 2026-09-18)
        hourly[f"cloud_cover_low_previous_day{day}"] = [None, None]
    return {"latitude": 38.125, "longitude": -2.3125, "elevation": 1650.0,
            "hourly": hourly}


def test_the_default_is_still_ecmwf_and_its_rows_have_not_changed():
    rows = B.to_rows(_payload(), NERPIO, RETRIEVED)
    first = rows[0]
    assert first == {
        "site_id": "nerpio",
        "model": "ecmwf_ifs025_prev1d",
        "run_utc": dt.datetime(2026, 3, 9, 0, 0, tzinfo=dt.timezone.utc),
        "valid_utc": dt.datetime(2026, 3, 10, 0, 0, tzinfo=dt.timezone.utc),
        "lead_h": 24,
        "member": None,
        "cloud_total": 10.0,
        "wind10m": 3.0,
        "provider_model_version": "ecmwf_ifs025",
        "provider_grid_lat": 38.125,
        "provider_grid_lon": -2.3125,
        "site_elevation": NERPIO.site_elevation,
        "model_grid_elevation": NERPIO.model_grid_elevation,
        "downscaling_elevation": 1650.0,
        "native_resolution_km": 25.0,
        "native_timestep_minutes": 60,
        "interpolated": False,
        "retrieved_at": RETRIEVED,
        "license_tag": "open-meteo-cc-by-nc",
        "quality_flags": "previous_runs;lead_days=1;run_hour_assumed_00z",
    }
    assert B.to_rows(_payload(), NERPIO, RETRIEVED, B.MODEL) == rows


def test_another_model_gets_its_own_name_and_no_invented_resolution():
    rows = B.to_rows(_payload(), NERPIO, RETRIEVED, "icon_seamless")
    assert {r["model"] for r in rows} == {"icon_seamless_prev1d", "icon_seamless_prev2d",
                                          "icon_seamless_prev3d"}
    assert {r["provider_model_version"] for r in rows} == {"icon_seamless"}
    # A seamless product stitches several grids: no single number is true.
    assert {r["native_resolution_km"] for r in rows} == {None}
    lead3 = [r for r in rows if r["model"] == "icon_seamless_prev3d"]
    assert [r["lead_h"] for r in lead3] == [72, 73]


def test_a_lead_the_model_does_not_publish_writes_nothing():
    """Meteo-France answers leads 4 and 5 with nulls. A row per null hour
    would be 'no data' written as if it were a forecast."""
    rows = B.to_rows(_payload(leads_with_data=(1, 2, 3)), NERPIO, RETRIEVED,
                     "meteofrance_seamless")
    assert not any(r["model"].endswith(("prev4d", "prev5d")) for r in rows)
    # The hour with cloud null but wind present still carries the wind.
    second = [r for r in rows if r["valid_utc"].hour == 1 and r["model"].endswith("prev1d")]
    assert second and "cloud_total" not in second[0] and second[0]["wind10m"] == 4.0


def test_gfs_rounding_past_the_range_is_clamped_and_flagged_but_nonsense_still_fails():
    """GFS publishes 101 % and -1 % now and then; the write boundary rejected
    the whole month over it. Rounding is clamped and said; a unit error is not."""
    payload = _payload(leads_with_data=(1,))
    payload["hourly"]["cloud_cover_previous_day1"] = [101.0, 100.0]
    rows = B.to_rows(payload, NERPIO, RETRIEVED, "gfs_seamless")
    first, second = sorted(rows, key=lambda r: r["valid_utc"])
    assert first["cloud_total"] == 100.0
    assert first["quality_flags"].endswith(";cloud_clamped_to_range")
    assert not second["quality_flags"].endswith("clamped_to_range")

    payload["hourly"]["cloud_cover_previous_day1"] = [-1.0, 0.0]
    rows = B.to_rows(payload, NERPIO, RETRIEVED, "gfs_seamless")
    first = sorted(rows, key=lambda r: r["valid_utc"])[0]
    assert first["cloud_total"] == 0.0 and first["quality_flags"].endswith("clamped_to_range")

    payload["hourly"]["cloud_cover_previous_day1"] = [150.0, 100.0]
    rows = B.to_rows(payload, NERPIO, RETRIEVED, "gfs_seamless")
    assert max(r.get("cloud_total", 0) for r in rows) == 150.0   # left for the boundary


def test_run_passes_the_model_to_the_api_and_the_rows(monkeypatch):
    asked, written = [], []
    monkeypatch.setattr(B, "fetch", lambda site, a, b, model: asked.append(model) or _payload())
    monkeypatch.setattr(B, "store_raw", lambda *a, **k: None)
    monkeypatch.setattr(B, "write_canonical", lambda rows, **k: written.extend(rows))
    monkeypatch.setattr(B.time, "sleep", lambda s: None)

    B.run(start=dt.date(2026, 3, 1), end=dt.date(2026, 3, 31), model="gfs_seamless")
    assert asked == ["gfs_seamless"]
    assert written and all(r["model"].startswith("gfs_seamless_prev") for r in written)


# --- the fence around ECMWF's curve ------------------------------------------

def _fila(model, cloud, valid=NOCHE, lead_h=0):
    return {"site_id": "nerpio", "model": model, "member": None,
            "run_utc": valid - dt.timedelta(hours=lead_h), "valid_utc": valid,
            "lead_h": lead_h, "cloud_total": cloud}


def _archivo(tmp_path, filas):
    write_canonical(filas, dataset="forecasts", site_id="nerpio", root=tmp_path)
    # `connect()` mounts the measurements view too and fails without it.
    write_canonical([{"site_id": "nerpio", "t_utc": NOCHE, "sky_mag_raw": 21.6,
                      "instrument_type": "TESS", "instrument_id": "stars289",
                      "bandpass_id": "tess-w"}],
                    dataset="measurements", site_id="nerpio", root=tmp_path)
    return tmp_path


def test_is_backfill_of_matches_only_the_ecmwf_backfill():
    assert C.is_backfill_of("ecmwf_ifs025_prev1d")
    assert C.is_backfill_of("ecmwf_ifs025_prev5d")
    for other in ("ecmwf_ifs025", "icon_seamless_prev1d", "gfs_seamless_prev3d",
                  "meteofrance_seamless_prev2d", "weathernext3", "ecmwf_ifs025_prevd",
                  "ecmwf_ifs025_prev1dx", "ecmwf_ifs_prev1d"):
        assert not C.is_backfill_of(other), other


def test_ecmwf_training_pairs_ignore_every_other_backfill(tmp_path):
    raiz = _archivo(tmp_path, [
        _fila("ecmwf_ifs025_prev2d", 10.0),
        _fila("icon_seamless_prev2d", 90.0),
        _fila("gfs_seamless_prev5d", 80.0),
        _fila("meteofrance_seamless_prev1d", 70.0),
        # The live run of the SAME model does count since 26-10-2026: the
        # backfill was downloaded once and does not grow, so a curve retrained
        # every week needs it (calibration.training_rows). Since M16 (27-10)
        # its lead is BY RETRIEVAL: fetched on 08-03 at 10 UTC, it is what was
        # known at 15 UTC the day before the night of 09-03, lead 1.
        {**_fila("ecmwf_ifs025", 60.0, lead_h=30),
         "retrieved_at": dt.datetime(2026, 3, 8, 10, tzinfo=dt.timezone.utc)},
        _fila("weathernext3", 50.0, lead_h=50),
    ])
    assert C.training_pairs({HORA: True}, raiz) == [(60.0, 1.0, True), (10.0, 2.0, True)]


def _marcador(tmp_path, monkeypatch):
    """30 synthetic nights where ICON says the opposite of ECMWF."""
    filas, verdad, ecmwf = [], {}, []
    rng = np.random.default_rng(4)
    for dia in range(30):
        valida = NOCHE + dt.timedelta(days=dia)
        nube = float(rng.uniform(0, 100))
        verdad[valida.replace(tzinfo=None)] = bool(nube < 50)
        ecmwf.append(nube)
        filas.append(_fila("ecmwf_ifs025_prev1d", nube, valid=valida))
        filas.append(_fila("icon_seamless_prev1d", 100.0 - nube, valid=valida))
    _archivo(tmp_path, filas)
    curva = C.Calibrator(intercept=2.0, cloud_weight=-4.0, lead_weight=0.0,
                         trained_on=500, brier=0.1, brier_climatology=0.2,
                         base_rate=0.7, fitted_at="x")
    curva.save(tmp_path / "cloud_calibration.json")
    monkeypatch.setattr(C, "MODEL_PATH", tmp_path / "cloud_calibration.json")
    monkeypatch.setattr(S, "observed_clear_hours", lambda since, root: verdad)
    esperado = np.array([curva.probability(n, 24.0) for n in ecmwf])
    resultado = np.array([1.0 if verdad[(NOCHE + dt.timedelta(days=d)).replace(tzinfo=None)]
                          else 0.0 for d in range(30)])
    return S.cloud_scores(root=tmp_path)["models"], float(((esperado - resultado) ** 2).mean())


def test_the_calibrated_row_is_ecmwf_through_ecmwf_curve_and_nothing_else(tmp_path, monkeypatch):
    modelos, brier_solo_ecmwf = _marcador(tmp_path, monkeypatch)
    calibrado = [m for m in modelos if m["model"] == "calibrado (nuestro)"]
    assert len(calibrado) == 1 and calibrado[0]["lead_days"] == 1
    # With ICON's rows averaged in, the probability of each hour is the mean
    # of ECMWF's and its mirror image, and this Brier moves.
    # (cloud_total is stored as float32, hence the tolerance.)
    assert calibrado[0]["brier"] == pytest.approx(brier_solo_ecmwf, abs=1e-6)


def test_other_backfills_are_scored_under_their_own_name_not_as_ecmwf(tmp_path, monkeypatch):
    modelos, _ = _marcador(tmp_path, monkeypatch)
    icon = [m for m in modelos if m["model"] == "icon_seamless_prev1d"]
    assert len(icon) == 1 and icon[0]["lead_days"] == 1      # lead parsed generically
    # `decay_with_lead` is printed as "ECMWF crudo": ICON must not be in it.
    for entry in modelos:
        entry["enough"] = True
    decay = {e["model"] for e in S.decay_with_lead(modelos)}
    assert "icon_seamless_prev1d" not in decay
    assert {"ecmwf_ifs025_prev1d", "calibrado (nuestro)"} <= decay


def test_lead_days_reads_any_models_backfill():
    assert S._lead_days("icon_seamless_prev4d", 0) == 4
    assert S._lead_days("meteofrance_seamless_prev3d", 999) == 3
    assert S._lead_days("gfs_seamless", 49) == 2


def test_the_wind_correction_never_picks_a_backfill(monkeypatch):
    """Tied on pairs with WeatherNext, `meteofrance_seamless_prev2d` came out
    first and its ratio went into the published wind warning."""
    from astroweather import verification as V

    def _s(model, n, ratio):
        return {"model": model, "n": n, "bias": -1.0, "rmse": 1.0, "observed": 4.0,
                "forecast": 4.0 * ratio, "ratio": ratio, "trustworthy": n >= 200}

    monkeypatch.setattr(V, "wind_scores", lambda root=None: [
        _s("meteofrance_seamless_prev2d", 300, 0.43),
        _s("ecmwf_ifs025_prev1d", 300, 0.50),
        _s("ecmwf_ifs", 250, 0.58)])
    # Aunque los retroanalisis publicaran racha, no se eligen sin pedirlos.
    monkeypatch.setattr(V, "_models_with_gust", lambda root=None: {
        "meteofrance_seamless_prev2d", "ecmwf_ifs025_prev1d", "ecmwf_ifs"})
    auto = V.wind_correction()
    assert auto["ratio"] == 0.58 and auto["n"] == 250
    # Asked for by name, a backfill can still be scored.
    assert V.wind_correction(model="ecmwf_ifs025_prev1d")["ratio"] == 0.50
