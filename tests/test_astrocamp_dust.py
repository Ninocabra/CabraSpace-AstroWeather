# -*- coding: utf-8 -*-
"""Tests del PM2.5 de AstroCamp, que se lee de un PNG (08-09-2026).

Aqui no se esta probando un parser: se esta probando un INSTRUMENTO. El dato
no viene de un proveedor que lo declare, viene de contar pixeles de un dibujo,
y un digitalizador que se equivoca no da error -- da numeros. Asi que el test
que manda es un CONTROL POSITIVO: se inyectan curvas de valor conocido en el
PNG real, con su misma caja, su misma rejilla y su misma leyenda, y se exige
recuperarlas. Al escribirlo destapo TRES fallos que ya estaban en el codigo:

1. La calibracion iba contra las lineas de rejilla, y matplotlib no siempre las
   pone cada 10: en el primer control las puso cada 20 y el digitalizador
   devolvio la mitad, sin una queja.
2. Con la curva alta el relleno TAPA la rejilla de debajo -- a 45 ug/m3
   desaparecen 4 de las 9 lineas -- y el guardian saltaba en falso.
3. La leyenda es OPACA: por encima de ~90 ug/m3 el extractor devolvia su borde
   inferior como si fuera el sensor. Un 7% de menos, en silencio, y solo en el
   episodio mas fuerte que puede haber.

Lo que este fichero NO puede probar es que una marca del eje valga 10 ug/m3:
comprobarlo contra el propio dibujo seria circular. Eso lo cubre el guardian,
que se prueba en rojo mas abajo.
"""

import datetime as dt
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from astroweather.archive import write_canonical
from astroweather.site import NERPIO
from astroweather.workers import fetch_astrocamp_dust as polvo

FIXTURE = Path(__file__).parent / "fixtures" / "astrocamp_dust_20260908T1145Z.png"
# El Last-Modified real de esa descarga. Es la unica fecha que trae la imagen.
DIBUJADO = dt.datetime(2026, 9, 8, 11, 45, 12, tzinfo=dt.timezone.utc)

# Geometria del PNG real, comprobada abajo contra la que mide el digitalizador.
X0, X1, Y0, Y1 = 161, 1151, 116, 767
MARCA_MAS_BAJA, DY = 702.5, 65.375
REJILLA_Y = [179.5, 245.5, 310.5, 375.5, 441.5, 506.5, 571.5, 636.5, 702.5]
REJILLA_X = [169.5, 293.5, 417.5, 541.5, 665.5, 790.5, 914.5, 1038.5]
FONDO = np.array([204, 229, 204], np.uint8)
# Caja exacta de la leyenda en el PNG real (x0, x1, y0, y1).
LEYENDA = (173, 339, 128, 179)
GRIS = np.array([176, 176, 176], np.uint8)


def _y_de(valor: float) -> float:
    """La verdad terreno del eje: donde TIENE que caer un valor dado."""
    return MARCA_MAS_BAJA - (valor - 10.0) / 10.0 * DY


def _inyecta(valores: np.ndarray) -> Image.Image:
    """Repinta el PNG real con una curva de valor conocido.

    Se conserva todo lo demas -- marco, marcas del eje, leyenda, titulo -- para
    que lo que se pruebe sea el extractor entero contra el grafico de verdad y
    no una maqueta que se le parezca.
    """
    a = np.asarray(Image.open(FIXTURE).convert("RGB")).astype(np.uint8).copy()
    leyenda = a[LEYENDA[2]:LEYENDA[3] + 1, LEYENDA[0]:LEYENDA[1] + 1].copy()
    a[Y0:Y1, X0:X1] = FONDO
    for y in REJILLA_Y:
        a[int(y), X0:X1] = GRIS
    for x in REJILLA_X:
        a[Y0:Y1, int(x)] = GRIS
    for i, valor in enumerate(valores):
        y = int(round(min(max(_y_de(valor), Y0), Y1 - 1)))
        a[y:Y1, X0 + i] = polvo.AZUL_RELLENO
        a[max(Y0, y - 1):y + 2, X0 + i] = polvo.AZUL_LINEA
    # La leyenda, encima, que es el estorbo. Se pega su caja EXACTA: pegar de
    # mas tapaba curva que en el grafico real se ve, y el test mentia.
    a[LEYENDA[2]:LEYENDA[3] + 1, LEYENDA[0]:LEYENDA[1] + 1] = leyenda
    return Image.fromarray(a)


def _digitaliza_imagen(imagen: Image.Image, tmp_path: Path):
    destino = tmp_path / "caso.png"
    imagen.save(destino)
    return polvo.digitiza(destino, DIBUJADO)


N = X1 - X0


# --- el control positivo ----------------------------------------------------

@pytest.mark.parametrize("nombre, verdad", [
    ("bajo, como el dia que se calibro", 6 + 2 * np.sin(np.arange(N) / 30.0)),
    # 86 es lo mas alto que se puede medir entero: por encima empieza la
    # leyenda. El pico cae justo debajo de su borde, que es el caso limite.
    ("calima de 86, rozando el borde de la leyenda",
     6 + 80 * np.exp(-((np.arange(N) - N * 0.35) / (N * 0.10)) ** 2)),
    ("plano alto, que tapa media rejilla", np.full(N, 45.0)),
    ("escalon brusco", np.where(np.arange(N) < N // 2, 5.0, 60.0)),
])
def test_recupera_una_curva_de_valor_conocido(nombre, verdad, tmp_path):
    """El unico test que dice si este instrumento mide o se lo inventa."""
    _, serie, _ = _digitaliza_imagen(_inyecta(verdad), tmp_path)

    assert np.isfinite(serie).all(), "hay columnas sin curva: %s" % nombre
    error = np.abs(serie - verdad)
    # 0,23 ug/m3 es lo medido; el margen deja sitio al redondeo a pixel entero
    # (un pixel son 0,15 ug/m3) y no a un error de escala, que valdria x2 o x5.
    assert error.max() < 0.4, (
        "%s: error maximo %.3f ug/m3 -- un fallo de ESCALA se ve aqui como un "
        "factor, no como decimas" % (nombre, error.max()))


def test_la_muestra_de_color_de_la_leyenda_no_se_cuela_como_dato(tmp_path):
    """La leyenda lleva un trozo de linea azul a ~94 ug/m3.

    Es el fallo mas facil de cometer y el mas dificil de ver: con el dato bajo,
    un maximo de 94 en vez de 8 pasaria por un episodio de calima historico.
    """
    _, serie, _ = _digitaliza_imagen(_inyecta(np.full(N, 7.0)), tmp_path)

    assert serie.max() < 8.0, (
        "la muestra de la leyenda se ha colado: maximo %.1f ug/m3" % serie.max())


def test_lo_que_toca_el_techo_del_eje_sale_marcado(tmp_path):
    """El techo del eje esta en 99,7: lo que llega ahi es una cota inferior."""
    momentos, serie, diagnostico = _digitaliza_imagen(_inyecta(np.full(N, 99.5)), tmp_path)

    assert diagnostico["px_en_el_techo"] > 0
    filas = polvo.to_rows(momentos, serie, diagnostico, NERPIO, DIBUJADO)
    assert filas, "recortado no es lo mismo que ausente: se publica, marcado"
    assert all("recortado_por_el_techo_del_eje" in f["quality_flags"] for f in filas)


def test_la_leyenda_tapa_la_curva_alta_y_eso_no_se_publica_como_dato(tmp_path):
    """El fallo que destapo el control, y el peor de todos.

    La leyenda es OPACA. A 95 ug/m3 la curva pasa por detras y el extractor
    devolvia 88,4 -- un 7% de menos, en silencio, y SOLO en el episodio mas
    fuerte que puede haber, que es el unico que de verdad importa. Ahora esas
    columnas se declaran desconocidas y la hora sale marcada.
    """
    momentos, serie, diagnostico = _digitaliza_imagen(_inyecta(np.full(N, 95.0)), tmp_path)

    assert diagnostico["px_ocultos"] > 100, "la leyenda tapa ~180 columnas"
    tapadas = diagnostico["ocluido"]
    assert np.isnan(serie[tapadas]).all(), (
        "una columna tapada no puede salir con valor: seria el borde de la "
        "leyenda publicado como si fuera el sensor")
    # Y lo que SI se ve se sigue midiendo bien.
    assert np.nanmax(np.abs(serie[~tapadas] - 95.0)) < 0.4

    filas = polvo.to_rows(momentos, serie, diagnostico, NERPIO, DIBUJADO)
    marcadas = [f for f in filas if "parcialmente_oculto_por_la_leyenda" in f["quality_flags"]]
    assert marcadas, "las horas afectadas tienen que decir que su media es un minimo"


def test_una_curva_normal_no_se_marca_como_recortada(tmp_path):
    """Control negativo de la bandera: si se pusiera siempre, no diria nada."""
    momentos, serie, diagnostico = _digitaliza_imagen(_inyecta(np.full(N, 8.0)), tmp_path)

    assert diagnostico["px_en_el_techo"] == 0
    assert diagnostico["px_ocultos"] == 0, "control negativo de la oclusion"
    filas = polvo.to_rows(momentos, serie, diagnostico, NERPIO, DIBUJADO)
    assert not any("recortado" in f["quality_flags"] for f in filas)
    assert not any("oculto" in f["quality_flags"] for f in filas)


# --- el guardian, visto en rojo ---------------------------------------------

def test_el_guardian_salta_si_el_eje_cambia_de_escala(tmp_path):
    """EL fallo silencioso que justifica todo esto.

    Si AstroCamp pasa el eje a 0-200 con marcas cada 50, la caja es la misma y
    la curva se sigue extrayendo igual de bien... con los numeros multiplicados
    por cinco. Nadie se enteraria mirando el resultado.
    """
    a = np.asarray(Image.open(FIXTURE).convert("RGB")).astype(np.uint8).copy()
    a[:, X0 - 8:X0 - 2] = 255                       # fuera las marcas de ahora
    for k in range(1, 4):                           # marcas cada 50: 3 en vez de 9
        y = int(Y1 - k * (Y1 - Y0) / 4.0)
        a[y - 1:y + 2, X0 - 8:X0 - 2] = 0
    destino = tmp_path / "reescalado.png"
    Image.fromarray(a).save(destino)

    with pytest.raises(polvo.GeometriaCambiada, match="marcas en el eje Y"):
        polvo.digitiza(destino, DIBUJADO)


VACIO = Path(__file__).parent / "fixtures" / "astrocamp_dust_vacio_20261125T1740Z.png"


def test_el_grafico_vacio_es_sensor_sin_datos_y_no_cambio_de_escala():
    """PNG real del 25-11-2026: AstroCamp sin datos de polvo.

    matplotlib reescala los ejes de un grafico vacio y el guardian lo tomaba
    por un cambio de escala. Tiene que seguir sin publicar, pero diciendo que
    el sensor no manda datos.
    """
    with pytest.raises(polvo.SensorSinDatos, match="vacio"):
        polvo.digitiza(VACIO, dt.datetime(2026, 11, 25, 17, 40, tzinfo=dt.timezone.utc))


def test_una_curva_pegada_al_cero_no_se_toma_por_grafico_vacio(tmp_path):
    """El caso que el detector de vacio podria comerse: aire limpisimo."""
    _, serie, _ = _digitaliza_imagen(_inyecta(np.full(N, 0.5)), tmp_path)

    assert np.isfinite(serie).mean() > 0.9


def test_el_guardian_salta_si_cambia_el_tamano_del_grafico(tmp_path):
    destino = tmp_path / "encogido.png"
    Image.open(FIXTURE).resize((1024, 768)).save(destino)

    with pytest.raises(polvo.GeometriaCambiada):
        polvo.digitiza(destino, DIBUJADO)


def test_sin_last_modified_no_se_inventa_la_fecha(monkeypatch):
    """El PNG no lleva fecha dentro. Sin la cabecera, el eje seria una suposicion."""
    class Respuesta:
        content = b""
        headers: dict = {}
        def raise_for_status(self): pass

    monkeypatch.setattr(polvo.requests, "get", lambda *a, **k: Respuesta())
    with pytest.raises(polvo.GeometriaCambiada, match="Last-Modified"):
        polvo.fetch()


# --- el grafico real --------------------------------------------------------

def test_el_png_real_se_lee_con_la_geometria_de_referencia():
    momentos, serie, diagnostico = polvo.digitiza(FIXTURE, DIBUJADO)

    assert len(serie) == N and np.isfinite(serie).all()
    assert diagnostico["dy"] == pytest.approx(polvo.REF["dy"], abs=0.01)
    assert diagnostico["horas"] == pytest.approx(24.0, abs=0.2)
    # Lo que se veia en el grafico del 08-09-2026: un dia tranquilo.
    assert 4.5 < serie.min() < 5.0
    assert 11.0 < serie.max() < 11.6


def test_el_eje_de_tiempos_se_ancla_en_la_rejilla_no_en_la_cabecera_http():
    """El Last-Modified va 3,6 min por detras del borde derecho del dibujo.

    Son 2,5 pixeles. Usarlo tal cual desplazaria la serie entera; anclando en
    la marca vertical, que es una hora EN PUNTO, el desfase se corrige solo.
    """
    momentos, _, diagnostico = polvo.digitiza(FIXTURE, DIBUJADO)

    assert diagnostico["desfase_min"] == pytest.approx(-3.6, abs=0.5)
    assert momentos == sorted(momentos)
    abarca = (momentos[-1] - momentos[0]).total_seconds() / 3600
    assert abarca == pytest.approx(24.0, abs=0.2)
    # El borde derecho es el momento del dibujo, no una hora redonda.
    assert abs((momentos[-1] - DIBUJADO).total_seconds()) < 5 * 60


def test_un_last_modified_de_otra_hora_mueve_la_serie_entera():
    """Control de que el ancla se USA: si no, este test no distinguiria nada."""
    _, _, _ = polvo.digitiza(FIXTURE, DIBUJADO)
    otros, _, _ = polvo.digitiza(FIXTURE, DIBUJADO + dt.timedelta(hours=3))
    propios, _, _ = polvo.digitiza(FIXTURE, DIBUJADO)

    assert (otros[-1] - propios[-1]) == dt.timedelta(hours=3)


# --- las filas --------------------------------------------------------------

def test_las_horas_son_medias_y_declaran_de_donde_salen():
    momentos, serie, diagnostico = polvo.digitiza(FIXTURE, DIBUJADO)
    filas = polvo.to_rows(momentos, serie, diagnostico, NERPIO, DIBUJADO)

    assert 22 <= len(filas) <= 24
    for fila in filas:
        assert fila["t_utc"].minute == 0 and fila["t_utc"].second == 0
        assert 4.0 < fila["pm2_5"] < 12.0
        assert fila["instrument_type"] == "AirQuality"
        assert fila["source"] == polvo.SOURCE
        # Que el numero salga de un dibujo tiene que viajar CON el numero:
        # dentro de dos anios nadie se va a acordar.
        assert "digitalizado_de_png" in fila["quality_flags"]
        assert "paso_eje=10" in fila["quality_flags"]
    assert [f["t_utc"] for f in filas] == sorted(f["t_utc"] for f in filas)


def test_una_hora_a_medias_en_el_borde_no_se_publica():
    """El grafico empieza a mitad de hora. Media hora no es una media horaria."""
    momentos, serie, diagnostico = polvo.digitiza(FIXTURE, DIBUJADO)
    filas = polvo.to_rows(momentos, serie, diagnostico, NERPIO, DIBUJADO)

    primera = momentos[0].replace(minute=0, second=0, microsecond=0)
    cuantos = sum(1 for m in momentos if m.replace(minute=0, second=0, microsecond=0) == primera)
    assert cuantos < polvo.PX_MINIMOS_POR_HORA
    assert filas[0]["t_utc"] > primera


def test_la_frontera_de_escritura_acepta_estas_filas(tmp_path):
    """Que el esquema conozca `pm2_5` no es evidente: se acaba de anadir."""
    momentos, serie, diagnostico = polvo.digitiza(FIXTURE, DIBUJADO)
    filas = polvo.to_rows(momentos, serie, diagnostico, NERPIO, DIBUJADO)

    destino = write_canonical(filas, dataset="measurements",
                              site_id=NERPIO.site_id, root=tmp_path)
    assert destino is not None and destino.exists()


def test_un_pm2_5_negativo_lo_para_el_esquema(tmp_path):
    """Control positivo del rango: si nunca lo vi en rojo, no se si discrimina."""
    fila = {"site_id": NERPIO.site_id, "t_utc": DIBUJADO, "pm2_5": -3.0,
            "instrument_type": "AirQuality", "source": polvo.SOURCE}

    with pytest.raises(ValueError, match="pm2_5"):
        write_canonical([fila], dataset="measurements",
                        site_id=NERPIO.site_id, root=tmp_path)
