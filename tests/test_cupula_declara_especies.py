# -*- coding: utf-8 -*-
"""La cupula tampoco puede avisar de calima sin decir si la ha medido.

El JSON de la web lleva `calima` y `especies` juntas desde el 01-09-2026, pero
la cupula -- el payload que sale de `nightview.build` y que la web pinta pixel a
pixel -- se quedo publicando `dust_alert` a solas. Y `false` a solas es
indistinguible de "no tenemos el desglose por especies": del 27-08 al 01-09
significo exactamente lo segundo durante seis dias, con `netCDF4` sin declarar
en el runner y polvo real por encima del umbral TODAS las horas del 01.

AQUI ADEMAS CAMBIA EL PIXEL, y por eso importa mas que en la tabla.
`extinction.evaluate` corrige el aerosol por la altura del sitio, y sin desglose
rebaja TAMBIEN el polvo -- que viaja por encima del observatorio y no se rebaja
-- asi que `aod_site`, que es de donde la cupula saca su rojo, sale corto: un
14 % en la k, medido el 01-09. El render se ve mas limpio de lo que esta y nada
en el JSON lo decia.
"""

import pytest

import astroweather.dome as dome
import astroweather.nightview as nightview
from astroweather.extinction import evaluate as evaluate_transparency
from astroweather.site import NERPIO


def _sin_nubes(monkeypatch, modulo):
    """La rejilla son 49 peticiones a Open-Meteo y aqui no pinta nada."""
    monkeypatch.setattr(modulo, "fetch_cloud_grid",
                        lambda site: ([], []))


# --------------------------------------------------------------------------
# La regla, en las dos vistas que publican transparencia
# --------------------------------------------------------------------------

@pytest.mark.parametrize("modulo", [nightview, dome])
def test_el_aviso_de_calima_viaja_con_su_medida(monkeypatch, modulo):
    """Si sale una, sale la otra. En la cupula y en la boveda."""
    _sin_nubes(monkeypatch, modulo)
    transp = modulo.build()["transparency"]
    assert ("dust_alert" in transp) == ("species_resolved" in transp), (
        "una sin la otra deja al lector sin saber si el false es una medida "
        "o un hueco")
    assert isinstance(transp["species_resolved"], bool)
    if transp["dust_alert"]:
        assert transp["species_resolved"] is True, (
            "no se puede avisar de calima sin tener el desglose que la define")


def test_la_cupula_publicada_conserva_la_pareja(monkeypatch):
    """`webexport._cupula` recorta claves: que no se lleve esta por delante.

    `CUPULA_FUERA` existe porque `nightview.build()` da 623 kB y la mayoria no
    se pinta. Un filtro por lista negra es facil de ampliar sin mirar, y el dia
    que alguien meta `transparency` ahi la cupula se quedaria sin su aviso de
    calima Y sin su declaracion, en silencio.
    """
    import astroweather.webexport as webexport
    _sin_nubes(monkeypatch, nightview)
    cupula = webexport._cupula(NERPIO)
    assert cupula is not None, "la cupula no se construyo"
    assert "species_resolved" in cupula["transparency"]
    assert "dust_alert" in cupula["transparency"]


# --------------------------------------------------------------------------
# Y el motivo por el que la bandera no es cosmetica: mueve el rojo
# --------------------------------------------------------------------------

def test_sin_especies_el_aod_del_pixel_sale_CORTO():
    """CONTROL POSITIVO de la frase que justifica el campo.

    Mismo aerosol total, con y sin desglose. Sin el, el polvo se rebaja por la
    altura del sitio como si fuera capa limite -- y el polvo sahariano viaja
    por ENCIMA del observatorio, que es justo el caso para el que existe la
    correccion -- asi que la cupula pinta menos calima de la que hay.

    Si este test empieza a fallar por el lado de la igualdad, es que alguien ha
    quitado la correccion por especies y el campo `species_resolved` ya no
    significa nada para el render.
    """
    con = evaluate_transparency(aod_total=0.30, aod_dust=0.18,
                                elevation_m=NERPIO.site_elevation,
                                delta_h_m=NERPIO.delta_h)
    sin = evaluate_transparency(aod_total=0.30, aod_dust=None,
                                elevation_m=NERPIO.site_elevation,
                                delta_h_m=NERPIO.delta_h)
    assert con.species_resolved is True and sin.species_resolved is False
    assert sin.aod_site < con.aod_site, (
        "sin desglose el aerosol del sitio tiene que salir MENOR: es el sesgo "
        "que la bandera avisa")
    assert sin.dust_alert is False and con.dust_alert is True, (
        "y ademas el aviso se calla, que es como se perdieron seis dias")
