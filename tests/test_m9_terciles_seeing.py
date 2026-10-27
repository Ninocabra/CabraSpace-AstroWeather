"""M9: los terciles del seeing salen de la distribucion propia del modelo en el
ultimo anio (`seeingterciles.py`) y `seeing.py` los usa, con respaldo fijo."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from astroweather import archive, archivepush, seeing, seeingterciles
from astroweather.seeing import integrate

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 27, 12, tzinfo=UTC)


def _archive(root: Path, jets_by_night: list[float]):
    """Un archivo minimo: una pasada con el jet constante en cada noche."""
    t, valid, run, v250 = [], [], [], []
    for i, jet in enumerate(jets_by_night):
        night = (NOW - dt.timedelta(days=len(jets_by_night) - i)).replace(hour=19)
        for h in range(11):
            stamp = night + dt.timedelta(hours=h)
            valid.append(stamp)
            run.append(stamp.replace(hour=0) - dt.timedelta(days=1))
            v250.append(float(jet))
    n = len(valid)
    d = root / "forecasts" / "site=nerpio"
    d.mkdir(parents=True)
    ts = pa.timestamp("us", tz="UTC")
    pq.write_table(pa.table({
        "site_id": ["nerpio"] * n, "model": ["ecmwf_ifs025"] * n, "member": [0] * n,
        "run_utc": pa.array(run, ts), "valid_utc": pa.array(valid, ts),
        "retrieved_at": pa.array(run, ts),
        "v200": pa.array([None] * n, pa.float64()), "v250": pa.array(v250, pa.float64()),
        "v300": pa.array([None] * n, pa.float64())}), d / "f.parquet")
    m = root / "measurements" / "site=nerpio"
    m.mkdir(parents=True)
    pq.write_table(pa.table({"instrument_id": ["x"], "t_utc": pa.array([NOW], ts),
                             "pipeline_version": ["v"], "retrieved_at": pa.array([NOW], ts)}),
                   m / "m.parquet")


def test_los_cortes_son_los_terciles_de_las_noches(tmp_path):
    jets = list(np.linspace(5, 45, 60))
    _archive(tmp_path, jets)
    record = seeingterciles.fit(tmp_path, NOW)
    values = np.array([integrate(j).seeing_arcsec for j in jets])
    assert record["nights"] == 60
    assert record["good"] == pytest.approx(np.percentile(values, 100 / 3), abs=1e-3)
    assert record["poor"] == pytest.approx(np.percentile(values, 200 / 3), abs=1e-3)
    low = (values <= record["good"]).mean()
    high = (values >= record["poor"]).mean()
    assert 0.28 < low < 0.39 and 0.28 < high < 0.39


def test_con_pocas_noches_no_se_escribe_nada(tmp_path, monkeypatch):
    _archive(tmp_path, [20.0] * (seeingterciles.MIN_NIGHTS - 1))
    assert seeingterciles.fit(tmp_path, NOW) is None
    msg = seeingterciles.refit(tmp_path, NOW)
    assert "respaldo" in msg
    assert not (tmp_path / seeingterciles.TERCILES_FILE).exists()


def _with_file(monkeypatch, tmp_path, record):
    monkeypatch.setattr(archive, "DATA_ROOT", tmp_path)
    (tmp_path / seeingterciles.TERCILES_FILE).write_text(json.dumps(record), encoding="utf-8")
    seeing._current_terciles_from_file.cache_clear()


def test_seeing_lee_el_fichero(tmp_path, monkeypatch):
    _with_file(monkeypatch, tmp_path, {"good": 0.956, "poor": 1.006, "nights": 62})
    assert seeing._current_terciles_from_file() == (0.956, 1.006)
    seeing._current_terciles_from_file.cache_clear()


@pytest.mark.parametrize("record", [
    {"good": 1.1, "poor": 1.0, "nights": 62},        # cortes al reves
    {"good": 0.95, "poor": 1.0, "nights": 5},        # pocas noches
    {"good": "x", "poor": 1.0, "nights": 62},        # ilegible
])
def test_un_fichero_raro_vuelve_al_respaldo(tmp_path, monkeypatch, record):
    _with_file(monkeypatch, tmp_path, record)
    assert seeing._current_terciles_from_file() == (seeing.TERCILE_GOOD, seeing.TERCILE_POOR)
    seeing._current_terciles_from_file.cache_clear()


def test_el_tercil_usa_los_cortes_vigentes(monkeypatch):
    # Control: con los cortes fijos, un chorro de 25 m/s salia "favorable".
    assert integrate(25.0).tercile == "favorable"
    monkeypatch.setattr(seeing, "current_terciles", lambda: (0.956, 1.006))
    assert integrate(25.0).tercile == "desfavorable"     # 1,07"
    assert integrate(5.0).tercile == "favorable"         # 0,91"
    assert integrate(17.0).tercile == "normal"           # 0,98"


def test_el_fichero_viaja_al_archivo_y_vuelve_al_runner():
    assert seeingterciles.TERCILES_FILE in archivepush.LOOSE
    workflow = (Path(__file__).resolve().parent.parent / ".github" / "workflows"
                / "colector.yml").read_text(encoding="utf-8")
    assert seeingterciles.TERCILES_FILE in workflow
