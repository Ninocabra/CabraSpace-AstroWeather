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
- [x] **WeatherNext 3** (Google DeepMind) por Earth Engine, proyecto
      `astroweather-cabra`, sin facturacion (BigQuery descartado: factura la
      malla global aunque se pida un punto). Gano el examen del 17-09-2026
      contra el ECMWF sobre las mismas 1270 h de fotometro: +0,024 de AUC a
      1 dia (IC [+0,004, +0,046]) y hasta +0,074 a 4 dias. Backfill ene-sep
      2026 archivado en 9 ficheros mensuales (4,3 MB); el crudo no sube.
      Trabajo `weathernext` cada 12 h, pasadas 00Z y 12Z de 144 h.
- [ ] **WeatherNext en el runner**: PENDIENTE de que Google conceda acceso a
      `astroweather-runner@astroweather-cabra.iam.gserviceaccount.com` (pedido
      el 17-09-2026 por el formulario, campo de cuenta de servicio adicional).
      Hasta entonces recoge EL ESCRITORIO, con una tarea cada 3 h: si el PC
      esta apagado, esas pasadas no se archivan. El runner ya esta listo
      (`.gitignore` del archivo escrito por `archivepush`, cache de las
      pasadas sueltas y las dos variables en el workflow); cuando llegue el
      acceso solo faltan DOS cosas: poner los secretos `EE_SERVICE_ACCOUNT_KEY`
      y `GOOGLE_CLOUD_PROJECT` en Actions, y declarar `earthengine-api` en
      `requirements-colector.txt` quitando `ee` de FUERA.
- [ ] **WeatherNext en el veredicto**: su curva en `calibration.py` y ver si
      mezclado con el ECMWF mejora a cada uno por separado. En la web, solo el
      veredicto derivado y con la cita de Google: su nubosidad no se publica.

## Fase 1 — en curso

- [x] **Rodaja 1: Noche + Luna** (`ephem.py`, `moonsky.py`, `moonscatter.py`) —
      ventana de la noche, efemerides precisas y el modelo lunar con la
      estructura de Krisciunas & Schaefer pero con la **ley de dispersion MEDIDA
      aqui**: la de Mauna Kea se queda corta por un factor de 1,2 con la Luna
      baja a 5,5 con la Luna alta. Y con una de sus "tres trampas" derogada por
      el archivo: **la Luna bajo el horizonte NO aporta cero**, sigue dando
      +0,19 mag con el disco ya invisible, decayendo con la sombra geometrica de
      la atmosfera. Validado contra noches reservadas (`moonvalidation.py`).
- [x] **Rodaja 2: Nubes** — modelo de cielo despejado (`skymodel.py`), deteccion de
      nubes historica sobre los 7 anos del TESS, y calibrador de probabilidad
      (`calibration.py`) entrenado con 33.799 pares de 2,6 anos.
      **BSS +11,7 % sobre climatologia.** Era +3,5 % hasta el 28-08-2026, y la
      diferencia no fue mas ajuste sino arreglar la VERDAD: con el modelo lunar
      de Mauna Kea, una noche despejada con Luna salia hasta 1,8 mag fuera del
      modelo y se archivaba como nublada. Eso caia casi entero sobre horas que
      el forecast habia dado por despejadas, que es donde la curva estaba plana:
      previsto 0-10 % de nubes pasa de 75,3 % a 90,1 % de horas despejadas.
- [x] **Rodaja 3: Lluvia + viento + techo** (`gates.py`, `tonight.py`) → **MVP
      OPERACIONAL COMPLETO**: `python -m astroweather.tonight` responde de punta
      a punta "¿podre observar esta noche, con seguridad, y cuando?".
- [x] **Rodaja 4: Calima + transparencia** (`extinction.py`) — k por altitud y
      especies, dependencia con la longitud de onda, penalizacion de exposicion,
      y 22.512 horas de AOD historico. Validado contra el fotometro: **mas
      aerosol = cielo mas OSCURO** aqui (lo contrario de la intuicion urbana).
- [x] **Rodaja 5: Fondo de cielo** (`skybackground.py`) — presupuesto por
      componentes MEDIDO en el sitio: suelo natural 21.71 (no el 21.9 supuesto;
      **pero ver SPEC 2.2 bis: ese suelo deriva +0,56 mag entre 2019 y 2026 y no
      describe ningun ano**),
      zodiacal 0.27 mag en la ecliptica, airglow lento por F10.7, y el coste en
      exposicion de cada magnitud perdida.
- [x] **Rodaja 6: Seeing** (`seeing.py`) — perfil Hufnagel-Valley con los tres
      contratos (h sobre el sitio, W ponderada por espesor, v_eff pesada por
      Cn²), Richardson como features y flag de riesgo. **Publicado en terciles,
      nunca en arcosegundos**: el modelo predijo 1.21" donde se midieron 3.16",
      y esa diferencia es casi toda instrumental — calibrar A ahora ajustaria
      el telescopio, no el cielo.
- [x] **Motor de horas-SNR por target** (`targets.py`) → **MVP RECOMENDADOR**.
      Responde "que fotografiar y cuanto cundira", con dos numeros por objeto
      (si despeja / esperadas) y su motivo. **Con una regresion conocida y sin
      tapar** (test en xfail): con el cielo lunar corregido, las constantes de
      banda estrecha -- calibradas contra un cielo 1,8 mag demasiado oscuro --
      hacen que con Luna llena ya no recomiende banda estrecha, que como consejo
      es falso. Se re-derivan con la LUT de SkyCalc.
- [ ] Lienzo nocturno (mockup SVG antes que backend)

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
python -m astroweather.webexport                          # el JSON de la noche, a data/web/
python -m astroweather.webpublish                         # y de ahi al repo publico
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

## Dónde vive el archivo

`data/` **ya no vive solo en este disco**. La copia está en
[CabraSpace-AstroWeather-Archive](https://github.com/Ninocabra/CabraSpace-AstroWeather-Archive)
(privado), y el criterio no es subirlo todo, es peso contra relevancia:

- lo canónico (`measurements`, `forecasts`, `daily_observations`) se copia tal
  cual, y sale barato porque el almacén es de **solo-añadir**: nada se reescribe,
  así que el historial de git pesa lo que pesan los datos una vez;
- los scrapes de AstroCamp se agrupan en **un `.tar.xz` por día terminado** —
  826 kB sueltos contra 23 kB juntos, medido: **35,8×**, porque son la misma
  página cada 5 minutos y de uno en uno no se aprovecha la repetición;
- el raw del fotómetro **no sube**: 37,3 MB, el 93 % de todo el raw, y es lo
  único grande recuperable entero (`fetch_tess`), con sus valores ya dentro de
  `measurements/`.

```bash
python -m astroweather.archivepush
```

Busca el clon en `../AstroWeather-Archive` o donde diga
`ASTROWEATHER_ARCHIVE_REPO`; si no está, no archiva y no falla.

## Dónde sale el producto

El motor es privado; **lo que predice es público**, en su propio repositorio de
datos ([CabraSpace-AstroWeather-Data](https://github.com/Ninocabra/CabraSpace-AstroWeather-Data)):

```
https://raw.githubusercontent.com/Ninocabra/CabraSpace-AstroWeather-Data/main/noche.json
```

URL estable, `access-control-allow-origin: *`, se reescribe cada 6 h. El
historial de lo que dijimos **es el `git log` de ese fichero**, porque siempre
se sobrescribe la misma ruta; y un commit significa que cambió la previsión, no
que volvimos a calcularla.

`webpublish` necesita el clon del repo de datos: por defecto lo busca al lado
de éste (`../AstroWeather-Data`), o donde diga `ASTROWEATHER_DATA_REPO`. En una
máquina que no lo tenga, no publica y no falla. **El raw de terceros no se
republica**: ver SPEC §7.1.

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
regenerar en opendata.aemet.es. Aviso: **2026-11-21**.
