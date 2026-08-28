"""Archiva LO QUE NOSOTROS PREDECIMOS, no solo lo que predicen los demas.

El hueco que esto tapa, encontrado el 2026-08-28 haciendo el inventario del
archivo: las columnas `p_raw` y `p_calibrated` existen en el esquema desde el
principio y estaban VACIAS. Nadie escribia nunca la probabilidad que el producto
publica. El marcador la recalculaba desde el `cloud_total` guardado, que parece
lo mismo y no lo es:

* Si el calibrador cambia -- y el 28-08 cambio dos veces en una tarde -- cambia
  RETROACTIVAMENTE lo que dijimos que dijimos. No se puede auditar que vio el
  usuario una noche concreta, porque la respuesta se reconstruye con la curva de
  hoy y no con la de aquella noche.
* Y un sistema de prevision que no guarda sus previsiones no puede medirse a si
  mismo fuera de muestra. Todo lo que el marcador llama "nuestro" es DENTRO de
  muestra por construccion, y el propio informe lo advierte.

Se archiva como un proveedor mas, con nombre propio, para que el marcador pueda
puntuarlo con la misma vara que a ECMWF, ICON o GFS. Y cada fila lleva de que
calibrador salio: sin eso, dentro de un anio no se sabria si una prediccion mala
fue del modelo, de la curva o del cielo.

Como todo lo que solo existe hacia adelante, cada noche que no corra es una
noche que no se podra auditar nunca.
"""

from __future__ import annotations

import argparse
import datetime as dt

from ..archive import utcnow, write_canonical
from ..calibration import Calibrator
from ..ephem import night_for
from ..site import NERPIO, Site
from ..tonight import report

MODEL = "astroweather"


def rows_for(day: dt.datetime, site: Site = NERPIO, root=None) -> list[dict]:
    """Nuestra prevision para esa noche, hora a hora, lista para archivar."""
    night_report = report(day, site, root)
    if not night_report.hours:
        return []

    calibrator = Calibrator.load()
    run = utcnow()
    # De que curva salio. `fitted_at` es lo que distingue dos predicciones
    # identicas hechas con calibradores distintos, que es justo lo que hay que
    # poder separar al auditar.
    version = (f"{MODEL}/{calibrator.trained_on_model}@{calibrator.fitted_at}"
               if calibrator else f"{MODEL}/sin-calibrar")

    rows = []
    for hour in night_report.hours:
        lead = (hour.t_utc - run).total_seconds() / 3600.0
        if lead < 0:
            # Una "prediccion" de una hora que ya paso no es una prediccion. La
            # ventana del producto abre una hora antes del ocaso, asi que al
            # correr de noche siempre hay horas por detras; archivarlas
            # ensuciaria con retrospectiva justo el registro que existe para
            # poder auditar que dijimos ANTES.
            continue
        rows.append({
            "site_id": site.site_id,
            "model": MODEL,
            "run_utc": run,
            "valid_utc": hour.t_utc,
            "lead_h": int(round(lead)),
            "member": None,
            "cloud_total": hour.cloud_total,
            # La probabilidad cruda del proveedor, antes de nuestra curva, para
            # que la diferencia entre las dos columnas SEA lo que anade calibrar.
            "p_raw": (1.0 - hour.cloud_total / 100.0
                      if hour.cloud_total is not None else None),
            "p_calibrated": hour.clear_probability,
            "provider_model_version": version,
            "retrieved_at": run,
        })
    return rows


def run(site: Site = NERPIO, *, nights: int = 2, root=None) -> int:
    """Esta noche y la siguiente: las dos que el producto responde."""
    written = 0
    for offset in range(nights):
        day = utcnow() + dt.timedelta(days=offset)
        if not night_for(day, site).window_start:
            continue
        rows = rows_for(day, site, root)
        if rows:
            write_canonical(rows, dataset="forecasts", site_id=site.site_id)
            written += len(rows)
    print(f"[archive_prediction] {written} horas de prevision propia archivadas")
    return written


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nights", type=int, default=2)
    args = parser.parse_args()
    run(nights=args.nights)
