"""Tests del campo de nubes de MAPA (21-09-2026).

Lo que hay que proteger aqui no es la aritmetica, es el CONTRATO con quien
pinta al otro lado. Un mapa mal leido no se cae: sale plausible. Si el orden de
las celdas se invierte, Espana aparece espejada y con las nubes en el sitio
equivocado, y eso no lo detecta nadie mirando -- un cielo nublado por el oeste
en vez de por el este sigue pareciendo un mapa. Por eso casi todo lo de aqui
comprueba el orden, el anclaje y lo que significa un hueco.
"""

import datetime as dt
import math

import pytest

from astroweather import cloudmap
from astroweather.site import NERPIO


def _respuestas(n, marcas, valor=50, viento=(5.0, 90.0, 7.0)):
    """Lo que devolveria el proveedor: una entrada por punto, en orden."""
    velocidad, direccion, racha = viento
    fijo = {cloudmap.VIENTO["velocidad"]: [velocidad] * len(marcas),
            cloudmap.VIENTO["direccion"]: [direccion] * len(marcas),
            cloudmap.VIENTO["racha"]: [racha] * len(marcas)}
    return [{"hourly": {"time": list(marcas),
                        **{v: [valor] * len(marcas) for v in cloudmap.CAMPOS.values()},
                        **fijo}}
            for _ in range(n)]


def _marcas(desde, cuantas):
    return [(desde + dt.timedelta(hours=k)).strftime("%Y-%m-%dT%H:%M")
            for k in range(cuantas)]


def test_el_sitio_cae_exactamente_en_una_celda():
    """La razon de anclar la rejilla en el sitio y no en una caja redonda.

    Si Nerpio cayera entre celdas, el numero que se pinta sobre el observatorio
    seria una interpolacion de cuatro vecinos y no un valor del modelo, y
    entonces no se podria cruzar con el fotometro.
    """
    r = cloudmap.rejilla()
    coords = cloudmap.puntos()
    centro = coords[r["iy"] * r["nx"] + r["ix"]]

    assert centro[0] == pytest.approx(NERPIO.latitude, abs=1e-6)
    assert centro[1] == pytest.approx(NERPIO.longitude, abs=1e-6)


def test_el_orden_declarado_es_el_orden_real():
    """El fichero DICE `j*nx+i`, de sur a norte y de oeste a este. Si `puntos`
    no lo cumple, el mapa sale espejado y parece correcto."""
    r = cloudmap.rejilla()
    coords = cloudmap.puntos()

    assert len(coords) == r["nx"] * r["ny"]
    # Dentro de una fila, la longitud crece hacia el este.
    fila = coords[:r["nx"]]
    assert all(b[1] > a[1] for a, b in zip(fila, fila[1:]))
    assert all(p[0] == pytest.approx(fila[0][0]) for p in fila)
    # Entre filas, la latitud crece hacia el norte.
    primeras = [coords[j * r["nx"]][0] for j in range(r["ny"])]
    assert all(b > a for a, b in zip(primeras, primeras[1:]))


def test_la_caja_cubre_el_mapa_que_pinta_la_web():
    """De donde salen el 15 y el 7. Si la rejilla se queda corta, el campo no
    llega a los bordes del marco y el borde vacio parece cielo despejado."""
    r = cloudmap.rejilla()
    km_lat = 111.32
    km_lon = km_lat * math.cos(math.radians(NERPIO.latitude))
    ancho = (r["nx"] - 1) * r["paso"] * km_lon
    alto = (r["ny"] - 1) * r["paso"] * km_lat

    assert ancho > 1300, f"solo {ancho:.0f} km de ancho"
    assert alto > 770, f"solo {alto:.0f} km de alto"
    # Y con la proporcion del marco de la web, o habria que estirar algo.
    assert abs(ancho / alto - 1.686) < 0.02, f"proporcion {ancho / alto:.3f}"


def test_un_hueco_no_es_cero_nubes():
    """El error que este fichero existe para no cometer. Si el proveedor no
    manda valor y se publica un 0, el mapa dibuja cielo despejado sobre una
    zona de la que no se sabe nada."""
    texto = cloudmap.codificar([0, None, 100])
    vuelta = cloudmap.decodificar(texto)

    assert vuelta[0] == 0
    assert vuelta[1] is None
    assert vuelta[2] == 100
    assert texto[1] == cloudmap.SIN_DATO
    assert texto[0] != texto[1], "el hueco y el cero no pueden ser el mismo caracter"


def test_la_ida_y_vuelta_solo_pierde_la_cuantizacion():
    valores = [0, 1, 7, 13, 50, 63, 87, 99, 100]
    vuelta = cloudmap.decodificar(cloudmap.codificar(valores))

    for original, leido in zip(valores, vuelta):
        assert abs(leido - original) <= cloudmap.PASO_CUANT / 2 + 1e-9


def test_todo_valor_cabe_en_un_caracter_imprimible():
    """Si algun codigo se saliera del rango imprimible, el JSON tendria que
    escaparlo y el fichero dejaria de pesar lo que se ha calculado que pesa."""
    texto = cloudmap.codificar(list(range(0, 101)) + [None])

    assert len(texto) == 102
    assert all(33 <= ord(c) <= 126 for c in texto)
    assert '"' not in texto and "\\" not in texto


def test_se_publica_desde_la_hora_actual_y_no_desde_medianoche():
    """Open-Meteo devuelve el dia entero desde las 00:00. Publicar lo ya
    pasado engorda el fichero y ademas contradice al satelite, que si es
    medida."""
    desde = dt.datetime(2026, 9, 21, 0, 0, tzinfo=dt.timezone.utc)
    marcas = _marcas(desde, 72)
    ahora = dt.datetime(2026, 9, 21, 14, 20, tzinfo=dt.timezone.utc)
    r = cloudmap.rejilla()

    payload = cloudmap.montar(_respuestas(r["nx"] * r["ny"], marcas), ahora=ahora)

    assert payload["horas"][0] == "2026-09-21T14:00Z"
    assert len(payload["horas"]) == min(cloudmap.HORAS, 72 - 14)


def test_una_respuesta_corta_no_se_publica_a_medias():
    """El orden es el contrato: el proveedor contesta a N coordenadas con N
    entradas en la misma posicion. Si llegan menos, no se sabe CUALES faltan,
    asi que publicar lo que hay seria publicar un mapa desplazado."""
    marcas = _marcas(dt.datetime(2026, 9, 21, 0, 0, tzinfo=dt.timezone.utc), 72)
    r = cloudmap.rejilla()

    with pytest.raises(ValueError):
        cloudmap.montar(_respuestas(r["nx"] * r["ny"] - 1, marcas))


def test_cada_hora_trae_una_celda_por_punto():
    marcas = _marcas(dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.timezone.utc), 20)
    ahora = dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.timezone.utc)
    r = cloudmap.rejilla()

    payload = cloudmap.montar(_respuestas(r["nx"] * r["ny"], marcas), ahora=ahora)

    assert set(payload["campos"]) == set(cloudmap.CAMPOS)
    for nombre, serie in payload["campos"].items():
        assert len(serie) == len(payload["horas"]), nombre
        assert all(len(h) == r["nx"] * r["ny"] for h in serie), nombre


def test_el_fichero_dice_como_se_descodifica():
    """Un campo codificado sin su clave DENTRO es un campo que en seis meses
    nadie sabe leer."""
    marcas = _marcas(dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.timezone.utc), 5)
    r = cloudmap.rejilla()

    payload = cloudmap.montar(_respuestas(r["nx"] * r["ny"], marcas),
                              ahora=dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.timezone.utc))

    clave = payload["codificacion"]
    assert clave["base"] == cloudmap.BASE and clave["paso"] == cloudmap.PASO_CUANT
    assert clave["sin_dato"] == cloudmap.SIN_DATO
    assert "orden" in payload["rejilla"]
    assert payload["modelo"] == cloudmap.MODELO


# ------------------------------------------------------ el viento (flechas)

def test_el_viento_viaja_en_componentes_y_no_en_angulo():
    """LA razon de publicar u/v y no velocidad y direccion.

    La direccion es un angulo y promediar angulos esta mal: entre 350 y 10
    grados la media aritmetica da 180, que es el viento CONTRARIO. Eso pasaria
    en cada paso intermedio de la animacion. Con componentes no hay nada que
    arreglar, y este test lo ensena con el caso que rompe.
    """
    u_a, v_a = cloudmap._uv(10.0, 350.0)
    u_b, v_b = cloudmap._uv(10.0, 10.0)
    # Las dos soplan casi hacia el sur: la v tiene que ser negativa en ambas.
    assert v_a < 0 and v_b < 0
    # Y su media tambien, que es lo que la media de angulos se cargaba.
    assert (v_a + v_b) / 2 < 0, "interpolar el viento no puede darle la vuelta"
    assert abs((u_a + u_b) / 2) < 0.5, "las dos componentes este-oeste se cancelan"


def test_la_direccion_meteorologica_es_de_donde_viene():
    """0 grados es viento DEL norte, o sea que sopla HACIA el sur."""
    u, v = cloudmap._uv(10.0, 0.0)
    assert abs(u) < 1e-9 and v == pytest.approx(-10.0)
    u, v = cloudmap._uv(10.0, 270.0)      # del oeste: sopla hacia el este
    assert u == pytest.approx(10.0) and abs(v) < 1e-9


def test_el_viento_va_en_rejilla_gruesa_y_lleva_sus_umbrales():
    marcas = _marcas(dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.timezone.utc), 10)
    r = cloudmap.rejilla()
    payload = cloudmap.montar(_respuestas(r["nx"] * r["ny"], marcas),
                              ahora=dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.timezone.utc))
    v = payload["viento"]

    assert v["nx"] < r["nx"] and v["ny"] < r["ny"], "la rejilla del viento es mas gruesa"
    for nombre in ("u", "v", "racha"):
        assert len(v["campos"][nombre]) == len(payload["horas"]), nombre
        assert all(len(h) == v["nx"] * v["ny"] for h in v["campos"][nombre]), nombre
    # Los umbrales son los de `gates.py`, no unos inventados para el mapa.
    from astroweather.gates import GUST_DEGRADED, GUST_CLOSE
    assert v["umbrales"]["degradado"] == GUST_DEGRADED
    assert v["umbrales"]["cierre"] == GUST_CLOSE


def test_la_ida_y_vuelta_del_viento():
    """5 m/s del este (90 grados) sopla hacia el OESTE: u negativa."""
    marcas = _marcas(dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.timezone.utc), 3)
    r = cloudmap.rejilla()
    payload = cloudmap.montar(
        _respuestas(r["nx"] * r["ny"], marcas, viento=(5.0, 90.0, 9.0)),
        ahora=dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.timezone.utc))
    v = payload["viento"]

    u = ord(v["campos"]["u"][0][0]) - cloudmap.BASE - v["offset"]
    w = ord(v["campos"]["v"][0][0]) - cloudmap.BASE - v["offset"]
    racha = ord(v["campos"]["racha"][0][0]) - cloudmap.BASE

    assert u == pytest.approx(-5.0, abs=1.0)
    assert abs(w) <= 1.0
    assert racha == pytest.approx(9.0, abs=1.0)


def test_el_sitio_tambien_tiene_flecha():
    """La celda de Nerpio es la 15 de 31, impar. Tomando una de cada dos desde
    el cero, el observatorio se quedaba entre dos flechas y la que se leia como
    suya era la de 55 km al nordeste."""
    marcas = _marcas(dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.timezone.utc), 3)
    r = cloudmap.rejilla()
    payload = cloudmap.montar(_respuestas(r["nx"] * r["ny"], marcas),
                              ahora=dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.timezone.utc))
    v = payload["viento"]

    ies = list(range(v["i0"], r["nx"], v["salto"]))
    jotas = list(range(v["j0"], r["ny"], v["salto"]))
    assert r["ix"] in ies, "el sitio no cae en ninguna columna de flechas"
    assert r["iy"] in jotas, "el sitio no cae en ninguna fila de flechas"
    assert ies[v["ix"]] == r["ix"] and jotas[v["iy"]] == r["iy"]
