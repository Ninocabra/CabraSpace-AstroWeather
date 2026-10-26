# Aprendizaje continuo: el producto se puntua solo cada manana

Desde el 26-10-2026. Tres piezas encadenadas, que corren dentro del colector
(`astroweather/scheduler.py`, tabla `JOBS`) y escriben en el archivo:

| pieza | trabajo | cuando | escribe | se lee con |
|---|---|---|---|---|
| 1. Nota de cada noche | `nota` (`nightscore.py`) | una vez al dia, desde las 10 UTC | `night_scores/` (y `night_publications/` en cada commit de `noche.json`) | `nightscore.latest_scores(root)` |
| 2. Aspirante contra campeona | `techo_curva` (`curvecontest.py`) | una vez al dia, desde las 10 UTC, despues de `nota` | `roof_calibration.json` (solo si gana) y `curve_decisions/` | `curvecontest.decisions(root, "techo")` |
| 3. Salud | `salud` (`health.py`) | cada pasada, justo antes de `archivo` | `salud.json`, bloque `salud` de `estado_colector.json`, `marcador.json` del repo de datos | `health.read(root)` o `marcador.json` |

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

**Para la curva del fotometro** (`calibration.py`, verdad por hora):
`curvecontest.compare(p_vigente, p_aspirante, verdad, noche_de_cada_hora)` ya
sirve tal cual (remuestrea por noches), y `log_decision` escribe en el mismo
registro con `curve="fotometro"`. Falta solo el pegamento (que horas reservar y
como ajustar la aspirante); hoy esa curva no se reentrena en el colector.

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

## Coste (medido en el escritorio, copia del archivo del 26-10-2026)

| trabajo | cuando | tiempo |
|---|---|---|
| `nota` | 1 vez al dia | 9,0 s (8,4-9,0) |
| `techo_curva` | 1 vez al dia | 14,2 s (antes `train()` + `save()`: ~14 s; el examen anade < 1 s) |
| `salud` | cada pasada | 0,6 s (1,8 s la primera del dia) |
| `publicar` (captura) | cada commit de `noche.json` | una escritura de 2 filas |

La pasada que lleva `nota` y `techo_curva` (la primera tras las 10 UTC) sube
~10 s sobre lo que costaba con el `techo_curva` anterior: si una pasada normal
son 45-47 s, esa pasada pasa a facturar 2 minutos una vez al dia (~30 min al
mes). `salud` anade 0,6 s a cada pasada. NO medido en el runner (Linux, otra
CPU). Sin matplotlib: ninguno de los modulos nuevos lo importa.
