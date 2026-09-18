"""Lo que vale para todos los tests.

EL LOG DE LOS TESTS NO VA AL LOG DE VERDAD. `colector.main()` configura el log
sobre `data/scheduler.log`, y los tests que la llaman escribian ahi sus fallos
de mentira ("roto: el proveedor esta caido", "lento", una AEMET_API_KEY
caducada...) mezclados con los de la recogida real. El 18-09-2026, para saber
por que la recogida de las 07:43 no habia subido nada, habia que leer ese log
sorteando lineas que no habian pasado.
"""

import logging

import pytest

from astroweather import scheduler


@pytest.fixture(autouse=True)
def _log_de_tests_aparte(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "LOG_PATH", tmp_path / "scheduler.log")
    yield
    # `basicConfig(force=True)` deja el FileHandler abierto sobre el fichero
    # temporal; se cierra para que Windows pueda borrar la carpeta.
    for handler in list(logging.getLogger().handlers):
        if isinstance(handler, logging.FileHandler):
            handler.close()
            logging.getLogger().removeHandler(handler)
