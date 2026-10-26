"""La curva del fotometro se reentrena sola, en su escala, y solo entra si gana.

Lo que protegen estos tests (26-10-2026), en el orden en que fallaria:

1. La escala (M17): el retroanalisis del ECMWF 0,25 solo entra desde las
   pasadas del 21-11-2025, y el corte va por la PASADA, no por la hora valida.
   Lo vivo del mismo modelo entra con su alcance, y la misma hora con el mismo
   alcance de las dos fuentes es un par, no dos.
2. El examen detecta lo que tiene que detectar: una aspirante entrenada con la
   escala mezclada pierde contra la nativa cuando la verdad sigue a la nativa
   (control positivo del instrumento), y una curva rota no entra.
3. Sin datos recientes (WeatherNext, M14) no se decide nada ni se escribe nada.
4. El ranking de la noche siguiente pone delante al modelo que informa.
5. Los trabajos van detras de la nota y de la curva del techo.

Sin red y sin archivo real: filas sinteticas.
"""

import dataclasses
import datetime as dt
import json

import numpy as np
import pytest

from astroweather import calibration as C
from astroweather import curvecontest as CC
from astroweather import nighttruth as NT
from astroweather.archive import write_canonical

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 26, 10, 30, tzinfo=UTC)


# --- 1. la escala y de donde salen los pares ---------------------------------------

def _fila(model, valid, cloud, run=None, lead_h=None):
    run = run or valid.replace(hour=0)
    return {"site_id": "nerpio", "model": model, "member": None, "run_utc": run,
            "valid_utc": valid, "cloud_total": cloud,
            "lead_h": lead_h if lead_h is not None else int((valid - run).total_seconds() // 3600)}


def _archivo(tmp_path, filas):
    write_canonical(filas, dataset="forecasts", site_id="nerpio", root=tmp_path)
    write_canonical([{"site_id": "nerpio", "t_utc": filas[0]["valid_utc"], "sky_mag_raw": 21.6,
                      "instrument_type": "TESS", "instrument_id": "stars289",
                      "bandpass_id": "tess-w"}],
                    dataset="measurements", site_id="nerpio", root=tmp_path)
    return tmp_path


def test_el_corte_de_escala_va_por_la_pasada_y_no_por_la_hora(tmp_path):
    """El 23-11-2025 a las 22 UTC: el prev1d salio de la pasada del 22-11 (TCC
    nativa) y el prev5d de la del 18-11 (la formula de Open-Meteo). Solo el
    primero es de la escala con la que se usa la curva."""
    hora = dt.datetime(2025, 11, 23, 22, tzinfo=UTC)
    raiz = _archivo(tmp_path, [
        _fila("ecmwf_ifs025_prev1d", hora, 80.0, run=dt.datetime(2025, 11, 22, tzinfo=UTC)),
        _fila("ecmwf_ifs025_prev5d", hora, 24.0, run=dt.datetime(2025, 11, 18, tzinfo=UTC)),
    ])
    verdad = {hora.replace(tzinfo=None): False}
    assert C.training_pairs(verdad, raiz) == [(80.0, 1.0, False)]
    # Sin corte (el control de la escala mezclada) entran las dos.
    assert sorted(C.training_pairs(verdad, raiz, since=None)) == [(24.0, 5.0, False),
                                                                (80.0, 1.0, False)]
    assert C.NATIVE_TCC_SINCE == dt.date(2025, 11, 21)


def test_lo_vivo_entra_con_su_alcance_y_una_hora_es_un_par(tmp_path):
    """El vivo del 27-10 a las 00 UTC para el 28-10 a las 22 es alcance 1 (46 h);
    si el retroanalisis da la misma hora y alcance, se promedian. El dia 0 y el 6
    no tienen retroanalisis con el que compararse y no entran."""
    hora = dt.datetime(2026, 10, 28, 22, tzinfo=UTC)
    raiz = _archivo(tmp_path, [
        _fila("ecmwf_ifs025", hora, 60.0, run=dt.datetime(2026, 10, 27, tzinfo=UTC)),
        _fila("ecmwf_ifs025_prev1d", hora, 40.0, run=dt.datetime(2026, 10, 27, tzinfo=UTC)),
        _fila("ecmwf_ifs025", hora, 99.0, run=dt.datetime(2026, 10, 28, tzinfo=UTC)),   # dia 0
        _fila("ecmwf_ifs025", hora, 11.0, run=dt.datetime(2026, 10, 22, tzinfo=UTC)),   # dia 6
        _fila("icon_seamless", hora, 5.0, run=dt.datetime(2026, 10, 27, tzinfo=UTC)),
    ])
    filas = C.training_rows({hora.replace(tzinfo=None): True}, raiz)
    assert [(c, lead, ok) for _, c, lead, ok in filas] == [(50.0, 1.0, True)]
    # Y el de ICON solo sale si se le pide por su nombre.
    icon = C.model_rows({hora.replace(tzinfo=None): True}, raiz, "icon_seamless")
    assert [(c, lead) for _, c, lead, _ in icon] == [(5.0, 1.0)]


# --- 2. el examen ----------------------------------------------------------------------

def _filas(n_noches=60, primera=dt.date(2026, 8, 1), escala=lambda c: c, semilla=7):
    """Ocho horas por noche y dos alcances; la verdad sigue a la nube NATIVA:
    P(despejado) = sigmoid(3 - 5 * nube). `escala` deforma la nube que ve el
    modelo (la formula vieja aplasta lo alto) sin tocar la verdad."""
    rng = np.random.default_rng(semilla)
    filas = []
    for d in range(n_noches):
        noche = primera + dt.timedelta(days=d)
        base = dt.datetime(noche.year, noche.month, noche.day, 20)
        for h in range(8):
            hora = base + dt.timedelta(hours=h)
            nube = float(rng.uniform(0, 100))
            despejada = bool(rng.random() < 1 / (1 + np.exp(-(3 - 5 * nube / 100))))
            for lead in (1.0, 2.0):
                filas.append((hora, escala(nube), lead, despejada))
    return filas


def _curva(a, b, fitted, model="ecmwf_ifs025"):
    return C.Calibrator(intercept=a, cloud_weight=b, lead_weight=0.0, trained_on=1000,
                        brier=0.1, brier_climatology=0.2, base_rate=0.6, fitted_at=fitted,
                        trained_on_model=model)


def test_la_buena_entra_y_queda_registrada(tmp_path):
    _curva(0.4, 0.0, "plana").save(C.model_path(root=tmp_path))
    rec = CC.cloud_duel(tmp_path, now=NOW.replace(month=10, day=1), rows=_filas())
    assert rec["decision"] == "sustituye" and rec["n_nights"] == CC.CLOUD_K_NIGHTS
    guardada = json.loads(C.model_path(root=tmp_path).read_text())
    assert guardada["fitted_at"] != "plana" and guardada["cloud_weight"] < -3
    registro = CC.decisions(tmp_path, "fotometro")
    assert len(registro) == 1 and registro[0]["d_brier"] < -CC.CLOUD_MARGIN


def test_la_aspirante_se_ajusta_sin_las_noches_del_examen(tmp_path):
    filas = _filas()
    entreno, examen, noches = CC.cloud_exam_split(filas, dt.date(2026, 9, 29))
    assert len(noches) == CC.CLOUD_K_NIGHTS and noches[-1] == dt.date(2026, 9, 29)
    assert max(r[0] for r in entreno) < min(r[0] for r in examen)
    assert all(r[0] < dt.datetime(2026, 9, 30, 12) for r in examen)


@pytest.mark.parametrize("estropeada", [
    {"intercept": -2.0, "cloud_weight": 4.0},          # la pendiente al reves
    {"intercept": 4.6, "cloud_weight": 0.0},           # siempre 0,99
])
def test_control_en_rojo_una_curva_rota_sale_rechazada(tmp_path, estropeada):
    buena = _curva(3.0, -5.0, "vigente buena")
    buena.save(C.model_path(root=tmp_path))
    antes = C.model_path(root=tmp_path).read_text()
    rec = CC.cloud_duel(tmp_path, now=NOW.replace(month=10, day=1), rows=_filas(),
                        challenger=dataclasses.replace(buena, fitted_at="rota", **estropeada))
    assert rec["decision"] == "se_queda"
    assert rec["challenger_brier"] > rec["champion_brier"]
    assert C.model_path(root=tmp_path).read_text() == antes
    assert CC.decisions(tmp_path, "fotometro")[-1]["decision"] == "se_queda"


def test_control_en_rojo_la_escala_mezclada_pierde_contra_la_nativa(tmp_path):
    """El instrumento VE un cambio de escala. La vigente se ajusta con la nube
    nativa; la aspirante, con la mitad de las noches en la escala vieja (lo alto
    aplastado al 30 %, como un cielo de cirros con la formula de Open-Meteo). La
    verdad sigue a la nativa: la mezclada tiene que perder y no entrar."""
    nativa = _filas(n_noches=120, primera=dt.date(2026, 4, 1))
    vieja = _filas(n_noches=120, primera=dt.date(2025, 12, 1), semilla=8,
                   escala=lambda c: c if c < 30 else 30 + 0.3 * (c - 30))
    antes_del_examen = nativa[:-CC.CLOUD_K_NIGHTS * 16]     # 16 pares por noche
    vigente = C.fit([r[1:] for r in antes_del_examen])
    mezclada = C.fit([r[1:] for r in vieja + antes_del_examen])
    vigente.save(C.model_path(root=tmp_path))
    rec = CC.cloud_duel(tmp_path, now=dt.datetime(2026, 8, 1, 10, tzinfo=UTC),
                        rows=nativa, challenger=mezclada)
    # El examen empieza justo donde acaba lo que vieron las dos curvas.
    assert json.loads(rec["nights"])[0] == str(NT.night_of(antes_del_examen[-1][0])
                                               + dt.timedelta(days=1))
    assert rec["decision"] == "se_queda"
    assert rec["d_brier"] > 0                      # pierde, no solo "no gana por el margen"


def test_sin_vigente_entra_la_aspirante(tmp_path):
    rec = CC.cloud_duel(tmp_path, now=NOW.replace(month=10, day=1), rows=_filas())
    assert rec["decision"] == "arranque_en_frio"
    assert C.Calibrator.load(C.model_path(root=tmp_path)).trained_on_model == "ecmwf_ifs025"


def test_una_curva_de_otro_modelo_no_defiende_la_plaza(tmp_path):
    _curva(3.0, -5.0, "de weathernext", model="weathernext3").save(C.model_path(root=tmp_path))
    rec = CC.cloud_duel(tmp_path, now=NOW.replace(month=10, day=1), rows=_filas())
    assert rec["decision"] == "arranque_en_frio"


# --- 3. sin datos recientes ----------------------------------------------------------------

def test_weathernext_sin_su_mes_en_curso_no_se_examina_ni_se_toca(tmp_path):
    """Hoy (26-10) WeatherNext llega hasta el 30-09 (M14): no hay examen, la curva
    no se toca y no se escribe ninguna decision."""
    vigente = _curva(3.7, -3.5, "18-09", model="weathernext3")
    vigente.save(C.model_path("weathernext3", tmp_path))
    antes = C.model_path("weathernext3", tmp_path).read_text()
    rec = CC.cloud_duel(tmp_path, model="weathernext3", now=NOW, rows=_filas())   # hasta 29-09
    assert rec["decision"] == "sin_examen"
    assert "M14" in rec["reason"] and "2026-09-29" in rec["reason"]
    assert C.model_path("weathernext3", tmp_path).read_text() == antes
    assert CC.decisions(tmp_path) == []


# --- 4. el ranking de la noche siguiente ------------------------------------------------

def test_el_ranking_d1_pone_delante_al_modelo_que_informa(tmp_path):
    """Tres modelos sobre las mismas horas: uno ve la nube de verdad, otro la ve
    con ruido y otro es ruido puro. Gana el primero y queda registrado."""
    rng = np.random.default_rng(3)
    base = _filas(n_noches=90, primera=dt.date(2026, 5, 1))
    filas = {"ecmwf_ifs025": [(h, float(np.clip(c + rng.normal(0, 30), 0, 100)), l, ok)
                              for h, c, l, ok in base],
             "icon_seamless": base,
             "weathernext3": [(h, float(rng.uniform(0, 100)), l, ok) for h, c, l, ok in base]}
    rec = CC.rank_d1(tmp_path, now=NOW, rows=filas)
    assert rec["decision"] == "icon_seamless"
    assert rec["challenger_fitted_at"] == "icon_seamless" and rec["d_brier_hi"] < 0
    tabla = json.loads(rec["challenger_json"])["modelos"]
    assert tabla["weathernext3"]["brier"] > tabla["ecmwf_ifs025"]["brier"] > tabla["icon_seamless"]["brier"]
    assert CC.decisions(tmp_path, "modelo_d1")[-1]["decision"] == "icon_seamless"


def test_el_ranking_sin_dos_meses_comunes_no_decide(tmp_path):
    base = _filas(n_noches=20, primera=dt.date(2026, 9, 1))
    rec = CC.rank_d1(tmp_path, now=NOW, rows={m: base for m in CC.D1_MODELS})
    assert rec["decision"] == "sin_examen" and CC.decisions(tmp_path) == []


# --- 5. los trabajos ---------------------------------------------------------------------

def test_los_trabajos_de_la_curva_del_fotometro_van_en_su_sitio():
    from astroweather.scheduler import JOBS
    orden = list(JOBS)
    assert orden.index("techo_curva") < orden.index("nube_curva") < orden.index("modelo_d1")
    assert orden.index("modelo_d1") < orden.index("salud")
    for nombre in ("nube_curva", "modelo_d1"):
        assert JOBS[nombre].desde_hora_utc == 10
        assert JOBS[nombre].periodo == dt.timedelta(days=7)
