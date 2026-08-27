# CabraSpace AstroWeather — qué se ha construido, para auditoría externa

Sistema de predicción y recomendación para observación de cielo profundo en
AstroCamp (Nerpio, Albacete; 38.16583 N, −2.32672 W, 1650 m, código MPC I79).
Especificación vigente en `SPEC.md` (v8, auditada por ChatGPT ×2 y Gemini ×1
antes de escribir código). **19 commits, 139 tests en verde.**

Este documento resume el ESTADO REAL, incluidos los límites. Para auditar el
diseño está `SPEC.md`; esto es lo que de verdad corre.

---

## 0. Lo que responde hoy, de punta a punta

**MVP operacional** (`python -m astroweather.tonight`) — «¿podré observar esta
noche, con seguridad, y cuándo?». Salida real del 27-08-2026:

```
oscuridad astronómica 7.7 h · Luna al 100 % · horas utilizables esperadas 8.0
probabilidad de condiciones para abrir: 74 %
mejor ventana: 18:00–03:00 UTC (media 66 %)
hora a hora: nubes, P(despejado), P(utilizable), factor limitante, seguridad
```

**MVP recomendador** (`astroweather.targets`) — «¿qué fotografío y cuánto
cundirá?». Dos números por objeto: `Q_cond` (horas equivalentes SI despeja) y
`E[Q]` (por la probabilidad de que despeje). Verificado que el consejo se
invierte solo con la Luna:

| | luna llena | luna nueva |
|---|---|---|
| mejor | NGC 7000 (NB) 3.2 h | Cruz de Einstein 16.2 h |
| descartados | IFN, M51, M31 = 0 | banda estrecha baja al montón |

---

## 1. Arquitectura de datos (Fase 0)

Regla rectora: **archivar antes de mostrar**, porque hay fuentes que olvidan.

- **Dos niveles**: RAW inmutable (respuesta original, direccionada por
  contenido, nunca se sobrescribe) + CANÓNICO en Parquet ancho tipado con
  procedencia completa (11 campos: versión del proveedor, celda, las TRES cotas,
  resolución nativa, timestep, interpolado, licencia, versión de pipeline…).
- **Vistas de lectura** que deduplican por clave natural y prefieren la versión
  de pipeline más nueva.
- **Scheduler** corriendo (arranque automático en Windows), con cada trabajo al
  ritmo de lo rápido que SU FUENTE olvida, aislamiento de fallos y aviso de
  caducidad de credenciales.

**Archivo actual**: 3,7 M medidas del fotómetro (2019→hoy) · 116 k filas de
forecast (2,6 años por lead time) · 22,5 k horas de AOD · 2.431 días de AEMET ·
15 años de climatología de cúpula · 60 medidas de FWHM sobre FITS reales.

### Fuentes integradas y verificadas
Open-Meteo (forecast híbrido 9 km + perfiles 0.25°, EPS de 51 miembros,
Previous Runs, Air Quality) · CAMS/ADS (AOD por especies) · AEMET (estación
7067Y a 11,5 km) · TESS stars289 (fotómetro, 2019→) · scraper de AstroCamp
(CloudWatchers, estaciones, 18 techos, log de seguridad, climatología) · FITS
propios.

---

## 2. Física implementada

| módulo | qué hace | validación |
|---|---|---|
| `ephem` | ventana de noche, Sol y Luna | efemérides astropy |
| `moonsky` | Krisciunas & Schaefer 1991 | luna llena → 18.75 mag/arcsec² (valor clásico) |
| `skybackground` | suelo natural + zodiacal + airglow | **medido en el sitio** (ver §3) |
| `skymodel` | cielo despejado completo + crepúsculo | crepúsculo **ajustado con 646 muestras** |
| `extinction` | k por altitud, especies y λ; penalización de exposición | Rayleigh 0.118 a 1650 m |
| `seeing` | Hufnagel-Valley → r0, ε, θ0, τ0; Richardson | HV5/7 → r0 4.9 cm, 2.07″ |
| `calibration` | P(despejado) calibrada | 33.924 pares, fiabilidad verificada |
| `gates` | seguridad escalonada por horizonte | |
| `targets` | horas-SNR por objeto | consejo se invierte con la Luna |

---

## 3. Hallazgos medidos (lo que el archivo enseñó)

1. **El suelo de cielo de Nerpio es 21.71, no 21.9** (en banda TESS), y la
   **luz zodiacal aporta 0.27 mag** cuando la eclíptica pasa por el cenit. Ambos
   recuperados del residuo sobre 18.791 horas limpias sin Luna.
2. **Más aerosol = cielo más OSCURO aquí** (21.593 → 21.738 de AOD bajo a alto,
   2451 horas). Refuta la intuición urbana: en un sitio oscuro el aerosol apaga
   el cielo natural más de lo que rebota la LP. Cuadra en magnitud (1.086·ΔAOD
   predice 0.25; medido 0.145).
3. **El forecast de nubes acierta más avisando de noches malas que prometiendo
   buenas**: 100 % nubes → 40 % de horas despejadas; 0-10 % nubes → solo 75 %.
   BSS sobre climatología +6.3 % (d+1) a +3.2 % (d+5).
4. **El TESS de AstroCamp NO detecta nubes**: publica tamb=0 y tsky=0 (sus
   sensores de T no reportan, contrastado con otras 3 estaciones).
5. **La Luna domina la variabilidad del fotómetro**: cualquier detector de nubes
   sobre la serie cruda es un detector de Luna. La detección real es el residuo
   contra el modelo de cielo despejado.

---

## 4. Límites conocidos (declarados, no escondidos)

- **Seeing sin calibrar.** El modelo predijo 1.21″ donde se midieron 3.16″. La
  mayor parte del hueco es instrumental (el FWHM lleva foco, óptica, guiado,
  montura). **A NO se ajusta**: hacerlo calibraría el telescopio, no el cielo.
  Se publica en terciles con banda, nunca un número suelto.
- **Calibración probabilística débil.** BSS **+3.9 %** tras limpiar la verdad
  (era +4.6 % con la verdad envenenada: publicábamos más pericia de la que
  había). La probabilidad sigue bien calibrada (promete 0.64, ocurre 0.641)
  pero discrimina poco. La mejora vendrá del EPS y del nowcast local.
- **La tasa base cayó de 64.1 % a 54.8 %** al limpiar la verdad, y no tenemos
  con qué contrastarla: `roof_open` tiene dato en 42 de 3.666.893 filas.
- **No hay término galáctico.** La Vía Láctea por el cenit mueve el fondo tanto
  como la luz zodiacal, que sí modelamos. Es medible con el mismo método del
  residuo y está pendiente.
- **Miembros EPS**: solo desde ayer (caducan en ~4 días upstream). La
  calibración probabilística del ensemble tendrá que crecer con el archivo.
- **Bandas fotométricas**: el 21.71 es banda TESS, no V. Sin calibración de
  banda todavía.
- **Factor lunar de banda estrecha** (0.12): aproximación marcada como baja
  calidad; el valor real sale de una LUT de SkyCalc pendiente.
- **B_LP direccional**: no existe. Solo cenit.
- **Perfil de horizonte**: pendiente del DEM (720 bins).
- **AEMET no tiene histórico horario** (verificado): el MOS horario se apoya en
  el TESS.
- **El scheduler solo recoge con el PC encendido** hasta que haya VPS.

---

## 5. Errores propios cazados durante el desarrollo

Se listan porque dicen más del método que los aciertos:

1. `elevation` de Open-Meteo tiene **tres semánticas** (DEM de downscaling /
   celda del modelo / la que pides). Δh del aerosol pasó de 348 a 583 m.
2. Mi `moon_phase_angle` devolvía la **elongación** (176° en llena) en vez del
   ángulo de fase (0° en llena). Alimentar eso a K&S habría invertido **todas
   las noches del año**.
3. El pipeline de FWHM **sobreestimaba un 30 %** por medir con momentos de 2º
   orden; contrastado con gaussiana (3.60″) y HFD (4.32″) sobre el mismo frame.
4. Mi término de crepúsculo, inventado, sobreestimaba el brillo **2.5-3 mag**.
   Calibrado con datos: residuo de +2.54 a +0.01.
5. El motor de targets usaba el suelo **sin crepúsculo**: las horas de día
   pasaban por noche cerrada.
6. La Luna se **contaba dos veces** en el motor de targets (cenit + target).
7. Dos escrituras en el mismo segundo **colisionaban de nombre** y una pisaba a
   la otra: desapareció un modelo entero del Parquet.
8. `cloud.html` publica **UTC, no hora local**: leerlo mal descartaba por
   caducados justo a los sensores vivos.
9. DuckDB devolvía horas en **zona local** aunque el archivo guarda UTC: el
   cruce forecast↔verdad daba 0 pares en silencio.
10. `np.allclose` con atol por defecto de 1e-8 declara **idénticos todos los
    Cn²** (valen ~1e-17).
11. La verdad terreno marcaba despejada cualquier hora **estable**, sin mirar el
    nivel: un estrato homogéneo tiene dispersión casi nula. **22 % del conjunto
    de entrenamiento era basura**, y el 97 % de esa basura era más OSCURA, no
    más brillante — aquí no hay ciudad debajo que la nube refleje.
12. El filtro de banda estrecha se aplicaba **solo a la Luna**, no al fondo:
    anulaba el narrowband en todo el crepúsculo, que es justo donde funciona.
13. Los cortes por clase eran un **escalón**: 0.000 a 0.685 en 0.02 mag.
14. El rendimiento de compactos leía el **valor puntual** del seeing, un modelo
    sin calibrar y sesgado optimista, contra nuestra propia regla de publicar
    terciles. La Cruz de Einstein valía 16.2 h; vale 8.6 h.
15. El umbral de dispersión citaba una validación contra el registro de techo
    **que el archivo no contiene**. La columna existe, la consulta corre, el
    número sale, y no significa nada.

---

## 6. Qué queremos que audites

1. **¿Hay algún número que estemos publicando con más confianza de la que
   soporta?** Es el pecado que más nos preocupa.
2. **La cadena de verdad terreno**: residuo del fotómetro → «hora despejada» →
   calibrador. ¿Es circular en algún punto? ¿El umbral de 0.10 de dispersión es
   defendible?
3. **El motor de horas-SNR**: ¿los cortes por clase (SKY_GATE) y el exponente
   α por morfología están bien planteados? ¿Falta alguna penalización?
4. **Lo que NO está**: ¿qué falta que no aparezca ni en los límites del §4?
5. **El plan**: con los MVPs cerrados, ¿atacarías la cúpula visual, el VPS, o
   más calibración?

Marca tus afirmaciones **[LO SÉ]** / **[NO ME CONSTA]**. Todo lo que afirmes con
seguridad se verificará ejecutando.
