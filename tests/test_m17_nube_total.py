"""M17: the two ways a total cloud cover ends up under its own layers."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "m17_nube_total", Path(__file__).parents[1] / "tools" / "m17_nube_total.py")
m17 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m17)


def test_la_formula_vieja_de_open_meteo_deja_el_cirro_en_un_30():
    # Cielo cubierto solo de nubes altas: la formula ponderada dice 30 %.
    assert m17.weighted_total(0, 0, 100) == 30.0
    assert m17.below_its_layers(m17.weighted_total(0, 0, 100), 0, 0, 100)
    # Y nunca pasa de 100 aunque las tres capas esten llenas.
    assert m17.weighted_total(100, 100, 100) == 100.0


def test_un_punto_por_debajo_es_redondeo_y_no_cuenta():
    assert not m17.below_its_layers(49, 50, 0, 0)
    assert m17.below_its_layers(45, 50, 0, 0)


def test_la_proporcion_cuenta_filas_no_capas():
    rows = [(100, 100, 0, 0), (30, 0, 0, 100), (0, 0, 0, 0), (80, 20, 90, 10)]
    assert m17.incoherent_share(rows) == 0.5
    assert m17.incoherent_share([]) != m17.incoherent_share([])   # nan
