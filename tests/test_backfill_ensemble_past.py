"""El rescate por `past_days` no puede pasar por una pasada del EPS.

14-11-2026: las pasadas del 11 al 14 se perdieron y `past_days` es lo unico que
queda de esas horas, pero es la serie "ultima pasada de cada hora", no una
pasada con su alcance. Estas pruebas fijan que las filas (a) salen marcadas,
(b) no tienen alcance ni pasada inventados y (c) ningun lector del EPS las
confunde con `ecmwf_ifs025`.
"""

import datetime as dt
import glob
import gzip
import json
import shutil
import subprocess
from pathlib import Path

import duckdb
import pytest

from astroweather import verification
from astroweather.archive import write_canonical
from astroweather.asof import raw_ensemble_asof
from astroweather.site import NERPIO
from astroweather.workers import backfill_ensemble_past as bf
from astroweather.workers.fetch_ensemble import to_rows

UTC = dt.timezone.utc
RETRIEVED = dt.datetime(2026, 11, 14, 10, 1, 50, tzinfo=UTC)
REPO = Path(__file__).resolve().parent.parent


def _payload(precip_member7: float = 0.0) -> dict:
    """Respuesta de past_days: 6 horas; las dos primeras con miembros a nulo (ya
    caducados), 3 pasadas y 1 futura respecto a RETRIEVED (10:01 -> corte 10:00)."""
    times = ["2026-11-09T22:00", "2026-11-09T23:00", "2026-11-10T00:00",
             "2026-11-14T08:00", "2026-11-14T09:00", "2026-11-14T10:00"]
    hourly: dict = {"time": times}
    for member in range(51):
        suffix = "" if member == 0 else f"_member{member:02d}"
        hourly[f"cloud_cover{suffix}"] = [None, None, 10.0, 20.0, 30.0, 40.0]
        hourly[f"precipitation{suffix}"] = [None, None, 0.0, 0.0, 0.0, 0.0]
    hourly["precipitation_member07"] = [None, None, 0.0, precip_member7, 0.0, 0.0]
    return {"latitude": 38.25, "longitude": -2.25, "elevation": 1650.0, "hourly": hourly}


def test_rows_are_marked_and_carry_no_invented_lead_or_run():
    rows = bf.to_backfill_rows(_payload(), NERPIO, RETRIEVED)

    assert len(rows) == 51 * 3                      # 3 horas pasadas con miembros
    valid = {r["valid_utc"] for r in rows}
    assert min(valid) == dt.datetime(2026, 11, 10, 0, tzinfo=UTC)
    assert max(valid) == dt.datetime(2026, 11, 14, 9, tzinfo=UTC)   # la de las 10 es futura
    for r in rows:
        assert r["model"] == "ecmwf_ifs025_pastdays"
        assert r["lead_h"] is None                  # ningun alcance inventado
        assert r["run_utc"] == dt.datetime(2026, 11, 14, 10, tzinfo=UTC)
        assert r["run_utc"] > r["valid_utc"]        # un filtro run<=valid las excluye
        assert r["retrieved_at"] == RETRIEVED       # el instante real de la peticion
        assert r["quality_flags"].startswith("backfill_past_days;lead_unknown")


def test_clamp_flag_is_kept_alongside_the_backfill_flag():
    rows = bf.to_backfill_rows(_payload(-0.1), NERPIO, RETRIEVED)
    flagged = [r for r in rows if "precip_clamped_to_zero" in r["quality_flags"]]
    assert len(flagged) == 1
    assert flagged[0]["quality_flags"].startswith("backfill_past_days;")
    assert flagged[0]["precipitation"] == 0.0


def _con(root):
    """Conexion sin las vistas de `queries.connect` (piden `measurements/`); el
    SQL de las pruebas lee el Parquet crudo, que es lo que lee `asof`."""
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"CREATE VIEW forecasts AS SELECT * FROM "
                f"read_parquet('{root.as_posix()}/forecasts/**/*.parquet', union_by_name=true)")
    return con


def _write(rows, root):
    write_canonical(rows, dataset="forecasts", site_id=NERPIO.site_id, root=root)


def test_ensemble_readers_do_not_see_the_backfill(tmp_path):
    """Control positivo: las MISMAS filas con model='ecmwf_ifs025' si las lee el
    lector; con el nombre del rescate, no."""
    since = dt.datetime(2026, 11, 1, tzinfo=UTC)

    control = tmp_path / "control"
    rows = bf.to_backfill_rows(_payload(), NERPIO, RETRIEVED)
    for r in rows:
        r["model"] = "ecmwf_ifs025"
    _write(rows, control)
    seen = raw_ensemble_asof(_con(control), control, "ecmwf_ifs025", since)
    assert len(seen.by_hour) == 3                   # el instrumento ve filas (en verde)

    real = tmp_path / "real"
    _write(bf.to_backfill_rows(_payload(), NERPIO, RETRIEVED), real)
    seen = raw_ensemble_asof(_con(real), real, "ecmwf_ifs025", since)
    assert len(seen.by_hour) == 0                   # y con el nombre del rescate, ninguna
    # ni las lecturas deterministas (member IS NULL) ni la vista las ven como EPS
    con = _con(real)
    assert con.execute("SELECT count(*) FROM forecasts WHERE member IS NULL").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM forecasts WHERE model='ecmwf_ifs025'").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM forecasts WHERE lead_h IS NOT NULL").fetchone()[0] == 0


def test_verification_report_survives_a_null_lead(monkeypatch):
    monkeypatch.setattr(verification, "cloud_scores", lambda root=None: [
        {"model": "ecmwf_ifs025_pastdays", "lead_h": None, "n": 100, "brier": 0.2}])
    monkeypatch.setattr(verification, "temperature_scores", lambda root=None: [])
    text = verification.report()
    assert "ecmwf_ifs025_pastdays" in text and "lead   ?h" in text


def test_run_from_saved_raw_writes_only_flagged_rows(tmp_path):
    raw = tmp_path / "20261114T100150Z_abcdef0123456789.json.gz"
    with gzip.open(raw, "wb") as handle:
        handle.write(json.dumps(_payload()).encode())
    n = bf.run(from_raw=raw, root=tmp_path / "out")
    assert n == 51 * 3
    con = _con(tmp_path / "out")
    assert con.execute("SELECT count(DISTINCT model), min(model) FROM forecasts").fetchone() == (
        1, "ecmwf_ifs025_pastdays")


def test_ordinary_to_rows_is_untouched():
    """`fetch_ensemble.to_rows` sigue dando pasada y alcance de verdad."""
    rows = to_rows(_payload(), NERPIO, RETRIEVED)
    assert {r["model"] for r in rows} == {"ecmwf_ifs025"}
    assert all(r["lead_h"] is not None for r in rows)


def test_raw_path_is_not_git_ignored():
    """Sin la excepcion del .gitignore el raw del rescate se perderia con el disco."""
    if shutil.which("git") is None:
        pytest.skip("sin git")
    probe = "data/raw/site=nerpio/source=openmeteo_ensemble_past_days/year=2026/month=11/x.json.gz"
    done = subprocess.run(["git", "check-ignore", "-q", probe], cwd=REPO)
    assert done.returncode == 1                     # 1 = no ignorado
    control = subprocess.run(["git", "check-ignore", "-q", "data/raw/site=nerpio/source=otra/x"],
                             cwd=REPO)
    assert control.returncode == 0                  # el control: una fuente cualquiera SI se ignora


_SAVED = sorted(glob.glob(str(REPO / "data/raw/site=nerpio/source=openmeteo_ensemble_past_days/**/*.json.gz"),
                          recursive=True))


@pytest.mark.skipif(not _SAVED, reason="el raw del rescate no esta en esta copia")
def test_saved_real_raw_recovers_members_from_2026_11_10():
    path = Path(_SAVED[0])
    payload = json.loads(gzip.open(path).read())
    rows = bf.to_backfill_rows(payload, NERPIO, bf._retrieved_from_name(path))
    hours = sorted({r["valid_utc"] for r in rows})
    assert hours[0] == dt.datetime(2026, 11, 10, tzinfo=UTC)
    assert {r["member"] for r in rows} == set(range(51))
    assert len(rows) == 51 * len(hours)             # ninguna hora a medias
