"""Unit tests for fetch_weathernext.py without external network or BigQuery dependencies."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

# Ensure repo root and coop/gemini are importable
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GEMINI_DIR = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(GEMINI_DIR) not in sys.path:
    sys.path.insert(0, str(GEMINI_DIR))

from astroweather.archive import write_canonical
from astroweather.schema import FORECAST_SCHEMA
from astroweather.site import NERPIO

from fetch_weathernext import (
    MODEL,
    STATS,
    _rows,
    build_query,
    derive_rh2m,
    derive_wind_direction,
    run,
)


@pytest.fixture
def fixture_records() -> list[dict]:
    path = GEMINI_DIR / "fixture_weathernext_records.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def retrieved_at() -> datetime:
    return datetime(2026, 9, 7, 22, 0, 0, tzinfo=timezone.utc)


def test_rows_count_and_models(fixture_records, retrieved_at):
    """1. _rows sobre el fixture produce 6 modelos x 6 horas = 36 filas."""
    rows = _rows(fixture_records, NERPIO, retrieved_at)
    assert len(rows) == 36

    expected_models = {
        "weathernext3",
        "weathernext3_p10",
        "weathernext3_p25",
        "weathernext3_p50",
        "weathernext3_p75",
        "weathernext3_p90",
    }
    models = {r["model"] for r in rows}
    assert models == expected_models

    for model in expected_models:
        model_rows = [r for r in rows if r["model"] == model]
        assert len(model_rows) == 6
        assert all(r["member"] is None for r in model_rows)


def test_rows_column_names(fixture_records, retrieved_at):
    """2. Cada fila solo tiene columnas de FORECAST_SCHEMA.names."""
    rows = _rows(fixture_records, NERPIO, retrieved_at)
    schema_names = set(FORECAST_SCHEMA.names)

    for i, row in enumerate(rows):
        unknown_cols = set(row.keys()) - schema_names
        assert not unknown_cols, f"Fila {i} contiene columnas desconocidas: {unknown_cols}"


def test_unit_conversions(fixture_records, retrieved_at):
    """3. Conversiones:
    fracción 0.42 -> 42.0 %
    283.15 K -> 10.0 °C
    0.0012 m -> 1.2 mm
    101325 Pa -> 1013.25 hPa
    """
    rows = _rows(fixture_records, NERPIO, retrieved_at)
    row_h1_mean = next(
        r for r in rows if r["model"] == "weathernext3" and r["lead_h"] == 1
    )

    assert row_h1_mean["cloud_total"] == 42.0
    assert row_h1_mean["t2m"] == 10.0
    assert row_h1_mean["precipitation"] == 1.2
    assert row_h1_mean["pressure_msl"] == 1013.25

    # Check derived fields are plausible
    assert 0.0 <= row_h1_mean["rh2m"] <= 100.0
    assert 0.0 <= row_h1_mean["wind_dir10m"] < 360.0


def test_lead_h_invariant_and_manipulated_fixture(fixture_records, retrieved_at):
    """4. lead_h coincide con diferencia de tiempos; fixture manipulado lanza ValueError."""
    rows = _rows(fixture_records, NERPIO, retrieved_at)
    for r in rows:
        expected_lead = int((r["valid_utc"] - r["run_utc"]).total_seconds() // 3600)
        assert r["lead_h"] == expected_lead

    # Fixture manipulado con hours incoherente
    manipulated = copy.deepcopy(fixture_records)
    manipulated[0]["forecast"][0]["hours"] = 99
    with pytest.raises(ValueError, match="Incoherent lead_h"):
        _rows(manipulated, NERPIO, retrieved_at)


def test_build_query_structure():
    """5. build_query con init_time=None contiene subconsulta MAX(init_time) y con fecha dada, parámetro; ambos ST_CONTAINS."""
    # init_time is None
    sql_none, params_none = build_query("weathernext_3_0_0_0p1deg", NERPIO, None, max_hours=48)
    assert "MAX(init_time)" in sql_none
    assert "ST_CONTAINS" in sql_none
    assert "init_time" not in params_none
    assert params_none["max_hours"] == 48

    # init_time is provided
    dt = datetime(2026, 9, 7, 18, 0, 0, tzinfo=timezone.utc)
    sql_dt, params_dt = build_query("weathernext_3_0_0_0p1deg", NERPIO, dt, max_hours=48)
    assert "@init_time" in sql_dt
    assert "ST_CONTAINS" in sql_dt
    assert params_dt.get("init_time") == dt


def test_run_missing_env_raises_runtime_error(monkeypatch):
    """6. run() sin GOOGLE_CLOUD_PROJECT ni WEATHERNEXT_BQ_TABLE lanza RuntimeError."""
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("WEATHERNEXT_BQ_TABLE", raising=False)
    with pytest.raises(RuntimeError, match="Faltan variables de entorno"):
        run()


def test_write_canonical_integration(fixture_records, retrieved_at, tmp_path):
    """Integración con write_canonical: las filas superan la frontera de escritura y Parquet."""
    rows = _rows(fixture_records, NERPIO, retrieved_at)
    output_path = write_canonical(rows, dataset="forecasts", site_id=NERPIO.site_id, root=tmp_path)
    assert output_path is not None
    assert output_path.exists()
