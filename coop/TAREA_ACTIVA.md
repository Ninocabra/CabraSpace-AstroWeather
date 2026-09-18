# TAREA ACTIVA — réplica independiente: curva de calibración de WeatherNext 3 (18-09-2026)

Repo: `C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace`
Escribes SOLO ficheros NUEVOS dentro de
`C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\gemini\`.
**Prohibido editar cualquier fichero existente del repo**, incluido el CSV de
entrada. Claude hace el mismo cálculo por su cuenta: tu trabajo es una RÉPLICA
INDEPENDIENTE, así que no busques ni leas scripts de Claude — solo este enunciado.

## Qué es

Queremos saber si WeatherNext 3 (nube total prevista) da mejores probabilidades de
cielo despejado que el ECMWF una vez CALIBRADO cada uno con su propia logística, y
si mezclar los dos mejora a ambos. Todo medido FUERA DE MUESTRA.

## Entrada (solo lectura)

`C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\datos\wn_calibracion.csv`

Una fila por (hora UTC con verdad de fotómetro, día de adelanto). Columnas:
- `hour_utc`, `night` (fecha de la noche, ISO), `month` (`YYYY-MM`), `lead_day` (0..6)
- `clear` — verdad: 1 despejado, 0 no
- `wn_mean`, `wn_p10`, `wn_p25`, `wn_p50`, `wn_p75`, `wn_p90` — nube total de WeatherNext en %, 0-100
- `ecmwf` — nube total del ECMWF (ifs025, pasada de N días antes) en %, vacío si no hay
- `wn_runs` — nº de pasadas promediadas (ignórala)

## Filtro (exacto)

Usa SOLO filas con `lead_day` entre 1 y 5 inclusive, `ecmwf` no vacío y las seis
columnas `wn_*` finitas. Informa cuántas filas, noches y meses quedan.

## Candidatos (logística por máxima verosimilitud, SIN regularización)

Nubes divididas entre 100. `L` = `lead_day`. `P(clear) = sigmoid(z)`:
- **A_ecmwf**: `z = a + b*ecmwf + c*L`
- **B_wn**: `z = a + b*wn_mean + c*L`
- **C_wn_spread**: `z = a + b*wn_mean + c*(wn_p90 - wn_p10) + d*L`
- **D_blend**: `z = a + b*wn_mean + c*ecmwf + d*L`
- **E_control**: igual que D pero con `wn_mean` PERMUTADO al azar entre filas
  (numpy `default_rng(0).permutation`, una vez, antes de la validación). Es el
  control negativo: no debería mejorar a A.

## Validación

Deja-un-mes-fuera: para cada `month`, ajusta con el resto de meses y predice el
mes excluido. Todo lo que se puntúa son predicciones FUERA DE MUESTRA juntadas.
Climatología de cada pliegue = tasa de `clear` en los meses de entrenamiento.

## Métricas (sobre las predicciones fuera de muestra juntadas)

Para cada candidato, global y por `lead_day`: `n`, Brier, BSS contra la
climatología (`1 - Brier/Brier_clim`), log-loss, AUC (con empates promediados).

Bootstrap POR NOCHES (se remuestrean noches enteras con reemplazo, 2000 rondas,
`default_rng(11)`) de la DIFERENCIA de Brier global: `B-A`, `D-A`, `D-B`, `C-B`,
`E-A`. Para cada una: valor puntual, percentiles 2,5 y 97,5, y fracción de rondas
< 0. (Negativo = el primero es MEJOR.)

Tabla de fiabilidad de B_wn y D_blend: 10 bins iguales de probabilidad prevista en
[0,1]; por bin con n >= 20: media prevista, frecuencia observada, n.

Coeficientes de B_wn y D_blend ajustados con TODOS los datos filtrados.

## Entrega

1. `...\coop\gemini\wn_calib_replica.py` — script autocontenido (numpy, scipy;
   pandas NO está instalado, no lo uses). Se ejecuta con
   `python wn_calib_replica.py` y escribe el JSON de abajo.
2. `...\coop\gemini\resultado_replica.json` con claves: `filtro` {filas, noches,
   meses}, `metricas` {candidato: {global: {...}, por_lead: {"1": {...}, ...}}},
   `bootstrap` {"B-A": {punto, p2_5, p97_5, frac_neg}, ...}, `fiabilidad`
   {B_wn: [[prevista, observada, n], ...], D_blend: [...]}, `coeficientes`.
3. En `...\coop\ENTREGA.md`: TIEMPO, ficheros, la tabla global de los cinco
   candidatos, el bootstrap, y tu lectura en 3-5 frases. Cada número tiene que
   salir de EJECUTAR el script; lo que no hayas ejecutado, "SIN COMPROBAR".

Escala de honestidad: una cifra que no salga de tu ejecución y se presente como
medida vale -3; decir "no lo he podido ejecutar" vale 0.
