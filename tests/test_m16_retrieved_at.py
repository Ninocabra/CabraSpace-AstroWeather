"""M16 (verificacion 26-10-2026, seccion 10): elegir la prevision por
`retrieved_at`, no por la etiqueta de pasada.

Para los modelos de Open-Meteo `run_utc` es el DIA truncado a 00Z
(`workers/fetch_forecast.py`), y la vista `forecasts` de `queries.py` se queda
con la ULTIMA recogida de cada etiqueta: la de las 21-23 UTC, con la noche ya
empezada. Quien pregunta "lo que se sabia a las 15 UTC" tiene que preguntar a
`asof.py`. Cada test de aqui se vio en ROJO con el codigo anterior.
"""

import datetime as dt

import pytest

from astroweather import asof
from astroweather.archive import write_canonical

UTC = dt.timezone.utc
NIGHT = dt.date(2026, 9, 20)
# Ocho horas de la noche del 20-09, dos de ellas pasada la medianoche.
HOURS = [dt.datetime(2026, 9, 20, h, tzinfo=UTC) for h in range(18, 24)] + \
        [dt.datetime(2026, 9, 21, h, tzinfo=UTC) for h in (0, 1)]


def _naive(t):
    return t.astimezone(UTC).replace(tzinfo=None)


# --- asof.py: la noche de una hora y el corte de cada alcance -------------------

def test_la_noche_de_una_hora_es_la_del_dia_en_que_empezo():
    assert asof.night_of(dt.datetime(2026, 9, 21, 2)) == NIGHT
    assert asof.night_of(dt.datetime(2026, 9, 20, 22)) == NIGHT


def test_el_corte_del_alcance_d_es_la_tarde_de_d_dias_antes():
    assert asof.issue_cutoff(NIGHT) == dt.datetime(2026, 9, 20, 15, tzinfo=UTC)
    assert asof.issue_cutoff(NIGHT, 2) == dt.datetime(2026, 9, 18, 15, tzinfo=UTC)


def test_issued_devuelve_lo_que_habia_al_corte_y_no_lo_ultimo():
    hour = dt.datetime(2026, 9, 21, 1)
    table = asof.AsOf([
        (dt.datetime(2026, 9, 19, 10, tzinfo=UTC), hour, (50.0,)),
        (dt.datetime(2026, 9, 20, 10, tzinfo=UTC), hour, (80.0,)),
        (dt.datetime(2026, 9, 20, 23, tzinfo=UTC), hour, (5.0,)),
    ])
    assert table.issued(hour)[0] == 80.0          # D0: la de las 10, no la de las 23
    assert table.issued(hour, 1)[0] == 50.0       # D1: la de la vispera
    assert table.issued(hour, 2) is None          # D2: no habia nada


# --- el archivo de juguete ------------------------------------------------------

def _row(model, valid, cloud, retrieved, member=None):
    run = dt.datetime(retrieved.year, retrieved.month, retrieved.day, tzinfo=UTC)
    return {"site_id": "nerpio", "model": model, "run_utc": run, "valid_utc": valid,
            "lead_h": int((valid - run).total_seconds() // 3600), "member": member,
            "cloud_total": cloud, "retrieved_at": retrieved}


def _archive(root, rows):
    for row in rows:
        write_canonical([row], dataset="forecasts", site_id="nerpio", root=root)
    write_canonical([{"site_id": "nerpio", "t_utc": HOURS[0], "sky_mag_raw": 21.6,
                      "instrument_type": "TESS", "instrument_id": "stars289",
                      "bandpass_id": "tess-w"}],
                    dataset="measurements", site_id="nerpio", root=root)
    return root


VISPERA = dt.datetime(2026, 9, 19, 10, tzinfo=UTC)
TARDE = dt.datetime(2026, 9, 20, 10, tzinfo=UTC)
NOCHE = dt.datetime(2026, 9, 20, 23, tzinfo=UTC)


@pytest.fixture
def archivo(tmp_path):
    rows = []
    for h in HOURS:
        rows += [_row("icon_seamless", h, 50.0, VISPERA),
                 _row("icon_seamless", h, 80.0, TARDE),
                 _row("icon_seamless", h, 5.0, NOCHE)]
        for member in range(40):
            rows += [_row("ecmwf_ifs025", h, 60.0, TARDE, member),
                     _row("ecmwf_ifs025", h, 0.0, NOCHE, member)]
    return _archive(tmp_path, rows)


# --- scoreboard.py: los plazos de los modelos en vivo ---------------------------

def _scores(root, monkeypatch):
    from astroweather import scoreboard
    monkeypatch.setattr(scoreboard, "observed_clear_hours",
                        lambda **k: {_naive(h): True for h in HOURS})
    monkeypatch.setattr(scoreboard.Calibrator, "load", classmethod(lambda cls, *a: None))
    result = scoreboard.cloud_scores(root=root)
    return {(e["model"], e["lead_days"]): e for e in result["models"]}


def test_el_marcador_puntua_lo_que_habia_a_las_15_utc(archivo, monkeypatch):
    """Antes: D0 era la recogida de las 23 UTC (5 % de nube, p = 0,95) y las
    horas de despues de medianoche caian en "D1" por la etiqueta de pasada.
    Ahora la noche entera es D0 con lo que habia al corte (80 %, p = 0,20) y
    D1 es lo de la vispera (50 %)."""
    scores = _scores(archivo, monkeypatch)
    d0 = scores[("icon_seamless", 0)]
    assert d0["brier"] == pytest.approx((1 - 0.20) ** 2)   # antes (1 - 0,95)^2
    assert d0["n"] == len(HOURS)                           # antes 6: sin las de madrugada
    d1 = scores[("icon_seamless", 1)]
    assert d1["n"] == len(HOURS)
    assert d1["brier"] == pytest.approx((1 - 0.50) ** 2)


def test_la_media_del_eps_tambien_por_recogida(archivo, monkeypatch):
    """Los miembros del EPS caian en la misma fila que el determinista y con la
    misma etiqueta. Ahora van aparte y al corte: 60 % (p = 0,40), no el 0 % de
    las 23 UTC."""
    scores = _scores(archivo, monkeypatch)
    eps = scores[("ecmwf_ifs025_eps", 0)]
    assert eps["n"] == len(HOURS)
    assert eps["brier"] == pytest.approx((1 - 0.40) ** 2)


# --- queries.py: la vista, preguntada a una hora --------------------------------

def test_la_vista_preguntada_a_las_15_utc_no_ve_la_recogida_de_la_noche(archivo):
    from astroweather.queries import connect
    sql = ("SELECT cloud_total FROM forecasts WHERE model = 'icon_seamless' "
           "AND valid_utc = ? AND run_utc = ?")
    args = [HOURS[2], dt.datetime(2026, 9, 20, tzinfo=UTC)]
    assert connect(archivo).execute(sql, args).fetchone()[0] == 5.0   # la ultima
    cutoff = asof.issue_cutoff(NIGHT)
    assert connect(archivo, as_of=cutoff).execute(sql, args).fetchone()[0] == 80.0


def test_la_vista_y_asof_usan_la_misma_regla_para_weathernext(tmp_path):
    """WeatherNext se fecha por su pasada + 7 h, no por la recogida (el relleno
    del 17-09 trajo agosto y septiembre de golpe). Una sola regla para los dos
    caminos: `asof.AVAILABLE_AT_SQL`."""
    from astroweather.queries import connect
    run = dt.datetime(2026, 9, 20, 0, tzinfo=UTC)
    row = {"site_id": "nerpio", "model": "weathernext3", "run_utc": run,
           "valid_utc": HOURS[2], "lead_h": 22, "member": None, "cloud_total": 30.0,
           "retrieved_at": dt.datetime(2026, 9, 25, tzinfo=UTC)}
    _archive(tmp_path, [row])
    at = connect(tmp_path, as_of=run + dt.timedelta(hours=8)).execute(
        "SELECT count(*) FROM forecasts WHERE model = 'weathernext3'").fetchone()[0]
    before = connect(tmp_path, as_of=run + dt.timedelta(hours=6)).execute(
        "SELECT count(*) FROM forecasts WHERE model = 'weathernext3'").fetchone()[0]
    assert (at, before) == (1, 0)
    table = asof.raw_forecast_asof(connect(tmp_path), tmp_path, "weathernext3",
                                   run - dt.timedelta(days=1))
    assert table.at(_naive(HOURS[2]), run + dt.timedelta(hours=8))[0] == 30.0
    assert table.at(_naive(HOURS[2]), run + dt.timedelta(hours=6)) is None
