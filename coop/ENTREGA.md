TIEMPO: empecé 18:20 — terminé 18:24

# Informe de Entrega: Pintor del Campo de Nubes (`AWNubes`)

## 1. Criterios de Aceptación (Mediciones Reales)

Todas las mediciones se han ejecutado directamente en motor real Chromium/Blink (Microsoft Edge Headless) sobre `coop/gemini/nubes_prueba.html`.

### Criterio 1: Orientación (Norte = Arriba)
- **Configuración:** Rejilla 31×15 con 100 en la fila más al NORTE (`j = ny - 1 = 14`) y 0 en todas las demás (`j = 0..13`), sobre lienzo de 310×150.
- **Cuarto superior (`y: 0..37`):**
  - Alfa Máximo: **217 / 255** (cobertura 100% con `opacidadMax: 0.85` -> `round(0.85 * 255) = 217`).
  - Alfa Media: **31.68**.
  - Píxeles opacos (alfa > 0): **3.100** de 11.470 píxeles.
- **Cuarto inferior (`y: 113..150`):**
  - Alfa Máximo: **0 / 255**.
  - Alfa Media: **0.00**.
  - Píxeles opacos (alfa > 0): **0** de 11.470 píxeles.
- **Resultado:** **SUPERADO (PASS)**. El norte opaco queda arriba y el sur queda completamente limpio/transparente.

### Criterio 2: Hueco ≠ Despejado
- **Configuración:** Rejilla 31×15 con el campo entero a `null` sobre lienzo de 310×150.
- **Celdas y huecos reportados:**
  - `celdas`: **465**
  - `huecos`: **465** (se cumple `huecos === nx * ny`).
- **Opacidad del lienzo:**
  - Píxeles con alfa > 0: **11.624** de 46.500 (exactamente el **25.0%** del lienzo, correspondiente a la trama diagonal de 2 px de trazo cada 8 px).
  - Alfa de la trama: **46 / 255** (correspondiente exacto a `rgba(207, 171, 74, 0.18)` -> `round(0.18 * 255) = 46`).
- **Resultado:** **SUPERADO (PASS)**. Los huecos se contabilizan y el lienzo no queda transparente, mostrando la trama dorada.

### Criterio 3: Control Negativo y Discriminación
- **Configuración:** Rejilla 31×15 con campo entero a 3 (por debajo del umbral por defecto 8) sobre lienzo de 310×150.
- **Control negativo (campo = 3, umbral = 8):**
  - Píxeles con alfa > 0: **0** (Alfa Máximo: 0).
- **Prueba de discriminación (campo = 50, umbral = 8):**
  - Píxeles con alfa > 0: **46.500** de 46.500 (100% del lienzo con velo).
  - Alfa Máximo: **99 / 255** (`(50 - 8)/92 * 0.85 * 255 = 99`).
- **Resultado:** **SUPERADO (PASS)**. No pinta nada por debajo del umbral y discrimina inmediatamente al superar el umbral.

### Criterio 4: Coste y Rendimiento
- **Configuración:** Rejilla 31×15 (465 celdas) sobre lienzo de 920×518 (476.560 píxeles) con gradientes continuos y huecos sintéticos.
- **Tiempo medido en primera pasada (devuelto por `AWNubes.pintar`):** **12.20 ms**.
- **Serie de 10 pasadas consecutivas (`performance.now`):**
  - Media: **5.39 ms**
  - Mínimo: **4.60 ms**
  - Máximo: **8.60 ms**
- **Tope contractual:** 30 ms.
- **Resultado:** **SUPERADO (PASS)**. Se sitúa entre 5 y 12 ms, muy por debajo del límite de 30 ms.

---

## 2. Decisiones de Diseño Fuera del Contrato Estricto

1. **Definición de `celdas` vs `huecos` devueltos:**
   Se fijó que `celdas` represente el tamaño total de la rejilla (`nx * ny = 465`) y `huecos` el conteo de elementos `null` o indefinidos dentro del array, permitiendo verificar `huecos === nx * ny` cuando no hay datos.
2. **Mapeo geométrico de bordes (Canvas vs Rejilla):**
   Se proyecta el intervalo continuo `0..(ancho - 1)` sobre `0..(nx - 1)` y `0..(alto - 1)` sobre `(ny - 1)..0`. Esto garantiza que los extremos del lienzo coinciden con los puntos frontera de la caja calculada por `cloudmap.py` (`(nx - 1) * paso` y `(ny - 1) * paso`).
3. **Composición con la imagen inferior del satélite (`source-over`):**
   `putImageData` sobrescribe los píxeles del lienzo destruyendo cualquier capa previa donde el alfa sea 0. Para comportarse como un auténtico "velo encima de la imagen de satélite", `AWNubes.pintar` genera el buffer en un canvas offscreen y lo estampa sobre `ctx` mediante `ctx.drawImage(offscreen, 0, 0)`. Si el lienzo ya contenía la imagen de satélite, ésta se conserva debajo intacta.
4. **Patrón de trama diagonal para huecos:**
   Se adoptó una trama diagonal a 45 grados (`(px + py) % 8 < 2`) con ancho de 2 px cada 8 px en color `rgba(207, 171, 74, 0.18)`. Proporciona visibilidad clara sin tapar el fondo. En fronteras con datos válidos, se reponderan los vecinos no nulos para que un dato faltante no tire artificialmente la cobertura a 0%.
5. **Precomputación y optimización:**
   Se precalculan las coordenadas y factores de interpolación horizontal para cada columna `px` antes del barrido de filas, evitando divisiones y asignaciones de objetos en el bucle caliente (518 × 920 iteraciones).

---

## 3. Qué NO se ha Podido Comprobar

- **Superposición con satélite real en vivo:** No se ha probado con el satélite Meteosat-12 ni la barra de tiempo en ejecución web integrada (la integración corre a cargo de Claude en `Pagina Web/` según el reparto de la tarea).
- **Navegadores obsoletos sin Canvas 2D / ES5:** Se asume soporte de Canvas 2D y ES5 (IE9+ y todos los navegadores modernos).
