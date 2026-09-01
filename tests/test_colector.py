# -*- coding: utf-8 -*-
"""La recogida en una maquina que se destruye al terminar.

Lo que estos tests protegen no es el calendario, es la propiedad que hace que
la recogida pueda vivir en GitHub Actions: **una pasada perdida se recupera
sola**. Los cron de GitHub son best-effort -- el propio workflow del EPS lleva
escrito que su primera cita no llego a existir -- asi que preguntar "que hora
es" no vale y hay que preguntar "que lleva demasiado sin correr".
"""

import datetime as dt

import pytest

from astroweather import colector
from astroweather.scheduler import JOBS, TALLY, Trabajo

AHORA = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def tabla(monkeypatch):
    """Una tabla de juguete con las tres formas que existen de verdad."""
    hechos = []
    falla = {"rapido": False}

    def _rapido():
        if falla["rapido"]:
            raise RuntimeError("la fuente no responde")
        hechos.append("rapido")

    def _lento():
        hechos.append("lento")

    def _de_manana():
        hechos.append("de_manana")

    def _empuja():
        hechos.append("archivo")

    falsa = {
        "rapido": Trabajo(_rapido, dt.timedelta(minutes=30)),
        "lento": Trabajo(_lento, dt.timedelta(hours=6)),
        "de_manana": Trabajo(_de_manana, dt.timedelta(hours=24), desde_hora_utc=8),
        "archivo": Trabajo(_empuja, dt.timedelta(minutes=30)),
    }
    monkeypatch.setattr(colector, "JOBS", falsa)
    TALLY.clear()
    return {"hechos": hechos, "falla": falla, "jobs": falsa}


def test_sin_estado_esta_todo_vencido(tabla):
    """Primera pasada de una maquina nueva: se hace todo una vez.

    Es el lado seguro al que hay que caerse si el estado se pierde: repetir es
    barato y el deduplicador lo absorbe; saltarse una recogida no se recupera.
    """
    assert colector.vencidos(AHORA, {}) == ["rapido", "lento", "de_manana", "archivo"]


def test_lo_recien_hecho_no_se_repite(tabla):
    estado = {"rapido": AHORA - dt.timedelta(minutes=10),
              "lento": AHORA - dt.timedelta(hours=1),
              "de_manana": AHORA - dt.timedelta(hours=4),
              "archivo": AHORA - dt.timedelta(minutes=10)}
    assert colector.vencidos(AHORA, estado) == []


def test_pasado_el_periodo_vuelve_a_tocar(tabla):
    estado = {n: AHORA - dt.timedelta(minutes=31) for n in tabla["jobs"]}
    assert "rapido" in colector.vencidos(AHORA, estado)
    assert "lento" not in colector.vencidos(AHORA, estado)


def test_una_pasada_perdida_se_recupera_sola(tabla):
    """La propiedad que justifica todo esto.

    Si el runner no llego a arrancar en tres horas, la siguiente pasada ve el
    trabajo vencido y lo hace. Con un cron, esas citas simplemente no habrian
    existido.
    """
    estado = {"rapido": AHORA - dt.timedelta(hours=3)}
    assert "rapido" in colector.vencidos(AHORA, estado)


def test_lo_de_la_manana_espera_a_su_hora(tabla):
    """Verificar la noche anterior antes de que termine mide otra cosa."""
    de_madrugada = AHORA.replace(hour=3)
    assert "de_manana" not in colector.vencidos(de_madrugada, {})
    assert "rapido" in colector.vencidos(de_madrugada, {})


def test_lo_de_la_manana_no_se_repite_toda_la_tarde(tabla):
    """Ya corrio hoy a las 08:10; a las 12:00 no vuelve a tocar.

    Mirando solo el periodo seguiria vencido hasta manana a las 08:10, asi que
    correria en CADA pasada de la tarde: 22 veces en vez de una.
    """
    estado = {"de_manana": AHORA.replace(hour=8, minute=10)}
    assert "de_manana" not in colector.vencidos(AHORA, estado)


def test_el_que_falla_no_marca_fecha_y_se_reintenta(tabla, tmp_path):
    """Un trabajo caido sigue vencido; los demas no se enteran."""
    tabla["falla"]["rapido"] = True
    resumen = colector.pasada(ahora=AHORA, root=tmp_path)

    assert resumen["fallidos"] == ["rapido"]
    assert "lento" in resumen["ejecutados"]
    estado = colector.leer_estado(tmp_path)
    assert "rapido" not in estado, "un fallo no puede marcarse como hecho"
    assert "lento" in estado


def test_el_archivo_va_el_ultimo_y_con_el_estado_ya_escrito(tabla, tmp_path):
    """`archivo` es el que EMPUJA: en un runner efimero, lo que no suba muere.

    Y tiene que salir DESPUES de escribir el estado, o la marca de esta pasada
    se quedaria en un disco que se destruye y todo volveria a estar vencido.
    """
    escrito = {}
    original = colector.escribir_estado

    def espia(estado, root=None):
        escrito["antes_de_archivar"] = "archivo" not in tabla["hechos"]
        return original(estado, root)

    colector.escribir_estado = espia
    try:
        resumen = colector.pasada(ahora=AHORA, root=tmp_path)
    finally:
        colector.escribir_estado = original

    assert tabla["hechos"][-1] == "archivo"
    assert escrito["antes_de_archivar"] is True
    assert resumen["ejecutados"][-1] == "archivo"


def test_los_trabajos_de_verdad_corren_en_orden_de_dependencia():
    """Sobre la tabla REAL, no sobre la de juguete.

    La prevision antes que la prediccion que la archiva, la web antes de
    publicarla, el fotometro antes del suelo que se mide contra el, y el
    archivo el ultimo. Es orden de insercion en `JOBS`, y por eso un
    reordenamiento distraido tiene que romper algo.
    """
    orden = list(JOBS)
    assert orden.index("forecast") < orden.index("prediccion")
    assert orden.index("prediccion") < orden.index("publicar")
    assert orden.index("tess") < orden.index("suelo")
    assert orden[-1] == "archivo" == colector.ULTIMO


def test_cada_trabajo_declara_su_periodo():
    """Sin periodo no hay forma de saber si algo esta vencido."""
    for nombre, trabajo in JOBS.items():
        assert trabajo.periodo > dt.timedelta(0), nombre
        assert callable(trabajo.funcion), nombre


def test_forzar_arrastra_siempre_al_que_empuja(monkeypatch, tmp_path):
    """Una pasada que CALCULA y no PERSISTE es peor que no correr.

    Medido el 01-09-2026: `--forzar aerosol` corrio el trabajo, escribio sus
    161 filas -- con las 41 de especies de aerosol que llevaban seis dias sin
    llegar al archivo -- y las tiro a la basura al morir el runner, porque
    `archivo` es el unico trabajo que commitea y `--forzar` lo habia dejado
    fuera de `only`. La pasada salio en verde y el dato no existe.
    """
    import astroweather.colector as colector

    vistos = {}

    def falso_pasada(*, ahora=None, root=None, only=None, en_seco=False):
        vistos["only"] = only
        return {"ahora": "2026-09-01T20:00:00+00:00", "vencidos": [],
                "ejecutados": [], "fallidos": []}

    monkeypatch.setattr(colector, "pasada", falso_pasada)
    monkeypatch.setattr(colector, "leer_estado", lambda root=None: {})
    monkeypatch.setattr(colector, "escribir_estado",
                        lambda estado, root=None: tmp_path / "estado.json")
    monkeypatch.setattr("sys.argv", ["colector", "--forzar", "aerosol"])

    colector.main()
    assert vistos["only"] == {"aerosol", colector.ULTIMO}


def test_sin_forzar_no_se_limita_nada(monkeypatch, tmp_path):
    """La pasada normal sigue mirando TODOS los trabajos, no solo el que empuja."""
    import astroweather.colector as colector

    vistos = {}

    def falso_pasada(*, ahora=None, root=None, only=None, en_seco=False):
        vistos["only"] = only
        return {"ahora": "2026-09-01T20:00:00+00:00", "vencidos": [],
                "ejecutados": [], "fallidos": []}

    monkeypatch.setattr(colector, "pasada", falso_pasada)
    monkeypatch.setattr(colector, "leer_estado", lambda root=None: {})
    monkeypatch.setattr("sys.argv", ["colector"])

    colector.main()
    assert vistos["only"] is None


def test_forzar_deja_al_que_empuja_VENCIDO_y_no_solo_permitido(monkeypatch, tmp_path):
    """Colarlo en `only` no basta: `only` respeta el periodo.

    Medido en el run #53 del 01-09-2026. `archivo` corre cada 10 minutos y una
    pasada normal acababa de empujar hacia cuatro, asi que no estaba vencido y
    el filtro abierto no sirvio de nada: el aerosol escribio sus 161 filas y el
    log dijo `hechos: aerosol` a secas. El primer test que escribi para esto
    comprobaba el filtro y pasaba en verde mientras el fallo seguia vivo.
    """
    import datetime as dt

    import astroweather.colector as colector

    guardado = {}
    ahora = dt.datetime(2026, 9, 1, 20, 7, tzinfo=dt.timezone.utc)
    reciente = {"aerosol": ahora, colector.ULTIMO: ahora}

    monkeypatch.setattr(colector, "leer_estado", lambda root=None: dict(reciente))
    monkeypatch.setattr(colector, "escribir_estado",
                        lambda estado, root=None: guardado.update(estado) or tmp_path)
    monkeypatch.setattr(colector, "pasada",
                        lambda **kw: {"ahora": "x", "vencidos": [],
                                      "ejecutados": [], "fallidos": []})
    monkeypatch.setattr("sys.argv", ["colector", "--forzar", "aerosol"])

    colector.main()
    assert "aerosol" not in guardado, "el trabajo forzado tiene que quedar vencido"
    assert colector.ULTIMO not in guardado, (
        "y el que empuja tambien, o la pasada calcula y no persiste")
