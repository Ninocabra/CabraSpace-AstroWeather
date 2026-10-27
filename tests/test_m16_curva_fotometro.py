"""M16 en la curva del fotometro (27-10-2026): la prevision VIVA del ECMWF que
entra en `calibration.training_rows` -- con la que se entrena la curva y con la
que el examen `nube_curva` puntua aspirante y vigente -- es la que habia a las
15 UTC de `alcance` dias antes de su noche (`asof.AsOf.issued`), no la ultima
recogida de la etiqueta de pasada.

Antes salia de la vista deduplicada con `lead_h // 24`: para una hora de
madrugada, el "1 dia" era la recogida de las 23 UTC de la vispera, tres horas
antes de la hora prevista. Medido sobre el archivo: la nube cambia en 1777 de
2357 pares vivos (docs/m16_curva_fotometro.md).

Los tres primeros tests y el del examen se vieron en ROJO con el codigo
anterior; el del corte de escala es una guarda (ya pasaba).
"""

import datetime as dt

import pytest

from astroweather import calibration as C
from astroweather import curvecontest as CC
from astroweather.archive import write_canonical

UTC = dt.timezone.utc
# La hora 01 UTC del 21-09 es de la noche del 20-09.
HORA = dt.datetime(2026, 9, 21, 1, tzinfo=UTC)


def _fila(model, valid, cloud, retrieved=None, run=None):
    """Una fila como las de `fetch_forecast`: `run_utc` = el DIA de la recogida a
    las 00Z, y `lead_h` contado desde ahi."""
    if run is None:
        run = dt.datetime(retrieved.year, retrieved.month, retrieved.day, tzinfo=UTC)
    return {"site_id": "nerpio", "model": model, "member": None, "run_utc": run,
            "valid_utc": valid, "cloud_total": cloud, "retrieved_at": retrieved,
            "lead_h": int((valid - run).total_seconds() // 3600)}


def _archivo(tmp_path, filas):
    for fila in filas:
        write_canonical([fila], dataset="forecasts", site_id="nerpio", root=tmp_path)
    write_canonical([{"site_id": "nerpio", "t_utc": filas[0]["valid_utc"], "sky_mag_raw": 21.6,
                      "instrument_type": "TESS", "instrument_id": "stars289",
                      "bandpass_id": "tess-w"}],
                    dataset="measurements", site_id="nerpio", root=tmp_path)
    return tmp_path


def _at(day, hour):
    return dt.datetime(2026, 9, day, hour, tzinfo=UTC)


def test_lo_vivo_entra_con_lo_que_habia_a_las_15_utc(tmp_path):
    """Antes: el "1 dia" era la recogida de las 23 UTC del 20-09 (5 %, dos horas
    antes de la hora prevista), el "2 dias" la del 19-09 y el "3 dias" la del
    18-09, por la etiqueta. Ahora el alcance d es lo que habia a las 15 UTC de d
    dias antes de la noche: 1 dia = la recogida del 19-09 a las 10 (50 %), 2
    dias = la del 18-09 (70 %), y a 3 dias no habia nada."""
    raiz = _archivo(tmp_path, [
        _fila("ecmwf_ifs025", HORA, 70.0, _at(18, 10)),
        _fila("ecmwf_ifs025", HORA, 50.0, _at(19, 10)),
        _fila("ecmwf_ifs025", HORA, 80.0, _at(20, 10)),      # dia 0: no entrena
        _fila("ecmwf_ifs025", HORA, 5.0, _at(20, 23)),       # ya de noche: nunca
    ])
    filas = C.training_rows({HORA.replace(tzinfo=None): True}, raiz)
    assert [(c, lead) for _, c, lead, _ in filas] == [(50.0, 1.0), (70.0, 2.0)]


def test_retroanalisis_y_vivo_de_la_misma_hora_y_alcance_son_un_par(tmp_path):
    """El `previous_day1` del retroanalisis (40 %) y lo vivo que habia a las 15
    UTC de la vispera (60 %) son dos opiniones sobre la misma hora y alcance: un
    par con la media. El retroanalisis sigue entrando por su nombre."""
    raiz = _archivo(tmp_path, [
        _fila("ecmwf_ifs025_prev1d", HORA, 40.0, _at(25, 0), run=_at(20, 0)),
        _fila("ecmwf_ifs025", HORA, 60.0, _at(19, 10)),
        _fila("ecmwf_ifs025", HORA, 99.0, _at(19, 23)),      # despues del corte del 19
        _fila("icon_seamless", HORA, 5.0, _at(19, 10)),
    ])
    verdad = {HORA.replace(tzinfo=None): False}
    assert C.training_pairs(verdad, raiz) == [(50.0, 1.0, False)]
    # ICON, por el mismo camino y solo si se le pide por su nombre.
    icon = C.model_rows(verdad, raiz, "icon_seamless")
    assert [(c, lead) for _, c, lead, _ in icon] == [(5.0, 1.0)]


def test_lo_vivo_sin_recogida_no_se_puede_fechar_y_no_entra(tmp_path):
    raiz = _archivo(tmp_path, [
        _fila("ecmwf_ifs025", HORA, 50.0, None, run=_at(19, 0)),
        _fila("ecmwf_ifs025_prev2d", HORA, 30.0, _at(25, 0), run=_at(19, 0)),
    ])
    assert C.training_pairs({HORA.replace(tzinfo=None): True}, raiz) == [(30.0, 2.0, True)]


def test_el_corte_de_escala_vale_tambien_para_lo_vivo(tmp_path):
    """Guarda (ya pasaba): lo vivo de una pasada anterior al 21-11-2025 es de la
    escala de la formula de Open-Meteo y no entra; sin corte, si. Y una sola
    recogida, sin otra detras, es lo que habia a las 15 UTC del 20-11 (alcance
    2) Y del 21-11 (alcance 1): las dos cosas publico el producto."""
    hora = dt.datetime(2025, 11, 22, 22, tzinfo=UTC)
    recogida = dt.datetime(2025, 11, 20, 10, tzinfo=UTC)     # pasada del 20-11
    raiz = _archivo(tmp_path, [_fila("ecmwf_ifs025", hora, 30.0, recogida)])
    verdad = {hora.replace(tzinfo=None): True}
    assert C.training_pairs(verdad, raiz) == []
    assert C.training_pairs(verdad, raiz, since=None) == [(30.0, 1.0, True), (30.0, 2.0, True)]


def test_el_examen_puntua_con_lo_que_habia_a_las_15_utc(tmp_path):
    """El examen `nube_curva` lee sus pares de `model_rows`. Dos noches nubladas:
    a las 15 UTC de la vispera el ECMWF decia 90 % (lo que se publico); a las 23
    UTC, ya sin nada que publicar para esa noche, 0 %. Con la vista, la vigente
    se puntuaba con el 0 % (p = 0,88, Brier 0,78); con lo publicable, 90 %
    (p = 0,17, Brier 0,03)."""
    noches = (dt.date(2026, 10, 20), dt.date(2026, 10, 21))
    filas, verdad = [], {}
    for n in noches:
        hora = dt.datetime(n.year, n.month, n.day, 22, tzinfo=UTC)
        vispera = hora - dt.timedelta(days=1)
        filas += [_fila("ecmwf_ifs025", hora, 90.0, vispera.replace(hour=10)),
                  _fila("ecmwf_ifs025", hora, 0.0, vispera.replace(hour=23))]
        verdad[hora.replace(tzinfo=None)] = False
    raiz = _archivo(tmp_path, filas)
    vigente = C.Calibrator(intercept=2.0, cloud_weight=-4.0, lead_weight=0.0, trained_on=1000,
                           brier=0.1, brier_climatology=0.1, base_rate=0.8,
                           fitted_at="2026-08-30T17:54:56+00:00")
    vigente.save(C.model_path(C.BACKFILL_MODEL, raiz))
    aspirante = C.Calibrator(**{**vigente.__dict__, "intercept": 1.0,
                                "fitted_at": "2026-10-23T10:30:00+00:00"})
    record = CC.cloud_duel(raiz, now=dt.datetime(2026, 10, 23, 10, 30, tzinfo=UTC), k=2,
                           challenger=aspirante, write=False, truth=verdad)
    assert record["n_nights"] == 2
    assert record["champion_brier"] == pytest.approx(vigente.probability(90.0, 24.0) ** 2)
    assert record["challenger_brier"] == pytest.approx(aspirante.probability(90.0, 24.0) ** 2)
