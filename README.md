# CabraSpace AstroWeather

Motor de predicción y evaluación de condiciones de observación para cielo profundo.
Sitio de referencia: **AstroCamp, Nerpio** (MPC I79, 38.16583 N / −2.32672 W, 1650 m).

Sustituirá a *Astrofotografía → Astro Forecast* en la web CabraSpace, que consumirá
este motor por API (repos separados: aquí el motor, allí la página).

## Documentos

| Fichero | Qué es |
|---|---|
| **`SPEC.md`** | **La especificación VIGENTE.** Si algo la contradice, manda ella. |
| `CHANGELOG.md` | Historia v1→v8, auditorías y decisiones con su porqué |
| `docs/historia/` | Versiones anteriores (archivo, no implementar desde ahí) |

## Estado

**Fase 0 — instrumentación de datos** (en curso). El principio que gobierna esta
fase: *un sistema solo puede aprender de lo que archiva*, y hay datos que caducan
— los 51 miembros del EPS viven ~4-5 días en el proveedor, así que el archivado
empieza antes que ninguna otra cosa.

- [x] Contrato de identidad del sitio (`astroweather/site.py`) — las tres cotas separadas
- [x] Esquema canónico + provenance (`astroweather/schema.py`)
- [x] Archivo raw inmutable + Parquet particionado (`astroweather/archive.py`)
- [x] Invariantes de ingesta + tests (`astroweather/invariants.py`, `tests/`)
- [x] Worker del EPS, archivando los 51 miembros (`astroweather/workers/fetch_ensemble.py`)
- [x] Worker de forecast híbrido: 9 km (superficie+nubes+BLH) + perfiles de 4 modelos (`fetch_forecast.py`)
- [x] Worker TESS con backfill 2019→hoy (`fetch_tess.py`) — brillo de cielo, ~450k muestras/año
- [ ] Workers: air quality (CAMS/ADS), scraper de cloud.html
- [ ] Backfill determinista (Previous Runs)
- [ ] Pipeline FWHM (FITS → estrellas → FWHM + excentricidad → cenit/λ → Parquet)
- [ ] Panel de verificación mínimo
- [x] AEMET verificada (clave en `.env`): sin histórico horario; estación 7067Y a 11,5 km y 1485 m
- [x] Credenciales ADS (CAMS) en `.env`
- [ ] Pendientes de terceros: credenciales BDRC, campo exacto del CloudWatcher

## Uso

```bash
pip install pyarrow duckdb requests pytest
python -m astroweather.workers.fetch_ensemble --days 7   # archiva los 51 miembros de hoy
python -m astroweather.workers.fetch_forecast --days 7   # hibrido: 9 km + perfiles 4 modelos
python -m astroweather.workers.fetch_tess                 # backfill TESS (reanudable por meses)
python -m pytest tests/ -q
```

Consultar lo archivado:

```python
import duckdb
duckdb.sql("""
  SELECT valid_utc,
         sum(CASE WHEN cloud_total < 20 THEN 1 ELSE 0 END)*1.0/count(*) AS p_raw
  FROM read_parquet('data/forecasts/**/*.parquet', hive_partitioning=true)
  GROUP BY valid_utc ORDER BY valid_utc
""")
```

`data/` no va al repo (ver `.gitignore`): son datos, no código.

## Reglas de la casa

- **El raw nunca se sobrescribe.** Cada predicción conserva raw → física → calibrado
  → observación → error de verificación.
- **Se muestra la noche (ocaso−1h → orto+1h), se archiva el día** (la tarde predice la noche).
- **Nada se publica sin su incertidumbre** y su estado de calibración; terciles antes
  que decimales falsos.
- **Ficha de fuente obligatoria** antes de integrar cualquier fuente nueva: qué significa
  el dato, retención, resolución nativa, licencia, SLA — verificado con llamada real.

## Secretos y caducidades

`.env` (fuera del repo) guarda las claves. **La clave de AEMET caduca el 2026-12-05**;
regenerar en opendata.aemet.es. Aviso a Nino: **2026-11-21**.
