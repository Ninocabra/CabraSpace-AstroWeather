# TAREA ACTIVA — pintor del campo de nubes (21-09-2026)

## Contexto en dos líneas

La web de CabraSpace va a dejar de incrustar el mapa de Windy y a pintar **su
propio** campo de nubes del ECMWF encima de la imagen de Meteosat-12. El motor
ya publica ese campo (`astroweather/cloudmap.py`, hecho y con 19 tests en
verde). Falta **el pintor**: un módulo de JavaScript puro que convierta el
campo en píxeles.

Tú haces el pintor. La integración —el bloque, el satélite de fondo, la barra
de tiempo— la hago yo en paralelo. Por eso el contrato de abajo es literal: si
cambias una firma o una orientación, lo que yo escriba no encajará.

## REGLAS DE ORO (las de siempre, y aquí importan más que nunca)

- **NO edites NINGÚN fichero existente de este repo.** Ni uno.
- **NO toques** `C:\Users\ninoc\Documents\PixInsight\CabraSpace\Pagina Web\`.
  Ese repo no es tuyo en esta tarea; yo integro tu fichero desde `coop\gemini\`.
- Escribes **exactamente dos ficheros nuevos**, y ninguno más:
  - `C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\gemini\astroweather-nubes.js`
  - `C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\gemini\nubes_prueba.html`
- **Sin dependencias.** Nada de npm, nada de CDN, nada de módulos ES. La web es
  JavaScript de toda la vida servido estático.
- Aviso honesto: **el repo NO está commiteado ahora mismo** (hay trabajo mío sin
  cerrar). Por eso lo de no tocar nada existente no es formalismo: un borrado
  tuyo aquí no se recupera con `git checkout`.

## El contrato

Fichero `astroweather-nubes.js`: una IIFE al estilo del resto del sitio
(`(function () { 'use strict'; ... })();`), compatible con ES5, que deja en
`window.AWNubes` exactamente esto:

### `AWNubes.version`
Cadena, por ejemplo `"1.0"`.

### `AWNubes.descodificar(cadena, codificacion)`
- `cadena`: un texto de `nx*ny` caracteres, uno por celda.
- `codificacion`: `{ base: 35, paso: 2, max: 50, sin_dato: "V" }` (viene dentro
  del propio JSON; **no lo hardcodees**, úsalo).
- Devuelve un `Array` de `nx*ny` números en 0..100, o `null` en las celdas cuyo
  código sea `sin_dato`.
- Regla: `porcentaje = (codigo - base) * paso`, y si `codigo - base > max`, es
  `null`.

### `AWNubes.pintar(ctx, opciones)`
Dibuja el campo interpolado sobre el canvas y **devuelve**
`{ celdas: <int>, huecos: <int>, ms: <número> }` (milisegundos medidos).

`opciones`:

| clave | qué es |
|---|---|
| `campo` | el array que devuelve `descodificar`, de largo `nx*ny` |
| `nx`, `ny` | columnas y filas de la rejilla |
| `ancho`, `alto` | tamaño del lienzo en píxeles |
| `umbral` | por debajo de este % no se pinta nada. Por defecto **8** |
| `opacidadMax` | alfa máxima, 0..1. Por defecto **0,85** |

**LA ORIENTACIÓN ES LO ÚNICO QUE NO SE PUEDE EQUIVOCAR.** El campo va en orden
`indice = j*nx + i`, con **`j` creciendo hacia el NORTE** e `i` hacia el ESTE.
En un canvas la `y` crece hacia ABAJO. Por tanto la fila `j = 0` se pinta en la
parte **inferior** del lienzo. Si lo inviertes sale un mapa especular que
parece perfectamente plausible y que nadie detecta mirando: un cielo nublado
por el oeste en vez de por el este sigue pareciendo un mapa.

**Interpolación bilineal** de la rejilla al lienzo. La rejilla es de 39 km por
celda sobre un mapa de 900: se tiene que ver como un campo continuo, no como
cuadraditos.

**Paleta — esto se pinta ENCIMA de una imagen de satélite, así que es un velo,
no un relleno.** Nada de escalas arcoíris. La nube es blanco cálido,
`rgba(232, 228, 216, a)`, con `a` creciendo con la cobertura desde `umbral`
hasta 100 y topando en `opacidadMax`. Por debajo del umbral, alfa 0 — se tiene
que ver el satélite limpio.

**UN HUECO NO ES CIELO DESPEJADO.** Las celdas `null` no se pintan como 0: van
con una trama diagonal tenue en `rgba(207, 171, 74, 0.18)` (el oro de la
página), para que se lea "aquí no sabemos" y no "aquí está despejado". Y van
contadas en `huecos`.

## Criterios de aceptación (mídelos y pon los números)

Haz `nubes_prueba.html` con casos sintéticos y **ejecútalo**. Estos cuatro:

1. **Orientación.** Campo con 100 en la fila más al NORTE (`j = ny-1`) y 0 en
   todo lo demás. Los píxeles opacos tienen que quedar **ARRIBA**. Compruébalo
   leyendo el alfa con `getImageData` en el cuarto superior y en el inferior, y
   da los dos números.
2. **Hueco ≠ despejado.** Campo entero de `null`: `huecos === nx*ny`, y el
   lienzo **no** puede quedar totalmente transparente.
3. **Control negativo.** Campo entero a 3 (por debajo del umbral 8): **ningún**
   píxel con alfa > 0. Si este no lo ves fallar cuando subes el campo a 50, es
   que no discrimina.
4. **Coste.** Pintar una rejilla 31×15 en un lienzo de 920×518. Da los
   milisegundos MEDIDOS. Si pasa de 30 ms, dilo, no lo escondas.

## Formato de la entrega

En `ENTREGA.md`, y con la regla de honestidad de siempre —número ejecutado o
"SIN COMPROBAR", nunca supuesto presentado como medido—:

1. `TIEMPO: empecé HH:MM — terminé HH:MM`
2. Los cuatro criterios, con sus números reales.
3. Qué decisiones tomaste que el contrato no fijaba, y por qué.
4. Qué NO has podido comprobar.

## Presupuesto

**Este es el UNICO encargo de la sesión.** A Nino le queda el 4 % de su cuota
semanal, así que no habrá un segundo. Dos consecuencias prácticas:

- Si algo del contrato te parece ambiguo, **elige y déjalo escrito** en
  `ENTREGA.md` en vez de parar a preguntar: no hay ida y vuelta.
- Si te quedas sin tiempo a medias, entrega **lo que tengas** con los criterios
  que SÍ hayas medido y di cuáles no. Media pieza documentada se integra; una
  pieza entera sin entregar no existe.
