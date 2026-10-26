# CHANGELOG — CabraSpace AstroWeather

La especificación vigente es **`astroweather_cabraspace_v8_SPEC.md`** (solo estado en vigor). Este fichero conserva la historia: qué cambió en cada versión, qué auditorías hubo y qué se verificó. Los documentos v4–v7 se conservan como archivo histórico de consulta (razonamientos completos, fórmulas con contexto, snippets extensos).

| Versión | Fichero | Estado |
|---|---|---|
| v8 (27-08-2026) | `astroweather_cabraspace_v8_SPEC.md` | **VIGENTE** |
| v7 (27-08-2026) | `astroweather_cabraspace_v7_MAESTRO.md` | histórico |
| v6 (27-08-2026) | `astroweather_cabraspace_v6.md` | histórico |
| v5 (26/27-08-2026) | `astroweather_cabraspace_v5.md` | histórico |
| v4 (original) | `astroweather_cabraspace (3).md` | histórico |

---

## 26-10-2026 — M1: la curva del techo, por horas y sin fuga

Primera mejora del retroanalisis de hoy (`docs/verificacion_2026-10-26.md`,
seccion 10). La curva P(techo abierto | nube prevista) tenia un TECHO: el
cruce salia de la climatologia mensual (30 %, base 0,56) y solo la pendiente
de las horas, asi que con 0 % de nube no pasaba de sigmoid(0,955) = 0,72,
cuando hora a hora, al decir 0,64-0,73, el techo estaba abierto el 96-98 %.

**Que cambia** (`roofcalibration.py`):

- Con `NIGHTS_FOR_HOURLY_REFIT` noches (20, antes 60) los DOS parametros salen
  de las horas; los meses quedan como comprobacion. Por debajo, o si el ajuste
  libre sale con pendiente >= 0, manda el metodo anterior; sin horas
  suficientes `fit()` se niega como antes (arranque en frio intacto).
- Horas de NOCHE ASTRONOMICA (Sol < -18 grados, `ephem`), no de 18 a 05 UTC:
  la ventana fija metia horas con el Sol sobre el horizonte a finales de agosto.
- La nube es la que habia al corte de las 15 UTC, elegida por `retrieved_at`
  en el Parquet crudo (`asof.py`, la reconstruccion del retroanalisis movida a
  un modulo comun). La vista deduplicada daba la recogida de las 21-23 UTC de
  esa misma noche: en estas 266 horas coincide con la del corte solo en el 42 %.
- `method` dice ahora "horario: 2 parametros, horas de noche astronomica, nube
  disponible a las 15 UTC"; el JSON conserva sus campos. El aviso de la web ya
  no manda comprobar el cruce contra el 30 % mensual (era el techo).

**Medido** (`python -m astroweather.hindcast --m1`, cada noche con una curva
ajustada por el codigo de produccion SOLO con las noches anteriores; IC95 por
remuestreo de noches, 2000 remuestreos):

- Control del instrumento: la P(abrir) publicada, recalculada con la curva
  vigente al publicar, sale identica en 42 de 42 noches (error max 0,0005); el
  metodo anterior reajustado reproduce la curva publicada (dBrier 0,000).
- Veredicto, 42 noches del 05-09 al 25-10: Brier 0,159 -> 0,119 (dif -0,040
  [-0,082, +0,001]); con el arranque en 8 noches, como el retroanalisis, 0,092
  (dif -0,068 [-0,111, -0,023]): reproduce el -0,067 del informe. Acierto 0,86
  -> 0,83 (0,86 con 8 noches); AUC 0,90 -> 0,81 (dif -0,09 [-0,22, +0,01]).
- Horas esperadas contra reales (41 noches): sesgo -3,5 h -> -1,7 h [-2,9,
  -0,5]; MAE 4,3 -> 3,5 h.
- Hora a hora (227 h de noche astronomica): Brier 0,202 -> 0,191 (dif -0,011
  [-0,068, +0,050], NO significativo) y AUC 0,83 -> 0,73: lo pierde el periodo
  con curvas que cambian de noche a noche, no la forma (cualquier curva fija da
  el AUC de la nube cruda, 0,85).
- **Noches cerradas: empeora 2 de 7.** 27-09 (0,09 -> 0,58) y 19-10 (0,48 ->
  0,76) pasan a falsa alarma; 28-09 ya lo era (0,68 -> 0,89); 17 y 18-10 suben a
  0,25 y siguen en Cerrado; 16-09 y 20-10 quedan en 0 por las puertas.
- Meses: MAE mensual 0,124 -> 0,145 en los 31 meses (el cruce viejo estaba
  ajustado a ellos); en enero-julio de 2026, que ninguna ha visto por horas,
  0,102 contra 0,104: empate. El BSS mensual de la nota de `tonight` baja de
  +54 % a +19 %.
- Curva de hoy (55 noches, 266 h): intercepto 0,955 -> 2,104, pendiente -3,18
  -> -3,13 [-4,8; -2,0]; cruce 30 % -> 67 %. P(0/30/60/100 %) = 0,72 / 0,50 /
  0,28 / 0,10 -> 0,89 / 0,76 / 0,56 / 0,26.

El umbral de 20 noches es un juicio, no una medida: en este otono seco el de 8
gana al de 20 (dif -0,027 [-0,052, -0,011]), pero con 8-16 noches las curvas
salian planas (P(100 %) de 0,52-0,58, "Abierto" con cubierto previsto). Hoy hay
55 noches y el resultado es el mismo con cualquiera de los dos. Llega a
produccion cuando se empuje: el runner reentrena la curva cada dia.

Ademas: `tests/conftest.py` pone astropy sin descarga del IERS (los servidores
daban 400 hoy y 11 tests de `test_webexport.py` caian en rojo con HEAD sin
tocar); `matplotlib` queda excluido a proposito del colector (solo lo importa
`hindcast.py`, que se corre a mano; `test_requisitos_del_colector` lo pedia);
y `monthly_pairs` lee la tabla de horas abiertas de la misma raiz que la
prevision.

---

## 26-10-2026 (2) — M6: la ventana y las horas esperadas, dentro de la noche astronomica

Segunda mejora del retroanalisis de hoy (seccion 10 de
`docs/verificacion_2026-10-26.md`). La rejilla de presentacion va de ocaso-1 h
a orto+1 h (`ephem.WINDOW_MARGIN`), pero la curva del techo de M1 se ajusta solo
con horas de Sol < -18 grados; aplicarla al crepusculo es extrapolar. Con M1, la
noche del 26-10 daba 11,9 horas esperadas con 10,3 h de noche astronomica
(imposible), 13 + 26 ventanas publicadas acababan despues del orto y la falsa
alarma del 28-09 se apoyo en una ventana de 06-08 UTC.

**Que cambia**:

- `ephem.dark_slot_weights`: una casilla cuenta si su punto medio tiene el Sol
  por debajo de `ASTRONOMICAL` (= `roofcalibration.DARK_SUN_ALTITUDE`, la
  constante de M1; un test las ata), y la que cruza -18 grados cuenta en
  PROPORCION (su fraccion oscura), no entera. Decidido asi porque el criterio
  del punto medio a secas se pasaba: 11 casillas en 10,3 h. Con el peso, la suma
  de `peso * p` no puede pasar de las horas de noche astronomica.
- `NightReport` (`tonight.py`) guarda el ocaso y el orto astronomicos y
  `usable_hours`, `verdict_window`, `opening_probability` y `best_window` solo
  miran esas casillas; las ventanas se recortan a [ocaso, orto] astronomicos.
  `gates.opening_window` y `opening_probability` aceptan `weights` (opcional:
  sin el, todo igual que antes). Un informe sin ocaso/orto astronomicos
  conserva el comportamiento anterior.
- Rendimiento por objeto (`q_expected`): `webexport`, `nightview` y `dome` ponen
  probabilidad 0 a las muestras con el Sol por encima de -18 grados.
- JSON publicado: la REJILLA (`horas`) no cambia. `horas_utilizables_esperadas`
  y `horas_utilizables_oscuras` pasan a ser la misma cuenta (solo noche
  astronomica); la de antes, crepusculo incluido, va en
  `horas_utilizables_rejilla` (campo nuevo, aditivo). `expected_hours` de la
  tarjeta del techo de la cupula, igual que `horas_utilizables_esperadas`.

**Medido** (`python -m astroweather.hindcast --m6`, 42 noches del 05-09 al 25-10,
curva de M1 ajustada prequencialmente, la rejilla publicada a las 16 UTC, IC95
por remuestreo de noches, 2000 remuestreos; `NightReport` real, sin
reimplementar):

- Control positivo: con M6 quitado, las 42 noches reproducen
  `m1_resultados.json` con diferencia 0 (P(abrir) de M1 y de la curva publicada,
  Brier, acierto, sesgo y MAE de horas).
- Invariante: horas esperadas > noche astronomica en 17 de 42 noches sin M6
  (hasta x1,28), en 0 con M6 (maximo x0,89). Ventanas fuera de [ocaso, orto]
  astronomicos: 13 -> 0; acabando tras el orto: 2 -> 0. En las 207
  publicaciones (esta noche y la siguiente): tras el orto 13 + 26 -> 0, fuera de la noche (+-40 min)
  36 + 52 -> 0; el veredicto pasaria a Cerrado en 5 + 4 de ellas (nunca a
  Abierto).
- Veredicto: NINGUNO de los 42 cambia. Brier 0,119 -> 0,122 (+0,003 [-0,003,
  +0,011]); acierto 0,83 igual (31 aciertos, 3 falsas alarmas, 4 fallos, 4
  cierres). El 28-09 sigue siendo falsa alarma (0,89 -> 0,85; la ventana pasa
  de 06-08 UTC a 03:00-04:35 UTC), 27-09 y 19-10 no se mueven (0,58 y 0,76: su
  ventana ya estaba en la oscuridad). M6 corrige la ventana, no esas noches.
- Horas esperadas contra horas de techo de la tabla (41 noches, incluyen la
  apertura en crepusculo): sesgo -1,7 [-2,9, -0,5] -> -4,0 [-5,2, -2,7] h; MAE
  3,5 -> 5,0 h. Contra horas abiertas con el Sol < -18 (24 noches con
  lecturas): +0,6 [-0,7, +1,9] -> -1,9 [-3,1, -0,7] h; MAE 2,6 -> 3,3 h. O sea
  que las horas de crepusculo tapaban una curva conservadora (tope 0,89); no es
  un defecto de M6, pero la cifra nueva sale corta y esta dicho.
- `tonight` (curva de M1 ajustada en memoria con el archivo): noche del 26-10,
  11,9 -> 8,2 h esperadas con 10,3 h de noche; P(abrir) 0,89 igual, ventana
  00:00-02:00 UTC igual.

---

## 08-09-2026 (tarde) — H_a no se puede calibrar, y no es falta de datos

Intento de cerrar el pendiente historico: medir `AEROSOL_SCALE_HEIGHT_M`, el
1700 que lleva desde el principio con el comentario «calibrable contra AERONET».
Modulo nuevo `aerosolfit.py` con el estimador, su guardian y 12 tests.

**Correccion de lo escrito esta misma manana:** el PM2.5 de AstroCamp NO sirve
para esto. H_a es una altura de escala de extincion optica y el PM2.5 es masa
por metro cubico; y una sola altura no separa el perfil del sesgo del aparato.
El dato lo canta: el sensor da 6,99 ug/m3 donde CAMS da 5,10 a 583 m mas abajo
— razon 1,37, que bajo el modelo exponencial daria una altura NEGATIVA.

**Lo medido, contra el repo de datos** (el `data/` local estaba atrasado y por
poco concluyo con un cero falso):

- **Sin especies, H_a es indeterminable POR CONSTRUCCION.** `site_aod =
  aod_total * exp(-583/H_a)`: la pendiente absorbe el factor. Barriendo H_a de
  500 m a 20 km sobre 3.200 horas limpias, el R2 sale **identico a doce
  decimales**. Esa es la rama que corre hoy en `extinction.evaluate`.
- **Con especies tampoco, con el fotometro como observable.** Colinealidad
  `aod_dust`~`aod_total` = 0,957. Senal/ruido = 0,138 por hora (0,033 mag de
  senal contra 0,241 de dispersion). Simulado sobre la distribucion real de
  AOD, +/-16 % pide ~100.000 horas; con 1.000 el intervalo sigue en [562, 3746].
- **Ritmo real: 7 horas utiles en 16 dias** (129 h de CAMS ADS -> 40 con
  fotometro en noche astronomica -> 7 sin Luna). 1.000 horas son seis anios.

**No es «todavia no», es «asi no».** El prerrequisito es un observable con
coeficiente CONOCIDO: `k_extinction` medida, donde B = 1,086 por definicion y f
se despeja de una noche con estrellas a varias masas de aire. Esa columna esta
**VACIA (0 de 3.765.260 filas)**, el hueco que SPEC §1.1 ya declaraba. Queda
como el cuello de botella declarado.

`aerosolfit.fit_scale_height` se NIEGA con motivo en las tres situaciones
reales (sin especies, colinealidad alta, pocas horas) en vez de devolver un
numero. El observable entra por parametro: el dia que haya k medida, el mismo
estimador la acepta sin tocar una linea.

Ademas, un fallo que era del TEST y no del codigo: `assert bajo < verdad < alto`
sobre UN intervalo del 68 % falla una de cada tres veces por definicion.
Sustituido por una prueba de cobertura sobre 60 realizaciones.

---

## 08-09-2026 — El PM2.5 de AstroCamp, leido de un PNG

La pregunta era si el numero de particulas en suspension de la pagina de
AstroCamp tiene que ver con la calima. **Medido sobre nuestro archivo** (22.752
horas, feb-2024 -> sep-2026): correlacionados pero no lo mismo. PM2.5 contra
AOD total r = +0,67; contra el polvo en superficie r = +0,84. En las colas se
despegan: con umbral p95, el **53 % de las horas de AOD alto tienen PM2.5
normal** y el **51 % de las de PM2.5 alto tienen AOD normal**. La calima viaja
en capa elevada (1,5-5 km) y a 1650 m el observatorio esta dentro o debajo, que
es justo por lo que `site_aod()` no escala el termino de polvo. Sirve de
**alarma de episodio**, no de medidor de transparencia: en el 1 % de horas de
mas polvo, el PM2.5 se pone en su propio percentil 99.

**Worker nuevo `fetch_astrocamp_dust.py`.** Se sondeo el servidor entero: no
hay CSV, JSON ni API -- ese sensor solo existe como PNG. Asi que se digitaliza.
Media horaria a `measurements.pm2_5` (columna nueva), cada 8 h, con el PNG
archivado entero para poder recalcular cualquier otra agregacion.

**El control positivo -- curvas de valor conocido inyectadas en el PNG real --
destapo tres fallos que ya estaban escritos y ninguno daba error, solo numeros:**

1. La calibracion iba contra las lineas de rejilla; matplotlib no siempre las
   pone cada 10 y el digitalizador devolvia la mitad. Ahora va contra las
   MARCAS del eje.
2. Con la curva alta el relleno tapa la rejilla de debajo (a 45 ug/m3
   desaparecen 4 de 9) y el guardian saltaba en falso.
3. **La leyenda es opaca**: por encima de ~90 ug/m3 se publicaba su borde
   inferior como si fuera el sensor. Un 7 % de menos, en silencio, y solo en el
   episodio mas fuerte que puede haber. Esas columnas se declaran desconocidas
   y la hora sale marcada.

Precision final: **error maximo 0,23 ug/m3 de 4 a 86**. Contra CAMS en las
ultimas 23 h: r = +0,76, sesgo +1,7 ug/m3. Y **guardian**, visto en rojo: si
AstroCamp reescala el eje o cambia el tamano del grafico, el worker revienta en
vez de publicar numeros mal escalados con el trabajo en verde.

De regalo, el ejemplo en vivo de la pregunta: de 09-07 12h a 09-08 10h el AOD
se multiplico por 4,6 (0,050 -> 0,230) mientras el sensor de superficie subia un
30 % (6,5 -> 8,5 ug/m3).

---

## 07-09-2026 — WeatherNext 3 entra como candidato, no como sustituto

Google publicó el 03-09 WeatherNext 3: inicialización horaria con imagen
geoestacionaria en vivo, nubes por capas a 0,1°, 64 miembros servidos como
media y cinco cuantiles, horizonte 48 h en las corridas horarias. Lo que NO
publica es una sola cifra de nubosidad: las mejoras anunciadas son de
precipitación. Por eso entra como **un modelo más del marcador** (`scoreboard`,
Brier y AUC contra el fotómetro), y no toca nada hasta que gane en AUC al
ifs025 con la misma verdad.

**Worker `fetch_weathernext.py`** (escrito por Gemini vía `coop/`, integrado
con tres correcciones): media y cuantiles como seis modelos con `member=None`,
para que el esquema no cambie; unidades a las canónicas (fracción→%, K→°C,
m→mm, Pa→hPa); `lead_h` comprobado contra `valid-run` como invariante;
consulta filtrada por partición `init_time` antes de nada. Las correcciones:
dirección del viento y humedad derivadas **solo de la media** (el p10 de u con
el p10 de v no es un vector; un cuantil de una función no es la función de los
cuantiles); `model_grid_elevation=None` porque la cota de la celda de Open-Meteo
no es la de WeatherNext, y la provenance no puede prestar números; y el BOM
que PowerShell deja en los ficheros. 10 tests en frío con fixture; sin acceso
todavía, así que nada de esto ha visto un dato real: los nombres de columna y
la forma de `geography` están marcados SIN COMPROBAR hasta el primer
`SELECT * LIMIT 1`.

**Licencia, corregida:** el pronóstico del futuro cae bajo los términos
experimentales de GDM, no bajo CC BY (eso es solo el dato de hace más de una
hora). Uso interno sin límite; publicable solo como servicio de valor añadido
**no recuperable** con la cita obligatoria. Publicar la nubosidad por capas tal
cual en la web no está permitido. Condiciona el lienzo, no el motor.

Acceso pedido el 07-09-2026 (formulario de Google DeepMind, 5-7 días hábiles).

## 05-09-2026 — El veredicto se calibra contra el techo real, y nombra su ventana

**El síntoma (04-09, 21 UTC):** la web decía «Abierto 92 %» con 100 % de nubes
previstas de 21 a 23 UTC. No era un fallo de datos: el JSON decía exactamente
eso. Eran dos mecanismos encadenados, y ninguno podía salir en rojo.

1. **La curva del fotómetro no bajaba del 58 % ni con el cielo cubierto.** Su
   verdad terreno es el residuo contra el modelo de cielo, que mide la Luna
   además de las nubes. Contra el techo real de Nerpio —horas abiertas por mes
   de AstroCamp, 31 meses de 2024-02 a 2026-08— sale **26 puntos optimista**.
2. **La noche valía lo que su mejor hora** en cuanto sumaba 4 h esperadas, y con
   una curva que nunca baja de 0,49 eso era siempre. **74 veredictos publicados,
   cero «Cerrado».**

**Lo medido antes de tocar nada.** La fracción abierta mensual sigue a la
fracción de horas nocturnas con nube prevista < 30 %: r = 0,89, sesgo 0,00
(control con meses barajados: r = 0,00). A escala de minutos, los 18 techos
abren y cierran como uno solo (0/18 o 18/18), nunca están abiertos con la
bandera del CloudWatcher en nublado (1 muestra de 180), y abren 30-60 min
después de que pase a verde. En 9 noches no hay un solo cierre por tiempo a
media noche: todos los cierres son 30-45 min después del orto. Esa misma noche
del 04-09 el techo abrió a las 22:01 UTC, 36 min después de que la bandera
pasara a verde y con 100 % previsto: en Nerpio el 100 % del ECMWF acierta el
42 % de las horas.

**`roofcalibration.py` (nuevo).** `P(techo abierto | nube prevista)` como
logística con cada parámetro de la escala que lo determina: el **cruce** (30 %)
de los meses —31 medias mensuales no distinguen la pendiente: el ajuste libre
se va a un escalón— y la **pendiente** (−3,3, intervalo 10-90 % por bootstrap de
noches [−7,5; −0,3]) de las 53 horas de techo con previsión. MAE mensual 0,123
contra 0,181 de la climatología constante; 0,119 en los 8 meses de 2026 sin
verlos. `load_calibrator()` prefiere esta curva y cae a la del fotómetro si no
existe; la nota del informe y `confianza.verdad_terreno` dicen cuál manda.

**`gates.opening_window` (nuevo) y `opening_probability` (cambiado).** La noche
vale la hora más floja de la mejor ventana de dos horas seguidas —mínimo, no
producto: las horas vecinas comparten el error de previsión— y la ventana viaja
en `veredicto_abrir.ventana`. La web la pinta bajo la palabra. Con la nueva
regla y la nueva curva, la noche del 04-09 sale **Abierto 62 %, ventana 02-04
UTC** (cierto: abrieron), y la del 05-09 **Cerrado 35 %**: el primer Cerrado
del producto.

**LA NOTA DE LAS 60 NOCHES, dentro del producto.** `NIGHTS_FOR_HOURLY_REFIT =
60`. `fit()` cuenta noches y cambia solo a ajuste horario de los dos
parámetros; el JSON publica `noches` y `noches_objetivo` y un aviso con cuántas
faltan (hoy 54); el aviso cambia a «comprobar que el cruce horario siga cerca
del 30 % mensual» el día que se cumpla. `tests/test_roofcalibration.py` cubre
el cambio de método, y `test_control_positivo_la_curva_del_fotometro_sale_optimista`
es el control positivo: la curva vieja sobre meses de techo tiene que salir
optimista, o el test no mide. El trabajo `techo_curva` del colector reajusta
cada día tras las 08 UTC y `roof_calibration.json` viaja como fichero suelto.

**Lo que NO cambia y hay que reconciliar:** la cúpula (`nightview`) sigue
estimando su «probabilidad de techo» con la curva del fotómetro por el factor
humano de `roof.py`; ahora puede discrepar del JSON. Y dos avisos de
instrumentos: la allsky cae a 0 estrellas todas las noches desde las 03 UTC
(no es nube) y la temperatura de cielo «despejado» del CloudWatcher bajó de −14
a −8 °C con la calima: la bandera no es un umbral fijo.

---

## 30-08-2026 — El suelo pasa a ser una serie, y la verdad de nubes deja de medir la Luna

Tres deudas que venían con fecha puesta, cerradas juntas porque son la misma
cadena: el suelo → el residuo del modelo → la etiqueta de despejado → el
calibrador → la probabilidad que se publica.

### El suelo era un número donde hacía falta una curva

El 29-08 se jubiló `NATURAL_FLOOR_MAG = 21.71` por promediar una rampa de siete
años. Lo que quedó fue **un** número: el de hoy, medido sobre 400 días… aplicado
también a 2019. Sobre 15.514 horas estables, el residuo del modelo salía así:

| año | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|
| centro | −0,374 | −0,374 | −0,405 | −0,312 | −0,244 | −0,182 | −0,071 | +0,064 |

Una rampa de **0,437 mag** que no es del modelo: es el cielo cambiando debajo, y
se comía el 64 % de la tolerancia de la verdad terreno sin que se notara.

`fit_floor_series` mide la deriva con **la misma definición** que `fit_epoch_floor`
—el suelo que anula el sesgo en horas sin Luna— aplicada a una ventana que se
desliza: 22 puntos de 2019-09 (21,585) a 2026-08 (22,056). `epoch_floor(when)`
interpola y **acota en los extremos**: fuera del archivo no se extrapola una
tendencia, que es exactamente como se inventó la constante que acabamos de
jubilar. Sin argumento sigue devolviendo el de hoy, así que el producto no
cambia de camino.

Resultado: rango entre años **0,437 → 0,077 mag**, centro global **−0,239 → +0,000**,
sigma 0,309 → 0,243. Constantes re-derivadas sobre 11.790 horas de ajuste:
`CLEAR_LEVEL_CENTRE` −0,0164 → **+0,001**, `CLEAR_LEVEL_TOLERANCE` 0,687 → **0,511**
(tres sigmas robustas; la sigma bajó de 0,2290 a 0,1704 porque la deriva la
inflaba una cuarta parte).

### La verdad de nubes medía la Luna, y era el umbral de dispersión

`CLEAR_SCATTER_THRESHOLD = 0.10` se aplicaba igual con Luna que sin ella, y el
residuo no tiene el mismo ruido en los dos casos: la ley lunar cae como
10^(−θ/48,55), así que un error pequeño en ella se vuelve una rampa **dentro** de
la hora. Medido sobre horas con 0-10 % de nubes previstas —fuente independiente
de nuestra etiqueta— la dispersión mediana del residuo es 0,0180 sin Luna y
0,0339 con ella: **factor 1,89**.

Con umbral fijo eso rechazaba una de cada cuatro horas limpias con Luna. El
desglose por régimen lo dejaba claro: el rechazo por **dispersión** pasaba de
10,4 % a 46,8 % mientras el de nivel apenas se movía de 2,4 % a 13,1 %.

| | sensibilidad sin/con Luna | falso claro sin/con Luna | r(etiqueta, altura lunar) |
|---|---|---|---|
| umbral fijo | 96,4 % / 75,6 % | 53,1 % / 21,0 % | −0,393 |
| umbral ×1,89 | 96,4 % / 85,7 % | 53,1 % / ~50 % | **−0,275** |

**El factor sale de la medida, no de cuadrar el resultado**, y la distinción
importa porque el resultado se cuadra solo: ensanchando el umbral la tasa de
despejado sube siempre. Lo que dice que 1,89 es honesto es que a ese valor los
dos regímenes pasan a **equivocarse igual** — con umbral fijo la Luna no daba
más especificidad, daba rechazo indiscriminado. (Ese 53 % de falso claro sin
Luna no es nuestro: es que una previsión de 90-100 % de nubes acierta poco aquí,
así que sirve para comparar regímenes y no como acierto absoluto.)

**Lo que NO arregla:** con Luna la sensibilidad se queda en 85,7 % contra 96,4 %.
Quedan diez puntos que ya no son umbral, es que el modelo lunar tiene más error
que el modelo sin Luna. Se declara en vez de taparse ensanchando más.

### El calibrador, refitado

Brier **0,169 → 0,136**; climatología 0,192 → 0,149. El **BSS baja de +11,7 % a
+9,1 %**, y hay que decir por qué: la tasa base de despejado sube del 74,1 % al
81,8 % —ya no se rechazan las horas limpias con Luna— así que la climatología es
un rival más difícil. La previsión acierta más en absoluto y saca menos ventaja
relativa. Efecto en el producto: P(abrir) de esta noche 0,879 → 0,920.

### La puerta de viento: la corrección se medía y no se aplicaba

`wind_correction()` mide desde el 29-08 que el modelo prevé el **55 %** del
viento medio real (terreno de montaña contra una celda de 25 km) y eso viajaba
al panel **como aviso** mientras `tonight.report` seguía pasándole a la puerta la
racha **cruda**. Un aviso no cierra un techo: ahora el factor se aplica, y
mientras no haya pares suficientes vale 1,0 y el aviso sigue en pie (hoy: 48
pares de los 200 que exige `MIN_WIND_PAIRS`).

Aplicar al racheado el factor del viento medio es un supuesto y se declara: no
hay racha medida en ninguna fuente —la página del sitio publica media— pero las
dos salen de la misma celda y el mismo esquema, y el supuesto falla del lado
seguro (sube la racha, cierra antes).

Y se crea la verdad que faltaba: `gust_scores()` compara la racha prevista con
el **pico** de los promedios medidos en la hora. No es una racha —es una cota
inferior, y el nombre y el docstring lo dicen— pero ya da un número accionable:
en el **14,6 %** de las horas la racha prevista queda **por debajo de un promedio
ya observado**, y eso no es incertidumbre, es estar mal.

---

## 30-08-2026 — La allsky escribía magnitudes instrumentales en la columna del fotómetro

Salió preguntándose por qué el panel no enseñaba «Cielo medido». La respuesta
buena está más abajo (el scheduler duerme con el escritorio), pero por el camino
apareció esto: **3.350 filas con `sky_mag_raw` entre −16,80 y −15,07**, todas
desde el 29-08 y todas del instrumento `indi-allsky-cam1`. El rango físicamente
posible es [14, 23] y `is_plausible_sky_mag` lo aplica… en el camino del TESS.

No es un fallo de la allsky: `fetch_allsky` guarda `-2.5·log10(ADU)` **a
propósito**, y su comentario lo dice — es una magnitud **instrumental**, monótona
en el ADU y con **punto cero arbitrario**, porque convertirla sin cruzarla antes
con el TESS sería inventarse el punto cero. El fallo es que la guarda en **la
misma columna** que el fotómetro calibrado, y de las cuatro consultas que leen
`median(sky_mag_raw)` solo **una** filtraba por instrumento:

| Consumidor | Filtraba por instrumento |
|---|---|
| `skybackground.medir_suelo` | sí |
| `verification` / `webexport._sensores` | sí |
| `calibration.observed_clear_hours` | **no** |
| `moonvalidation.collect` | **no** |
| `queries` (vista `nights`) | **no** |

**Ya estaba entrando.** Desde el 29-08 hay 103 cubos de 10 minutos con lectura de
cielo y **85 de ellos son SOLO de la allsky** (los dos instrumentos no se solapan
en el tiempo, así que la mediana del cubo es la suya entera). Tres de esos cubos
pasan ya los filtros de noche cerrada y Luna baja de la verdad terreno de nubes,
con **residuos de −34 mag** contra el modelo. Hoy no llegan al mínimo de cuatro
muestras por hora, así que aún no han torcido el calibrador; con una noche
completa sin fotómetro lo harían.

**Corregido:** las tres consultas filtran `instrument_type = 'TESS'`, que es lo
que las otras hacían ya. Test de regresión con las dos fuentes en el mismo cubo:
sin el filtro, una hora de fotómetro limpio sale etiquetada como nublada.

---

## 30-08-2026 — «Cielo más oscuro» publicaba el instante más brillante de la noche

**Cómo salió:** la pregunta fue por qué el panel daba **17,14** para esta noche
—sin nubes— cuando la noche anterior el fotómetro había medido **20** con la Luna
*más* llena. No era una contradicción entre el modelo y la medida: era un signo.

En mag/arcsec² **más grande es más oscuro** (21,9 es cielo negro, 17 es cielo con
la Luna alta). `webexport._night` calculaba
`darkest = min(s.predicted_mag for s in skies if s.is_night)`, o sea el instante
**más brillante**, y lo publicaba en un campo llamado `mag_cenit_mas_oscuro` y
rotulado «Cielo más oscuro». Estaba así desde que nació el fichero (`54ba594`).
`nightview.py` calcula lo mismo con `max()` y `higher_is_better=True` desde
siempre: **dos ficheros, el mismo concepto, signos opuestos, y el que se
publicaba era el malo.**

**Qué decía la medida, comprobado contra el archivo.** La noche del 29-08 (Luna
96,9 %) el fotómetro subió a **20,30** a las 21:56 locales, con la Luna a 6,6°
recién salida, y bajó a 18,46 a las 23:55 con la Luna a 29°. El modelo la seguía
con **+0,36 mag** de desvío en la parte oscura. Para esta noche (Luna 92,2 %) el
modelo predice la misma forma: **20,19** al cerrar el crepúsculo y **17,05** en
la culminación lunar. El «20» medido era el MEJOR momento de anoche y el «17»
del panel el PEOR de hoy — la misma noche, dos extremos, y el rótulo decía que
era el mismo tipo de número. (No hay medida del fondo de la madrugada del 30
porque el fotómetro deja de reportar entre las 00:01 y las 05:40 locales.)

**Corregido, y en dos números en vez de uno**, porque en noche con Luna uno solo
no describe nada: entre el mejor instante y el habitual hay tres magnitudes, o
sea un **factor 16** de fondo.

- `mag_cenit_mas_oscuro` pasa a ser el **máximo** — lo que dice su nombre.
- Nuevo `mag_cenit_tipico`: la **mediana** sobre la oscuridad astronómica, que
  es lo que uno se va a encontrar de verdad. El panel lo enseña al lado.
- **Al semáforo de consejos va el típico, no un extremo.** Sus textos dicen «el
  fondo de cielo se queda en X», y eso solo es cierto de un valor
  representativo: con el mejor instante recomendaría galaxias por veinte minutos
  de cielo bueno. Medido sobre esta noche, los semáforos salen **idénticos** con
  la mediana y con el mínimo de antes, y distintos con el máximo — o sea que el
  arreglo corrige el número que se cita sin cambiar el consejo.

**Y el fallo de método:** el único test que tocaba este campo lo llevaba como
**dato de entrada** (`"mag_cenit_mas_oscuro": 17.08`, copiado de una salida real
y por tanto equivocada), así que enseñaba el valor malo en vez de protegerlo.
Ahora hay uno que compara contra `max()` y `median()` recalculados, y se ha
comprobado que falla volviendo a poner `min()`.

---

## 30-08-2026 — La separación a la Luna se medía desde el baricentro del sistema solar

**Cómo salió:** haciendo que el buscador de la web encontrara M31. Al comprobar
que el número del motor y el que dibuja la cúpula del navegador fueran el mismo,
no lo eran: para NGC 7635 esa noche el motor decía **70,7°** donde la Luna estaba
a **55,7°**.

**Qué pasaba.** `Target.positions` calculaba
`coordinates.separation(get_body("moon", when))`: el objeto en **ICRS**, que es
*baricéntrico*, contra la Luna en **GCRS**, que es *geocéntrico*. Entre esos dos
marcos hay una **traslación de una unidad astronómica**, no un giro, así que la
Luna acababa colocada donde se la ve desde el baricentro del sistema solar.
Astropy lo avisaba en CADA llamada —«angular separation can depend on the
direction of the transformation»— y el aviso llevaba semanas leyéndose como ruido
de la librería: era el número saliendo mal, escrito en la salida de los tests.

**Cuánto importaba.** Sobre los 983 objetos puntuados de la noche del 30-08: error
**mediano de 23°** y **máximo de 38°** (NGC 1893). Con la ley de dispersión medida
aquí —10^(−θ/48,55°)— quince grados son un **factor dos** en el brillo lunar sobre
el objeto, que entra en su rendimiento hora a hora. Cambian las horas de **100
objetos** (hasta −3,3 h), **seis veredictos** (M57 y M17 mejoran, NGC 1333 e
IC 143 empeoran) y el primero de la lista de la noche.

**Qué NO tocaba:** la ley lunar en sí. Se ajustó con el fotómetro **cenital**,
donde θ es la distancia cenital de la Luna (90 − altura) y no pasa por esta
función; el término del cenit en `targets.py` también la usaba ya así. Sólo estaba
mal la separación **del objetivo**, que es la que hace direccional al modelo.

**Corregido:** la separación se mide en el MISMO marco que la altura que se
publica (`altaz.separation(moon.transform_to(frame))`), verificado contra las tres
formas honestas de calcularlo —los dos en AltAz, los dos en GCRS, y a mano sobre
la AR/Dec aparente de la Luna—: las tres dan 55,74. Y ahora el motor y la cúpula
del navegador coinciden dentro de **0,46°** sobre los 40 objetos × 59 marcos, que
antes discrepaban hasta 38°. Test de regresión en
`tests/test_targets.py::test_separacion_a_la_luna_en_el_mismo_marco_que_la_altura`.

**Y el fallo de método que lo dejó vivir tanto:** ningún test comparaba la
separación contra nada. Los 276 que había pasaban con el número mal.

## 30-08-2026 — Se publica el catálogo entero de la noche, no sólo lo que se recomienda

El motor puntuaba ~1.000 objetos y publicaba los ~150 que sacaban horas. Un objeto
se caía del buscador de la web **exactamente cuando la respuesta era interesante**:
M31 con Luna llena no aparecía, la web lo mandaba a SIMBAD y lo pintaba como
objeto externo con el rótulo «el motor NO lo ha puntuado». Lo había puntuado:
cero, y con el motivo escrito («el cielo no baja de 20.0 mag/arcsec² esta noche:
esta clase no cuaja»).

`noche.json` cambia `ranking` (top 150 con horas > 0) por **`catalogo`** (todo lo
evaluado, ordenado, con `porque`). El fichero pasa de 34 a 60 kB comprimidos, que
es lo que cuesta que el buscador cubra lo que el motor mira. La lista de
recomendados del desplegable la filtra ya la página.

También nuevo: **`veredicto_abrir`** por noche —Abierto/Cerrado con su umbral y su
regla dentro— para que la palabra que enseña la página salga del motor y no de un
corte escrito en el navegador.

---

## 28-08-2026 — La ley de dispersión lunar del sitio, y el contrato de v8 que resultó falso

**Qué se buscaba:** la "respuesta angular del fotómetro", porque el 28-08 por la
mañana se había concluido que el exceso de brillo con Luna era luz parásita del
instrumento (el test del AOD, sobre un rango de 52×, no encontró dependencia con
el aerosol → "no es atmósfera").

**Qué salió:** la conclusión era errónea y el propio archivo la refuta. El test
del AOD descarta el **aerosol**, no la atmósfera: la dispersión Rayleigh la fija
la densidad del aire, que es constante, así que un fallo en ese término tampoco
correlaría con el AOD. Cuatro medidas, en orden:

1. **No son nubes iluminadas por la Luna** (el rival que el AOD no toca, y del
   mismo signo). Con horas planas —dispersión intrahoraria < 0,02 mag, donde sin
   Luna el modelo clava el cero: residuo +0,004— el exceso sigue entero: −0,67
   mag a 20-30° de altura lunar y −1,02 a 30-40°.
2. **La forma es una función de fase.** La función de dispersión medida se
   describe con una exponencial única a **3,8 % de rms**.
3. **El aerosol sigue descartado** sobre la cantidad correcta: 1.730 horas
   estables, AOD de 0,010 a 0,540 (**54×**), respuesta plana (×0,97 a ×1,15).
4. **Y el que decide: la Luna BAJO el horizonte.** La luz parásita necesita el
   haz directo. Con el centro de la Luna por debajo de −0,83° —ya invisible— el
   exceso es **+0,19 mag a 5-7σ** y decae con la altura de la sombra geométrica
   de la atmósfera. Control con Luna nueva: **+0,009 mag**.

**Cambios en el código:**
- Nuevo `astroweather/moonscatter.py`: `f(θ) = 1,3612e7·10^(−θ/48,55°)` con suelo
  en la función publicada de Mauna Kea (este sitio no puede dispersar menos), y
  `beam_transmission(altura)`, la transmisión del haz medida al cruzar el
  horizonte, que llega a cero a −6°.
- **Derogado el contrato de SPEC v8 §2.2 «B_moon_physical = 0 bajo el
  horizonte»**. El `smoothing_UI` con escala inventada de 1,5° estaba tapando
  física real; ahora es física y entra en mag_sky y en horas-SNR.
- `calibration.py`: centro y tolerancia del test de nivel re-derivados contra el
  modelo nuevo (+0,10/0,74 → **−0,016/0,687**), y exclusión de las horas con la
  Luna dentro del campo del TESS-W (θ < 12°, 144 muestras de 131.096: ahí el
  instrumento mira a la Luna y la medida se va ×2,8).
- Nuevo `astroweather/moonvalidation.py`: el criterio conjunto contra noches
  reservadas por hash de la fecha (no por corte temporal, que mezclaría la
  validación con el ciclo de 18,6 años de la declinación lunar).
- `scoreboard.py`: el titular de la persistencia sale ahora del signo del número
  y no de lo que se creía. Afirmaba que la línea base ganaba cuando ya perdía.

**Lo que arregla, medido:** contra la verdad limpia, la curva de fiabilidad pasa
de 75,3 % a **90,1 %** de horas despejadas cuando la previsión da 0-10 % de
nubes (el extremo de cubierto no se mueve: 40,0 → 41,6 %), la tasa base pasa de
53,5 % a 74,1 %, y el BSS del calibrador de **+3,5 % a +11,7 %**. Y la
persistencia, que "batía al modelo por 14 puntos" con intervalo de confianza
limpio, **pierde por 54,1 puntos**: no era pericia meteorológica, era que entre
una noche y las 1-5 siguientes la fase lunar apenas cambia.

**Lo que NO se cumple, y no se fuerza:** el criterio pedía |r| < 0,15 entre
iluminación lunar y fracción de noche despejada. Queda en **−0,42**. Lo que resta
es **sensibilidad y no sesgo**: con Luna se ve nube fina que sin Luna se pierde
(dispersión con Luna llena ×1,59 en horas que la previsión da despejadas contra
×2,96 en las que da nubladas; un artefacto del modelo escalaría igual en las
dos). Un umbral que lleva r a −0,05 hace que las horas con previsión de 90-100 %
de nubes salgan despejadas el **71,5 %** de las veces, contra el 42,1 % de ahora.
Eso no es una verdad mejor, es un detector apagado.

**Rojo a propósito:** `test_a_full_moon_night_is_a_narrowband_night` queda en
xfail. Con el cielo corregido el par de constantes de banda estrecha
(`MOON_FILTER_FACTOR` 0,12 y `SKY_GATE` 19,5) estaba calibrado contra un cielo
1,8 mag demasiado oscuro, y el producto pasa a decir que con Luna llena no se
hace banda estrecha, que como consejo es falso. SPEC §2.3 ya da valores por
filtro (Ha 3nm ≈ 0,05, OIII ≈ 0,20, SII ≈ 0,08) que el código no usa; se
re-derivan los dos juntos con la LUT de SkyCalc.

**Y un problema nuevo y mayor, abierto en SPEC §2.2 bis:** midiendo la
reproducibilidad de la ley lunar aparece que **la mediana del cielo sin Luna sube
+0,56 mag entre 2019 y 2026**, monótona y robusta a estación y a suelo modelado
fijo, con el ciclo solar prediciendo el signo contrario. `NATURAL_FLOOR_MAG =
21.71` es una media de siete años que no describe ningún año, y eso es lo que
domina la incertidumbre honesta de la ley lunar: **±17 % (0,17 mag) entre años a
geometría fija**, no el 3,8 % del ajuste.

---

## v8 — Especificación normativa limpia (27-08-2026)

**Motivo:** la auditoría ChatGPT-2 demostró (grep confirmado) que el maestro v7 contenía instrucciones normativas obsoletas conviviendo con sus correcciones ("BLH solo GFS" en §1.6/§4 vs corregido en §1.4.3d; `score.py` y endpoints "score+confianza" vs el score eliminado en v6) — un riesgo técnico real si un agente de código implementa la instrucción muerta. La v8 reescribe TODO en formato corto: contratos, invariantes, física vigente, plan y decisiones. La historia sale del documento normativo (a este fichero).

**Cambios integrados en v8 (de las auditorías Gemini-1 y ChatGPT-2, verificados antes de aplicar):**

*Verificados empíricamente el 27-08-2026:*
- **`elevation` de Open-Meteo tiene tres semánticas** (ChatGPT-2, BLOQUEANTE; verificado con las 3 llamadas): por defecto = DEM de downscaling en la coordenada (1542 m en la coord exacta; 1302 con la coord redondeada — ¡el redondeo importa!); `&elevation=1650` = ajusta superficie a cota real (P_sfc 836 ≈ 843 medidos); `&elevation=nan` = cota real de la celda del modelo (**1067 m**). Consecuencias: Δh de aerosol pasa de 348 a **583 m**; todas las llamadas de superficie llevan `&elevation=1650` y coordenadas exactas 38.16583/−2.32672. *(Invalida la interpretación de un "verificado" de v5: se verificó el valor, no su significado — segunda vez con la misma lección.)*
- **RainViewer discontinuó el nowcast el 1-1-2026** (ChatGPT-2; verificado: `nowcast: 0 frames`). Queda solo radar observado (licencia personal/educativa); el nowcast de precipitación será propio (advección).
- **BDRC (dust.aemet.es): THREDDS navegable pero datos con 401** en fileServer/OPeNDAP/NCSS incluso en la raíz "Public" (Gemini-1, BLOQUEANTE; verificado). BDRC sale de la cadena por defecto del AOD_dust; GEOS-FP promovido.
- **El dominio EPS `ecmwf_ifs` (9 km) existe pero hoy no sirve cloud_cover en Nerpio** (0 valores; ChatGPT-2 lo propuso con etiquetas [LO SÉ]/[NO ME CONSTA] exactas). Queda como prueba en sombra periódica.
- **Texto normativo obsoleto en v7 confirmado por grep** (ChatGPT-2) → esta v8.

*Adoptados por lógica/documentación (ChatGPT-2):*
- Horas-SNR: separar `Q_cond` y `E[Q] = P_usable × Q_cond`; publicar ambas ("si despeja / esperadas").
- Techo: "probabilidad de condiciones aptas para abrir"; climatología como feature/intercepto (multiplicarla sobre P calibrada contaba dos veces el prior); validación asimétrica contra techo real.
- Capas de nubes ECMWF derivadas de RH en niveles fijos → en un sitio a 1650 m "low" incluye niveles bajo tierra: reconstrucción site-relative por proveedor; C_eff como candidato a validar (no axioma).
- MOS "desde el día 1" matizado: AEMET OpenData no ofrece archivo horario largo (endpoint = tiempo reciente; pendiente confirmar con clave) → **las etiquetas históricas de nubes/cielo salen del TESS (2019→)**, hallazgo propio que ningún auditor explotó; T/viento superficie: ERA5-Land como pseudo-obs o serie propia.
- LUT de SkyCalc sin eje AOD (SkyCalc no lo expone); corrección de aerosol aparte vía k.
- Bucle 1 renombrado "AR inspirada en Masciadri" (el paper usa P=50 a 1 min multi-noche; réplica como challenger) y restringido a variables inerciales (nubes por extrapolación cinemática — coincide con Gemini-1).
- OpenNGC `SurfBr` solo galaxias/banda B → contraste cuantitativo solo para la lista curada; categorías de dificultad para el resto.
- Airglow: F10.7 como componente lenta suavizada (27/81 d) con incertidumbre — no predictor horario del MVP.
- `setup_id` obligatorio en cada FWHM + tabla de configuración instrumental.
- Calibrador de P: logístico global con covariables; estratificar solo con muestra demostrada.
- Rodajas: Calima + Transparencia fusionadas (el catálogo v7 tenía un parámetro sin rodaja).
- Dos MVPs con nombre: operacional (rodajas 1–3) / recomendador (4–6) — resolvía la contradicción "producto central en Fase 1" vs "necesita fondo y seeing".
- Atlas Lorenz: licencia comercial no confirmada → benchmark. CAMS global ~45 km (no 11).
- HV: contrato h = AGL (z_geopotencial − 1650) con test sintético.

*Adoptados de Gemini-1:*
- CloudWatcher: mide la ventana 8–14 µm, no el IR integral (coincide con ChatGPT-2 vía "Corrected Sky Temperature") → predictor empírico calibrado contra eventos de rocío como primario; Brunt fallback. Identificar la semántica exacta del campo de cloud.html.
- Término BL de HV con escala dinámica (BLH del ECMWF 9 km) como challenger en sombra.
- `provider_model_version` como feature + decaimiento temporal de muestras en el Bucle 5.
- Filtro de excentricidad en el pipeline FWHM.
- Validación estratificada por régimen meteorológico (frentes vs convección) además de walk-forward.

*Rechazados con motivo:*
- "Migrar todo al ECMWF 9 km" (ChatGPT-1) — sin niveles de presión (verificado): híbrido.
- "Eliminar la física analítica del seeing, GBM directo" (Gemini-1) — arranque en frío sin FWHM histórico y sin física no hay producto día-1 ni prior para sitios sin sensores; el GBM ya es el challenger final del plan.
- Rodajas 1-2-6 de Gemini (seeing antes que seguridad) — para un observatorio remoto los gates no son opcionales; ChatGPT-2 (1-2-3) coherente con su propia crítica.
- BDRC como "institucional GTS/EUMETCast" (Gemini-1) — el 401 es real pero la vía es un login/registro cuyas condiciones se desconocen: "pendiente de credenciales", no "imposible".

**Marcador de auditorías (verificación ejecutada):** ChatGPT-2: 6 bloqueantes → 4 verificados y confirmados, 2 correctos por lógica; etiquetado [LO SÉ]/[NO ME CONSTA] impecable; la mejor ronda del proyecto. Gemini-1: 5/6 objeciones válidas (1 verificada), 1 tropiezo de lectura (propuso verificar el backfill de miembros EPS que el documento ya declaraba imposible) y 1 detalle inflado. ChatGPT-1 (sobre v5): ~20 confirmaciones, 6–7 huecos reales, 1 error corregido (9 km).

## v7 — Documento maestro para auditoría cruzada (27-08-2026)

v6 íntegra + Guía para el auditor externo (§0: qué atacar, escala de honestidad, formato) + Plan de implementación (§9: catálogo de parámetros de cara al usuario, tronco Fase 0 + rodajas verticales con DoD, lienzo nocturno sobre la orografía, predicción de techo, "se muestra la noche / se archiva el día"). Prompt de auditoría en `PROMPT_AUDITORIA_V7.md`.

## v6 — Rediseño tras auditoría ChatGPT-1, contrastada (27-08-2026)

Correcciones factuales verificadas: Historical Forecast = serie de leads cortos (no "tal como se emitió"); Previous Runs = backfill determinista real (≥2,5 años ecmwf_ifs025); **miembros EPS retenidos solo ~4–5 días → archivarlos desde el día 1**; "probabilidad real" → P_raw + calibrador; ECMWF híbrido 9 km (superficie/BLH) + 0.25° (perfiles); v_eff definida. Rediseño: score único → tres salidas; horas-SNR como producto central; bandas fotométricas separadas (V/sensor/e⁻); seeing en terciles; β de Richardson → features+flag (β como challenger); B_moon físico=0 bajo horizonte (rolloff=UI); LUT SkyCalc para NB; rocío con T_sky medida y margen en °C; almacenamiento raw+canónico ancho (fuera EAV); sin Redis/Celery; Fase 0 de instrumentación; presupuesto.

## v5 — Verificación empírica de la ingesta (26/27-08-2026)

Todas las APIs llamadas en real. Hallazgos: `dust` de Open-Meteo AQ = µg/m³ superficie (NO AOD de polvo; la corrección v4 se quedaba sin fuente síncrona → CAMS prefetch + BDRC + heurística); Ensemble API (51 miembros EPS); Historical/Previous Runs; vientos en km/h por defecto (→ `&wind_speed_unit=ms`); presión de sitio por cota real; endpoint Wyoming migrado a wsgi+CSV; K&S rama ρ<10° + validación SkyCalc; twilight; zodiacal Leinert; sesgo de censura del dataset. **Inventario de AstroCamp verificado**: TESS stars289 (serie pública desde ~2019, 21.97 mag/arcsec² medidos; API Grafana `tsdb/query` datasourceId:2), allsky indi-allsky, 2 CloudWatchers, 2 estaciones (843 hPa), estado de techos, horas de cúpula 2012–2026. Webs analizadas: DarkSkySites (LUMIX/APEX/LLFC), lightpollutionmap (SB_2012–2025, API con key), red STARS4ALL (658 dashboards; conector sondeado en 19 estaciones: ~40 % vivas), GEOS-FP accesible, F10.7 JSON, OpenTopoData, RainViewer, Barcelona Dust RC.

## v4 y anteriores (documento original + 2 revisiones externas previas)

v4: filtro de niveles subterráneos, corrección de AOD por especies, acoplamiento viento-rocío, Ri_bulk reescalado, mar de nubes (γ con signo), rolloff lunar, α_comp por morfología, FlatBuffers/cdsapi asíncrono/particionado Hive. v3: modelo radiativo de rocío, detector Ri, retrodispersión LP por nubes, doble zona sb_core/sb_faint, masa de aire dinámica, Parquet+DuckDB, árboles-con-lags primero. v2: checklist exhaustivo de variables, Hufnagel–Valley implementable, endpoints y snippets, sistemas operacionales profesionales (ALTA/FATE/MKWC), motor de recomendación de targets. v1: concepción (cinco familias de parámetros, K&S, ensemble multi-modelo, verificación como producto).

---

**Método de versionado adoptado** (ficha CabraBase `auditoria-cruzada-de-documentos`): el autor no audita su propio documento — cada versión mayor pasa por auditoría externa cruzada + verificación empírica de las afirmaciones de TODOS (documento y auditores) antes de aplicarse. Verificar existencia ≠ verificar significado.
