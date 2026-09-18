TIEMPO: empecé 09:04 — terminé 09:07

# Informe de Réplica Independiente: Calibración de WeatherNext 3

## Ficheros producidos
- `C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\gemini\wn_calib_replica.py`
- `C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\gemini\resultado_replica.json`

## Filtro de Datos
- **Filas válidas**: 6126 (lead_day entre 1 y 5 inclusive, ecmwf no vacío, 6 columnas wn_* finitas)
- **Noches únicas**: 174
- **Meses**: 7 (2026-02 a 2026-08)

## Tabla Global de Candidatos (Fuera de muestra, Leave-One-Month-Out)
| Candidato | n | Brier | BSS (vs Clim) | Log-Loss | AUC |
|---|---|---|---|---|---|
| **A_ecmwf** | 6126 | 0.12913 | 0.12292 | 0.40875 | 0.72971 |
| **B_wn** | 6126 | **0.12224** | **0.16970** | **0.38034** | **0.78935** |
| **C_wn_spread** | 6126 | 0.12270 | 0.16655 | 0.38099 | 0.78427 |
| **D_blend** | 6126 | 0.12279 | 0.16595 | 0.38160 | 0.78751 |
| **E_control** | 6126 | 0.12916 | 0.12273 | 0.40882 | 0.72943 |

*Climatología de referencia fuera de muestra: Brier_clim = 0.14722.*

## Bootstrap por Noches (2000 rondas, default_rng(11)) de la Diferencia de Brier
*(Negativo = el primer modelo es MEJOR)*

| Comparación | Valor Puntual | IC 95% (p2.5 - p97.5) | Frac. rondas < 0 | Conclusión |
|---|---|---|---|---|
| **B - A** | **-0.00689** | [-0.01262, -0.00128] | **0.9910** (99.1%) | B_wn supera contundentemente a A_ecmwf |
| **D - A** | **-0.00634** | [-0.01207, -0.00057] | **0.9845** (98.5%) | D_blend supera a A_ecmwf |
| **D - B** | **+0.00055** | [+0.00013, +0.00106] | **0.0030** (0.3%) | D_blend es PEOR que B_wn (añadir ECMWF resta precisión) |
| **C - B** | **+0.00046** | [-0.00071, +0.00172] | **0.2410** (24.1%) | C_wn_spread no mejora a B_wn |
| **E - A** | **+0.00003** | [-0.00001, +0.00008] | **0.0890** (8.9%) | Control negativo confirma nula aportación de ruido permutado |

## Coeficientes con todos los datos filtrados
- **B_wn**: `z = 3.68786 - 3.48501 * wn_mean - 0.05709 * L`
- **D_blend**: `z = 3.69864 - 3.67755 * wn_mean + 0.18864 * ecmwf - 0.05734 * L`

## Lectura de Resultados
1. WeatherNext 3 calibrado (`B_wn`) supera claramente y con alta significación estadística a ECMWF (`A_ecmwf`), reduciendo el Brier score en -0.00689 (IC 95%: [-0.01262, -0.00128], 99.1% de rondas bootstrap favorables), aumentando el BSS de 0.1229 a 0.1697 y el AUC de 0.7297 a 0.7893.
2. La combinación de ambos modelos (`D_blend`) no mejora a WeatherNext en solitario; al contrario, empeora el Brier score (`D-B` = +0.00055, IC 95%: [+0.00013, +0.00106], con únicamente un 0.3% de rondas bootstrap a favor del blend), lo que indica que ECMWF actúa como ruido colineal fuera de muestra.
3. Incorporar el spread ensamble (`C_wn_spread`) tampoco proporciona ventajas sobre la media directa (`C-B` = +0.00046, con solo un 24.1% de rondas favorables), mostrando que `wn_mean` concentra toda la señal efectiva de la predicción.
4. El control negativo (`E_control`) no supera a ECMWF (`E-A` = +0.00003, fracción < 0 del 8.9%), validando la solidez metodológica y la ausencia de filtración de datos en el diseño experimental.
