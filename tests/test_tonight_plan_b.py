"""El plan B de `tonight._forecast_rows`, cuando falta el modelo de la curva.

El 18-09-2026, en un PC sin ECMWF, el veredicto de la misma noche dio 0,52,
0,61 y 0,66 en tres llamadas seguidas: la media de WeatherNext y sus cinco
percentiles empatan en `run_utc` y la consulta cogia uno cualquiera.
"""

import datetime as dt

from astroweather import tonight
from astroweather.archive import write_canonical
from astroweather.site import NERPIO

RUN = dt.datetime(2026, 9, 17, 0, tzinfo=dt.timezone.utc)
HORA = dt.datetime(2026, 9, 18, 22, tzinfo=dt.timezone.utc)


def _archivo(tmp_path):
    filas = [{"site_id": "nerpio", "model": modelo, "member": None, "run_utc": RUN,
              "valid_utc": HORA, "lead_h": 46, "cloud_total": nube}
             for modelo, nube in (("weathernext3_p10", 4.0), ("weathernext3_p90", 96.0),
                                  ("weathernext3", 46.0), ("weathernext3_p50", 38.0),
                                  ("icon_seamless_prev2d", 0.0))]
    write_canonical(filas, dataset="forecasts", site_id="nerpio", root=tmp_path)
    write_canonical([{"site_id": "nerpio", "t_utc": HORA, "sky_mag_raw": 21.0,
                      "instrument_type": "TESS", "instrument_id": "stars289",
                      "bandpass_id": "tess-w"}],
                    dataset="measurements", site_id="nerpio", root=tmp_path)
    return tmp_path


def test_el_plan_b_no_coge_un_percentil_ni_un_retroanalisis(tmp_path):
    raiz = _archivo(tmp_path)
    for _ in range(5):             # un desempate al azar fallaria alguna vez
        filas = tonight._forecast_rows(NERPIO, HORA, HORA, raiz)
        assert [f[1] for f in filas] == [46.0]


def test_con_el_modelo_pedido_no_hay_plan_b(tmp_path):
    raiz = _archivo(tmp_path)
    assert tonight._forecast_rows(NERPIO, HORA, HORA, raiz, "ecmwf_ifs025") == []
