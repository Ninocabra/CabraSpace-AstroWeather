TIEMPO: empecé 22:42 — terminé 22:48
## Ficheros entregados
- `C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\gemini\fetch_weathernext.py` — 378 líneas
- `C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\gemini\fixture_weathernext_records.json` — 429 líneas
- `C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\gemini\test_fetch_weathernext.py` — 146 líneas

## Salida de pytest
```
.......                                                                  [100%]
7 passed in 0.11s
```

## Prueba en rojo
- **Qué quité:** En `fetch_weathernext.py`, dentro de `_rows()`, se eliminó temporalmente la comprobación de coherencia entre `lead_h` y `(valid_utc - init_time)`:
  ```python
  if diff_seconds != lead_h * 3600:
      raise ValueError(f"Incoherent lead_h: ...")
  ```
  reemplazándola por un simple `pass`.
- **Qué falló:** Al ejecutar `python -m pytest coop/gemini/test_fetch_weathernext.py -q`, el test `test_lead_h_invariant_and_manipulated_fixture` falló con:
  ```
  ...F...                                                                  [100%]
  ================================== FAILURES ===================================
  ________________ test_lead_h_invariant_and_manipulated_fixture ________________
  ...
  >       with pytest.raises(ValueError, match="Incoherent lead_h"):
  E       Failed: DID NOT RAISE ValueError
  =========================== short test summary info ===========================
  FAILED coop/gemini/test_fetch_weathernext.py::test_lead_h_invariant_and_manipulated_fixture
  1 failed, 6 passed in 0.26s
  ```
- **Restauración:** Se restauró la comprobación estricta y al relanzar la prueba, los 7 tests pasaron en verde (0.11s).

## Supuestos marcados SIN COMPROBAR
- `[LO SÉ]` El producto WeatherNext 3 (0.1°) de Google DeepMind / Google Research se distribuye en BigQuery vía Analytics Hub bajo el identificador de tabla `weathernext_3_0_0_0p1deg`, cubriendo variables horarias y 6 estadísticos (`mean`, `p10`, `p25`, `p50`, `p75`, `p90`) para un ensemble de 64 miembros.
- `[SIN COMPROBAR]` El nombre exacto de la columna de partición en BigQuery: se ha asumido `init_time` (TIMESTAMP) según la especificación, pero no se ha podido validar contra el esquema DDL real por falta de acceso activo a la tabla (en espera de aprobación).
- `[SIN COMPROBAR]` La estructura exacta del registro repetido: se asume `forecast` como `ARRAY<STRUCT>` (REPEATED RECORD) conteniendo `time` y `hours`, y las variables con los sufijos `_mean`, `_p10`, `_p25`, `_p50`, `_p75`, `_p90`.
- `[SIN COMPROBAR]` Formato de salida de `geography`: se ha asumido que la tabla expone `geography` (POINT) y `geography_polygon` (POLYGON de 0.1°). El extractor se ha implementado de forma tolerante para admitir WKT (`POINT(lon lat)`), GeoJSON o diccionarios con claves `latitude`/`longitude`.
- `[SIN COMPROBAR]` Nombre exacto de columna de precipitación: se asume `total_precipitation_1hr` en metros según el encargo, sin comprobar si en la tabla definitiva se abrevia `total_precipitation`.
- `[SIN COMPROBAR]` Partición obligatoria: se construyó `build_query` filtrando por `init_time` en `WHERE` antes de cualquier cálculo GIS para asegurar el podado de particiones (partition pruning). Si la tabla exige `require_partition_filter=true`, la subconsulta `(SELECT MAX(init_time) ...)` podría requerir una cota temporal explícita (`TIMESTAMP_SUB(...)`).

## Dudas para Claude
1. Cuando se obtenga acceso al dataset en BigQuery Analytics Hub, convendrá hacer un `SELECT * FROM weathernext_3_0_0_0p1deg LIMIT 1` para confirmar los nombres exactos de campos en el STRUCT y la representación nativa que devuelve el cliente de Python para `geography`.
2. En `build_query`, se añadió un filtro `ARRAY(SELECT AS STRUCT * FROM UNNEST(forecast) WHERE hours <= @max_hours ORDER BY hours) AS forecast` para reducir el ancho de banda transferido al consultar solo las horas necesarias (ej. 48h de 360h posibles). ¿Deseas mantener este filtrado de array en el SQL o prefieres traer el array completo y truncarlo en memoria en `_rows`?
