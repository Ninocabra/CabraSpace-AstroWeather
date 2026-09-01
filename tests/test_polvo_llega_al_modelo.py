# -*- coding: utf-8 -*-
"""El polvo de CAMS tiene que llegar al MODELO, no solo al archivo.

TERCERA VEZ que este proyecto se come el mismo fallo: coger la FILA mas
reciente en vez del valor mas reciente de cada COLUMNA. Las dos anteriores
fueron la pasada de solo nubes desplazando a la de superficie completa (cuatro
dias de puertas de seguridad ciegas) y la nube contra la racha en `tonight`.

Aqui: al mismo `valid_utc` escriben dos fuentes de aerosol distintas.
`cams_openmeteo` trae el AOD total cada hora y se refresca a diario;
`cams_ads` trae el desglose por especies -- el polvo -- cada tres horas y con el
analisis de la vispera. La fila mas reciente es SIEMPRE la de Open-Meteo, que no
lleva polvo.

Medido en el archivo de produccion el 01-09-2026, ya con las 41 filas de
especies dentro: `night_inputs` seguia devolviendo `aod_dust` a None y el
modelo publicaba k = 0,3376 y `dust_alert: false` en una noche con polvo entre
0,156 y 0,180 todas las horas. Con el valor por columna: k = 0,3884, un 15 %
mas, y la calima declarada.

El dato estaba en disco. No llegaba.
"""

import datetime as dt

import pytest

from astroweather.archive import write_canonical
from astroweather.extinction import evaluate as evaluate_transparency
from astroweather.queries import night_inputs, night_series
from astroweather.site import NERPIO

UTC = dt.timezone.utc
CUANDO = dt.datetime(2026, 9, 1, 21, tzinfo=UTC)
VENTANA = (CUANDO - dt.timedelta(hours=1), CUANDO + dt.timedelta(hours=1))


def _archivo_minimo(root):
    """`connect` monta los dos almacenes: sin `measurements` no abre.

    Y si no abre, `night_inputs` devuelve sus constantes por defecto sin una
    queja -- degradar esta bien, pero un test que se apoye en eso estaria
    midiendo el fallback y no la consulta.
    """
    write_canonical([{
        "site_id": "nerpio", "t_utc": CUANDO, "sky_mag_raw": 20.9,
        "instrument_id": "stars4all_nerpio",
    }], dataset="measurements", site_id="nerpio", root=root)


@pytest.fixture
def archivo(tmp_path):
    """Las dos fuentes de aerosol como estan en la vida: distinta frescura.

    `cams_ads` es MAS VIEJO y es el unico que sabe cuanto del aerosol es polvo.
    """
    _archivo_minimo(tmp_path)
    write_canonical([{
        "site_id": "nerpio", "model": "cams_ads",
        "run_utc": dt.datetime(2026, 8, 31, tzinfo=UTC), "valid_utc": CUANDO,
        "aod_total": 0.274, "aod_dust": 0.178,
    }], dataset="forecasts", site_id="nerpio", root=tmp_path)
    write_canonical([{
        "site_id": "nerpio", "model": "cams_openmeteo",
        "run_utc": dt.datetime(2026, 9, 1, tzinfo=UTC), "valid_utc": CUANDO,
        "aod_total": 0.260, "aod_dust": None,
    }], dataset="forecasts", site_id="nerpio", root=tmp_path)
    return tmp_path


def test_el_polvo_sobrevive_a_una_fila_mas_fresca_sin_polvo(archivo):
    """Cada columna se queda con su valor mas reciente que EXISTE."""
    salida = night_inputs(*VENTANA, "nerpio", archivo)
    assert salida["aod_dust"] == pytest.approx(0.178, abs=1e-3), (
        "el polvo lo publica la fuente mas VIEJA; si se coge la fila mas "
        "fresca se pierde entero")
    assert salida["aod_total"] == pytest.approx(0.260, abs=1e-3), (
        "y el total sigue saliendo de la fuente mas fresca, que es lo suyo")
    assert salida["aod_source"] == "cams"


def test_la_serie_horaria_tambien_ve_el_polvo(archivo):
    """`night_series` alimenta la k de cada hora: el mismo fallo, el mismo sitio."""
    serie = night_series(*VENTANA, "nerpio", archivo)
    con_polvo = [p["aod_dust"] for p in serie.values()
                 if p.get("aod_dust") is not None]
    assert con_polvo and con_polvo[0] == pytest.approx(0.178, abs=1e-3)


def test_sin_especies_no_se_inventa_un_desglose(tmp_path):
    """Sin polvo medido, `aod_dust` es None y NO una constante plausible.

    Hasta el 01-09-2026 se rellenaba con 0,01, y eso dejaba
    `species_resolved` en True: el producto afirmaba saber que fraccion del
    aerosol era polvo cuando lo unico que tenia era un numero elegido para no
    llamar la atencion. Es el fallo de las constantes plausibles otra vez.
    """
    _archivo_minimo(tmp_path)
    write_canonical([{
        "site_id": "nerpio", "model": "cams_openmeteo",
        "run_utc": dt.datetime(2026, 9, 1, tzinfo=UTC), "valid_utc": CUANDO,
        "aod_total": 0.260, "aod_dust": None,
    }], dataset="forecasts", site_id="nerpio", root=tmp_path)

    salida = night_inputs(*VENTANA, "nerpio", tmp_path)
    assert salida["aod_dust"] is None
    transparencia = evaluate_transparency(
        aod_total=salida["aod_total"], aod_dust=salida["aod_dust"],
        elevation_m=NERPIO.site_elevation, delta_h_m=NERPIO.delta_h)
    assert transparencia.species_resolved is False, (
        "sin desglose medido, el producto tiene que DECIR que no lo tiene")


def test_con_polvo_la_extincion_sube_y_la_calima_salta(archivo):
    """Lo que costaba el fallo, en las dos magnitudes que se publican.

    El polvo sahariano viaja en capas por ENCIMA del observatorio, asi que no se
    corrige por altura; el resto del aerosol si. Tratar el total como capa
    limite deja la extincion baja y apaga la alerta.
    """
    salida = night_inputs(*VENTANA, "nerpio", archivo)
    con = evaluate_transparency(
        aod_total=salida["aod_total"], aod_dust=salida["aod_dust"],
        elevation_m=NERPIO.site_elevation, delta_h_m=NERPIO.delta_h)
    sin = evaluate_transparency(
        aod_total=salida["aod_total"], aod_dust=None,
        elevation_m=NERPIO.site_elevation, delta_h_m=NERPIO.delta_h)

    assert con.k_total > sin.k_total * 1.10, (
        "sin especies la k se queda corta y no por poco: medido un 14 % "
        "el 01-09-2026")
    assert con.dust_alert is True
    assert sin.dust_alert is False, (
        "y la alerta no puede saltar sin el dato: eso seria el fallo simetrico")
