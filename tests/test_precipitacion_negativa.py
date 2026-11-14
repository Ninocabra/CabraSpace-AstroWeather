"""Un -0.1 mm del proveedor no puede tumbar el archivo del EPS.

Regresion del 11-11-2026: Open-Meteo devolvio `precipitation=-0.1` en un miembro
del ensemble y `write_canonical` rechazo el lote entero (`forecasts[3922]`);
`archivo-eps.yml` fallo seis veces seguidas y esas pasadas, que no se pueden
volver a bajar, se perdieron. El arreglo vive en el adaptador (recortar a 0 los
negativos pequenos); el validador de escritura NO se afloja, y un negativo
grande sigue siendo un error de unidades que tiene que fallar.
"""

from datetime import datetime, timezone

import duckdb
import pytest

from astroweather.archive import write_canonical
from astroweather.normalize import clamp_small_negative
from astroweather.site import NERPIO
from astroweather.workers import fetch_forecast
from astroweather.workers.fetch_ensemble import to_rows

RETRIEVED = datetime(2026, 11, 11, 6, 43, tzinfo=timezone.utc)


def _payload(precip_member7: float) -> dict:
    """Respuesta minima de Open-Meteo: 51 miembros, 3 horas, lluvia 0 salvo una."""
    hourly: dict = {"time": ["2026-11-11T00:00", "2026-11-11T01:00", "2026-11-11T02:00"]}
    for member in range(51):
        suffix = "" if member == 0 else f"_member{member:02d}"
        hourly[f"cloud_cover{suffix}"] = [10.0, 20.0, 30.0]
        hourly[f"precipitation{suffix}"] = [0.0, 0.0, 0.0]
    hourly["precipitation_member07"] = [0.0, precip_member7, 0.0]
    return {"latitude": 41.9, "longitude": 0.7, "elevation": 700.0, "hourly": hourly}


def _precipitation_on_disk(root) -> list[float]:
    rows = duckdb.sql(
        f"SELECT precipitation FROM read_parquet('{root.as_posix()}/forecasts/**/*.parquet')"
    ).fetchall()
    return [r[0] for r in rows]


def test_tiny_negative_is_archived_as_zero(tmp_path):
    rows = to_rows(_payload(-0.1), NERPIO, RETRIEVED)

    write_canonical(rows, dataset="forecasts", site_id=NERPIO.site_id, root=tmp_path)

    values = _precipitation_on_disk(tmp_path)
    assert len(values) == 51 * 3
    assert min(values) == 0.0            # nada negativo llego al Parquet
    flagged = duckdb.sql(
        f"SELECT count(*) FROM read_parquet('{tmp_path.as_posix()}/forecasts/**/*.parquet') "
        f"WHERE quality_flags = 'precip_clamped_to_zero'"
    ).fetchone()[0]
    assert flagged == 1                  # y la fila recortada queda marcada


def test_clearly_wrong_negative_still_raises(tmp_path):
    """-5 mm es un error de unidades, no un artefacto: el validador sigue parandolo."""
    rows = to_rows(_payload(-5.0), NERPIO, RETRIEVED)

    with pytest.raises(ValueError, match="precipitation=-5.0 fuera de"):
        write_canonical(rows, dataset="forecasts", site_id=NERPIO.site_id, root=tmp_path)


@pytest.mark.parametrize("value, expected, was_clamped", [
    (-0.1, 0.0, True),
    (-0.5, 0.0, True),        # el margen es inclusivo
    (-0.51, -0.51, False),    # justo fuera: intacto, lo rechaza el validador
    (0.0, 0.0, False),
    (3.2, 3.2, False),
    (None, None, False),
])
def test_clamp_small_negative_boundaries(value, expected, was_clamped):
    assert clamp_small_negative(value) == (expected, was_clamped)


def test_deterministic_forecast_shares_the_same_rule(tmp_path):
    """fetch_forecast usa la misma frontera: mismo artefacto, mismo tratamiento."""
    hourly = {
        "time": ["2026-11-11T00:00", "2026-11-11T01:00"],
        "precipitation": [-0.1, 0.4],
    }
    payload = {"latitude": 41.9, "longitude": 0.7, "elevation": 700.0, "hourly": hourly}

    rows = fetch_forecast._rows(payload, NERPIO, ["ecmwf_ifs"], RETRIEVED)

    assert [r["precipitation"] for r in rows] == [0.0, 0.4]
    write_canonical(rows, dataset="forecasts", site_id=NERPIO.site_id, root=tmp_path)
