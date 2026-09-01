# -*- coding: utf-8 -*-
"""En la frontera de escritura, una fila rota se rechaza entera.

LA CATEGORIA QUE FALTABA. Teniamos 965 tests que comprueban aritmetica dando
por hecho que el mundo exterior es perfecto, y ni uno que cogiera un dato REAL,
le quitara un campo y exigiera que el sistema se entere. Esa ausencia es la que
dejo pasar seis dias de aerosol sin especies y cuatro de puertas de seguridad
ciegas: los dos fallos escribian filas perfectamente validas segun el esquema.

Lo que se exige aqui no es que el pipeline "no reviente". Es lo contrario: que
REVIENTE, en el sitio donde todavia se puede distinguir "no lo sabiamos" de "no
lo habia". Una vez la fila esta en el Parquet, las dos cosas son iguales.
"""

import datetime as dt

import pytest

from astroweather.archive import write_canonical

UTC = dt.timezone.utc
CUANDO = dt.datetime(2026, 9, 1, 22, tzinfo=UTC)


def _fila(**cambios):
    """Una fila de prevision sana, como la escribiria un worker de verdad."""
    fila = {"site_id": "nerpio", "model": "ecmwf_ifs025", "run_utc": CUANDO,
            "valid_utc": CUANDO, "lead_h": 6, "member": None,
            "cloud_total": 12.0, "t2m": 18.5, "wind10m": 3.2}
    fila.update(cambios)
    return fila


def _escribir(fila, tmp_path, dataset="forecasts"):
    return write_canonical([fila], dataset=dataset, site_id="nerpio",
                           root=tmp_path)


# --------------------------------------------------------------------------
# La fila sana sigue pasando: sin esto, lo de abajo no probaria nada
# --------------------------------------------------------------------------

def test_una_fila_sana_se_escribe(tmp_path):
    assert _escribir(_fila(), tmp_path) is not None


def test_member_nulo_es_legitimo(tmp_path):
    """`member` es NULL en toda pasada determinista: no es un dato que falte."""
    assert _escribir(_fila(member=None), tmp_path) is not None


# --------------------------------------------------------------------------
# Identidad: una fila sin claves no se puede volver a encontrar
# --------------------------------------------------------------------------

@pytest.mark.parametrize("clave", ["site_id", "model", "run_utc", "valid_utc"])
def test_sin_una_clave_la_fila_se_rechaza(clave, tmp_path):
    """El test de contrato con datos basura: coge un dato real y quitale algo.

    `model` es el caso con nombre y fecha. El 28-08-2026 la consulta de la
    noche se filtro a `ecmwf_ifs025` y resulto que ese modelo no publica racha:
    cuatro dias de puertas de seguridad devolviendo "ok" sin mirar. Una fila a
    la que le falta de QUE modelo viene es exactamente esa clase de agujero,
    escrita en disco.
    """
    with pytest.raises(ValueError, match="no se puede volver a encontrar"):
        _escribir(_fila(**{clave: None}), tmp_path)


def test_una_fila_sin_ningun_valor_no_es_un_dato(tmp_path):
    """Claves perfectas y ni una medida: eso es ausencia con aspecto de dato.

    Es el fallo de la casa escrito en el sitio donde ya no tiene arreglo. Al
    leerla, `cloud_total IS NULL` no dice si el modelo no lo publica, si la
    descarga se rompio o si el cielo estaba raso.
    """
    vacia = {"site_id": "nerpio", "model": "ecmwf_ifs025",
             "run_utc": CUANDO, "valid_utc": CUANDO}
    with pytest.raises(ValueError, match="sin un solo valor"):
        _escribir(vacia, tmp_path)


# --------------------------------------------------------------------------
# Rangos: fuera de estos limites el numero no es raro, es imposible
# --------------------------------------------------------------------------

def test_una_cobertura_nubosa_no_puede_ser_3000(tmp_path):
    with pytest.raises(ValueError, match="fuera de"):
        _escribir(_fila(cloud_total=3000.0), tmp_path)


def test_una_fraccion_donde_se_espera_un_porcentaje_pasa_pero_el_reves_no(tmp_path):
    """0,12 es un 12 % mal convertido y NO se puede detectar: es un valor legal.

    Lo dejo escrito para que nadie crea que esta guarda pilla las unidades en
    los dos sentidos. Pilla la direccion que se sale del rango -- multiplicar
    por 100 de mas -- y no la que se queda dentro. Media guarda es lo que hay,
    y saberlo vale mas que creer que estamos cubiertos.
    """
    assert _escribir(_fila(cloud_total=0.12), tmp_path) is not None
    with pytest.raises(ValueError, match="fuera de"):
        _escribir(_fila(cloud_total=1200.0), tmp_path)


def test_el_viento_en_kmh_colado_como_ms_se_rechaza(tmp_path):
    """La invariante 1 del proyecto es que el viento viaja en m/s.

    Una racha de 200 "m/s" son 720 km/h: no existe. Casi siempre es km/h sin
    convertir, que es el error que la invariante existe para evitar.
    """
    with pytest.raises(ValueError, match="revisa las unidades"):
        _escribir(_fila(gust10m=200.0), tmp_path)


def test_un_aod_negativo_se_rechaza(tmp_path):
    """Un espesor optico negativo no significa nada.

    Importa aqui y no en abstracto: la interpolacion de la serie horaria
    (`hourly.at_times`) sostiene los extremos en vez de extrapolar precisamente
    para no fabricar uno de estos. Esta guarda es la segunda linea.
    """
    with pytest.raises(ValueError, match="fuera de"):
        _escribir(_fila(aod_total=-0.05), tmp_path)


# --------------------------------------------------------------------------
# Y una fila mala no puede colarse escondida entre buenas
# --------------------------------------------------------------------------

def test_una_sola_fila_rota_tumba_la_escritura_entera(tmp_path):
    """120 horas de AOD con una hora corrupta se rechazan las 120.

    Escribir 119 y callar la que falta es justo el fallo que perseguimos: el
    hueco resultante es indistinguible de "esa hora no se pronostico".
    """
    filas = [_fila(valid_utc=CUANDO + dt.timedelta(hours=h)) for h in range(5)]
    filas[3]["cloud_total"] = 5000.0
    with pytest.raises(ValueError, match=r"forecasts\[3\]"):
        write_canonical(filas, dataset="forecasts", site_id="nerpio",
                        root=tmp_path)
