"""M13: la allsky de AstroCamp corta a una hora del RELOJ LOCAL, no de UTC.

La ventana de verdad (`nighttruth.allsky_hour_valid`) llega hasta las 05:00
locales, y el aviso de salud solo salta si el corte cae dentro de ella.
"""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pyarrow.parquet as pq

from astroweather import health
from astroweather import nighttruth as NT

UTC = dt.timezone.utc


def _valid_utc_hours(day: dt.date) -> list[int]:
    base = dt.datetime(day.year, day.month, day.day, tzinfo=UTC)
    return [h for h in range(24) if NT.allsky_hour_valid(base.replace(hour=h))]


def test_en_verano_la_ultima_casilla_es_la_de_las_02_utc():
    hours = _valid_utc_hours(dt.date(2026, 10, 15))      # CEST, UTC+2
    assert 2 in hours and 3 not in hours


def test_en_invierno_entra_la_de_las_03_utc():
    # Lo que tiraba la ventana fija 19-02 UTC desde el cambio de hora del 25-10.
    hours = _valid_utc_hours(dt.date(2026, 10, 27))      # CET, UTC+1
    assert 3 in hours and 4 not in hours


def test_las_horas_de_tarde_siempre_valen():
    for day in (dt.date(2026, 7, 1), dt.date(2026, 12, 21)):
        hours = _valid_utc_hours(day)
        assert all(h in hours for h in (17, 18, 19, 20, 21, 22, 23, 0, 1))


def test_sensor_truths_usa_la_hora_local():
    winter = dt.datetime(2026, 10, 27, 3, tzinfo=UTC)
    summer = dt.datetime(2026, 10, 15, 3, tzinfo=UTC)
    eph = {winter: (-30.0, 0.0), summer: (-30.0, 0.0)}
    T = NT.sensor_truths({}, {}, {winter: 400.0, summer: 400.0}, eph, lambda h: True)
    assert winter in T["allsky"] and summer not in T["allsky"]


def _write_allsky(root, cut_local: dt.time, nights: list[dt.date]):
    rows_t, rows_s = [], []
    for n in nights:
        start = dt.datetime(n.year, n.month, n.day, 17, tzinfo=UTC)
        for i in range(0, 14 * 60, 2):                       # cada 2 min, 17 -> 07 UTC
            t = start + dt.timedelta(minutes=i)
            local = t.astimezone(NT.ALLSKY_TZ)
            morning = local.hour < 12
            dead = morning and local.time() >= cut_local
            rows_t.append(t)
            rows_s.append(0 if dead else 600)
    d = root / "measurements" / "site=nerpio" / "year=2026" / "month=11"
    d.mkdir(parents=True)
    table = pa.table({"t_utc": pa.array(rows_t, pa.timestamp("us", tz="UTC")),
                      "instrument_type": ["ALLSKY"] * len(rows_t),
                      "star_count": pa.array(rows_s, pa.int64())})
    pq.write_table(table, d / "allsky.parquet")


NOW = dt.datetime(2026, 11, 10, 12, tzinfo=UTC)
NIGHTS = [NOW.date() - dt.timedelta(days=i) for i in range(health.ALLSKY_NIGHTS, 0, -1)]


def test_el_corte_habitual_de_la_camara_no_avisa(tmp_path):
    _write_allsky(tmp_path, dt.time(5, 0), NIGHTS)
    block = health.allsky_cut_block(tmp_path, NOW)
    assert block["noches_con_corte"] >= health.ALLSKY_CUT_WARN
    assert block["noches_con_corte_temprano"] == 0
    assert block["nivel"] == "ok"
    assert block["hora_corte_local"] == 5


def test_un_corte_dentro_de_la_ventana_de_verdad_avisa(tmp_path):
    # Control en rojo: la camara se apaga a las 02:00 locales.
    _write_allsky(tmp_path, dt.time(2, 0), NIGHTS)
    block = health.allsky_cut_block(tmp_path, NOW)
    assert block["noches_con_corte_temprano"] >= health.ALLSKY_CUT_WARN
    assert block["nivel"] == "aviso"
