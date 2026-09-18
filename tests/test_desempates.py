"""Los empates que el 18-09-2026 decidia DuckDB al azar en cada consulta.

La media de WeatherNext, sus cinco percentiles y ecmwf_ifs comparten pares de
viento y hora de pasada. Con `max` o `arg_max` a secas cada pasada del colector
elegia uno distinto: la racha de la puerta se multiplicaba por x1,3 o por x3,7
y la web llego a publicar "rachas de 104 km/h, por encima del cierre".
"""

import datetime as dt

from astroweather import tonight, verification
from astroweather.archive import write_canonical
from astroweather.site import NERPIO

RUN = dt.datetime(2026, 9, 17, 0, tzinfo=dt.timezone.utc)
T0 = dt.datetime(2026, 9, 17, 12, tzinfo=dt.timezone.utc)


def _archivo(tmp_path, horas=3):
    filas, medidas = [], []
    for i in range(horas):
        t = T0 + dt.timedelta(hours=i)
        filas.append({"site_id": "nerpio", "model": "ecmwf_ifs", "member": None,
                      "run_utc": RUN, "valid_utc": t, "lead_h": 12 + i,
                      "wind10m": 2.0, "gust10m": 5.0, "precipitation": 0.0})
        for modelo, viento, lluvia in (("weathernext3", 3.0, 0.1),
                                       ("weathernext3_p10", 1.0, 0.0),
                                       ("weathernext3_p90", 3.9, 9.0)):
            filas.append({"site_id": "nerpio", "model": modelo, "member": None,
                          "run_utc": RUN, "valid_utc": t, "lead_h": 12 + i,
                          "wind10m": viento, "precipitation": lluvia})
        medidas.append({"site_id": "nerpio", "t_utc": t, "wind": 4.0,
                        "source": "astrocamp_meteo", "instrument_type": "STATION",
                        "instrument_id": "astrocamp", "bandpass_id": "none"})
    write_canonical(filas, dataset="forecasts", site_id="nerpio", root=tmp_path)
    write_canonical(medidas, dataset="measurements", site_id="nerpio", root=tmp_path)
    return tmp_path


def test_la_correccion_de_viento_es_la_del_modelo_que_publica_racha(tmp_path):
    raiz = _archivo(tmp_path)
    for _ in range(5):
        c = verification.wind_correction(raiz)
        assert c["ratio"] == 0.5          # ecmwf_ifs: 2,0 frente a 4,0 medido


def test_la_lluvia_de_la_puerta_no_sale_de_un_percentil(tmp_path):
    raiz = _archivo(tmp_path)
    from astroweather.queries import connect
    for _ in range(5):
        filas = tonight._safety_rows(connect(raiz), NERPIO, T0, T0 + dt.timedelta(hours=2))
        assert {f[2] for f in filas.values()} == {0.0}       # la de ecmwf_ifs
        assert {f[1] for f in filas.values()} == {5.0}
