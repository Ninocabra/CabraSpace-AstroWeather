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
