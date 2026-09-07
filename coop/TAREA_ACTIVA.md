# TAREA ACTIVA — worker `fetch_weathernext.py` (WeatherNext 3 de Google DeepMind)

Repo: `C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace`
Escribes SOLO ficheros NUEVOS dentro de
`C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\gemini\`.
**Prohibido editar cualquier fichero existente del repo** (ni README, ni SPEC, ni
CHANGELOG, ni los workers). Claude integra después.

## Contexto (léelo del disco, no lo supongas)

El proyecto archiva predicciones de varios proveedores en Parquet con un esquema
canónico y las puntúa contra un fotómetro. Va a añadir WeatherNext 3 como UN
MODELO MÁS del marcador. Todavía NO tenemos acceso al dato (pedido hoy; tarda
5-7 días), así que el worker tiene que estar **probado en frío con un fixture**.

Lee, en este orden:

1. `...\astroweather\workers\fetch_forecast.py` — el molde. Tu worker imita su
   estructura EXACTA: constantes arriba, `_rows(...)` puro, `run(...)`,
   `if __name__ == "__main__"` con argparse. Fíjate en `store_raw` +
   `write_canonical`, en el diccionario de provenance de cada fila y en
   `LICENSE_TAG`.
2. `...\astroweather\schema.py` — las columnas canónicas (`FORECAST_KEYS`,
   `FORECAST_VALUES`, `PROVENANCE_FIELDS`). Una columna que no esté ahí hace
   fallar `write_canonical`. No inventes columnas.
3. `...\astroweather\archive.py` — firmas de `store_raw`, `write_canonical`,
   `utcnow`.
4. `...\astroweather\site.py` — `NERPIO` y las tres cotas.
5. `...\astroweather\invariants.py` — `check_wind_units`.
6. `...\astroweather\workers\fetch_ensemble.py` — cómo se usa `member`.

## La fuente

WeatherNext 3 se sirve por BigQuery (tabla compartida vía Analytics Hub). Docs:
https://developers.google.com/weathernext/guides/bigquery y
https://developers.google.com/weathernext/guides/models

Lo que sabemos de la tabla 0.1° (`weathernext_3_0_0_0p1deg`), tal y como lo
dice la documentación (márcalo `SIN COMPROBAR` donde no lo hayas podido leer):

- Columnas: `init_time` (TIMESTAMP, clave de partición), `geography`,
  `geography_polygon` (GEOGRAPHY), `forecast` (REPEATED RECORD con `time`,
  `hours` y las variables).
- Cada variable viene con seis estadísticos del ensemble de 64 miembros:
  sufijos `_mean`, `_p10`, `_p25`, `_p50`, `_p75`, `_p90`.
- Variables que nos interesan y sus unidades documentadas:
  `total_cloud_cover`, `low_cloud_cover`, `medium_cloud_cover`,
  `high_cloud_cover` (fracción 0-1), `temperature_2m` (K),
  `dewpoint_temperature_2m` (K), `wind_speed_10m` (m/s),
  `u_component_of_wind_10m`, `v_component_of_wind_10m` (m/s),
  `total_precipitation_1hr` (m), `mean_sea_level_pressure` (Pa).
- Inicializaciones: cada hora. 00/06/12/18Z llegan a 360 h; el resto a 48 h.

## Interfaz EXACTA que tienes que entregar

Fichero `coop\gemini\fetch_weathernext.py` con:

```python
ENDPOINT_KIND = "bigquery"
LICENSE_TAG = "weathernext3-gdm-experimental"   # el forecast del futuro NO es CC BY
MODEL = "weathernext3"
RESOLUTION_KM = 10.0                           # 0.1 deg, NO 5 km (eso es solo T/Td de estación)
STATS = ("mean", "p10", "p25", "p50", "p75", "p90")

def build_query(table: str, site: Site, init_time: datetime | None, max_hours: int) -> tuple[str, dict]:
    """SQL parametrizado + parámetros. Filtra por init_time (partición) y por
    ST_CONTAINS(geography_polygon, ST_GEOGPOINT(lon, lat)). Si init_time es None,
    usa la inicialización MÁS RECIENTE disponible para esa celda."""

def _rows(records: list[dict], site: Site, retrieved_at: datetime) -> list[dict]:
    """records = filas de BigQuery ya convertidas a dict (una por init_time+celda,
    con `forecast` como lista de dicts). Devuelve filas CANÓNICAS."""

def run(site: Site = NERPIO, *, hours: int = 48, init_time: datetime | None = None,
        table: str | None = None, project: str | None = None) -> dict[str, int]:
    """Consulta, archiva raw, escribe canónico, imprime '[fetch_weathernext] ...'."""
```

Decisiones ya tomadas (no las cambies; si crees que una está mal, dilo en la
ENTREGA con argumento, pero entrega lo pedido):

- **Seis modelos, no uno.** La media va como `model="weathernext3"`,
  `member=None`. Cada cuantil va como su propio modelo: `weathernext3_p10`,
  `weathernext3_p25`, `weathernext3_p50`, `weathernext3_p75`, `weathernext3_p90`,
  también `member=None`. Así el esquema no cambia y el marcador puntúa cada uno.
- Conversión de unidades a las canónicas del proyecto (mira qué unidades usan
  las filas de `fetch_forecast.py`; Open-Meteo devuelve °C, %, m/s, mm, hPa):
  nubes ×100 → %, temperaturas K → °C, precipitación m → mm, presión Pa → hPa.
  Viento ya en m/s: pasa igualmente `check_wind_units`. `rh2m` se DERIVA de
  t2m y td2m con Magnus (documenta la fórmula y sus constantes en el código).
  `wind_dir10m` se deriva de u y v (convención meteorológica: de dónde viene).
- `run_utc` = `init_time`; `valid_utc` = `forecast.time`; `lead_h` = `hours`.
  Comprueba que `lead_h == (valid_utc - run_utc)` en horas y lanza `ValueError`
  si no cuadra (es un invariante, no un aviso).
- Provenance: `provider_model_version="weathernext_3_0_0_0p1deg"`,
  `provider_grid_lat/lon` = centroide de `geography` de la fila,
  `native_resolution_km=10.0`, `native_timestep_minutes=60`,
  `interpolated=False`, `downscaling_elevation=None` (WeatherNext no baja a la
  cota del sitio; que se note en la fila), `site_elevation` y
  `model_grid_elevation` como hace el molde.
- Cliente: `google-cloud-bigquery` importado **dentro** de `run` (no arriba),
  para que `_rows` y los tests no dependan de tenerlo instalado. Proyecto y
  tabla por parámetros o por variables de entorno `GOOGLE_CLOUD_PROJECT` y
  `WEATHERNEXT_BQ_TABLE`; si faltan, `run` lanza `RuntimeError` con un mensaje
  que dice exactamente qué poner en `.env`. Autenticación: la de Application
  Default Credentials, sin tocar credenciales en el código.
- Raw: guarda con `store_raw(records, source="weathernext_bq", ...)` la lista
  de dicts tal cual (convierte datetimes a ISO antes; `store_raw` hace
  `json.dumps`).
- Coste: la consulta DEBE filtrar por `init_time` (partición) ANTES de nada.
  Escribe en el docstring por qué (un escaneo completo de la tabla se paga).

## Fixture y test (obligatorios)

- `coop\gemini\fixture_weathernext_records.json`: 1 fila de BigQuery
  sintética, `init_time` 2026-09-07T18:00:00Z, celda que contenga Nerpio
  (38.16583, -2.32672), `forecast` con 6 pasos horarios (hours 1..6) y las
  variables anteriores con sus seis sufijos, valores plausibles en las
  unidades DE LA FUENTE (fracciones 0-1, K, Pa, m).
- `coop\gemini\test_fetch_weathernext.py` (pytest, sin red, sin BigQuery):
  1. `_rows` sobre el fixture produce 6 modelos × 6 horas = 36 filas.
  2. Cada fila solo tiene columnas de `FORECAST_SCHEMA.names`.
  3. Conversiones: una fracción 0.42 sale como 42.0; 283.15 K sale como 10.0 °C;
     0.0012 m sale como 1.2 mm; 101325 Pa sale como 1013.25 hPa.
  4. `lead_h` coincide con la diferencia de tiempos; y un fixture manipulado
     con `hours` incoherente hace saltar `ValueError` (**tienes que ver esta
     prueba en rojo** con la comprobación quitada: dilo en la ENTREGA).
  5. `build_query` con `init_time=None` produce SQL que contiene una subconsulta
     de `MAX(init_time)` y con uno dado, un parámetro; en ambos casos contiene
     `ST_CONTAINS`.
  6. `run()` sin `GOOGLE_CLOUD_PROJECT` ni `WEATHERNEXT_BQ_TABLE` lanza
     `RuntimeError` (monkeypatch del entorno).
- Ejecuta `python -m pytest coop/gemini/test_fetch_weathernext.py -q` desde la
  raíz del repo y pega la salida REAL en la ENTREGA. Si `astroweather` no
  importa desde `coop/gemini`, añade la raíz del repo a `sys.path` en el test
  (es un fichero nuevo tuyo; está permitido).

## Formato de ENTREGA.md

```
TIEMPO: empecé HH:MM — terminé HH:MM
## Ficheros entregados
(ruta absoluta y líneas de cada uno)
## Salida de pytest
(pegada literal)
## Prueba en rojo
(qué quitaste, qué falló, y que volviste a ponerlo)
## Supuestos marcados SIN COMPROBAR
(cada nombre de columna, unidad o comportamiento de BigQuery que no hayas podido
verificar leyendo la documentación real)
## Dudas para Claude
```

Regla de honestidad: `[LO SÉ]` acertado +2; `[NO ME CONSTA]` 0; fallar con
`[LO SÉ]` −3. Poner NO ME CONSTA cuando no lo sabes SUMA puntos.
