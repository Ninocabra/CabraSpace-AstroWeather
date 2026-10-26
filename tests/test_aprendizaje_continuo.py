"""El producto que se puntua solo (26-10-2026): nota, aspirante y salud.

Lo que protegen estos tests, en el orden en que fallaria el producto:

1. La verdad del techo por la tabla de AstroCamp: solo cuentan los CAMBIOS de la
   tabla, una correccion de un mes viejo no se le apunta a anoche, y una noche
   cerrada (sin cambio) no se da por "0 horas" hasta que la acota otro cambio.
2. La nota diaria tal como corre en el runner, SIN historial git: la cadena de
   fotos de la tabla que guarda cada fila basta para puntuar la noche siguiente,
   y una pasada sin tabla no tapa con "no se sabe" una verdad ya escrita.
3. Lo publicado ida y vuelta por la captura: lo que se lee es lo que se guardo.
4. La aspirante solo entra si gana; una curva estropeada sale rechazada (el
   control en rojo) y el fichero de la vigente no se toca.
5. La salud salta en rojo con la cadencia de octubre y en verde con la de
   septiembre: un detector que nunca se ha visto fallar no discrimina.

Sin red: ni git remoto ni proveedores. Las efemerides van sin IERS (conftest).
"""

import dataclasses
import datetime as dt
import json
import subprocess

import pytest

from astroweather import curvecontest as CC
from astroweather import health as H
from astroweather import nightscore as NS
from astroweather import nighttruth as NT
from astroweather.archive import write_canonical

UTC = dt.timezone.utc
NIGHT = dt.date(2026, 10, 20)


# --- 1. la tabla de AstroCamp ------------------------------------------------------

def _tabla(oct_horas, sep_horas=258.0):
    return {(2026, 9): sep_horas, (2026, 10): oct_horas}


def test_una_foto_repetida_no_parte_el_intervalo():
    t0 = dt.datetime(2026, 10, 20, 9, tzinfo=UTC)
    fotos = [(t0, _tabla(100)), (t0 + dt.timedelta(days=1), _tabla(100)),
             (t0 + dt.timedelta(days=2), _tabla(112))]
    cambios = NT.table_changes(fotos)
    assert [t for t, _ in cambios] == [t0, t0 + dt.timedelta(days=2)]


def test_la_correccion_de_un_mes_viejo_no_se_apunta_a_anoche():
    t0 = dt.datetime(2026, 10, 20, 9, tzinfo=UTC)
    t1 = t0 + dt.timedelta(days=1)
    # Octubre sube 9 h (anoche) y AstroCamp corrige septiembre en -4 h a la vez.
    deltas = NT.table_deltas([(t0, _tabla(100, 258)), (t1, _tabla(109, 254))])
    assert deltas == [(t0, t1, 9.0)]


def test_dos_noches_en_un_intervalo_se_reparten_y_se_dice():
    noches = [NIGHT, NIGHT + dt.timedelta(days=1)]
    frames = NT.night_frames(noches)
    t0 = dt.datetime(2026, 10, 20, 9, tzinfo=UTC)
    t2 = t0 + dt.timedelta(days=2)
    verdad = NT.roof_truth(frames, [], [(t0, t2, 12.0)])
    assert {verdad[n]["table_quality"] for n in noches} == {"aprox"}
    assert sum(verdad[n]["table_hours"] for n in noches) == pytest.approx(12.0)
    # Y si falta una noche en el marco, la otra sale "limpia": por eso `score`
    # construye el marco con TODAS las noches que tocan los intervalos.
    sola = NT.roof_truth({NIGHT: frames[NIGHT]}, [], [(t0, t2, 12.0)])
    assert sola[NIGHT]["table_quality"] == "limpio"


# --- un archivo sintetico de una noche y su dia siguiente ----------------------------

def _medidas(root, noche, abierto=True, lecturas=24, extra=()):
    f = NT.night_frames([noche])[noche]
    paso = (f["sunrise"] - f["sunset"]) / lecturas
    filas = []
    for i in range(lecturas):
        t = (f["sunset"] + paso * (i + 0.5)).replace(microsecond=0)
        filas += [
            {"site_id": "nerpio", "t_utc": t, "instrument_id": "astrocamp_roofs",
             "instrument_type": "RoofStatus", "roof_open": abierto,
             "quality_flags": f"open={2 if abierto else 0}/4", "retrieved_at": t},
            {"site_id": "nerpio", "t_utc": t, "instrument_id": "cloudwatcher4",
             "instrument_type": "CloudWatcher", "clear_flag": abierto, "retrieved_at": t},
        ]
    for t in extra:
        filas.append({"site_id": "nerpio", "t_utc": t, "instrument_id": "astrocamp_roofs",
                      "instrument_type": "RoofStatus", "roof_open": False,
                      "quality_flags": "open=0/4", "retrieved_at": t})
    write_canonical(filas, dataset="measurements", site_id="nerpio", root=root)


def _prevision_minima(root):
    # `connect` monta la vista de previsiones aunque no se use: necesita un fichero.
    t = dt.datetime(2026, 10, 20, 12, tzinfo=UTC)
    write_canonical([{"site_id": "nerpio", "model": "ecmwf_ifs025", "run_utc": t,
                      "valid_utc": t, "cloud_total": 10.0, "retrieved_at": t}],
                    dataset="forecasts", site_id="nerpio", root=root)


def _noche_json(generado, noches, p=0.8, nube=10.0):
    out = {"generado_utc": generado.isoformat(), "noches": [],
           "confianza": {"curva_ajustada_el": "2026-10-19T10:00:00+00:00",
                         "verdad_terreno": "techo"}}
    for noche in noches:
        horas = [dt.datetime(noche.year, noche.month, noche.day, 17, tzinfo=UTC)
                 + dt.timedelta(hours=k) for k in range(15)]
        out["noches"].append({
            "noche": noche.isoformat(), "probabilidad_de_abrir": p,
            "veredicto_abrir": {"abierto": p >= 0.5, "ventana": {
                "desde": horas[5].isoformat(), "hasta": horas[7].isoformat()}},
            "horas_utilizables_esperadas": 8.0, "modelo_nubes": "ecmwf_ifs025",
            "verdad_terreno": "techo",
            "horas": [{"t_utc": h.isoformat(), "nubes_previstas": nube, "p_despejado": p,
                       "p_utilizable": p, "seguro_para_abrir": True, "limita": None}
                      for h in horas]})
    return out


def _tabla_json(root, oct_horas):
    (root / NT.OPEN_HOURS_FILE).write_text(json.dumps(
        {"2026": {"months": [None] * 8 + [258, oct_horas, None, None]}}), encoding="utf-8")


# --- 2 y 3. la nota diaria, como en el runner --------------------------------------------

def test_la_captura_de_lo_publicado_vuelve_igual(tmp_path):
    payload = _noche_json(dt.datetime(2026, 10, 20, 14, tzinfo=UTC), [NIGHT, NIGHT + dt.timedelta(days=1)])
    NS.record_publication(payload, root=tmp_path)
    leido = NS.captured_publications(tmp_path)
    original = NT.parse_publication(payload)
    assert len(leido) == 1 and leido[0]["generated"] == original["generated"]
    for a, b in zip(leido[0]["nights"], original["nights"]):
        assert a["night"] == b["night"] and a["open"] == b["open"]
        assert a["p_open"] == pytest.approx(b["p_open"])
        assert a["window"] == b["window"]
        assert [(h["t"], h["cloud"], h["p_usable"]) for h in a["hours"]] == \
               [(h["t"], h["cloud"], h["p_usable"]) for h in b["hours"]]
    assert leido[0]["source"] == "publicar"


def test_la_nota_del_runner_sin_historial_git(tmp_path):
    """Dia 1: la noche se puntua con las lecturas. Dia 2: la tabla cambia y la
    noche siguiente sale de la tabla, con la foto que guardo la fila del dia 1."""
    root = tmp_path / "data"
    root.mkdir()
    sin_repo = tmp_path / "no-es-un-repo"
    sin_repo.mkdir()
    _prevision_minima(root)
    manana = NIGHT + dt.timedelta(days=1)
    # Noche N abierta, 24 lecturas; una lectura de dia a las 10:00 del dia siguiente.
    _medidas(root, NIGHT, extra=[dt.datetime(2026, 10, 21, 10, tzinfo=UTC)])
    NS.record_publication(_noche_json(dt.datetime(2026, 10, 19, 14, tzinfo=UTC),
                                      [NIGHT - dt.timedelta(days=1), NIGHT]), root=root)
    NS.record_publication(_noche_json(dt.datetime(2026, 10, 20, 14, tzinfo=UTC),
                                      [NIGHT, manana], p=0.3, nube=80.0), root=root)
    _tabla_json(root, 100)

    dia1 = NS.run_daily(root, now=dt.datetime(2026, 10, 21, 10, 30, tzinfo=UTC),
                        data_repo=sin_repo, archive_repo=sin_repo)
    filas = NS.latest_scores(root)
    fila = filas[NIGHT]
    assert str(NIGHT) in dia1["filas_nuevas"]
    assert fila["roof_source"] == "lecturas" and fila["roof_opened"] is True
    assert fila["d0_p_open"] == pytest.approx(0.3) and fila["d0_outcome"] == "sorpresa"
    assert fila["dm1_p_open"] == pytest.approx(0.8) and fila["dm1_outcome"] == "acierto"
    assert fila["cw_clear_frac"] == pytest.approx(1.0)
    assert fila["table_json"] and fila["table_seen_utc"] is not None

    # Dia 2: la noche N+1 casi sin lecturas (la cadencia de octubre) y la tabla
    # sube 11 h. Solo la cadena de fotos puede decir que fue esa noche.
    _medidas(root, manana, lecturas=2, extra=[dt.datetime(2026, 10, 22, 10, tzinfo=UTC)])
    _tabla_json(root, 111)
    dia2 = NS.run_daily(root, now=dt.datetime(2026, 10, 22, 10, 30, tzinfo=UTC),
                        data_repo=sin_repo, archive_repo=sin_repo)
    filas = NS.latest_scores(root)
    assert filas[manana]["roof_source"] == "tabla limpio"
    assert filas[manana]["roof_hours"] == pytest.approx(11.0)
    assert filas[manana]["roof_opened"] is True
    # La noche N no gana ni pierde nada: no se reescribe.
    assert dia2["filas_nuevas"] == [str(manana)]
    assert filas[NIGHT]["roof_source"] == "lecturas"


def test_una_pasada_sin_tabla_no_tapa_una_verdad_ya_escrita():
    guardada = {"roof_hours": 11.0, "roof_source": "tabla limpio", "roof_opened": True,
                "roof_table_hours": 11.0, "roof_table_quality": "limpio", "roof_samples": 2,
                "roof_dark_hours": None, "phot_hours": 9, "phot_clear_frac": 1.0,
                "cw_hours": 1, "cw_clear_frac": 1.0, "sky_hours": 5, "sky_clear_frac": 1.0,
                "consensus_hours": 5, "consensus_clear_frac": 1.0}
    for p in ("d0", "dm1"):
        guardada.update({f"{p}_{k}": None for k in NS._PUB_KEYS})
    guardada.update({"d0_published_utc": dt.datetime(2026, 10, 20, 14, tzinfo=UTC),
                     "d0_p_open": 0.72, "d0_open": True, "d0_expected_h": 8.0})
    nueva = dict(guardada, roof_hours=None, roof_source=None, roof_opened=None,
                 roof_table_hours=None, roof_table_quality=None,
                 d0_published_utc=None, d0_p_open=None, d0_open=None, d0_expected_h=None,
                 phot_hours=2, phot_clear_frac=0.5)
    fundida = NS.merge(nueva, guardada)
    assert fundida["roof_opened"] is True and fundida["roof_source"] == "tabla limpio"
    assert fundida["d0_outcome"] == "acierto" and fundida["d0_p_open"] == 0.72
    assert fundida["phot_hours"] == 9            # el fotometro no "pierde" horas
    # ...pero una verdad MEJOR si sustituye: lecturas -> tabla limpio.
    peor = dict(guardada, roof_source="lecturas", roof_hours=1.0, roof_opened=False)
    assert NS.merge(guardada, peor)["roof_source"] == "tabla limpio"


@pytest.mark.parametrize("dijo, abrio, resultado", [
    (True, True, "acierto"), (True, False, "falsa_alarma"),
    (False, True, "sorpresa"), (False, False, "cierre_bien"), (True, None, None)])
def test_las_cuatro_casillas(dijo, abrio, resultado):
    assert NS._outcome(dijo, abrio) == resultado


# --- 4. aspirante contra campeona -------------------------------------------------------

def _curva(intercept, slope, fitted_at):
    from astroweather.roofcalibration import RoofCalibrator
    return RoofCalibrator(intercept=intercept, cloud_weight=slope, trained_on=300, brier=0.02,
                          brier_climatology=0.03, base_rate=0.6, fitted_at=fitted_at,
                          crossing=-intercept / slope if slope else 0.5, slope_low=slope,
                          slope_high=slope, months=31, nights=40, monthly_mae=0.1,
                          monthly_mae_climatology=0.18, method="sintetica")


def _examen(root, noches=16, cerradas=(3, 7, 11, 14)):
    """Noches puntuadas con su publicacion: nube 90 % las cerradas, 10 % las demas."""
    filas, primera = [], NIGHT - dt.timedelta(days=noches - 1)
    for i in range(noches):
        noche = primera + dt.timedelta(days=i)
        cerrada = i in cerradas
        NS.record_publication(_noche_json(dt.datetime(noche.year, noche.month, noche.day, 14,
                                                      tzinfo=UTC), [noche],
                                          nube=90.0 if cerrada else 10.0), root=root)
        filas.append({"site_id": "nerpio", "date": noche, "origin": "relleno",
                      "scored_utc": dt.datetime(2026, 10, 21, 10, tzinfo=UTC),
                      "roof_opened": not cerrada, "roof_hours": 0.0 if cerrada else 10.0})
    write_canonical(filas, dataset="night_scores", site_id="nerpio", root=root)


def test_compare_decide_con_margen_y_minimo_de_noches():
    o = [1, 1, 0, 1, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1]
    g = list(range(14))
    bueno = [0.9 if x else 0.1 for x in o]
    plano = [0.6] * 14
    assert CC.compare(None, bueno, o, g)["decision"] == "arranque_en_frio"
    assert CC.compare(plano, bueno, o, g)["decision"] == "sustituye"
    casi = [p - 0.005 if x else p + 0.005 for p, x in zip(plano, o)]
    assert CC.compare(plano, casi, o, g)["decision"] == "se_queda"     # gana, pero sin margen
    assert CC.compare(plano[:5], bueno[:5], o[:5], g[:5])["decision"] == "se_queda"


def test_la_aspirante_buena_entra_y_queda_registrada(tmp_path):
    _examen(tmp_path)
    _curva(0.4, 0.0, "plana").save(tmp_path / "roof_calibration.json")
    rec = CC.roof_duel(tmp_path, now=dt.datetime(2026, 10, 21, 10, 30, tzinfo=UTC),
                       data_repo=tmp_path, challenger=_curva(2.5, -5.0, "buena"))
    assert rec["decision"] == "sustituye" and rec["n_nights"] == CC.K_NIGHTS
    assert json.loads((tmp_path / "roof_calibration.json").read_text())["fitted_at"] == "buena"
    registro = CC.decisions(tmp_path, "techo")
    assert len(registro) == 1 and registro[0]["decision"] == "sustituye"
    assert registro[0]["d_brier"] < -CC.MARGIN


def test_control_en_rojo_una_curva_estropeada_sale_rechazada(tmp_path):
    """La pendiente al reves (mas nube, mas techo) tiene que perder, y la vigente
    no se toca. Si este test pasara con la estropeada dentro, el examen no
    examinaria nada."""
    _examen(tmp_path)
    buena = _curva(2.5, -5.0, "vigente buena")
    buena.save(tmp_path / "roof_calibration.json")
    antes = (tmp_path / "roof_calibration.json").read_text()
    rec = CC.roof_duel(tmp_path, now=dt.datetime(2026, 10, 21, 10, 30, tzinfo=UTC),
                       data_repo=tmp_path,
                       challenger=dataclasses.replace(buena, intercept=-2.0, cloud_weight=4.0,
                                                      fitted_at="estropeada"))
    assert rec["decision"] == "se_queda"
    assert rec["challenger_brier"] > rec["champion_brier"]
    assert (tmp_path / "roof_calibration.json").read_text() == antes
    assert CC.decisions(tmp_path)[-1]["decision"] == "se_queda"


def test_sin_vigente_entra_la_aspirante(tmp_path):
    _examen(tmp_path)
    rec = CC.roof_duel(tmp_path, now=dt.datetime(2026, 10, 21, 10, 30, tzinfo=UTC),
                       data_repo=tmp_path, challenger=_curva(2.5, -5.0, "primera"))
    assert rec["decision"] == "arranque_en_frio"
    assert (tmp_path / "roof_calibration.json").exists()


# --- 5. la salud ---------------------------------------------------------------------------

def _cadencia(root, por_dia, dias=8, hasta=dt.datetime(2026, 10, 26, 22, 0, tzinfo=UTC)):
    filas = []
    for d in range(dias, 0, -1):
        dia = (hasta - dt.timedelta(days=d)).replace(hour=0, minute=0)
        for i in range(por_dia):
            t = dia + dt.timedelta(minutes=int(i * 1440 / por_dia))
            filas.append({"site_id": "nerpio", "t_utc": t, "instrument_id": "astrocamp_roofs",
                          "instrument_type": "RoofStatus", "roof_open": False,
                          "quality_flags": "open=0/4", "retrieved_at": t})
    filas.append({"site_id": "nerpio", "t_utc": hasta, "instrument_id": "astrocamp_roofs",
                  "instrument_type": "RoofStatus", "roof_open": False,
                  "quality_flags": "open=0/4", "retrieved_at": hasta})
    write_canonical(filas, dataset="measurements", site_id="nerpio", root=root)


def test_la_alarma_del_techo_salta_con_la_cadencia_de_octubre(tmp_path):
    _cadencia(tmp_path, por_dia=4)                       # octubre: 2-4 lecturas al dia
    s = H.sensor_block(tmp_path, dt.datetime(2026, 10, 26, 22, 30, tzinfo=UTC))
    assert s["techo"]["nivel"] == "rojo" and s["techo"]["lecturas_ayer"] == 4
    # Y lo que no tiene ni una lectura sale en rojo, no en silencio.
    assert s["cloudwatcher"]["nivel"] == "rojo" and s["cloudwatcher"]["ultima_utc"] is None


def test_y_no_salta_con_la_de_septiembre(tmp_path):
    _cadencia(tmp_path, por_dia=36)                      # septiembre: 35,7 al dia
    s = H.sensor_block(tmp_path, dt.datetime(2026, 10, 26, 22, 30, tzinfo=UTC))
    assert s["techo"]["nivel"] == "ok", s["techo"]["motivo"]


def _notas(probabilidades, abrio):
    out = {}
    for i, (p, o) in enumerate(zip(probabilidades, abrio)):
        noche = dt.date(2026, 10, 25) - dt.timedelta(days=len(abrio) - 1 - i)
        out[noche] = {"roof_opened": o, "d0_p_open": p, "d0_open": p >= 0.5,
                      "d0_outcome": NS._outcome(p >= 0.5, o)}
    return out


def test_la_nota_movil_en_rojo_si_va_peor_que_la_climatologia():
    abrio = [True] * 11 + [False] * 3
    hundida = _notas([0.3] * 11 + [0.6] * 3, abrio)          # al reves de lo que paso
    bloque = H.score_block(hundida, dt.date(2026, 10, 26))
    assert bloque["nivel"] == "rojo" and bloque["brier"] > bloque["brier_climatologia"]
    buena = _notas([0.9] * 11 + [0.1] * 3, abrio)
    assert H.score_block(buena, dt.date(2026, 10, 26))["nivel"] == "ok"


def test_la_fusion_del_estado_conserva_la_salud_mas_reciente():
    from astroweather.archivepush import _fundir
    viejo = {"escrito_utc": "a", "trabajos": {}, "salud": {"calculado_utc": "2026-10-26T10:00:00",
                                                           "nivel": "ok"}}
    nuevo = {"escrito_utc": "b", "trabajos": {}, "salud": {"calculado_utc": "2026-10-26T11:00:00",
                                                           "nivel": "rojo"}}
    assert _fundir(viejo, nuevo)["salud"]["nivel"] == "rojo"
    assert _fundir(nuevo, viejo)["salud"]["nivel"] == "rojo"
    assert "salud" not in _fundir({"trabajos": {}}, {"trabajos": {}})


def test_el_estado_del_colector_lleva_la_salud(tmp_path):
    from astroweather import colector
    H.write({"version": 1, "calculado_utc": "2026-10-26T10:00:00+00:00", "nivel": "aviso"},
            tmp_path)
    ruta = colector.escribir_estado({"nota": dt.datetime(2026, 10, 26, 10, tzinfo=UTC)}, tmp_path)
    estado = json.loads(ruta.read_text(encoding="utf-8"))
    assert estado["salud"]["nivel"] == "aviso" and "nota" in estado["trabajos"]
    # y el colector la sigue leyendo como antes
    assert "nota" in colector.leer_estado(tmp_path)


def test_el_marcador_solo_se_publica_si_cambia_algo_que_importa(tmp_path):
    from astroweather import webpublish
    repo = tmp_path / "datos"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    for clave, valor in (("user.email", "test@example.com"), ("user.name", "Test")):
        subprocess.run(["git", "-C", str(repo), "config", clave, valor], check=True)

    def marcador(calculado, edad, nivel):
        return {"generado_utc": calculado, "salud": {
            "calculado_utc": calculado, "nivel": nivel,
            "sensores": {"techo": {"nivel": nivel, "edad_h": edad, "ultima_utc": calculado}}},
            "ultimas_noches": []}

    assert webpublish.publicar_marcador(payload=marcador("t1", 0.5, "rojo"), repo=repo, push=False)
    # Solo cambian la hora y la edad de la lectura: no deja rastro.
    assert webpublish.publicar_marcador(payload=marcador("t2", 1.5, "rojo"), repo=repo,
                                        push=False) is None
    # Cambia un nivel: si.
    assert webpublish.publicar_marcador(payload=marcador("t3", 0.2, "ok"), repo=repo, push=False)


def test_los_trabajos_nuevos_van_en_su_sitio():
    from astroweather.scheduler import JOBS
    orden = list(JOBS)
    assert orden.index("tess") < orden.index("nota")
    assert orden.index("allsky") < orden.index("nota")
    assert orden.index("nota") < orden.index("techo_curva")
    assert orden[-2:] == ["salud", "archivo"]
    assert JOBS["nota"].desde_hora_utc == JOBS["techo_curva"].desde_hora_utc == 10


def test_los_almacenes_nuevos_viajan_al_archivo():
    from astroweather.archivepush import MIRRORED
    for almacen in ("night_publications", "night_scores", "curve_decisions"):
        assert almacen in MIRRORED
