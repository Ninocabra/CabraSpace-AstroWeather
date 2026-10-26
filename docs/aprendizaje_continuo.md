# Aprendizaje continuo: el producto se puntua solo cada manana

Desde el 26-10-2026. Cuatro piezas encadenadas, que corren dentro del colector
(`astroweather/scheduler.py`, tabla `JOBS`) y escriben en el archivo:

| pieza | trabajo | cuando | escribe | se lee con |
|---|---|---|---|---|
| 1. Nota de cada noche | `nota` (`nightscore.py`) | una vez al dia, desde las 10 UTC | `night_scores/` (y `night_publications/` en cada commit de `noche.json`) | `nightscore.latest_scores(root)` |
| 2. Aspirante contra campeona | `techo_curva` (`curvecontest.py`) | una vez al dia, desde las 10 UTC, despues de `nota` | `roof_calibration.json` (solo si gana) y `curve_decisions/` | `curvecontest.decisions(root, "techo")` |
| 3. Salud | `salud` (`health.py`) | cada pasada, justo antes de `archivo` | `salud.json`, bloque `salud` de `estado_colector.json`, `marcador.json` del repo de datos | `health.read(root)` o `marcador.json` |
| 4. Curva del fotometro y modelo a 1 dia | `nube_curva` y `modelo_d1` (`curvecontest.py`) | una vez por semana, desde las 10 UTC, despues de `techo_curva` | `cloud_calibration.json` y `cloud_calibration_weathernext3.json` (solo si ganan) y `curve_decisions/` | `curvecontest.decisions(root, "fotometro")`, `"fotometro_weathernext3"`, `"modelo_d1"` |

La vara de medir y las verdades son las del retroanalisis: `skill.py` (Brier,
AUC, remuestreo por noches) y `nighttruth.py` (marco de la noche, verdades de
nubes y su consenso, verdad del techo, lo publicado). `hindcast.py` las importa
de ahi; sus cifras no cambian (comprobado con `--resamples 50` antes y despues:
solo difieren la fila "barajada", que cambia de una ejecucion a otra con el
mismo codigo, y el orden de dos filas de una tabla).

Los tres almacenes son de solo anadir (`archive.write_canonical`: un Parquet
nuevo por escritura, nunca se reescribe nada) y viajan al archivo como los
demas (`archivepush.MIRRORED`; en el runner, enlazados dentro del clon,
`colector.yml`). Se descarto un JSON suelto porque los sueltos se reescriben y
una maquina atrasada puede pisar a la buena (paso con `open_hours` el
17-09-2026); con un fichero por escritura no hay nada que fundir ni choque de
git posible.

---

## 1. La nota de cada noche (`night_scores`)

**Que hace.** Cada manana, desde las 10 UTC, puntua la noche cerrada de ayer y
rehace las tres anteriores (`RESCORE_NIGHTS = 4`). Solo anade fila a una noche
nueva o cuya nota cambio, y nunca la cambia por una verdad PEOR (`merge`): la
fila vigente de cada noche es la de `scored_utc` mas reciente.

**Por que las 10 UTC.** AstroCamp apunta las horas de la noche en su tabla una
vez al dia: con el colector cada 30 min, el cambio se vio a las 08:01-08:04 UTC
26 dias de 26 de septiembre (historial git de `open_hours_nerpio.json`), las
10:00 de su hora; con el horario de invierno pueden ser las 09:00 UTC.

**Por que se rehacen cuatro noches.** Una noche cerrada no cambia la tabla, y
"no ha cambiado" no distingue "0 horas" de "aun no la han apuntado". Como en el
retroanalisis, solo cuentan los cambios (`nighttruth.table_changes`) y una noche
sin cambio queda acotada cuando cambia por la siguiente. Cada fila guarda la
version de la tabla que vio y cuando se vio por primera vez (`table_json`,
`table_seen_utc`): el runner clona el archivo con profundidad 1 y esa cadena de
fotos es lo unico que le permite repartir manana lo de hoy.

**Lo publicado** sale de las capturas que hace `publicar` en cada commit de
`noche.json` (`night_publications`, `nightscore.record_publication`): el repo de
datos tambien se clona con profundidad 1, y a la manana siguiente la version de
las 16 UTC ya no esta. Donde hay historial completo (el escritorio) se usa
tambien. D0 = la publicacion vigente a las 16 UTC del dia de la noche; D-1 = la
de la vispera, para esa misma noche.

### Columnas de `night_scores`

Una fila por noche (y pasada de puntuacion). `date` = la noche, por la fecha en
que empieza. Horas en h, fracciones 0-1, instantes en UTC.

| columna | que es |
|---|---|
| `site_id`, `date`, `scored_utc`, `origin` | sitio, noche, cuando se puntuo, `diario` o `relleno` |
| `dark_hours`, `moon` | noche astronomica (h) e iluminacion lunar a las 23:30 UTC |
| `roof_hours`, `roof_source`, `roof_opened` | horas de techo abierto, de donde (`tabla limpio`, `tabla aprox`, `lecturas`, `cota: <=2 h en todo el tramo`, `lectura suelta abierta`) y si abrio (>= 2 h) |
| `roof_dark_hours` | horas abiertas con el Sol < -18 grados, de las lecturas (None con < 12 lecturas: desde el 30-09 casi siempre) |
| `roof_samples`, `roof_table_hours`, `roof_table_quality` | lecturas dentro de la noche; horas segun la tabla y calidad del reparto |
| `phot_*`, `cw_*`, `sky_*`, `consensus_*` | `_hours` horas medidas y `_clear_frac` fraccion despejada del fotometro, CloudWatcher, allsky y consenso horario |
| `vote` | voto de la noche: cada sensor dice `despejada` si lo estuvo >= la mitad de sus horas; mayoria con >= 2 y sin empate |
| `d0_published_utc`, `d0_p_open`, `d0_open` | lo publicado a las 16 UTC: generado, P(abrir), veredicto |
| `d0_window_from`, `d0_window_to`, `d0_expected_h` | ventana del veredicto y horas utilizables esperadas |
| `d0_curve_fitted`, `d0_truth_curve`, `d0_source` | curva que lo produjo (`curva_ajustada_el`), su verdad (`techo`/`fotometro`) y de donde se leyo (`publicar` o `git noche.json`) |
| `d0_outcome`, `d0_hit` | `acierto` / `falsa_alarma` / `sorpresa` / `cierre_bien`, y si el veredicto acerto |
| `d0_brier`, `d0_hours_error`, `d0_hours_error_dark` | (p - abrio)^2; esperadas - techo; esperadas - techo con Sol < -18 |
| `dm1_*` | lo mismo para la publicacion de la vispera |
| `table_seen_utc`, `table_json` | la version de la tabla de AstroCamp vigente al puntuar y su primer instante visto |

Ejemplo (noche del 25-10-2026, del relleno):

```json
{"date": "2026-10-25", "roof_hours": 11.0, "roof_source": "tabla limpio", "roof_opened": true,
 "phot_hours": 2, "phot_clear_frac": 1.0, "cw_hours": 1, "sky_hours": 5, "vote": "despejada",
 "d0_p_open": 0.721, "d0_open": true, "d0_outcome": "acierto", "d0_brier": 0.078,
 "d0_hours_error": -3.3, "dm1_p_open": 0.973, "dm1_outcome": "acierto"}
```

### Relleno y control positivo

`python -m astroweather.nightscore --backfill` (opciones `--root`, `--archive`,
`--data-repo`, `--dry-run`) puntua desde el 28-08-2026 con el historial git
completo de los dos repos y escribe tambien las capturas de las 216 versiones
de `noche.json`. 25 s en el escritorio. Medido el 26-10-2026 sobre una copia del
archivo:

| conjunto | noches | aciertos | FA | sorpresas | cierres | Brier | AUC | informe |
|---|---|---|---|---|---|---|---|---|
| D0 desde 05-09 | 42 | 30 | 1 | 5 | 6 | 0,160 | 0,89 | 42 / 30 / 1 / 5 / 6, 0,160, 0,89 |
| D0 todo | 49 | 36 | 2 | 5 | 6 | 0,155 | 0,80 | 49 / 36 / 2 / 5 / 6, 0,155, 0,80 |
| D-1 desde 05-09 | 42 | 33 | 3 | 2 | 4 | 0,134 | 0,80 | 42 / 33 / 3 / 2 / 4, 0,134, 0,80 |

Ademas: las horas de techo de las 59 noches salen identicas a las del
retroanalisis (0 diferencias); puntuar con las capturas releidas del Parquet en
vez del historial git da 0 diferencias en 59 noches x 35 campos; y la pasada
diaria sin historial git (como en el runner) sobre el archivo con el relleno no
reescribe ninguna noche ya puntuada.

---

## 2. Aspirante contra campeona (`curve_decisions`)

**Que hace.** Cada manana (`techo_curva`, despues de `nota`): toma las ultimas
K = 14 noches puntuables (techo conocido y publicacion D0 con sus horas), ajusta
la aspirante con el codigo de produccion (`roofcalibration.fit`) SOLO con las
noches anteriores, y mide las dos curvas en esas 14 noches con el Brier del
veredicto: P(abrir) con la regla de produccion de hoy (la hora mas floja de la
mejor ventana de dos horas, solo casillas de noche astronomica -M6- y las
puertas de seguridad que se publicaron), contra "el techo abrio >= 2 h". El AUC
se registra pero no decide (con 1-3 noches cerradas casi nunca esta definido).

| caso | decision |
|---|---|
| no hay `roof_calibration.json` | `arranque_en_frio`: entra la aspirante |
| menos de 7 noches puntuables | `se_queda` |
| Brier(aspirante) - Brier(vigente) <= -0,02 | `sustituye`: se guarda la aspirante |
| si no | `se_queda` |
| la aspirante no se puede ajustar | `se_queda`, con el error |

Se guarda la aspirante EXAMINADA (ajustada sin las 14 noches), no una
reajustada con todo: lo que entra es lo que se examino, y en regimen la
campeona tambien llega a las 14 noches sin haberlas visto.

**Por que K = 14 y margen = 0,02** (`python -m astroweather.curvecontest
--simular`: el protocolo dia a dia del 20-09 al 26-10 sobre el archivo):

| K | ventanas sin ninguna noche cerrada (de 37) | ruido de reajuste \|dif Brier\| p50 / p90 / p95 |
|---|---|---|
| 7 | 7 | 0,001 / 0,009 / 0,021 |
| 10 | 1 | 0,002 / 0,040 / 0,040 |
| 14 | 0 | 0,001 / 0,020 / 0,027 |
| 21 | 0 | 0,006 / 0,020 / 0,026 |

"Ruido de reajuste" = diferencia de Brier, sobre las K noches del examen, entre
dos aspirantes ajustadas con un dia de diferencia (el mismo metodo y
practicamente la misma curva). El margen es su p90 con K = 14.

Brier del veredicto que cada protocolo habria publicado LA NOCHE SIGUIENTE
(fuera de muestra para todos; 22 noches comunes, 6 cerradas; IC90 pareado por
noches contra "reajustar a ciegas con todo hasta ayer"):

| protocolo | Brier | dif. contra a ciegas | cambios de curva en 37 dias |
|---|---|---|---|
| lo publicado de verdad | 0,173 | +0,040 [-0,020, +0,100] | - |
| a ciegas (lo que hacia el trabajo hasta hoy, con el codigo de M1) | 0,133 | - | cada dia |
| K = 7, margen 0,01 | 0,126 | -0,006 [-0,024, +0,008] | 2 |
| K = 10, margen 0,01 | 0,129 | -0,004 [-0,049, +0,048] | 3 |
| K = 14, margen 0 | 0,130 | -0,002 [-0,046, +0,041] | 18 |
| K = 14, margen 0,01 | 0,132 | -0,001 [-0,042, +0,041] | 4 |
| **K = 14, margen 0,02** | **0,137** | **+0,004 [-0,040, +0,050]** | **3** |
| K = 21, margen 0,02 | 0,169 | +0,036 [-0,018, +0,093] | 1 |
| K = 14, examinar con reserva y publicar reajustada con todo | 0,125 | -0,007 [-0,027, +0,008] | 0 |

Lectura: con 22 noches no se distingue K = 7, 10 o 14 entre si ni de reajustar
a ciegas; K = 21 si se queda atras (va 21 noches por detras de los datos). Se
elige K = 14 porque es el menor que tiene noches cerradas en TODAS las ventanas
(el examen sin noches cerradas solo mide quien dice "Abierto" mas alto), y
margen 0,02 porque deja pasar solo diferencias por encima del 90 % del ruido de
reajuste: sin margen, la curva cambio 18 veces en 37 dias. La variante
"reajustada con todo" se descarta: con margen de 0,005 o mas cambia 0-1 veces en
37 dias, porque la campeona ya vio las noches del examen y gana por estar dentro
de muestra (se atasca en la primera curva).

**Lo que esto NO mide**: el protocolo protege contra una curva estropeada (una
regresion de codigo, datos malos), no hace mejor la curva; el coste de reservar
14 noches no es medible con este otono (+0,004 [-0,040, +0,050]).

**Con el archivo de hoy** (copia del 26-10, examen del 12-10 al 25-10): la
vigente (cruce mensual del 25-10) da 0,147 y la aspirante (horaria de M1,
ajustada hasta el 11-10) 0,092: `sustituye` (-0,054 [-0,121, +0,011]), lo
mismo que midio el retroanalisis. Al dia siguiente, la misma curva contra si
misma: `se_queda` (+0,000).

**Control en rojo**: una aspirante con la pendiente al reves (mas nube, mas
techo) pierde por +0,136 y otra que dice siempre 0,99 por +0,026: las dos
`se_queda`, y la vigente no se toca (`tests/test_aprendizaje_continuo.py`).

### Columnas de `curve_decisions`

| columna | que es |
|---|---|
| `date`, `decided_utc`, `curve` | dia, instante y curva (`techo`; `fotometro` cuando se enchufe) |
| `decision`, `reason` | `sustituye` / `se_queda` / `arranque_en_frio`, y por que, en una frase |
| `k_nights`, `margin`, `n_nights`, `nights` | parametros, noches examinadas y cuales (JSON) |
| `champion_fitted_at`, `challenger_fitted_at` | que curvas se compararon |
| `champion_brier`, `challenger_brier`, `d_brier`, `d_brier_lo`, `d_brier_hi` | Brier de cada una y diferencia aspirante - vigente con IC90 pareado por noches |
| `champion_auc`, `challenger_auc` | AUC (nulo si no hay noches de las dos clases) |
| `champion_json`, `challenger_json` | las dos curvas completas |

**La curva del fotometro** usa el mismo `compare` y el mismo registro desde el
26-10-2026: seccion 4. En `curve_decisions`, `curve` vale tambien
`fotometro` (ECMWF 0,25), `fotometro_weathernext3` y `modelo_d1` (solo medida;
`decision` = el modelo ganador y la tabla de los tres en `challenger_json`).

---

## 3. La salud (`salud` en `estado_colector.json` y `marcador.json`)

**Que hace.** En cada pasada (0,6 s; 1,8 s la primera del dia, que mira el
corte de la allsky) calcula un bloque con tres niveles, `ok` / `aviso` /
`rojo`; `nivel` es el peor de todos. Lo deja en `data/salud.json`, el colector
lo copia en `estado_colector.json` (que ya viaja al archivo en cada pasada; la
fusion entre maquinas conserva el calculado mas tarde) y, si cambio algo que
importa, lo publica en `marcador.json` del repo de datos. "Lo que importa" =
todo menos las horas y las edades de las lecturas (`health.significant`): el
historial guarda cuando cambia un nivel o una cifra diaria, no cada pasada.

### Umbrales

| parte | ok | aviso | rojo | de donde sale |
|---|---|---|---|---|
| `nota` (D0, ultimas 14 noches) | BSS >= 0,10 y acierto >= el de la climatologia | BSS 0-0,10, acierto por debajo, o < 7 noches | Brier peor que la climatologia | lo pedido: rojo = peor que no saber nada. La climatologia es la fraccion de noches abiertas ANTES de la ventana (fuera de muestra; con < 14 noches, la de la ventana, y se dice). Con 14 noches el BSS tiene un IC90 de +-0,3 o mas: un rojo suelto no prueba nada, varios seguidos si |
| `sensores.*` cadencia | >= 75 % de lo esperado ayer | 25-75 % | < 25 % | esperado = media de septiembre de 2026 con el reloj de 30 min: techo 36/dia, CloudWatcher 34, estacion 36, fotometro 600 (586-722 segun la noche), allsky 4.800 |
| `sensores.*` frescura | edad <= aviso | > aviso | > rojo | techo, CloudWatcher y estacion: aviso 2 h (hueco maximo de septiembre, 2,0 h), rojo 6 h; fotometro 16 / 24 h (no mide de dia: hueco de 13,9 h); allsky 6 / 12 h (tandas de 5 h cada 4 h) |
| `allsky_corte` | < 3 de 7 noches | >= 3 de 7 | nunca | noches con cielo antes (mediana >= 100 estrellas) y < 5 % de eso desde una hora hasta el alba. Septiembre 13-19: 5 de 7 a las 03 UTC; octubre 19-25: 2 de 7 |
| `curva_techo` | decision de hace <= 2 dias | 2-7 dias o ninguna | > 7 dias | el examen es diario |

### Control en rojo con los datos reales (copia del archivo del 26-10-2026)

| | techo | CloudWatcher | estacion | fotometro | allsky |
|---|---|---|---|---|---|
| hoy (lecturas del 25-10) | **rojo** 4/36 | **rojo** 4/34 | **rojo** 4/36 | aviso 205/600 | aviso 3.414/4.800 |
| 20-09 (lecturas del 19-09, control) | ok 35/36 | aviso 24/34 | ok 35/36 | ok 645/600 | ok 4.105/4.800 |

Es M11 del informe (octubre: 45 lecturas de techo contra 608 en septiembre): la
estacion cae igual porque sale de la misma pagina. El fotometro de los
ultimos cuatro dias (446, 178, 205 lecturas) es el hueco de `top_up` (M12).

### Formato de `marcador.json` (estable, version 1)

```json
{
 "generado_utc": "2026-10-26T22:11:23+00:00",
 "formato": "docs/aprendizaje_continuo.md",
 "salud": {
  "version": 1,
  "calculado_utc": "2026-10-26T22:11:25+00:00",
  "nivel": "rojo",
  "nota": {
   "nivel": "ok", "motivo": "BSS +0.37 y acierto 0.86 contra 0.71", "ventana_noches": 14,
   "d0": {"desde": "2026-10-12", "hasta": "2026-10-25", "noches": 14,
          "aciertos": 8, "falsas_alarmas": 0, "sorpresas": 2, "cierres_bien": 4,
          "acierto": 0.857, "acierto_climatologia": 0.714,
          "brier": 0.148, "brier_climatologia": 0.233, "bss": 0.366, "bss_ic90": [-1.185, 0.74],
          "climatologia_p": 0.886, "climatologia_fuente": "noches anteriores a la ventana (n=35)",
          "p_media": 0.484, "abrio_frac": 0.714, "nivel": "ok", "motivo": "..."},
   "dm1": {"...": "las mismas claves para la publicacion de la vispera"}
  },
  "sensores": {
   "techo": {"nivel": "rojo", "motivo": "4 lecturas el 25-10 de 36 esperadas (11%)",
             "ultima_utc": "2026-10-26T21:01:47+00:00", "edad_h": 1.2,
             "lecturas_ayer": 4, "esperadas_dia": 36, "fraccion": 0.11,
             "ultimos_dias": {"2026-10-19": 3, "2026-10-25": 4}},
   "cloudwatcher": {"...": "igual"}, "estacion": {}, "fotometro": {}, "allsky": {}
  },
  "allsky_corte": {"nivel": "ok", "noches_miradas": 7, "noches_con_corte": 2,
                   "hora_corte_utc": 3, "motivo": "..."},
  "curva_techo": {"nivel": "ok", "motivo": "...", "fecha_utc": "2026-10-26T22:11:09+00:00",
                  "decision": "sustituye", "razon": "...", "k_noches": 14, "margen": 0.02,
                  "noches_examinadas": 14, "brier_vigente": 0.147, "brier_aspirante": 0.092,
                  "dif_brier": -0.054, "dif_brier_ic90": [-0.121, 0.011],
                  "auc_vigente": 0.95, "auc_aspirante": 0.925,
                  "curva_vigente_ajustada": "2026-10-26T22:11:22+00:00"}
 },
 "ultimas_noches": [
  {"noche": "2026-10-25", "techo_abrio": true, "techo_horas": 11.0,
   "techo_fuente": "tabla limpio", "techo_horas_oscuras": null,
   "d0": {"p_abrir": 0.721, "abierto": true, "resultado": "acierto", "brier": 0.078,
          "error_horas": -3.3},
   "dm1": {"p_abrir": 0.973, "abierto": true, "resultado": "acierto", "brier": 0.001},
   "despejado": {"fotometro": 1.0, "cloudwatcher": 1.0, "allsky": 1.0, "consenso": 1.0},
   "voto": "despejada"}
 ]
}
```

Unidades: horas en h, fracciones y probabilidades 0-1, Brier y BSS sin unidad,
instantes ISO 8601 en UTC, noches por la fecha en que empiezan. Todas las
claves de nivel valen `ok`, `aviso` o `rojo`. `ultimas_noches` trae las 14
noches anteriores a hoy que tengan fila, en orden. Un cambio de formato sube
`salud.version`. El bloque `salud` de `estado_colector.json` es el mismo objeto
sin `ultimas_noches`. ~10 kB.

---

## 4. La curva del fotometro: escala nativa, examen semanal y modelo a 1 dia

**Que hace.** Una vez por semana (`nube_curva`, desde las 10 UTC, detras de
`techo_curva`) examina la curva P(despejado | nube prevista) de `calibration.py`
-- la que usan las noches siguientes a la de hoy, esta noche si no hay curva del
techo, y la rama de WeatherNext -- con la regla del techo: aspirante ajustada con
`calibration.fit` sin las ultimas K = 30 noches con verdad del fotometro; entra
solo si su Brier horario en esas noches es menor que el de la vigente por al
menos 0,0005 (IC90 pareado por noches, `compare`). Lo mismo para la curva de
WeatherNext con el mismo codigo (`cloud_duel(model="weathernext3")`). Detras,
`modelo_d1` puntua ECMWF 0,25, ICON y WeatherNext a 1 dia y lo deja en
`curve_decisions` (`curve="modelo_d1"`) sin cambiar el modelo del producto.
A mano: `python -m astroweather.curvecontest --nube [--modelo-d1] [--en-seco]`.

| caso | decision |
|---|---|
| no hay curva, o la que hay es de otro modelo | `arranque_en_frio` |
| la ultima noche del examen tiene mas de 7 dias (fotometro caido; WeatherNext sin su mes en curso, M14) | `sin_examen`: lo dice y no escribe nada |
| menos de 15 noches en el examen | `se_queda` |
| Brier(aspirante) - Brier(vigente) <= -0,0005 | `sustituye` |
| si no | `se_queda` |

### 4.1 La escala (M17, opcion B)

`calibration.NATIVE_TCC_SINCE = 2025-11-21`: solo pasadas con `run_utc` desde
esa fecha (el corte va por la pasada, no por la hora valida). Y desde hoy entra
tambien la prevision viva del mismo modelo (`ecmwf_ifs025`, alcance
`lead_h // 24`, dias 1-5; misma hora y alcance de las dos fuentes = un par): el
retroanalisis se bajo una vez, llega al 26-08-2026 y no crece, y sin lo vivo
una curva que se reentrena se reentrenaria siempre con lo mismo. Lo vivo guarda
la ultima bajada de cada dia con `run_utc` a las 00 UTC, asi que su "1 dia" y el
`previous_day1` del retroanalisis casan a unas horas, no exactos.

| pares (hora x alcance con verdad del fotometro) | n |
|---|---|
| antes: retroanalisis desde 2024-02, sin corte (con lo que se ajusto la vigente del 30-08) | 33.799 |
| retro + vivo, sin corte (la escala mezclada, solo control) | 36.137 |
| **despues: retro + vivo, pasadas >= 21-11-2025** | **10.357** (2.129 horas, 271 noches; 2.338 pares vivos) |

Enero de 2026 no tiene ni una hora de fotometro en el archivo.

**Fuera de muestra** (deja-un-mes-fuera sobre nov-2025 -> oct-2026, como el
examen de WeatherNext del 18-09: cada mes se predice con la curva ajustada con
los demas; verdad = fotometro, `observed_clear_hours`, la que ya usaba el
modulo; IC90 pareado por noches, 2.000 remuestreos):

| | pares | noches | Brier nativa | Brier mezclada | Brier vigente (30-08) | nativa - vigente | nativa - mezclada |
|---|---|---|---|---|---|---|---|
| nov-2025 -> oct-2026 | 10.357 | 271 | 0,1372 | 0,1359 | 0,1355 | +0,0017 [-0,0006, +0,0042] | +0,0013 [-0,0004, +0,0031] |
| solo sep-oct 2026 | 2.323 | 57 | 0,1172 | 0,1189 | 0,1194 | **-0,0022 [-0,0041, -0,0003]** | -0,0016 [-0,0033, -0,0000] |

Lectura. La vigente se ajusto el 30-08 con todo hasta el 26-08: de noviembre a
agosto esta DENTRO de muestra y por eso el anio entero la favorece; solo
septiembre y octubre son limpios para ella, y ahi la nativa gana por poco y
fuera del ruido. Contra la mezclada (fuera de muestra las dos) el anio entero no
distingue. La ganancia esta en la calibracion del extremo nublado, no en
ordenar: con 90-100 % de nube prevista estuvo despejado el 62,8 % de las horas,
y la nativa dice 63,6 %, la mezclada 60,0 %, la vigente 58,6 % (60-90 %:
observado 74,8 %, nativa 73,5 %, vigente 69,8 %). Con 10-30 % la nativa se pasa
(90,1 % contra 86,7 %). El AUC del anio entero sale peor para la nativa (-0,023)
porque al juntar las curvas de deja-un-mes-fuera, ajustadas con menos datos y
con un peso de alcance de -0,03 por dia, el orden entre meses y alcances se
mueve; la vigente es una sola curva con peso de alcance ~0, que ordena
exactamente como la nube cruda.

**Pendiente, M3**: la verdad sigue siendo el fotometro solo. El voto de los
tres sensores (fotometro + allsky + CloudWatcher) solo existe desde el 29-08 y
no da para entrenar; contra el consenso el ECMWF discrimina mucho mas
(AUC 0,82-0,86 contra 0,65 con el fotometro, informe del 26-10).

### 4.2 K = 30 y margen = 0,0005, medidos

`python -m astroweather.curvecontest --simular-nube --archive <copia>`: el
protocolo dia a dia del 01-03 al 26-10-2026 (240 dias), como el del techo. El
Brier "noche siguiente" es el de la curva que cada protocolo tenia publicada
sobre los pares de esa noche, que ninguna curva ha visto (206 noches y 7.212
pares comunes; IC90 pareado por noches contra reajustar a ciegas con todo hasta
ayer).

| K | pares nublados en el examen min / mediana | ruido de reajuste \|dif\| p50 / p90 / p95 |
|---|---|---|
| 7 | 0 / 25 | 0,0001 / 0,0006 / 0,0011 |
| 14 | 0 / 50 | 0,0001 / 0,0006 / 0,0009 |
| **30** | **15 / 156** | **0,0001 / 0,0005 / 0,0009** |
| 60 | 65 / 293 | 0,0001 / 0,0008 / 0,0010 (max 0,051) |

| protocolo | Brier noche siguiente | contra a ciegas | cambios de curva en 240 dias |
|---|---|---|---|
| a ciegas (todo hasta ayer, cada dia) | 0,1137 | - | cada dia |
| K = 14, margen 0,001 | 0,1141 | +0,0004 [-0,0001, +0,0010] | 8 |
| K = 30, margen 0 | 0,1142 | +0,0005 [-0,0000, +0,0009] | 66 |
| **K = 30, margen 0,0005** | **0,1147** | **+0,0010 [+0,0004, +0,0017]** | **7** |
| K = 30, margen 0,001 | 0,1149 | +0,0012 [+0,0005, +0,0020] | 5 |
| K = 30, margen 0,005 | 0,1142 | +0,0005 [-0,0005, +0,0015] | 0 |
| K = 60, margen 0,0005 | 0,1229 | +0,0092 [+0,0040, +0,0161] | 16 |

Se elige K = 30 por el criterio del techo: es el menor con horas nubladas en
TODAS las ventanas (con 7 y 14 hay examenes sin una sola hora nublada, que solo
miden quien dice "despejado" mas alto). K = 60 se queda atras: la serie nativa
empieza el 21-11-2025 y reservar dos meses deja la aspirante de primavera sin
datos. Margen = p90 del ruido de reajuste con K = 30: con 0 la curva cambia 66
veces en 240 dias por ruido; con 0,0005, 7. Es 40 veces menor que el del techo
(0,02) porque esta curva se ajusta con ~50 veces mas datos: con 0,02 no habria
entrado nunca nada, tampoco el cambio de escala de M17 (-0,002).

**Lo que cuesta reservar, esta vez si medible**: +0,001 de Brier la noche
siguiente con K = 30 (IC90 que no cruza el cero), el 1 % del Brier. Es el precio
de que lo que entra sea lo examinado. Lo que el examen compra: una regresion de
codigo o de datos (los controles en rojo de abajo) no entra.

**Semanal y no diario** (misma simulacion con solo K = 14 y 30, 209 noches
comunes): con K = 30 y margen 0,0005, examinar cada 7 dias da 0,1179 contra
0,1178 examinando cada dia, y 7 cambios de curva en los dos casos. El trabajo
cuesta 19,3 s (16 s la verdad del fotometro, que crece ~1,5 s por mes de serie):
diario sumaria esos segundos a la pasada que ya lleva `nota` y `techo_curva`
sin cambiar nada medible.

### 4.3 Con el archivo de hoy y los controles en rojo

Copia del archivo del 26-10-2026, examen del 26-09 al 25-10 (30 noches):

| | decision | Brier vigente / aspirante | dif [IC90] |
|---|---|---|---|
| ECMWF: vigente del 30-08 (mezclada) contra la nativa ajustada hasta el 25-09 | **sustituye** | 0,1442 / 0,1418 | -0,0024 [-0,0049, +0,0002] |
| WeatherNext | `sin_examen`: su ultima noche con prevision y verdad es el 05-10 (M14) | - | - |

Controles en rojo, con la nativa ya dentro y sobre las mismas 30 noches:

| aspirante | decision | dif [IC90] |
|---|---|---|
| mezclada (retro + vivo sin corte, ajustada hasta el 25-09) | se_queda | +0,0020 [-0,0003, +0,0043] |
| pendiente al reves (mas nube, mas despejado) | se_queda | +0,1704 [+0,1037, +0,2379] |
| siempre 0,99 | se_queda | +0,0156 [-0,0159, +0,0476] |

La mezclada pierde, pero dentro del ruido de 30 noches: el cambio de escala
toca sobre todo las horas muy nubladas (26 % de los pares) y mueve la curva
4 puntos ahi. En los tests (`tests/test_curva_fotometro.py`) el mismo examen
rechaza una escala aplastada sintetica, una pendiente al reves y una curva
plana a 0,99; y los tests se han visto fallar quitando el corte de escala y
haciendo que el examen acepte siempre.

### 4.4 Que modelo gana a 1 dia (`modelo_d1`)

Deja-un-mes-fuera, cada modelo con SU curva (ajustada con sus pares de los demas
meses, todos los alcances, escala nativa) y puntuados en las MISMAS horas: las
que tienen los tres a 1 dia y verdad del fotometro. Con el archivo de hoy:
1.438 horas de 200 noches, 2026-02 a 2026-10 (WeatherNext empieza en enero, que
no tiene fotometro, y su octubre llega solo hasta el 05-10):

| modelo | Brier | AUC | dif contra ECMWF [IC90] |
|---|---|---|---|
| WeatherNext 3 | 0,1115 | 0,813 | -0,0035 [-0,0077, +0,0008] |
| ECMWF 0,25 | 0,1150 | 0,795 | - |
| ICON | 0,1159 | 0,772 | +0,0009 [-0,0030, +0,0046] |

Gana WeatherNext en el punto, dentro del ruido; lo mismo que el examen del
18-09 y el informe del 26-10. **No cambia el producto**: la medida se acumula
cada semana para decidir con un invierno delante. ICON: el corte del 21-11-2025
se le aplica para comparar el mismo periodo; si su serie tiene un salto de
escala propio: NO VERIFICADO.

---

## Coste (medido en el escritorio, copia del archivo del 26-10-2026)

| trabajo | cuando | tiempo |
|---|---|---|
| `nota` | 1 vez al dia | 9,0 s (8,4-9,0) |
| `techo_curva` | 1 vez al dia | 14,2 s (antes `train()` + `save()`: ~14 s; el examen anade < 1 s) |
| `salud` | cada pasada | 0,6 s (1,8 s la primera del dia) |
| `nube_curva` | 1 vez por semana | 19,3 s (19,2-19,4; 16 s son la verdad del fotometro desde el 21-11-2025, que crece ~1,5 s por mes) |
| `modelo_d1` | 1 vez por semana, misma pasada | 8 s mas (27,0 s los dos juntos: comparten la verdad) |
| `publicar` (captura) | cada commit de `noche.json` | una escritura de 2 filas |

La pasada que lleva `nota` y `techo_curva` (la primera tras las 10 UTC) sube
~10 s sobre lo que costaba con el `techo_curva` anterior: si una pasada normal
son 45-47 s, esa pasada pasa a facturar 2 minutos una vez al dia (~30 min al
mes). `salud` anade 0,6 s a cada pasada. NO medido en el runner (Linux, otra
CPU). Sin matplotlib: ninguno de los modulos nuevos lo importa.

`nube_curva` y `modelo_d1` anaden ~27 s a UNA pasada por semana (la de las
10 UTC del dia que vencen, la misma que lleva `nota` y `techo_curva`): unos
4 minutos facturados al mes. Diario serian ~30 al mes sin cambiar nada medible
(seccion 4.2).
