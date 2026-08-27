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
- [x] Worker AEMET: histórico diario 2019→hoy de la estación 7067Y (`fetch_aemet.py`)
- [x] Scraper de AstroCamp (`scrape_astrocamp.py`) — CloudWatchers, estaciones, techos, log de seguridad y 15 años de horas de cúpula
- [x] Capa de consulta deduplicada (`queries.py`) — vistas `measurements`, `forecasts`, `nights`
- [x] Worker de aerosol (`fetch_aerosol.py`) — AOD total horario + AOD de polvo de CAMS con corrección por especies
- [x] Backfill determinista por lead time (`backfill_history.py`) — 2024→hoy
- [ ] Pipeline FWHM (FITS → estrellas → FWHM + excentricidad → cenit/λ → Parquet)
- [x] Panel de verificación (`verification.py`) — Brier de nubes, sesgo/MAE de T, baselines
- [x] AEMET verificada (clave en `.env`): sin histórico horario; estación 7067Y a 11,5 km y 1485 m
- [x] Credenciales ADS (CAMS) en `.env`
- [x] Pipeline FWHM (`fwhm_pipeline.py`) — sobre los FITS reales de `E:\ASTRO Sin Procesar\Observatorio Nerpio`
- [ ] Pendientes de terceros: credenciales BDRC (opcional)

## Recogida automatica

Hay datos que **caducan**: los 51 miembros del EPS viven ~4 dias en el proveedor
y `cloud.html` solo publica el instante actual. El scheduler los recoge solo:

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_scheduler.ps1 -Startup
```

Arranca al iniciar sesion (sin permisos de administrador) y escribe en
`data/scheduler.log`. Cadencias: sensores cada 5 min - EPS 2x/dia - forecast
cada 6 h - aerosol, AEMET y TESS 1x/dia - verificacion cada manana.
Mientras corra en este PC solo recoge dato con el equipo encendido; el VPS de
la fase 1 lo convierte en 24/7.

```bash
python -m astroweather.scheduler --once     # todo una vez (prueba o puesta al dia)
python -m astroweather.scheduler            # en primer plano, para ver que hace
```

## Uso

```bash
pip install pyarrow duckdb requests pytest
python -m astroweather.workers.fetch_ensemble --days 7   # archiva los 51 miembros de hoy
python -m astroweather.workers.fetch_forecast --days 7   # hibrido: 9 km + perfiles 4 modelos
python -m astroweather.workers.fetch_tess                 # backfill TESS (reanudable por meses)
python -m astroweather.workers.fetch_aemet                # historico diario de la estacion 7067Y
python -m astroweather.workers.scrape_astrocamp           # sensores del sitio (correr cada 5 min)
python -m astroweather.workers.fetch_aerosol --dust       # AOD total + calima de CAMS
python -m astroweather.workers.backfill_history --start 2024-01-01
python -m astroweather.verification                       # el panel
python -m astroweather.workers.fwhm_pipeline "E:\ASTRO Sin Procesar\Observatorio Nerpio\Imagenes\M826-01-11\LIGHT"
python -m pytest tests/ -q
```

Consultar lo archivado:

```python
from astroweather.queries import connect
c = connect()          # vistas ya deduplicadas
c.execute("""
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
