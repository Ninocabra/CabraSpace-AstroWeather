"""Canonical storage schema (SPEC v8 section 4.1).

Two levels, as decided in v6 after the EAV design was dropped:

* RAW      - the provider response, compressed and versioned, never rewritten.
* CANONICAL- one wide, typed row per (site, model, run, valid_time, member).

The provenance columns are not decoration: they are what lets us answer, two
years from now, whether a bad forecast came from the provider, our physics or a
broken sensor.
"""

from __future__ import annotations

import pyarrow as pa

# --- provenance -------------------------------------------------------------
# Attached to every canonical forecast row (SPEC v8 section 3.2, invariant 5).
PROVENANCE_FIELDS = [
    pa.field("provider_model_version", pa.string()),
    pa.field("provider_grid_lat", pa.float64()),
    pa.field("provider_grid_lon", pa.float64()),
    # The three elevations that must never be conflated (SPEC v8 section 0).
    pa.field("site_elevation", pa.float32()),
    pa.field("model_grid_elevation", pa.float32()),
    pa.field("downscaling_elevation", pa.float32()),
    pa.field("native_resolution_km", pa.float32()),
    pa.field("native_timestep_minutes", pa.int16()),
    pa.field("interpolated", pa.bool_()),
    pa.field("retrieved_at", pa.timestamp("s", tz="UTC")),
    pa.field("license_tag", pa.string()),
    pa.field("quality_flags", pa.string()),
    pa.field("pipeline_version", pa.string()),
]

# --- forecasts --------------------------------------------------------------
FORECAST_KEYS = [
    pa.field("site_id", pa.string()),
    pa.field("model", pa.string()),
    pa.field("run_utc", pa.timestamp("s", tz="UTC")),
    pa.field("valid_utc", pa.timestamp("s", tz="UTC")),
    pa.field("lead_h", pa.int16()),
    # NULL for deterministic runs; 0..50 for EPS members. Members are archived
    # raw on every download: upstream keeps them ~4-5 days only.
    pa.field("member", pa.int16()),
]

FORECAST_VALUES = [
    # surface
    pa.field("cloud_low", pa.float32()),
    pa.field("cloud_mid", pa.float32()),
    pa.field("cloud_high", pa.float32()),
    pa.field("cloud_total", pa.float32()),
    pa.field("t2m", pa.float32()),
    pa.field("rh2m", pa.float32()),
    pa.field("td2m", pa.float32()),
    pa.field("wind10m", pa.float32()),      # m/s always (invariant 1)
    pa.field("gust10m", pa.float32()),      # m/s
    pa.field("wind_dir10m", pa.float32()),
    pa.field("prob_precip", pa.float32()),
    pa.field("precipitation", pa.float32()),
    pa.field("cape", pa.float32()),
    pa.field("blh", pa.float32()),
    pa.field("surface_pressure", pa.float32()),
    pa.field("pressure_msl", pa.float32()),
    pa.field("snow_depth", pa.float32()),
    # pressure levels (seeing / Richardson)
    pa.field("t850", pa.float32()),
    pa.field("t700", pa.float32()),
    pa.field("t500", pa.float32()),
    pa.field("v500", pa.float32()),
    pa.field("v300", pa.float32()),
    pa.field("v250", pa.float32()),
    pa.field("v200", pa.float32()),
    pa.field("dir700", pa.float32()),
    pa.field("dir500", pa.float32()),
    pa.field("gph700", pa.float32()),
    pa.field("gph500", pa.float32()),
    pa.field("gph300", pa.float32()),
    pa.field("rh250", pa.float32()),        # contrail proxy
    # aerosol / water vapour
    pa.field("aod_total", pa.float32()),
    pa.field("aod_dust", pa.float32()),
    pa.field("dust_surface", pa.float32()),  # ug/m3 - NOT an AOD
    pa.field("pm10", pa.float32()),
    pa.field("pwv", pa.float32()),
    # our own derived predictions, versioned via pipeline_version
    pa.field("fwhm_est", pa.float32()),
    pa.field("mag_sky_est", pa.float32()),
    pa.field("p_raw", pa.float32()),
    pa.field("p_calibrated", pa.float32()),
]

FORECAST_SCHEMA = pa.schema(FORECAST_KEYS + FORECAST_VALUES + PROVENANCE_FIELDS)

# --- measurements (ground truth) -------------------------------------------
MEASUREMENT_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string()),
        pa.field("t_utc", pa.timestamp("s", tz="UTC")),
        # seeing: setup_id is mandatory, an optical train change must never
        # look like an atmospheric change (SPEC v8 section 4.1).
        pa.field("fwhm_zenith_500nm", pa.float32()),
        pa.field("setup_id", pa.string()),
        pa.field("fwhm_eccentricity", pa.float32()),
        # sky background: raw reading lives in the instrument's own band.
        pa.field("sky_mag_raw", pa.float32()),
        pa.field("instrument_type", pa.string()),     # 'TESS' | 'SQM' | ...
        pa.field("instrument_id", pa.string()),       # 'stars289'
        pa.field("bandpass_id", pa.string()),
        pa.field("sky_mag_calibrated", pa.float32()),
        # clouds / transparency
        pa.field("clear_flag", pa.bool_()),
        pa.field("cloud_frac", pa.float32()),
        # Indice de nubes del propio AAG CloudWatcher (columna "S.I." de la
        # pagina). El parser lo leia desde el principio y lo tiraba: es el
        # numero que el fabricante ya da corregido, y es el que mejor detecta
        # nube fina. NO es una fraccion, por eso no reutiliza `cloud_frac`.
        pa.field("sky_index", pa.float32()),
        # Punto de rocio que la estacion PUBLICA. Lo tirabamos y lo
        # recalculabamos por Magnus-Sonntag desde t2m y rh, perdiendo ademas la
        # comprobacion cruzada gratis (SPEC v8 seccion 2.6).
        pa.field("dew_point", pa.float32()),
        pa.field("k_extinction", pa.float32()),
        # Estrellas que la allsky detecta en la imagen. Es verdad de
        # nubes INDEPENDIENTE del modelo y del fotometro: con cielo
        # cubierto cae a cero sin depender de ninguna calibracion.
        # Cero de dia es correcto, no es un fallo.
        pa.field("star_count", pa.int32()),
        # IR sky sensor: store WHICH field it is, semantics are not obvious
        # (raw IR, Ts-Ta, or the vendor's corrected sky temperature).
        pa.field("t_sky_ir", pa.float32()),
        pa.field("t_sky_ir_semantics", pa.string()),
        # local weather station
        pa.field("t2m", pa.float32()),
        pa.field("rh", pa.float32()),
        pa.field("wind", pa.float32()),
        pa.field("gust", pa.float32()),
        pa.field("station_pressure", pa.float32()),
        # Aerosol MEDIDO en el sitio, no modelado: PM2.5 en superficie, ug/m3,
        # del sensor que AstroCamp publica en su grafico. Es lo unico in situ
        # que tenemos frente al AOD de CAMS, y por eso vive en `measurements` y
        # no junto al `dust_surface` de `forecasts`, que es de modelo.
        pa.field("pm2_5", pa.float32()),
        # censoring: roofs tell us whether anyone was observing at all
        pa.field("roof_open", pa.bool_()),
        pa.field("censored", pa.bool_()),
        pa.field("censor_reason", pa.string()),
        pa.field("source", pa.string()),
        pa.field("quality_flags", pa.string()),
        pa.field("retrieved_at", pa.timestamp("s", tz="UTC")),
        pa.field("pipeline_version", pa.string()),
    ]
)

SCHEMAS = {"forecasts": FORECAST_SCHEMA, "measurements": MEASUREMENT_SCHEMA}


# --- daily observations from external stations ------------------------------
# A daily mean is not the same kind of thing as a one-minute photometer
# reading, so it gets its own table instead of being flattened into
# `measurements`. The station is described in every row: this is ground truth
# from 11.5 km away and 165 m below the observatory, and whoever joins it to
# site data should be forced to see that.
DAILY_OBSERVATION_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string()),         # site this station serves as proxy for
        pa.field("date", pa.date32()),
        pa.field("station_id", pa.string()),      # AEMET indicativo, e.g. '7067Y'
        pa.field("station_name", pa.string()),
        pa.field("station_elevation", pa.float32()),
        pa.field("station_distance_km", pa.float32()),
        pa.field("t_mean", pa.float32()),
        pa.field("t_min", pa.float32()),
        pa.field("t_max", pa.float32()),
        pa.field("hour_t_min", pa.string()),
        pa.field("hour_t_max", pa.string()),
        pa.field("rh_mean", pa.float32()),
        pa.field("rh_min", pa.float32()),
        pa.field("rh_max", pa.float32()),
        pa.field("precipitation", pa.float32()),
        pa.field("precipitation_max_intensity", pa.float32()),
        pa.field("wind_mean", pa.float32()),      # absent for 7067Y, kept for other stations
        pa.field("source", pa.string()),
        pa.field("license_tag", pa.string()),
        pa.field("quality_flags", pa.string()),
        pa.field("retrieved_at", pa.timestamp("s", tz="UTC")),
        pa.field("pipeline_version", pa.string()),
    ]
)

SCHEMAS["daily_observations"] = DAILY_OBSERVATION_SCHEMA


# --- el producto se puntua a si mismo (26-10-2026) ----------------------------
#
# Tres almacenes de SOLO ANADIR, con el mismo mecanismo que los demas
# (`archive.write_canonical`: un Parquet nuevo por escritura, nunca se reescribe
# nada). Se eligio esto y no un JSON suelto como `roof_calibration.json` por lo
# que paso el 17-09-2026 con `open_hours`: los sueltos SE REESCRIBEN, y una
# maquina atrasada copio su version encima de la buena (258 -> 254). Un registro
# de aciertos que una maquina vieja puede pisar no es un registro. Con un
# fichero por escritura, dos maquinas que puntuan la misma noche dejan dos
# ficheros y el lector se queda con el mas reciente: no hay choque de git
# posible ni nada que fundir. `date` es la noche (la fecha en que empieza), y es
# por donde `write_canonical` particiona.

# Lo que se PUBLICO, capturado por el trabajo `publicar` en cada commit de
# `noche.json`. El historial git del repo de datos ya lo guarda, pero el runner
# lo clona con profundidad 1: a la manana siguiente la version de las 16 UTC ya
# no esta en su disco. Una fila por (publicacion, noche); las horas van en JSON
# porque son lo que permite repuntuar esa noche con OTRA curva y las mismas
# puertas (`nighttruth.gated_usable`).
NIGHT_PUBLICATION_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string()),
        pa.field("date", pa.date32()),                   # la noche
        pa.field("published_utc", pa.timestamp("s", tz="UTC")),   # generado_utc
        pa.field("night_index", pa.int16()),             # 0 esta noche, 1 la siguiente
        pa.field("p_open", pa.float32()),                # probabilidad_de_abrir
        pa.field("open", pa.bool_()),                    # veredicto_abrir.abierto
        pa.field("open_derived", pa.bool_()),            # sin regla escrita: p >= 0,5
        pa.field("window_from", pa.timestamp("s", tz="UTC")),
        pa.field("window_to", pa.timestamp("s", tz="UTC")),
        pa.field("expected_h", pa.float32()),            # horas_utilizables_esperadas
        pa.field("curve_fitted", pa.string()),           # confianza.curva_ajustada_el
        pa.field("truth_curve", pa.string()),            # 'techo' | 'fotometro'
        pa.field("cloud_model", pa.string()),
        pa.field("hours_json", pa.string()),             # t, nube, p_desp, p_util, seguro
        pa.field("source", pa.string()),                 # 'publicar' | 'git noche.json'
        pa.field("retrieved_at", pa.timestamp("s", tz="UTC")),
        pa.field("pipeline_version", pa.string()),
    ]
)

# La NOTA de cada noche: lo publicado a las 16 UTC del dia (d0_) y de la vispera
# (dm1_), lo que paso y sus aciertos. Una fila por noche y pasada de
# puntuacion; la vigente es la de `scored_utc` mas reciente (una noche solo se
# vuelve a escribir si su verdad cambia, p. ej. cuando AstroCamp apunta sus
# horas en la tabla). Formato completo en docs/aprendizaje_continuo.md.
_PUBLISHED_FIELDS = []
for _p in ("d0", "dm1"):
    _PUBLISHED_FIELDS += [
        pa.field(f"{_p}_published_utc", pa.timestamp("s", tz="UTC")),
        pa.field(f"{_p}_p_open", pa.float32()),
        pa.field(f"{_p}_open", pa.bool_()),
        pa.field(f"{_p}_window_from", pa.timestamp("s", tz="UTC")),
        pa.field(f"{_p}_window_to", pa.timestamp("s", tz="UTC")),
        pa.field(f"{_p}_expected_h", pa.float32()),
        pa.field(f"{_p}_curve_fitted", pa.string()),
        pa.field(f"{_p}_truth_curve", pa.string()),
        pa.field(f"{_p}_source", pa.string()),
        pa.field(f"{_p}_outcome", pa.string()),   # acierto|falsa_alarma|sorpresa|cierre_bien
        pa.field(f"{_p}_hit", pa.bool_()),
        pa.field(f"{_p}_brier", pa.float32()),
        pa.field(f"{_p}_hours_error", pa.float32()),       # esperadas - techo (h)
        pa.field(f"{_p}_hours_error_dark", pa.float32()),  # esperadas - techo con Sol < -18
    ]

NIGHT_SCORE_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string()),
        pa.field("date", pa.date32()),
        pa.field("scored_utc", pa.timestamp("s", tz="UTC")),
        pa.field("origin", pa.string()),                 # 'diario' | 'relleno'
        pa.field("dark_hours", pa.float32()),            # noche astronomica (h)
        pa.field("moon", pa.float32()),                  # iluminacion a las 23:30 UTC
        # el techo
        pa.field("roof_hours", pa.float32()),
        pa.field("roof_source", pa.string()),
        pa.field("roof_opened", pa.bool_()),             # >= 2 h, la ventana del veredicto
        pa.field("roof_dark_hours", pa.float32()),       # abiertas con Sol < -18 (lecturas)
        pa.field("roof_samples", pa.int16()),
        pa.field("roof_table_hours", pa.float32()),
        pa.field("roof_table_quality", pa.string()),     # limpio | aprox | reparto
        # el cielo: horas medidas y fraccion despejada de cada verdad
        pa.field("phot_hours", pa.int16()),
        pa.field("phot_clear_frac", pa.float32()),
        pa.field("cw_hours", pa.int16()),
        pa.field("cw_clear_frac", pa.float32()),
        pa.field("sky_hours", pa.int16()),
        pa.field("sky_clear_frac", pa.float32()),
        pa.field("consensus_hours", pa.int16()),
        pa.field("consensus_clear_frac", pa.float32()),
        pa.field("vote", pa.string()),                   # despejada | nublada | None
    ] + _PUBLISHED_FIELDS + [
        # La version de la tabla de AstroCamp vigente al puntuar y el primer
        # instante en que se vio: en el runner (clon de profundidad 1) es la unica
        # forma de saber manana cuanto abrieron hoy.
        pa.field("table_seen_utc", pa.timestamp("s", tz="UTC")),
        pa.field("table_json", pa.string()),
        pa.field("pipeline_version", pa.string()),
    ]
)

# Cada vez que una curva aspirante se mide contra la vigente: con que, quien
# gano y por que. Es el registro que dice cuando y por que cambio lo publicado.
CURVE_DECISION_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string()),
        pa.field("date", pa.date32()),                   # dia de la decision
        pa.field("decided_utc", pa.timestamp("s", tz="UTC")),
        # 'techo' | 'fotometro' (ECMWF 0,25) | 'fotometro_weathernext3' |
        # 'modelo_d1' (solo medida: que modelo gana a 1 dia; desde el 26-10-2026)
        pa.field("curve", pa.string()),
        # sustituye | se_queda | arranque_en_frio; en 'modelo_d1', el ganador
        pa.field("decision", pa.string()),
        pa.field("reason", pa.string()),
        pa.field("k_nights", pa.int16()),
        pa.field("margin", pa.float32()),
        pa.field("n_nights", pa.int16()),                # noches puntuadas de verdad
        pa.field("nights", pa.string()),                 # cuales (JSON)
        pa.field("champion_fitted_at", pa.string()),
        pa.field("challenger_fitted_at", pa.string()),
        pa.field("champion_brier", pa.float32()),
        pa.field("challenger_brier", pa.float32()),
        pa.field("d_brier", pa.float32()),               # aspirante - vigente
        pa.field("d_brier_lo", pa.float32()),            # IC90 pareado por noches
        pa.field("d_brier_hi", pa.float32()),
        pa.field("champion_auc", pa.float32()),
        pa.field("challenger_auc", pa.float32()),
        pa.field("champion_json", pa.string()),
        pa.field("challenger_json", pa.string()),
        pa.field("pipeline_version", pa.string()),
    ]
)

SCHEMAS["night_publications"] = NIGHT_PUBLICATION_SCHEMA
SCHEMAS["night_scores"] = NIGHT_SCORE_SCHEMA
SCHEMAS["curve_decisions"] = CURVE_DECISION_SCHEMA


# --- Lo que hace que una fila sea una fila -----------------------------------
#
# El esquema dice de que TIPO es cada columna y ahi se paraba: `write_canonical`
# rechazaba un nombre de columna inventado y aceptaba encantado una fila entera
# a nulos. Eso es la misma familia de fallo que llevamos dos semanas cazando,
# pero en la frontera de escritura: una fila sin identidad no se puede volver a
# encontrar, y una fila sin ningun valor es ausencia disfrazada de dato.
#
# Aqui no hay dependencia nueva a proposito. La frontera ya existia -- el bucle
# que valida columnas desconocidas en `archive.write_canonical` -- y lo unico
# que le faltaba era tener algo que exigir.

# Las columnas que IDENTIFICAN la fila. Un nulo aqui no es un dato que falta:
# es una fila que no se puede consultar nunca mas.
CLAVES_OBLIGATORIAS = {
    "forecasts": ("site_id", "model", "run_utc", "valid_utc"),
    "measurements": ("site_id", "t_utc"),
    "daily_observations": ("site_id", "date", "station_id"),
    "night_publications": ("site_id", "date", "published_utc", "source"),
    "night_scores": ("site_id", "date", "scored_utc", "origin"),
    "curve_decisions": ("site_id", "date", "decided_utc", "curve", "decision"),
}

# `member` es NULL en las pasadas deterministas y `lead_h` puede faltar: son
# claves del esquema pero no son obligatorias, y por eso no estan arriba.

# Rangos FISICOS, no estadisticos. Solo columnas donde el limite es una
# definicion y no una opinion: una cobertura nubosa es un porcentaje, un AOD y
# una velocidad no son negativos. Fuera de aqui, el numero no es "raro": es
# imposible, y significa que algo se ha convertido mal aguas arriba.
RANGOS = {
    "cloud_low": (0.0, 100.0), "cloud_mid": (0.0, 100.0),
    "cloud_high": (0.0, 100.0), "cloud_total": (0.0, 100.0),
    "rh2m": (0.0, 100.0), "prob_precip": (0.0, 100.0),
    "aod_total": (0.0, 5.0), "aod_dust": (0.0, 5.0),
    "wind10m": (0.0, 150.0), "gust10m": (0.0, 150.0),
    "precipitation": (0.0, 500.0),
    "t2m": (-90.0, 60.0), "td2m": (-90.0, 60.0),
    # Una concentracion no es negativa. El techo es generoso a proposito: el
    # record mundial de PM2.5 horario esta en unos pocos miles, asi que por
    # encima el numero no es "alto", es un error de lectura.
    "pm2_5": (0.0, 5000.0),
}
