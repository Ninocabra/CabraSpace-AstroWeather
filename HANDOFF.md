# HANDOFF — CabraSpace AstroWeather

## CIERRE 27-10-2026 (PC_CABRA, Opus 5.5 solo; en paralelo, otra sesión hizo M7/M8/M16)

### 1. Estado git
- Motor `main`, todo subido. Cabeza `0b08b1e`.
  - Commits de esta sesión: `53393b0` M13 y `0b08b1e` M9.
  - Commits de la otra sesión: `53e05dd` M7, `ac4d151` M8, `d9fca9c` y `4cf3341` M16.
- Batería: **1299 passed** sobre lo subido.
- Archivo y datos: no se han tocado (solo `pull`).

### 2. En una pantalla
- **Noche 26/27-10: acierto.**
  - Real: despejada (los tres sensores) y techo abierto unas 11 h, 7,8 h de ellas en noche astronómica.
  - Publicado la tarde del 26: Abierto, P = 0,72, todavía con la curva vieja, que se cambió a las 22 UTC.
  - Publicado la víspera: Abierto, P = 0,97.
- **La web ya publica con M1 y M6:** para la noche del 27, P(abrir) = 0,874 y 6,0 h esperadas sobre 10,3 h de noche astronómica.
- **Salud a 13 noches:**
  - misma tarde: acierto 0,85 (BSS +0,39);
  - víspera: acierto 0,92 (BSS +0,66);
  - «siempre Abierto» acierta 0,69.
- **«Curva del techo sin decisión registrada» NO es un fallo.** El runner de las 22:00 del 26 corría `1d1b9a5`, anterior al aprendizaje continuo, y reentrenó la curva sin examen. El primer examen real es el 27 a las 22 UTC.
- **M13, resuelto.** La allsky corta a su hora del reloj local (04:54-06:13 locales en 45 noches). El 25-10 saltó una hora en UTC con el cambio de hora.
  - La ventana de verdad pasa a hora local, hasta las 05:00 (`nighttruth.allsky_hour_valid`).
  - El aviso de salud solo salta si el corte cae dentro de esa ventana.
- **M9, aplicado con la opción A, que eligió Nino.** Terciles propios del último año, recalculados cada semana (`seeingterciles.py`, trabajo `seeing_terciles`, fichero `seeing_terciles.json` en el archivo).
  - Hoy: 0,957″ / 1,009″, que reparten las 62 noches en 21/20/21. Antes salía «favorable» 58 de 58.
  - El tercil ordena las noches por el chorro; no mide segundos de arco.

### 3. Hipótesis refutadas (27-10)
- «La allsky se apaga por rocío o exposición»: no. Corta a una hora fija del reloj local y saltó exactamente una hora en UTC con el cambio de hora.
- «Los cortes 1,35/1,90″ son los terciles del modelo»: el seeing previsto va de 0,91 a 1,49″ (domina la constante A, que da 0,90″ sin jet). Esos cortes piden 43 y 63 m/s de chorro, y el p90 es 33.

### 4. Pendientes (misma numeración que el 26/27)
1. **Paso 4, el marcador en la web.** Lo valida Nino.
2. **Mirar en el runner real:**
   - el primer examen de `techo_curva` (27-10, 22 UTC), en `curve_decisions` y en `salud.curva_techo`;
   - el primer `seeing_terciles.json` (pasada desde las 10 UTC) y la etiqueta en la web;
   - que `salud.allsky_corte` diga «corte habitual … fuera de la ventana» en «ok»;
   - que los sensores salgan del «aviso» del 26-10, que solo tuvo lecturas desde las 20:30.
3. M7, M8 y M16: **HECHOS** por la otra sesión (27-10).
4. Horas esperadas cortas tras M6 (−1,9 h por noche): revisar con más noches.
5. M2, M3 y M4: necesitan invierno.
6. M9 y M13: **HECHOS** (27-10). Queda **M10**, el sesgo del cielo más oscuro, de 0,13 mag.
7. Retroanálisis en enero (M18).
8. Salto de escala de ICON: NO VERIFICADO.
9. Minutos de Actions en noviembre. Desde el 26-10 a las 20:30 hay una pasada cada 30 min, en verde.

### 5. Para el ojo de Nino
- Web: la etiqueta de seeing debe alternar entre favorable, normal y desfavorable después del recálculo de las 10 UTC. Con lo que hay hoy, el 31-10 saldría «desfavorable» (chorro de 30 m/s).
- Informe con M9 y M13 marcados: `C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\docs\verificacion_2026-10-26.md`

---

## CIERRE 26/27-10-2026 (PC_CABRA, Opus 5.5 coordinando; agentes opus-xhigh, opus-high, sonnet-high; 2 sesiones en la nube)

### 1. Estado git

- Motor (`Ninocabra/CabraSpace-AstroWeather`, `main`): todo subido, cabeza `4d9fd4d`. Commits del día, por orden:
  - `9666e02` M1+M6;
  - las PR #1 (M12) y #2 (M17), fusionadas;
  - `1d1b9a5` M17 medido;
  - `b3fb5e1` aprendizaje continuo 1+2+5;
  - `bc40b73` paso 3.
- Archivo (`AstroWeather-Archive`): subido.
  - `60cc4904`: relleno del fotómetro de octubre.
  - `1834bb99`: relleno de la nota de cada noche (59 noches).
  - Queda sin subir, a propósito, `raw/site=nerpio/source=tess_stars289/`: el crudo del fotómetro no viaja (`archivepush.py`) y se puede volver a pedir.
- Batería del motor: **1263 passed** sobre lo subido.

### 2. Lo que hay que saber en una pantalla

- **Retroanálisis previsto vs. real** (28-08 → 25-10): [docs/verificacion_2026-10-26.md](docs/verificacion_2026-10-26.md), con las mejoras M1-M18 en la sección 10.
  - El veredicto acertaba 36 de 42 noches (0,86), pero publicaba probabilidades bajas: la curva del techo tenía un tope de 0,72 con 0 % de nube.
- **Arreglado y en producción:**
  - **M1:** curva del techo horaria, solo con noche astronómica y sin fuga. Tope 0,72 → 0,89; Brier del veredicto 0,159 → 0,119, con IC que roza el 0.
  - **M6:** la ventana y las horas esperadas, dentro de la noche astronómica.
  - **M12:** el fotómetro rellena sus propios huecos.
  - **M17:** la nube del ECMWF 0,25° cambia de escala el 21-11-2025; medido.
- **El producto aprende solo** ([docs/aprendizaje_continuo.md](docs/aprendizaje_continuo.md)):
  - `nota` (diario): puntúa cada noche en `night_scores`.
  - `techo_curva` (diario): aspirante contra vigente, K = 14 noches, margen 0,02.
  - `nube_curva` (semanal): curva del fotómetro en escala nativa, K = 30, margen 0,0005.
  - `modelo_d1` (semanal): ranking a 1 día, solo registrado.
  - `salud` (cada pasada): bloque en `estado_colector.json` y `marcador.json`.
  - Cada decisión queda en `curve_decisions`.
- **M11, la caída del colector desde el 30-09: CAUSA ENCONTRADA por otra sesión el 26-10** (ver CabraBase/DIARIO). El job de cron-job.org tenía meses `4-9`, así que no hubo citas desde el 01-10. Corregido a las 20:30 del 26-10. Lo no raspado entre el 01-10 y el 26-10 (techo, CloudWatcher, estación) se perdió.

### 3. Mapa de lo hecho

| Bloque | Qué | Dónde |
|---|---|---|
| Retroanálisis | `python -m astroweather.hindcast` (~5 min; `--m1`, `--m6`, `--from-json`) | `astroweather/hindcast.py`, `asof.py`, `docs/verificacion_2026-10-26/` |
| M1 | 2 parámetros por horas, Sol < −18°, nube disponible a las 15 UTC | `roofcalibration.py` |
| M6 | `dark_slot_weights`; las casillas de crepúsculo no cuentan | `ephem.py`, `tonight.py`, `gates.py`, `webexport.py`, `nightview.py`, `dome.py` |
| M12 | `top_up` pide los huecos de los últimos 4 días | `workers/fetch_tess.py` |
| M17 | Informe y herramienta | `docs/m17_nube_total_ecmwf025.md`, `tools/m17_nube_total.py` |
| Aprendizaje 1/2/5 | nota, aspirante contra vigente, salud | `nightscore.py`, `curvecontest.py`, `health.py`, `nighttruth.py`, `skill.py` |
| Paso 3 | `NATIVE_TCC_SINCE`, `nube_curva`, `modelo_d1` | `calibration.py`, `curvecontest.py`, `scheduler.py` |

### 4. Hipótesis refutadas, con su medida (no repetir)

- «El veredicto calibrado bate claramente a las reglas tontas»: acierta 0,86 frente a 0,83 de «siempre Abierto» y 0,88 de «como anoche». Su valor está en ordenar las noches (AUC 0,89), no en el acierto.
- «El fotómetro solo basta como verdad de nubes»: κ = 0,22 contra el CloudWatcher; de 41 horas nubladas para el CloudWatcher, 32 el fotómetro las llamó despejadas. El ECMWF da AUC 0,65 contra el fotómetro y 0,82 contra el voto de los tres sensores.
- «M6 mejora el veredicto»: no cambia ninguna de las 42 noches (Brier +0,003, dentro del ruido). Arregla la ventana y las horas imposibles, no los aciertos.
- «Leer la previsión por `run_utc` da lo que se sabía al publicar»: falso, coincide en el 56 % (42 % en noche astronómica). Hay que reconstruir por `retrieved_at` (`asof.py`).
- «Con la escala nativa la curva del fotómetro mejora mucho»: −0,0022 de Brier fuera de muestra (sep-oct). La ganancia es pequeña y está en el extremo nublado.
- «WeatherNext gana claramente a 1 día»: −0,0035 [−0,0077, +0,0008], dentro del ruido.
- «Con K = 7 noches basta para el examen del techo»: 7 de 37 ventanas no tienen ninguna noche cerrada. Con K = 21 se queda atrás (+0,036).

### 5. Pendientes numerados (por valor)

1. **Paso 4: marcador visible en la web** (repo `CabraSpace-Web`, `Pagina Web/astroweather.js`). Lee `marcador.json` del repo de datos; el formato está en `docs/aprendizaje_continuo.md`. Público: Nino lo valida antes de publicar. Sonnet · high.
2. **Comprobar en el runner real** las primeras pasadas con `nota`, `techo_curva` (examen), `salud` y el push de `marcador.json`. Mirar el log de Actions y `estado_colector.json` del archivo. Cuidado: la primera `techo_curva` en el runner debería decidir «sustituye» (M1 gana por −0,054).
3. **M7** (la fila archivada dice qué curva la produjo), **M8** (`p_despejado` publica P(techo abierto)) y **M16** (elegir por `retrieved_at` en `scoreboard.py` y `queries.py`). Ya no chocan con nada.
4. **Horas esperadas cortas** tras M6: −1,9 h por noche contra horas oscuras de techo (24 noches). La curva es prudente (tope 0,89). Revisar con más noches.
5. **M2/M3/M4**: ICON o una mezcla de modelos, verdad por voto de sensores, capas de nube. Necesitan un invierno de datos.
6. **M9** (terciles de seeing: «favorable» 58 de 58 noches), **M10** (cielo más oscuro, sesgo de 0,13 mag) y **M13** (allsky sin estrellas desde las 03 UTC).
7. **Repetir el retroanálisis en enero** (M18), con noches cerradas de invierno, antes de tocar el umbral del 50 %.
8. **ICON y su posible salto de escala**: NO VERIFICADO.
9. Coste de Actions: unos 30 min más al mes por la pasada diaria de 2 minutos. Vigilar noviembre junto con el reloj nuevo de cron-job.org.

### 6. Para revisión visual de Nino

- `C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\docs\verificacion_2026-10-26\calendario.png`: noche a noche, previsto frente a real.
- `C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\docs\verificacion_2026-10-26\m1_curva_techo.png`: curva antes y después.
- `C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\docs\verificacion_2026-10-26\fiabilidad.png`, `proveedores.png`, `sesgos.png`.
- La web real, cuando el runner publique con M1+M6: la P(abrir) de esta noche debería rondar el 0,89, y las horas esperadas no pueden superar las de noche astronómica.

### 7. Qué puede avanzarse sin él

- Pendientes 2 y 3, y la parte de datos del 6 (M9 se puede re-derivar contra la propia distribución archivada).
