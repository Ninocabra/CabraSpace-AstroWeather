# Catálogo de parámetros — qué medimos, de dónde, qué guardamos y para qué

> Fichero que SPEC v8 §1.2 declara como «versión viva en el repo» y que no
> existía hasta el 2026-08-28. Todas las cifras de cobertura salen de ejecutar
> contra el archivo real ese día; ninguna es de memoria.
>
> Regla del proyecto que este documento hace cumplir: **un número sin su porqué
> no se publica**, y una columna sin dato se declara vacía en vez de aparentar
> que existe.

---

## 1. De un vistazo

| | |
|---|---|
| `measurements` | 3.668.510 filas, 2019-03 → hoy |
| `forecasts` | 139.829 filas, 2024-02 → hoy |
| `daily_observations` | AEMET diario, 2019 → hoy |
| Archivo en disco | 83 MB, 646 ficheros, **no viaja en git** |
| Crecimiento medido | ~600 ficheros y 4-6 MB/día en régimen |

---

## 2. Lo que medimos en el sitio (`measurements`)

Cobertura = porcentaje de las 3.668.510 filas que llevan ese dato.

| Magnitud | Columna | Fuente | Cadencia | Cobertura | Para qué la guardamos |
|---|---|---|---|---|---|
| Brillo de cielo | `sky_mag_raw` | Fotómetro TESS-W `stars289` (STARS4ALL) | ~1/min | **44,3 %** (1.625.577) | Es la **verdad terreno de nubes** de todo el proyecto, y de donde salieron el suelo natural, el término zodiacal, la Vía Láctea, la ley del crepúsculo y la ley de dispersión lunar. Sin él no hay nada |
| Temperatura de cielo IR | `t_sky_ir` | AAG CloudWatcher de AstroCamp | ~5 min | 36,8 % | **Detector de nubes independiente del modelo de cielo**. Muerto de 2022 a 2026-08-26; **vivo otra vez desde el 27-08** |
| Índice de nubes del AAG | — | AAG CloudWatcher | ~5 min | **NO SE GUARDA** | El parser lo lee (`sky_index`) y lo tira. Ver §5 |
| Veredicto Clear/Cloudy | `clear_flag` | Log de seguridad de AstroCamp | ~5 min | 36,8 % | La llamada del propio observatorio. **No sale del sensor IR**, sale de su log |
| Temperatura del aire | `t2m` | Estación meteo de AstroCamp | ~5 min | 36,8 % | Verificar el sesgo de T del NWP a esta cota, y el margen de rocío. **Colisión semántica, ver §5** |
| Humedad relativa | `rh` | Estación meteo | ~5 min | **0,01 %** (362) | Margen de rocío. Solo desde el 27-08 |
| Viento | `wind` | Estación meteo | ~5 min | **0,01 %** (362) | Gate de guiado. Solo desde el 27-08 |
| Racha | `gust` | — | — | **VACÍA** | **Alimenta un gate de SEGURIDAD y no tiene ni un dato.** Ver §4 |
| Presión de estación | `station_pressure` | Estación meteo | ~5 min | **0,01 %** (362) | Contraste con la presión del modelo |
| Techos abiertos | `roof_open` | Página de AstroCamp | ~5 min | 0,005 % (183) | **Censura**: nadie observando ≠ mal tiempo. Solo desde el 27-08 |
| Aerosol en superficie | `pm2_5` | Sensor de AstroCamp, **digitalizado de su PNG** | 1 h | nueva (08-09-2026) | **La única medida in situ de aerosol que tenemos.** Todo lo demás es CAMS, o sea modelo. Es con lo que por fin se puede calibrar `AEROSOL_SCALE_HEIGHT_M`. No es un calímetro: mide superficie, y la calima viaja a 1,5-5 km (53 % de las horas de AOD alto tienen PM2.5 normal) |
| Seeing (FWHM) | `fwhm_zenith_500nm` | Pipeline propio sobre frames | por frame | **0,002 %** (60) | Calibrar el modelo de seeing, que hoy predice 1,21″ donde se midieron 3,16″ |
| Brillo calibrado en banda | `sky_mag_calibrated` | — | — | **VACÍA** | Requiere una conversión TESS→V versionada que no existe (SPEC §2.3) |
| Coeficiente de extinción | `k_extinction` | — | — | **VACÍA** | Lo mediría la allsky, que no existe. Hoy `k` es un valor de partida de 0,18 |

## 3. Lo que nos predicen (`forecasts`)

| Magnitud | Columnas | Fuente | Cobertura | Para qué |
|---|---|---|---|---|
| Nubes totales | `cloud_total` | ECMWF IFS 0,25° (Open-Meteo) | **82,8 %** | La entrada del calibrador de probabilidad. Es la única con 2,6 años de retroanálisis |
| Nubes por capas | `cloud_low/mid/high` | ECMWF | 1,1 % | Distinguir cirros de estratos; solo desde el 27-08 |
| T, HR, viento, precipitación | `t2m`, `rh2m`, `wind10m`, `precipitation` | ECMWF | 78-83 % | Gates y verificación |
| Racha, CAPE, capa límite, punto de rocío | `gust10m`, `cape`, `blh`, `td2m` | ECMWF | **0,2 %** | Gates de seguridad y rocío. **Solo existen desde el 27-08** |
| Niveles de presión | `t850`…`rh250`, `v200`…`v500`, `gph*` | ECMWF | **0,9 %** | Entradas del perfil Hufnagel-Valley del seeing. **1.226 filas: el modelo de seeing no tiene con qué validarse** |
| Aerosol | `aod_total`, `dust_surface`, `pm10` | CAMS (Open-Meteo AQ) | 16,2 % | Transparencia y acoplamiento con la Luna |
| Agua precipitable | `pwv` | — | **VACÍA** | Prevista para la LUT de banda estrecha |
| Multimodelo | varios `model` | ICON, GFS, MeteoFrance, ECMWF | 168 filas c/u | Marcador entre proveedores. **Empezó el 27-08; ninguno llega a 200 h** |
| EPS (51 miembros) | `member` 0-50 | Open-Meteo Ensemble | 1 fichero | **Lo único IRRECUPERABLE**: aguas arriba solo vive 4-5 días |
| Nuestras propias predicciones | `p_raw`, `p_calibrated`, `fwhm_est`, `mag_sky_est` | — | **VACÍAS** | Ver §4: no hay registro de lo que predijimos |

## 4. Los huecos, por orden de gravedad

1. **`gust` vacía y es un gate de seguridad.** El parser de AstroCamp no lee
   rachas y ninguna de las tres estaciones AEMET cercanas las publica
   (comprobado llamando: 7067Y, 7072Y y 7066Y sirven `ta`, `hr` y `prec`, nada
   de viento). Hoy el gate de racha corre solo con la previsión y **no tiene
   verdad terreno con la que comprobarse**.
2. **No archivamos lo que predecimos.** `p_calibrated` está vacía y el marcador
   recalcula la probabilidad desde el `cloud_total` guardado. Consecuencia: si
   el calibrador cambia, *cambia retroactivamente lo que dijimos que dijimos*, y
   no se puede auditar qué vio el usuario una noche concreta.
3. **El seeing no tiene con qué validarse.** 60 medidas de FWHM y 1.226 filas de
   niveles de presión. El modelo se publica en terciles precisamente por esto.
4. **Los sensores de la estación llevan un día.** `rh`, `wind`, `station_pressure`
   y `roof_open` existen desde el 27-08. Todo lo anterior es fotómetro y AAG.

## 5. Defectos de significado encontrados el 2026-08-28

1. **`t2m` mezcla dos instrumentos.** Las filas del CloudWatcher escriben en
   `t2m` el sensor *ambient* del AAG, que va en la caja y se calienta al sol;
   las de la estación escriben la temperatura real del aire. Medido en las
   mismas 24 horas: la estación va de 11,3 a **29,2 °C** y el AAG de 12,2 a
   **43,1 °C**. Ningún consumidor actual se lo come (los gates leen la página en
   vivo y la verificación usa AEMET), pero muerde al primero que consulte el
   archivo sin filtrar por `instrument_type`.
2. **El índice de nubes del AAG se lee y se tira.** El parser extrae
   `sky_index` y `to_rows` nunca lo escribe. Es el número que el fabricante da
   ya corregido, y es justo el que queremos.
3. **El punto de rocío de la estación se lee y se tira.** La página publica
   `dew_point` y nosotros lo recalculamos por Magnus-Sonntag desde `t2m` y `rh`
   — perdiendo además la comprobación cruzada gratis.
4. **La lluvia y la dirección del viento se leen y se tiran**, y no tienen
   columna donde ir en el esquema.
5. **`cloud_frac` no es una fracción.** Los datos de 2019-2021 van de −94,9 a
   275,2 con moda en 99,8. Sea lo que sea, no es lo que dice el nombre.
6. **`t_sky_ir` y `cloud_frac` no son «cero literal desde 2021»** como decía
   nuestra propia nota: son **NULL** desde 2022. La distinción importa porque un
   cero se promedia y un NULL no.

## 6. Resolución adoptada

**Espacial.** No hace falta rejilla: los dos términos del cielo son analíticos y
se evalúan en el punto exacto al que se apunta. El gradiente lunar es casi
constante entre 15° y 60° (0,051 mag/grado) y el suelo es tan suave que 27° de
espaciado bastarían. La rejilla solo hace falta para *pintar* la cúpula.

**Temporal.** No es la misma para todos:

| Flujo | Cadencia adoptada | Motivo |
|---|---|---|
| Fotómetro | Canónico a **10 min** (mediana, dispersión, n, mín, máx) | Medido: 0 % de cambio en los veredictos y 10× menos volumen. Nadie lee las lecturas sueltas |
| CloudWatcher | **5 min, sin tocar** | Es la verdad independiente de nubes y solo tenemos un día despejado: no hay con qué fijarla |
| Estación | 5 min | Todavía no hay serie con la que decidir |
| Previsiones | La del proveedor | No la elegimos; lo que decidimos es qué pasadas se conservan |
| AEMET horario | **2 sondeos/día** | El endpoint sirve ~12 h: sondeándolo dos veces al día la serie queda completa hacia adelante |

**El RAW nunca se agrega ni se sobrescribe.** Todo lo de arriba es sobre el
canónico, que es derivado y re-derivable.

## 7. Por qué guardamos cada cosa, en una frase

- **Fotómetro**: es el único instrumento con siete años de historia y de él sale
  toda la física medida del sitio.
- **CloudWatcher**: para dejar de deducir las nubes de un modelo y medirlas.
- **Estación y techos**: para los gates de seguridad y para saber cuándo *nadie
  estaba observando*, que no es lo mismo que mal tiempo.
- **Previsiones**: para saber a quién creer, y eso solo se puede medir
  archivando lo que dijo cada uno **antes** de que pasara.
- **EPS**: porque desaparece en 4-5 días y es la única fuente de incertidumbre
  real que tenemos.
- **AEMET**: verdad externa, calibrada y oficial, a 11,5 km — lo único que puede
  arbitrar si nuestros sensores derivan.
