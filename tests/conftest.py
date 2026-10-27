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

# SIN RED NO HAY IERS, Y SIN IERS ASTROPY SE NIEGA A DAR LA ALTURA DEL SOL. El
# 26-10-2026 los servidores del IERS devolvian 400 / conexion cortada y 11 tests
# de `test_webexport.py` caian en rojo con el codigo de HEAD sin tocar: fallaba
# la descarga, no el codigo. UT1-UTC < 0,9 s mueve las alturas < 0,004 grados,
# irrelevante para etiquetar horas (mismo razonamiento que
# `hindcast._astropy_offline`). Un test no debe depender de la red.
from astropy.utils import iers as _iers

_iers.conf.auto_download = False
_iers.conf.auto_max_age = None


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


# LOS TERCILES DEL SEEING NO SE LEEN DEL `data/` DE ESTA MAQUINA (M9, 27-10-2026).
# Desde que se ajustan solos (`seeingterciles.py`), `seeing_terciles.json` cambia
# cada semana; un test que dependa del fichero local daria un resultado en cada
# PC. Por defecto, los cortes fijos de respaldo; quien quiera probar el fichero
# usa `seeing._current_terciles_from_file`.
@pytest.fixture(autouse=True)
def _terciles_fijos(monkeypatch):
    from astroweather import seeing
    monkeypatch.setattr(seeing, "current_terciles",
                        lambda: (seeing.TERCILE_GOOD, seeing.TERCILE_POOR))
    yield
