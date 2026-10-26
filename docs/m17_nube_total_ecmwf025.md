# M17 — La nube total del ECMWF 0,25° y sus capas

Retroanálisis del 26-10-2026. En el archivo, `cloud_total` de `ecmwf_ifs025` queda
**por debajo de su capa más nubosa** en el 50 % de las filas; en `ecmwf_ifs` (9 km),
en el 2 %. De ese total cuelgan `calibration.py` (`BACKFILL_MODEL = "ecmwf_ifs025"`)
y `roofcalibration.py`.

Etiquetas usadas abajo:

- **LEÍDO**: está escrito en el código fuente de Open-Meteo, en la documentación del
  ECMWF o lo dicen los mantenedores de Open-Meteo. Se puede comprobar con el enlace.
- **MEDIDO**: sale de datos. En esta sesión **no se ha podido medir nada nuevo**: la
  política de red bloquea `api.open-meteo.com`, `previous-runs-api.open-meteo.com`,
  `historical-forecast-api.open-meteo.com` y `data.ecmwf.int`. Lo único medido son las
  cifras del 50 % y del 2 % que trae el retroanálisis.
- **PLAUSIBLE**: se deduce de lo leído y encaja con lo medido, pero falta pasarlo
  contra el archivo. Para eso está `tools/m17_nube_total.py` (ver al final).

## 1. Cómo lo pedimos

- `fetch_forecast.py`: API de previsión (`api.open-meteo.com/v1/forecast`), dos
  llamadas. En `ecmwf_ifs` (sola) van `cloud_cover`, `cloud_cover_low/mid/high` y lo
  de superficie. En `ecmwf_ifs025`, metido en una llamada con varios modelos, van las
  mismas cuatro de nube (el total, desde el 28-08-2026) y los niveles de presión.
- `backfill_history.py`: API de previsiones anteriores
  (`previous-runs-api.open-meteo.com`), `models=ecmwf_ifs025`, `cloud_cover*_previous_dayN`.
  Lo dice su docstring y lo confirma el código de Open-Meteo: de las capas no se guarda
  nada para días anteriores; solo se guarda el total.

Consecuencia: **todas las capas de `ecmwf_ifs025` que hay en el archivo vienen de la
previsión en vivo** (desde el 28-08-2026). El 50 % está medido sobre esas filas.

## 2. Qué número es cada uno

### `ecmwf_ifs` (9 km): todo es del ECMWF — LEÍDO

Open-Meteo lo recibe directamente del ECMWF (ECPDS), en la malla gaussiana O1280,
cada hora hasta +90 h. Pide `tcc/lcc/mcc/hcc` tal cual
([`EcmwfEcpdsDownloader.swift`](https://github.com/open-meteo/open-meteo/blob/main/Sources/App/EcmwfEcpds/EcmwfEcpdsDownloader.swift),
`paramsSide = ".../hcc/lcc/mcc/.../tcc/..."`;
[`EcmwfEcpdsDomain.swift`](https://github.com/open-meteo/open-meteo/blob/main/Sources/App/EcmwfEcpds/EcmwfEcpdsDomain.swift)).
Los cuatro números salen del mismo campo de fracción nubosa del modelo y del mismo
solapamiento, así que el total es ≥ cada capa por construcción. El 2 % que no cumple
es **PLAUSIBLE** que venga del redondeo a enteros y de la interpolación de Open-Meteo
al punto y a la hora.

### `ecmwf_ifs025`: el total y las capas son productos distintos — LEÍDO

Es el ECMWF *open data* a 0,25°, con pasos de 3 h. Las horas intermedias las
interpola Open-Meteo con Hermite, variable por variable
([`EcmwfDomain.swift`](https://github.com/open-meteo/open-meteo/blob/main/Sources/App/Ecmwf/EcmwfDomain.swift),
[`EcmwfVariable.swift`](https://github.com/open-meteo/open-meteo/blob/main/Sources/App/Ecmwf/EcmwfVariable.swift)).

- **El open data no trae `lcc`, `mcc` ni `hcc`.** Trae `tcc` y la humedad relativa
  `r` en niveles de presión
  ([tabla de parámetros de `ecmwf-opendata`](https://github.com/ecmwf/ecmwf-opendata#parameters-and-levels)).
- **Las capas las calcula Open-Meteo a partir de la humedad relativa**
  ([`DownloadEcmwfCommand.swift`](https://github.com/open-meteo/open-meteo/blob/main/Sources/App/Ecmwf/DownloadEcmwfCommand.swift),
  bloque *"Calculate mid/low/high/total cloudocover"*). Usa Sundqvist et al. (1989)
  con un umbral de humedad que depende de la presión
  ([`Meteorology.swift`](https://github.com/open-meteo/open-meteo/blob/main/Sources/App/Helper/Meteorology.swift),
  `cloudCoverFromRH`, `relativeHumidityThreshold`), y en cada capa se queda con el
  **máximo** de sus niveles:
  - baja: 1000/925/850 hPa
  - media: 700/600/500/400 hPa
  - alta: 300/250/200/150/100/50 hPa

  Estos niveles se cambiaron el 13-10-2025
  ([PR #1530](https://github.com/open-meteo/open-meteo/pull/1530),
  [discusión #1526](https://github.com/open-meteo/open-meteo/discussions/1526)).
- **El total ha tenido dos definiciones:**
  1. **Hasta el 20-11-2025** el open data no traía `tcc`, y Open-Meteo lo calculaba con
     sus propias capas: `min(0.9·baja + 0.6·media + 0.3·alta, 100)`
     (`Meteorology.cloudCoverTotal`; el mantenedor lo confirma en la
     [discusión #1526](https://github.com/open-meteo/open-meteo/discussions/1526):
     *"Total cloud cover in IFS0.25 is weighted by `min(low * 0.9 + mid * 0.6 + high * 0.3, 100)`"*).
  2. **Desde el 21-11-2025** el ECMWF añadió `tcc` al open data y Open-Meteo guarda
     **la TCC nativa** (escalada ×100). Solo calcula el total con la fórmula si falta
     `tcc` (`if !writer.contains(cloud_cover)`):
     [PR #1587](https://github.com/open-meteo/open-meteo/pull/1587), *"fix: ECMWF added tcc
     to grib files"*, que cerró la
     [issue #1586](https://github.com/open-meteo/open-meteo/issues/1586)
     (nube del ENS en 0–1 la pasada del 20-11-2025 12Z).

### Por qué el total queda por debajo de una capa

No es un fallo de ordenación ni de malla: **el total y las capas no son el mismo
campo.**

| Periodo | `cloud_total` de ifs025 | capas de ifs025 | ¿total < máx(capas)? |
|---|---|---|---|
| hasta 20-11-2025 | fórmula ponderada de las capas de Open-Meteo | humedad relativa, Sundqvist | **casi siempre que hay nube**. Por construcción, si solo hay una capa el total es 0,9/0,6/0,3 veces esa capa: un cielo lleno de cirros sale como 30 %. LEÍDO; el porcentaje exacto no se ha medido |
| desde 21-11-2025 | TCC nativa del ECMWF (nube del modelo, con solapamiento máximo-aleatorio) | humedad relativa, Sundqvist (diagnóstico de Open-Meteo, no del ECMWF) | **a menudo y en los dos sentidos**: con mucha humedad en altura sin nube en el modelo, la capa alta del diagnóstico supera a la TCC. Que esto dé el 50 % es PLAUSIBLE |

El 50 % del archivo es del segundo periodo (las capas de ifs025 solo se guardan
desde el 28-08-2026). Que salga de comparar la TCC nativa con capas diagnosticadas es
**PLAUSIBLE** y encaja: en el modelo de 9 km, donde los cuatro números son del ECMWF,
la incoherencia baja al 2 %.

## 3. Qué significa para el producto

1. **El total vivo de ifs025 (desde el 21-11-2025) es un buen número**: es la TCC del
   ECMWF, interpolada a 0,25° y en pasos de 3 h. LEÍDO.
2. **Las capas de ifs025 no son las del ECMWF.** Ningún código debería leerlas como
   «la nube baja del ECMWF» ni compararlas con el total. LEÍDO.
3. **El retroanálisis con el que se entrena la curva mezcla dos escalas** — PLAUSIBLE,
   y es lo que más importa. `observed_clear_hours` empieza el 2024-02-01, así que
   `training_pairs` usa:
   - unos 21 meses (2024-02 → 2025-11-20) con la fórmula ponderada, que comprime la
     nube alta (100 % de cirros = 30 %);
   - unos 11 meses (2025-11-21 → hoy) con la TCC nativa.

   La curva se aplica en vivo sobre la TCC nativa. Un «30 %» del primer periodo puede
   ser un cielo cubierto de cirros, y en el segundo no. Esto supone que la API de
   previsiones anteriores sirve lo que se guardó en su momento y que Open-Meteo no ha
   recalculado el histórico; no está comprobado.
4. Es posible que alguna pasada de alrededor del 20-11-2025 haya quedado en 0–1 en vez
   de 0–100 (lo que cuenta la issue #1586 para el ENS). PLAUSIBLE; se ve buscando
   pasadas con `max(cloud_total) <= 1`.

## 4. Opciones

| Opción | Qué es | Coste | Valoración |
|---|---|---|---|
| **A. No tocar nada** | Seguir como está | 0 | La curva sigue mezclando dos escalas y se aplica sobre una sola. No recomendada si la herramienta confirma el salto |
| **B. Entrenar solo desde el 21-11-2025** | Filtro `run_utc >= '2025-11-21'` en `training_pairs` (y lo mismo en `roofcalibration.py` cuando quede libre) y reentrenar | Pequeño: 1 línea y un test por curva. Se pierden ~2/3 de los pares; quedan ~11 meses, que crecen solos | **Recomendada**, si la herramienta confirma el salto. El entrenamiento y el uso quedan en la misma escala |
| **C. Pasar la curva al total de 9 km (`ecmwf_ifs`)** | Retroanálisis `--model ecmwf_ifs`, `BACKFILL_MODEL` a `ecmwf_ifs` y reentrenar | Medio: hay que medir desde cuándo sirve previsiones anteriores el de 9 km (no está medido), tocar las dos curvas y el marcador. Tiene paso horario y capas coherentes | Buena a medio plazo. Es el mismo modelo nativo con más resolución, y el producto ya usa ese modelo en superficie |
| **D. Recalcular el total con las capas** | Aplicar la fórmula 0,9/0,6/0,3 a todo | El retroanálisis no tiene capas, así que no se puede aplicar a la serie de entrenamiento | **Descartada**: además sustituye un número del ECMWF por un diagnóstico de humedad |
| **E. Corregir solo las capas** | Ignorar las capas de ifs025 o marcarlas como «diagnóstico de humedad» | Pequeño (documentación o una marca) | Complementa a B o C. No resuelve la curva |

## 5. Cómo pasar lo PLAUSIBLE a MEDIDO

Con el archivo privado, desde la raíz del repositorio:

```
python tools/m17_nube_total.py --root <ruta del archivo>
```

Imprime tres cosas:

1. el % de filas con total < máx(capas) por modelo; debería reproducir el 50 % y el 2 %;
2. el % de filas vivas de ifs025 cuyo total coincide con la fórmula ponderada: cerca de
   0 % quiere decir TCC nativa y cerca de 100 %, fórmula;
3. la proporción de totales ≥ 99 % y la media del retroanálisis antes y después del
   21-11-2025. Un salto claro confirma el punto 3 del apartado anterior.

Pendiente, cuando haya red hacia Open-Meteo: pedir 2–3 días recientes para Nerpio
(38.16583, −2.32672, `elevation=1650`) con `models=ecmwf_ifs025` y `models=ecmwf_ifs`
y las cuatro variables, para medir la incoherencia fuera del archivo; y pedir a
previous-runs un día de 2025 y otro de 2026 para ver el cambio de escala en origen.

## 6. Medido sobre el archivo (26-10-2026, escritorio)

`tools/m17_nube_total.py --root ../AstroWeather-Archive`:

| Comprobación | Resultado | Lectura |
|---|---|---|
| total < máx(capas), `ecmwf_ifs` | 1,9 % de 10.080 h | reproduce el 2 % |
| total < máx(capas), `ecmwf_ifs025` | 49,7 % de 9.744 h | reproduce el 50 % |
| total de ifs025 = fórmula 0,9/0,6/0,3 | 21,7 % | lejos de 100 %: el total vivo es la TCC nativa |
| retroanálisis antes del 21-11-2025 | 3,7 % de horas ≥ 99 %, media 27,5 | |
| retroanálisis desde el 21-11-2025 | 21,1 % de horas ≥ 99 %, media 46,1 | **salto de escala** |

Control estacional (el «antes» tiene dos veranos y el «después» un invierno):
mismos meses de cada año, horas ≥ 99 % / nube media.

| | dic | ene | feb |
|---|---|---|---|
| 2024-25 | 1,7 % / 26,5 | 3,2 % / 38,8 | 2,4 % / 33,3 |
| 2025-26 | 40,9 % / 67,6 | 47,3 % / 75,0 | 37,4 % / 63,9 |

Un ×15 en los cubiertos, justo en la fecha del PR #1587 de Open-Meteo, no es un
invierno más nublado: es el paso de la fórmula a la TCC nativa. Con esto el
punto 3 del apartado 3 pasa de PLAUSIBLE a **MEDIDO**, y la opción B queda
recomendada para `calibration.py` (curva del fotómetro). La curva del techo (M1)
**no** está afectada: se entrena solo con previsiones vivas desde 08-2026, todas
en la escala nativa.

## 7. Opción B aplicada (26-10-2026)

`calibration.NATIVE_TCC_SINCE = 2025-11-21`: `training_rows` / `training_pairs`
solo usan pasadas con `run_utc` desde esa fecha (el corte va por la pasada: un
`prev5d` válido el 23-11-2025 salió de la pasada del 18-11, con la fórmula).
Además entra la previsión viva del mismo modelo (`ecmwf_ifs025`, alcance
`lead_h // 24`, días 1-5), porque el retroanálisis se bajó una vez y no crece.
La curva ya no se guarda a ciegas: entra solo si gana el examen semanal
(`curvecontest.cloud_duel`, trabajo `nube_curva`). Cifras, examen y controles:
`docs/aprendizaje_continuo.md`, sección 4.

| | pares | horas | noches |
|---|---|---|---|
| antes (retroanálisis desde 2024-02, escala mezclada) | 33.799 | — | — |
| después (retro + vivo, pasadas ≥ 21-11-2025) | 10.357 | 2.129 | 271 |

Fuera de muestra la ganancia es pequeña y está en la calibración del extremo
nublado, no en ordenar mejor: con 90-100 % de nube prevista el cielo estuvo
despejado el 62,8 % de las horas; la curva nativa dice 63,6 %, la vigente 58,6 %.
