# CabraSpace AstroWeather — Especificación normativa (v8)

> **Estado: VIGENTE.** Este documento contiene ÚNICAMENTE instrucciones en vigor: contratos de datos, física adoptada, invariantes, plan y decisiones. La historia (v1–v7, razonamientos, fórmulas descartadas, hallazgos de auditoría) vive en `CHANGELOG.md`; los ficheros v4–v7 se conservan como archivo. **Si algo contradice a este documento, manda este documento.**
>
> Auditado antes de programar: verificación empírica de Claude (26/27-08-2026, llamadas reales a todas las APIs), auditoría ChatGPT ×2 y Gemini ×1, con las afirmaciones de los auditores verificadas ejecutando. Marcas "(verificado)" = comprobado contra el servicio real.

---

# 0. IDENTIDAD DEL SITIO (contrato, verificado 27-08-2026)

| Campo | Valor | Nota |
|---|---|---|
| Sitio | AstroCamp, Nerpio (Albacete) — código MPC **I79** | |
| Coordenadas EXACTAS | **38.16583 N, −2.32672 W** | NUNCA redondear: con 38.16/−2.32 el DEM de Open-Meteo cambia de 1542 a 1302 m |
| `site_elevation` | **1650 m** (cota real) | |
| `model_grid_elevation` (ecmwf_ifs025) | **1067 m** (obtenida con `&elevation=nan`) | La cota REAL de la celda del modelo |
| `dem_elevation` (coords exactas) | 1542 m (downscaling por defecto de la API) | Solo informativo — no es ni el sitio ni la celda |
| **Δh para corrección de aerosol** | **1650 − 1067 = 583 m** | (v7 usaba 348 m: era DEM, no celda — corregido) |
| Presión típica del sitio | 830–845 hPa (estación local: 843 medidos) | |
| Regla de llamadas | **TODA llamada de variables de superficie a Open-Meteo lleva `&elevation=1650`** | Verificado: ajusta T2m (−0.7 °C), Td y P_sfc (836 hPa ≈ 843 reales) |

**Ventana de producto:** solo interesa la noche, de **ocaso−1h a orto+1h**. La UI, el score y el catálogo viven en esa ventana. La ingesta y el archivo cubren las 24 h (el CAPE de la tarde, la llegada de calima y "horas desde el ocaso" son predictores de la noche).

---

# 1. PRODUCTO

## 1.1 Tres salidas separadas + el recomendador

**El sistema NO publica un score único.** Publica:

1. **Gates de seguridad** (binarios, por hora): `safe_to_open = rain_ok AND gust_ok AND storm_ok AND hardware_ok`. La tormenta es riesgo ESCALONADO: 0–60 min → sensor de lluvia + radar observado + allsky; 1–6 h → prob. precip + convección; >6 h → ensemble de precipitación, con CAPE elevando riesgo (nunca cerrando por sí solo).
2. **P_usable** (probabilidad de cielo utilizable, por hora): `P_raw` del EPS (fracción de los 51 miembros con nubes < 20 %) → **calibrador logístico GLOBAL con lead time y estación como covariables** → `P_calibrada`. No estratificar el calibrador en celdas lead×estación hasta que las curvas de fiabilidad demuestren muestra suficiente (una hora observada = UN Bernoulli, no 51). La UI etiqueta SIEMPRE el estado: "calibrada N meses" / "probabilidad del modelo (sin calibrar)".
3. **Calidad científica por dimensión**, en unidades interpretables y con incertidumbre:
   - `q_dark = min(1, B_ref/B_sky)` — en FLUJO: "esta hora rinde el X % de la integración útil de una hora de referencia" (B_ref = fondo de referencia del sitio, mag 21.9).
   - `q_transp = 10^(−0.4·(k_total − k_ref))` capado a [0,1]; `k_ref` se aprende por sitio y banda (percentil 20 del k medido; arranque 0.18).
   - `q_seeing` = **tercil (favorable/normal/desfavorable) + rango** ("1.4–2.1″, experimental"). El arcosegundo puntual se calcula y archiva siempre, pero NO se publica hasta que el FWHM local demuestre MAE/sesgo fuera de muestra.
   - `q_cloud = ((100 − C_eff)/100)²` con el C_eff de §2.4.
   - `margen_rocio = T_equipo − Td` en °C, con signo (§2.6).
   - Agregado orientativo opcional: media geométrica ponderada `Q = exp[Σw·ln(max(q,ε))/Σw]` — presentación, jamás base de decisión.

**El recomendador de targets es el producto estrella** y publica DOS números por target y noche:
```
Q_cond(target)  = Σ_h Q(h)                          # horas-SNR-equivalentes SI SE PUEDE observar
E[Q](target)    = Σ_h P_usable(h) · Q(h)            # horas-SNR-equivalentes ESPERADAS
Q(h) = 10^(−0.8·k·(X_h−1)) · [B_ref/B_sky(alt_h,az_h)] · (FWHM_ref/(fwhm_cenital·X_h^0.6))^(2α)
# α por morfología: 0 = extensas (IFN), 0.5 = galaxias, 1 = compactas (PN, lentes, fotometría)
```
UI: «Para M51: 3,2 h equivalentes si despeja / 1,9 h esperadas». La parte CUANTITATIVA de contraste se aplica solo a la **lista curada (~50 objetos)** con fotometría homogénea; el resto del catálogo usa **categorías de dificultad** (`extremo_contraste / bajo_contraste / normal / compacto_resolucion / narrowband`) — el `SurfBr` de OpenNGC solo existe para galaxias y en banda B: no se compara con mag_sky V como si fuera universal.

**Dos MVPs con nombre** (nunca prometer el segundo con el primero):
- **MVP operacional** (rodajas 1–3): noche/Luna + nubes con P_usable + gates + techo. Responde "¿podré observar con seguridad?".
- **MVP recomendador** (al completar rodajas 4–6): añade transparencia, fondo y seeing → horas-SNR por target. Responde "¿qué merece la pena fotografiar?".

## 1.2 Catálogo de parámetros de cara al usuario

(Versión viva: `CATALOGO_PARAMETROS.md` en el repo. Cada parámetro publica: influencia, qué permite/impide, incertidumbre y estado de calibración. Un número sin su porqué no se publica.)

| # | Parámetro | Cómo influye | Fuente primaria | Backup | Verdad terreno |
|---|---|---|---|---|---|
| 1 | Noche útil y crepúsculos | El marco temporal de todo | astropy local | — | — |
| 2 | Luna (fase, altura, separación) | Cada mag de fondo = ×2.5 de tiempo; con Luna: NB sí (LUT por filtro), IFN no | astropy + K&S | — (+ SkyCalc como test) | TESS (en su banda) |
| 3 | Nubes (capas site-relative + niebla) | EL gate; cirros "20 %" matan fotometría | EPS ECMWF → P_calibrada; híbrido 9 km | ICON/GFS/AROME + CloudWatcher/allsky (0–1 h) | **CloudWatcher + allsky** (el TESS de AstroCamp NO sirve de detector de nubes: sus sensores de T no reportan — §3.1 nota 6); el TESS aporta el fondo, que sube con nube+LP |
| 4 | Lluvia / tormenta | Gate de seguridad escalonado | EPS precip | Radar observado (RainViewer) + sensor local | Estación local |
| 5 | Viento / rachas | >35–40 km/h cierre; 15–30 degrada guiado | Open-Meteo (rachas, `ms`) | 2 estaciones AstroCamp | Estación local |
| 6 | Calima + transparencia (AOD polvo, AOD total, α, k) | +0.1 AOD ≈ +0.1 mag y cielo lechoso; el humo castiga el azul; la Luna molesta MÁS con calima | AOD total: Open-Meteo AQ · AOD_dust: CAMS prefetch | GEOS-FP (perfil vertical) → heurística; BDRC solo si conceden credenciales | AERONET Granada + k de la allsky |
| 7 | Fondo de cielo (LP+zodiacal+airglow+twilight) | El presupuesto de oscuridad, mag_sky por hora | Modelo local §2.2 | — | TESS (en su banda) |
| 8 | Seeing (jet + capa límite + orografía) | Decide PN/dobles/lucky; irrelevante para IFN | Perfiles 0.25° → HV + features Ri | 7Timer/meteoblue SOLO contraste | Pipeline FWHM (Fase 0) |
| 9 | Rocío / escarcha | Margen lente–rocío en °C, accionable | CloudWatcher (predictor empírico) | Modelo Brunt (fallback) | Eventos de rocío + T_lente |
| 10 | Techo ("condiciones aptas para abrir") | El resumen ejecutivo | Compuesto §1.4 | — | Estado real de techos (asimétrico) |
| 11 | Extras (estelas, contrails, centelleo, nieve) | Flags informativos | CelesTrak/RH250/HV/snow | — | — |

## 1.3 El lienzo nocturno (interfaz)

- **El lienzo ES la noche**: eje X ocaso−1h → orto+1h; abajo la **silueta real de la sierra** (del DEM MDT05/SRTM, el mismo horizonte de 720 bins acimutales que usará el motor de targets).
- El cielo se pinta con mag_sky; el **arco real de la Luna** con su iluminación; **tres franjas de nubes** (baja/media/alta, más oscura cuanto más alta) cuya **opacidad/trama codifica la certeza** (P_calibrada); iconos: gota (precipitación), naranja/rojo con número (AOD), estrella emborronada (tercil seeing), termómetro (margen rocío), manga (racha), candado/cúpula (gates).
- **Cada icono es tocable** → "qué permite / qué impide ahora" + factor limitante de la hora.
- Reglas duras: seguridad inequívoca; **la incertidumbre SIEMPRE se pinta**; todo número lleva su porqué a un toque.
- **Antes del backend: mockup SVG con tres noches inventadas** (buena/regular/mala) para pactar el lenguaje visual.

## 1.4 Techo

Producto publicado: **«Probabilidad de condiciones aptas para abrir»** (no "se abrirá": el techo lo abren humanos). Modelo: gates + P_usable, **con la climatología mensual (tabla 2012–2026 de AstroCamp) como feature/intercepto — JAMÁS como multiplicador posterior de una probabilidad ya calibrada** (doble conteo del prior). Entrenamiento contra "cielo útil" medido por TESS/CloudWatcher; validación asimétrica contra el techo real (techo abierto ⇒ noche útil; cerrado ⇏ mala). El estado de techos automatiza además el flag `censored` (§4.4).

---

# 2. FÍSICA VIGENTE

## 2.1 Extinción y aerosol

```
m_obs = m_0 + k·X
k_total = k_Rayleigh + k_aerosol + k_ozono
k_Rayleigh(V) ≈ 0.1451·exp(−1650/7996) ≈ 0.118 ;  k_ozono ≈ 0.016 ;  k_aerosol ≈ 1.086·AOD_550
k(λ) ≈ 1.086·AOD_550·(λ/550)^(−α)          # α Ångström: polvo ≲0.5 (neutro), humo ≳1.5 (azul)
```

**Corrección de AOD por cota — por especies, con Δh = 583 m (§0):**
```
AOD_sitio = AOD_dust + (AOD_total − AOD_dust)·exp(−583/H_a)      # H_a inicial 1.7 km, calibrable
```
El polvo sahariano viaja en capas de 1,5–5 km: NUNCA aplicar la corrección al AOD total (diría "limpio" en plena calima). **Solución superior si GEOS-FP responde:** `τ_sitio(λ) = ∫_{1650}^{TOA} α_ext(λ,z) dz` desde su perfil 3D — elimina las dos suposiciones; la exponencial queda de fallback.

**Cadena de AOD_dust (en orden):** (1) CAMS ADS, prefetch diario asíncrono (forecast 120 h; jamás en el camino caliente); (2) **GEOS-FP** por OPeNDAP (verificado accesible sin registro); (3) heurística de emergencia: AOD_total alto + `dust`(superficie) y PM10 bajos ⇒ capa elevada ⇒ no corregir (falla en seguro). **BDRC (dust.aemet.es): catálogo THREDDS navegable pero descarga de datos con 401 en fileServer/OPeNDAP/NCSS (verificado) — solo entra en la cadena si se obtienen credenciales.** ¡La variable `dust` de Open-Meteo AQ es µg/m³ EN SUPERFICIE, jamás proxy de AOD de polvo (verificado)!

Alerta de calima: AOD_dust > 0.15 en cualquier hora de las próximas 48 h. Aerosol volcánico/estratosférico: offset de calibración de k, no forecast. Higroscopicidad: multiplicador empírico de q_transp con RH>80–85 %.

## 2.2 Fondo de cielo

```
B_sky(t,alt,az) = B_natural + B_zodiacal(Δλ_helioecl, β) + B_LP + B_moon_physical + B_twilight + B_airglow_lento
mag_sky = −2.5·log10( Σ 10^(−0.4·mag_i) )        # SIEMPRE suma en flujo
```

- **Luna — estructura de Krisciunas & Schaefer (1991) con la ley de dispersión MEDIDA aquí** (`moonscatter.py`). Se mantienen de K&S: `X(Z)=(1−0.96·sin²Z)^(−0.5)` acotada en 5.0 (no sustituir por Kasten-Young), la rama de aureola **ρ<10°: f(ρ)∝ρ⁻²** (reescalada para empalmar sin salto), el término del blanco y el acoplamiento con la k del §2.1 (con calima la Luna molesta más). **Cambia la función de dispersión**: la de Mauna Kea se queda corta aquí por un factor que va de 1,2 con la Luna baja a 5,5 con la Luna alta, y sobre 18.834 muestras de 1.246 noches de ajuste lo que se mide es una exponencial única, `f(θ) = 1,3612e7·10^(−θ/48,55°)`, con **suelo en la función publicada** (este sitio tiene más aire y más polvo que Mauna Kea: no puede dispersar menos). Fuera de 12°–80° el valor está extrapolado y se declara.
  - **DEROGADO el contrato «B_moon_physical = 0 bajo el horizonte»** (v8, 28-08-2026). Es falso y lo dice el archivo: con el centro de la Luna por debajo de −0,83° (ya invisible, contando refracción y semidiámetro) el cenit sigue **+0,19 mag más brillante a 5-7σ**, y decae siguiendo la altura de la sombra geométrica de la atmósfera (medido 102,9 / 87,2 / 66,1 / 24,6 / 11,0 % contra 99,2 / 93,4 / 69,0 / 46,8 / 22,6 % de la geometría pura, a 0,25 / 0,75 / 1,75 / 2,50 / 3,50° de depresión; control con Luna nueva: +0,009 mag). El antiguo `smoothing_UI` con escala inventada de 1,5° estaba tapando física real. Ahora es `moonscatter.beam_transmission`, es **física** y entra en mag_sky y en horas-SNR, y llega a cero a −6°, donde toda la columna de aire sobre el sitio está en sombra.
  - **Esto NO es luz parásita del instrumento**, y ese fue el diagnóstico equivocado durante un día: la luz parásita necesita el haz directo y se corta cuando la Tierra lo tapa. Sí es instrumental el caso **θ < 12°**, con la Luna dentro del campo del TESS-W (~17° FWHM): son 144 muestras de 131.096 y se excluyen de la verdad de nubes.
  - **Incertidumbre declarada:** el rms del ajuste por franjas es 3,8 %, pero la reproducibilidad **entre años a geometría fija es ±17 % (0,17 mag)**, y ésa es la cota honesta. Está dominada por la deriva del fotómetro (ver §2.2 bis), no por la física de la dispersión.
- **Twilight** (−18°…−12°): `B_twilight = B_18·10^(0.4·s·(−18−alt_sol))`, s≈0.4–0.5 calibrable con TESS. Si no está implementado: gate binario noche = Sol<−18°. (Objetivo: Patat 2006 / SkyCalc.)
- **Resolución espacial del suelo (28-08-2026):** el fondo natural se evalúa **en la dirección a la que se apunta**, no en el cenit. Hasta esta fecha `skybackground` solo calculaba el cenit (`alt=90`) y `targets.py` aplicaba ese único número a todos los objetos: medido sobre cuatro noches de 2026, eso cuesta **hasta 0,405 mag** sobre el cielo útil, contra los 0,17 mag de incertidumbre que declaramos. Sobre 600 pares objeto-hora de doce noches: corrección mediana 0,086 mag, p95 0,293, peor 0,369, y **9 veredictos de gate que cambian** — todos IFN en M81/M82, que a |b| ≈ 41° tiene un cielo más oscuro que el cenit y al que se le estaba **negando** el gate de 21,3 con un 21,28 cenital cuando su cielo real es 21,59. El fallo descartaba horas buenas, no recomendaba malas.
  - **No hace falta rejilla.** Los dos términos son analíticos: el lunar tiene gradiente casi constante (0,051 mag/grado entre 15° y 60°, o sea lineal e interpolable), y el suelo es tan suave que con 27° de espaciado el error de interpolación sería 0,05 mag. Se evalúa en el punto exacto, que es más barato y exacto que cualquier malla. La rejilla solo hace falta para **pintar** la cúpula.
  - **Hueco declarado:** falta la dependencia con la MASA DE AIRE del propio fondo (ley de van Rhijn del airglow y columna creciente hacia el horizonte). El producto solo recomienda por encima de 25°, donde son un par de décimas, pero no está en el modelo y no puede citarse como si lo estuviera.
- **Zodiacal:** tablas de Leinert 1998 interpoladas (elongación Y β).
- **Airglow:** componente LENTA — F10.7 en medias de 27/81 días + estacionalidad, con incertidumbre; **no es predictor horario** y no entra en el forecast fino del MVP; el TESS captura el residuo nocturno.
- **LP:** para Nerpio, constante del sitio (~21.9, TESS manda). Direccional B_LP(alt,az): OBJETIVO pendiente (Garstang / diseño tipo APEX con horizonte 720 bins); hasta entonces, cenit + término de nubes: `B_LP_efectiva = B_LP·[1 + γ·c_frac·(R_100km/R_ref)]` con γ de signo dependiente de cota (espejo si nube encima / escudo si mar de nubes debajo), calibrado con TESS — **este γ se pospone hasta tener dataset etiquetado; no entra en el MVP**.
- **Conversiones** (verificadas contra lightpollutionmap): `B[nL]=34.08·exp(20.7233−0.92104·V)`; SQM=−2.5·log10(L_mcd/108e6); NELM Bartels.

### 2.2 bis — PROBLEMA ABIERTO: el suelo del cielo deriva (28-08-2026)

Encontrado al medir la reproducibilidad de la ley lunar, y es más grande que
ella. **La mediana del cielo sin Luna sube +0,56 mag en siete años**, de forma
monótona:

| año | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|
| mediana sin Luna | 21,32 | 21,29 | 21,30 | 21,43 | 21,44 | 21,52 | 21,69 | 21,88 |

Robusto a todos los controles que le he pasado: solo verano +0,51, solo invierno
+0,59, sujetando el suelo modelado (mismo zodiacal y misma Vía Láctea) +0,37, y
el p95 de las noches más oscuras +0,53. **El ciclo solar predice el signo
contrario**: 2019 fue mínimo y 2025 máximo, y más actividad significa más
airglow, o sea cielo más brillante.

Consecuencias que ya tocan a lo publicado:

1. `NATURAL_FLOOR_MAG = 21.71` es la media de siete años y **no describe ningún
   año**: sobra 0,4 mag en 2019 y falta 0,17 en 2026.
2. Todo lo derivado del residuo hereda un sesgo que depende del año, incluidos
   el centro y la tolerancia del test de nivel.
3. Es lo que domina la incertidumbre de ±17 % de la ley lunar.

**NO resuelto**: si es el punto cero del fotómetro (un TESS-W envejece y lee más
oscuro) o el cielo. El control que lo separaría —el contraste de la Vía Láctea,
invariante ante un cambio de ganancia— sale también cayendo (0,50 → 0,25 mag),
lo que no encaja con un punto cero puro; pero ese control usa un proxy burdo de
latitud galáctica y no me apoyo en él. **Necesita sesión propia y probablemente
una referencia independiente del TESS.**

## 2.3 Bandas fotométricas (invariante)

Tres representaciones internas: `sky_V_mag_arcsec2` (física), `sky_sensor_predicted(bandpass_id)` (lo que debería leer TESS/SQM — **TESS ≠ SQM: bandas distintas**), `sky_electron_rate(filter,camera)` (planificación → horas-SNR). La magnitud única es solo UI. **Toda comparación modelo↔sensor se hace en la banda del sensor.** Factores lunares de banda estrecha: **LUT generada con SkyCalc** en `fase × separación × alturas × PWV × filtro` — **SkyCalc NO expone parámetro AOD** (auditado): el eje de aerosol NO va en la LUT; la corrección de aerosol se aplica aparte vía k (§2.1). Fallback etiquetado `approximation_quality=low`: Ha 3nm≈0.05 · OIII≈0.20 · SII≈0.08.

## 2.4 Nubes

- **Semántica por proveedor (crítico a 1650 m):** las capas de ECMWF vía Open-Meteo se derivan de RH en niveles fijos (low: 1000/925/850; mid: 700/500; high: 300/250/200). En Nerpio los niveles de "low" están al nivel del sitio o BAJO TIERRA → **reconstruir capas site-relative excluyendo niveles ≤ P_sitio−25 hPa** (el mismo filtro de §2.5), por proveedor.
- `C_eff = 1 − Π(1−c_i)` (solape aleatorio) es un **candidato, no un axioma**: correr en sombra contra `cloud_cover_total` nativo y contra la versión site-relative, y quedarse con el que gane en Brier contra la allsky. Caso "mar de nubes" (low alto + BLH < sitio + inversión + allsky despejada): las nubes bajas NO bloquean — puntúa como despejado con flag.
- Niebla: RH>95 % + calma + depresión de rocío <1 °C.

## 2.5 Seeing

```
r0 = [0.423·(2π/λ)²·sec(z)·∫Cn²dh]^(−3/5) ;  ε = 0.98·λ/r0 ;  ε(z) = ε_cenital·X^0.6 ;  ε(λ) ∝ λ^(−1/5)
θ0 = [2.914·(2π/λ)²·sec(z)^(8/3)·∫Cn²·h^(5/3)dh]^(−3/5)
τ0 = 0.314·r0/v_eff ;   v_eff = [∫Cn²·V^(5/3)dh / ∫Cn²dh]^(3/5)     # jamás media simple/rms/V250

Cn²(h) = 5.94e−53·(W/27)²·h^10·e^(−h/1000) + 2.7e−16·e^(−h/1500) + A·e^(−h/100)
# CONTRATO: h es ALTURA SOBRE EL SITIO (AGL): h = z_geopotencial − 1650. Test obligatorio en
#   hv_profile.py con perfil sintético (el sanity-check de referencia: W=27, A=1.7e−14 → ε=2.07″).
W = [Σ V_i²·Δh_i / Σ Δh_i]^(1/2)  sobre 500…100 hPa, ponderada por espesor real (geopotencial)
# A: EL parámetro a calibrar con FWHM (HV5/7=1.7e−14; sitio bueno 1–2 órdenes menos).
# Challenger en sombra 1: término BL con escala dinámica A·e^(−h/H_bl), H_bl = max(50, BLH_ecmwf9km)
# Challenger en sombra 2: Cn²·β(Ri_bulk) [3.0/1.8/1.0]
```

- **Richardson = features + flag, no física publicada:** `ri_min`, `frac_capas_ri<0.5`, `max_shear` (cizalladura VECTORIAL: U,V desde speed+dir), `viento_cruzado_cordillera` → `orographic_turbulence_risk = low/medium/high`. Δz de geopotencial real. El umbral 0.25 es del Ri gradiente; cualquier umbral bulk es heurístico.
- Filtro de niveles subterráneos: `p ≤ P_sitio − 25 hPa` con P_sitio de `surface_pressure` pedida con `&elevation=1650` (verificado ≈836 hPa) → primer nivel útil 800 hPa. Sanity: barométrica sobre pressure_msl.
- Vientos SIEMPRE con `&wind_speed_unit=ms` + assert (V250>90 ⇒ llegó km/h).
- Estimador de dos términos (v1) y escalera Dewan/Trinquet-Vernin: vigentes como en v7 §1.4.3, con ΔT_conv = (T2m−T700) − 9.8·Δz_km.
- **Publicación: tercil + rango** (§1.1). El GBM (Bucle 5) es el challenger final del HV, no al revés.

## 2.6 Rocío

- **Primario (Nerpio): el CloudWatcher como predictor EMPÍRICO** — su lectura es la ventana IR 8–14 µm (con corrección propietaria del fabricante), NO la temperatura radiativa integral: antes de usarla, **identificar qué campo exacto expone cloud.html** (IR bruto / Ts−Ta / corregida), archivarlo con esa semántica, y calibrar una regresión contra eventos de rocío reales + T_lente medida.
- **Fallback (sitios sin sensor):** modelo radiativo Brunt: `ε_clear=0.605+0.048√e`; `ε_sky=ε_clear+(1−ε_clear)·0.85·c_frac`; `ΔT_rad=ΔT_max·[(1−ε_sky)/(1−ε_ref)]/(1+κ·V10^0.8)`; ΔT_max≈4 °C expuesta/2 °C cerrada, κ≈0.15–0.20 — todos calibrables, etiquetado como estimación.
- **Salida:** `margen_rocio = T_equipo − Td` en °C con evolución horaria; escarcha si T_equipo<0; `dew_heater=true` recalcula con ΔT_max reducido. Td por Magnus-Sonntag (17.62/243.12).

---

# 3. CONTRATOS DE DATOS (por fuente — solo lo vigente)

## 3.1 Llamadas canónicas (verificadas)

```
# 1) Superficie + nubes + BLH — ECMWF 9 km, downscaling a cota real:
https://api.open-meteo.com/v1/forecast?latitude=38.16583&longitude=-2.32672&elevation=1650
  &hourly=cloud_cover_low,cloud_cover_mid,cloud_cover_high,temperature_2m,relative_humidity_2m,
  dew_point_2m,wind_speed_10m,wind_gusts_10m,precipitation_probability,cape,boundary_layer_height,
  surface_pressure,pressure_msl&models=ecmwf_ifs&wind_speed_unit=ms&timezone=UTC

# 2) Perfiles (seeing/Ri) — 0.25° (el 9 km NO tiene niveles de presión, verificado) + multi-modelo:
https://api.open-meteo.com/v1/forecast?latitude=38.16583&longitude=-2.32672&elevation=1650
  &hourly=temperature_850hPa,temperature_700hPa,temperature_500hPa,wind_speed_500hPa,wind_speed_300hPa,
  wind_speed_250hPa,wind_speed_200hPa,wind_direction_700hPa,wind_direction_500hPa,
  geopotential_height_700hPa,geopotential_height_500hPa,geopotential_height_300hPa
  &models=ecmwf_ifs025,icon_seamless,gfs_seamless,meteofrance_seamless&wind_speed_unit=ms&timezone=UTC

# 3) EPS — ARCHIVAR LOS 51 MIEMBROS EN CADA DESCARGA (retención upstream ~4–5 días, verificado;
#    lo no archivado es irrecuperable). Guardar native_timestep e interpolated:
https://ensemble-api.open-meteo.com/v1/ensemble?latitude=38.16583&longitude=-2.32672
  &hourly=cloud_cover,precipitation&models=ecmwf_ifs025&timezone=UTC
#    (el dominio EPS 'ecmwf_ifs' 9 km existe pero HOY no sirve cloud_cover en Nerpio — 0 valores,
#     verificado 27-08-2026; re-probar en sombra periódicamente)

# 4) Calidad del aire (AOD TOTAL; 'dust' es superficie µg/m³ — solo confirmación secundaria):
https://air-quality-api.open-meteo.com/v1/air-quality?latitude=38.16583&longitude=-2.32672
  &hourly=aerosol_optical_depth,dust,pm10,pm2_5&timezone=UTC
#    Resolución CAMS: Europa ~11 km / global ~45 km (dominios distintos — archivar source_domain)

# 5) Backfill determinista por lead (≥2,5 años ecmwf_ifs025 verificado; profundidad varía por modelo):
https://previous-runs-api.open-meteo.com/v1/forecast?...&hourly=temperature_2m_previous_day1,..._day3...
#    Historical-Forecast API = serie de leads cortos ("mejor estimación"), NO el forecast emitido.

# 6) TESS stars289 (y cualquier estación de la red, ~250–300 vivas de 658):
POST https://tess.dashboards.stars4all.eu/api/tsdb/query   {datasourceId:2, InfluxQL sobre
  mqtt_consumer, name='stars289'}  → campos mag, tamb, tsky. Filtros: frescura <15 min
  Y rango 14≤mag≤23. Coordenadas: tags del dashboard (geocodificar) o catálogo tessdb.
#    VERIFICADO 27-08-2026 al implementar el worker:
#    · Histórico desde 2019 (~400–500k muestras/año, cadencia ~1/min). Volumen manejable.
#    · **stars289 publica tamb=0 y tsky=0 SIEMPRE: sus sensores de temperatura no reportan**
#      (comprobado contra stars1/stars4/stars1000, que sí dan valores reales). Corolario que
#      CORRIGE la suposición de versiones anteriores: **el TESS de AstroCamp da brillo de
#      cielo, NO detección de nubes por IR** — el índice `100−3·(tamb−tsky)` daría un "nublado"
#      constante. La señal de nubes sale de los CloudWatchers de cloud.html. El worker trata
#      el doble cero como dato ausente (nunca fabrica un índice a partir de ceros).
#    · Física validada: mediana nocturna de julio-2026 = **21.8–22.0 mag/arcsec²**, que
#      confirma el `B_ref = 21.9` del sitio (§0) medido, no supuesto.

# 7) cloud.html (scraper, sin SLA): 2 CloudWatchers, 2 estaciones (P real), techos S1R1…S2R4,
#    tabla de horas abiertas 2012–2026. Filtrar por frescura de timestamp, no por posición.

# 8) Sondeos Murcia 08430: https://weather.uwyo.edu/wsgi/sounding?...&type=TEXT:CSV&src=BUFR
#    (el cgi-bin viejo está muerto). Bulk: IGRA v2.
# 9) GEOS-FP: portal NCCS + OPeNDAP, sin registro (verificado) — especies de aerosol 3D.
# 10) SWPC: f107_cm_flux.json (componente lenta airglow) + Kp opcional.
# 11) SkyCalc (skycalc_cli): oráculo de regresión de moonsky/fondo. SIN parámetro AOD.
# 12) RainViewer: SOLO radar observado (nowcast discontinuado 1-1-2026, verificado: 0 frames).
#    Licencia superviviente personal/educativa — revisar antes de uso comercial. Nowcast propio
#    por advección/optical-flow es tarea nuestra (Fase 3).
# 13) CelesTrak TLEs; 14) OpenNGC (SurfBr: SOLO galaxias, banda B).
# 15) AEMET OpenData — VERIFICADO con clave 27-08-2026 (clave caduca 2026-12-05, avisar el 21-11):
#    · `/observacion/convencional/todas` → horario pero SOLO últimas ~12 h (9776 registros,
#      variables ta/hr/tpr/pres/prec/vv/dv). NO hay archivo horario histórico → confirmada la
#      objeción de auditoría: el MOS horario NO se apoya en AEMET (se apoya en TESS, 2019→).
#    · `/valores/climatologicos/diarios/datos/fechaini/{ISO}UTC/fechafin/{ISO}UTC/estacion/{id}`
#      → histórico DIARIO sí (probado marzo-2024): tmed/tmin/tmax/hrMedia/prec + horas de extremos.
#      Sirve para climatología y validación diaria, no para MOS horario.
#    · **Estación de oro: 7067Y "Cañadas del Nerpio", a 11,5 km y 1485 m** (cota casi idéntica a
#      AstroCamp) — la referencia externa natural del sitio. Otras: 7072Y Sege (14,6 km, 960 m),
#      7066Y Embalse de la Fuensanta (27 km), 7080X Benizar (32 km).
#    · Flujo en 2 pasos: la primera llamada devuelve {"datos": url} temporal; la segunda trae el
#      JSON (encoding latin-1). Clave en `.env` (nunca al repo).
# 16) Atlas Falchi (CC BY-NC) / atlas Lorenz (LICENCIA COMERCIAL NO CONFIRMADA — solo benchmark
#    hasta tener permiso escrito) / DarkSkySites (endpoint sin contrato — solo benchmark).
# 17) ESO Ambient Database (Paranal DIMM): banco externo para validar el estimador HV.
# 18) DEM: IGN MDT05 (España) / SRTM — horizonte 720 bins; OpenTopoData solo prototipo.
```

## 3.2 Invariantes de ingesta (asserts obligatorios)

1. Vientos en m/s (V250>90 ⇒ error de unidades). 2. `elevation=1650` presente en llamadas de superficie. 3. Miembros EPS: 51 series o alerta. 4. Sensores locales: descartar por frescura y rango físico. 5. Metadatos por registro: `provider_model_version, provider_grid_lat/lon, provider_elevation (las 3 cotas de §0), native_resolution_km, native_timestep, interpolated, retrieved_at, license_tag, quality_flags, pipeline_version`. 6. El raw nunca se sobrescribe.

## 3.3 Ficha de fuente (obligatoria antes de integrar cualquier fuente nueva)

Qué significa EXACTAMENTE el dato según el proveedor · retención/histórico · resolución y timestep nativos · licencia comercial · SLA/mantenedor · verificación con llamada real archivada en el repo.

---

# 4. APRENDIZAJE

## 4.1 Almacenamiento (dos niveles)

- **RAW**: respuesta original comprimida y versionada. Nunca se toca.
- **CANÓNICO** (Parquet ancho + DuckDB; partición Hive site/año/mes/modelo): una fila por `site × model × run × valid × member` con columnas tipadas + metadatos de §3.2.
- **daily_observations** (v8.1): observaciones DIARIAS de estaciones externas (AEMET). Tabla propia y no dentro de `measurements`: una media diaria no es la misma clase de cosa que una lectura instantánea, y mezclarlas invita a compararlas sin darse cuenta. Cada fila describe su estación (`station_id, station_name, station_elevation, station_distance_km`) y lleva `quality_flags='external_station;<km>;daily_aggregate'` — quien la cruce con datos del sitio está obligado a ver que viene de 11,5 km y 165 m más abajo.
- **measurements**: `site_id, t_utc, fwhm_zenith_500nm, setup_id, sky_mag_raw, instrument_type, instrument_id, bandpass_id, sky_mag_calibrated, clear_flag, cloud_frac, t_sky_ir (con semántica del campo), k_extinction, t2m, rh, wind, gust, roof_open, censored, quality_flags` — **`setup_id` OBLIGATORIO en cada FWHM** (tabla de configuración instrumental versionada: telescopio, cámara, binning, escala, filtro, foco). Sin él, un cambio de tren óptico parece un cambio de atmósfera.

## 4.2 Los cinco bucles (correcciones vigentes)

1. **Corrección AR "inspirada en Masciadri"** (el paper usa P=50 a cadencia 1-min con ajuste multi-noche; nuestra versión p≈2–4/60–120 min es una simplificación que debe validarse por walk-forward, con la réplica P=50 como challenger). **Solo para variables inerciales** (FWHM, SQM sin Luna, T); las nubes van por extrapolación cinemática (allsky/satélite), nunca por AR.
2. **MOS** por variable/modelo/lead: backfill determinista de Previous Runs + **etiquetas históricas del TESS (2019→) para nubes/cielo** — no de AEMET (sin archivo horario largo en el endpoint conocido); para T/viento superficie, ERA5-Land como pseudo-observación (declarando que es reanálisis) o esperar a la serie propia. Comparar siempre contra corrección nula.
3. **Calibración física mensual**: A del HV (y challengers BL-dinámico y β-Ri en sombra), gates de targets contra sesiones reales, k_ref por percentil.
4. **Pesos del ensemble** estacionales por Brier — solo determinista hasta acumular archivo propio de miembros.
5. **GBM del sitio** (LightGBM) con lags: validación **walk-forward Y estratificada por régimen** (frente estratiforme vs convectivo vs anticiclón — el promedio esconde el fallo en transiciones); `provider_model_version` como feature categórica + decaimiento temporal de pesos de muestra (los ciclos del IFS rompen relaciones aprendidas en silencio).

## 4.3 Verificación pública

Panel por fuente/variable/lead/mes contra **persistencia y climatología** (baseline de noche útil: la tabla de horas de cúpula 2012–2026 + climatología TESS). Nubes: Brier + fiabilidad. Seeing: **terciles en público**, MAE interno. Fondo: MAE contra TESS **en la banda del TESS**.

## 4.4 Higiene

Champion/challenger para TODO cambio de modelo (≥1 mes en sombra). Registro de modelos versionado. Deriva de ciclos NWP y de sensores (noches patrón). Filas sucias fuera del entrenamiento. **Censura**: registrar cada noche el motivo de no-observación (techos + TESS miran aunque no haya nadie); evaluar solo sobre verdad terreno independiente de la decisión de observar. **FWHM: filtro de excentricidad** (viento/guiado/bisinfín producen estrellas elípticas; el seeing, redondas) + percentil bajo por franja + suelo instrumental por `setup_id`. FWHM de allsky: solo indicador relativo, jamás seeing absoluto.

---

# 5. ARQUITECTURA

```
PROVEEDORES → RAW ARCHIVE → NORMALIZACIÓN → QC → MODELOS FÍSICOS → CALIBRACIÓN LOCAL → PRODUCTOS
```
- Cada predicción conserva `raw / derived_physics / locally_calibrated / observation / verification_error`.
- Stack v1: **FastAPI + APScheduler + SQLite (caché 72 h) + Parquet/DuckDB + filesystem/R2**. Sin Redis/Celery hasta concurrencia real. SDK `openmeteo-requests` para la ingesta.
- Workers: `fetch_openmeteo` (llamadas 1+2), `fetch_ensemble` (archiva miembros), `fetch_airquality`, `fetch_dust_aod` (CAMS/GEOS-FP diario), `fetch_swpc`, `fetch_tess`, `scrape_cloudhtml`, `fwhm_pipeline`, `verify_daily`.
- API: `GET /api/v1/astroweather` (horario: gates + P + q + desglose), `/tonight` (targets: Q_cond y E[Q] + veredictos con motivo), `/verification` (panel público).
- **Repo separado `CabraSpace-AstroWeather`** (motor+API+espec); la página que sustituye a Astrofotografía → Astro Forecast vive en CabraSpace-Web y solo consume la API (no se fusiona hasta que sirva en real).
- Licencias: auditoría formal ANTES de comercializar — Open-Meteo (Professional ~99 €/mes a confirmar), EUMETSAT L1<1h (fuera del MVP hasta aclarar por escrito), RainViewer (personal/educativo), Lorenz y DarkSkySites (sin confirmar), permiso escrito de AstroCamp/STARS4ALL para uso continuado de sus endpoints. Presupuesto: prototipo ≈10–45 €/mes; comercial ≈125–210 €/mes; el coste real es construir y verificar modelos, no la infraestructura.

---

# 6. PLAN

## 6.1 Fase 0 — instrumentación (semana 1, antes de cualquier frontend)

1. ✅ Esquema §4.1 versionado + raw archive. 2. ✅ Workers base con invariantes §3.2: `fetch_ensemble` (**miembros EPS desde la primera ejecución**) y `fetch_forecast` (híbrido 9 km + perfiles 4 modelos). 3. Backfill determinista (Previous Runs). 4. `fetch_tess` (histórico ~7 años → Parquet) + scraper cloud.html. 5. **Pipeline FWHM** (FITS → estrellas → FWHM robusta + excentricidad → plate-solve → cenit/λ → QC → Parquet, con `setup_id`) — cada noche sin él es irrecuperable. 6. Panel de verificación mínimo. 7. Verificaciones pendientes: ✅ AEMET (hecha: sin histórico horario, con estación 7067Y a 11,5 km), credenciales BDRC, campo exacto del CloudWatcher en cloud.html, semántica de capas de nubes por proveedor contra allsky.

**Deuda con fecha: la clave de AEMET caduca el 2026-12-05** — regenerar en opendata.aemet.es y avisar a Nino el **2026-11-21**.

## 6.2 Rodajas (cada una con ficha de fuente + backup probado apagando la primaria + lane visual + enchufe al aprendizaje + tests; DoD: pintado con datos reales + backup demostrado + verificación en el panel)

1. **Noche + Luna** → 2. **Nubes** (P_raw→P_calibrada; capas site-relative) → 3. **Lluvia + viento + techo** (= **MVP operacional**) → 4. **Calima + transparencia** (rodaja fusionada) → 5. **Fondo de cielo** (validación SkyCalc) → 6. **Seeing** (= **MVP recomendador**, con horas-SNR) → 7. **Rocío** → 8. Extras (flags).

## 6.3 Entregables inmediatos

1. Repo + esta spec + CHANGELOG. 2. `CATALOGO_PARAMETROS.md`. 3. Mockup SVG (3 noches). 4. Fase 0. 5. Rodaja 1.

---

# 7. DECISIONES (registro resumido; detalle en CHANGELOG)

1. Producto = tres salidas + recomendador con E[Q]; score único eliminado. 2. Nerpio primero; la app mundial es un segundo producto con sus propias garantías (no condiciona el MVP). 3. ECMWF híbrido 9 km/0.25°. 4. Física analítica como champion día-1; ML como challenger (nunca al revés hasta que gane). 5. Sensores locales mandan sobre estimaciones (rocío, nubes 0–1 h, fondo). 6. Todo lo irrecuperable se archiva desde el día 1 (miembros EPS, FWHM, sensores). 7. Publicar incertidumbre y estado de calibración siempre; terciles antes que decimales. 8. Especificación corta y vigente; historia en CHANGELOG; el autor no audita su propio documento (auditoría cruzada + verificación empírica antes de cada versión mayor).

## 7.1 Dónde vive el producto (decidido 29-08-2026)

**El producto se publica en un repositorio de datos PÚBLICO propio**,
`Ninocabra/CabraSpace-AstroWeather-Data`, y el motor sigue privado. La URL
estable es
`https://raw.githubusercontent.com/Ninocabra/CabraSpace-AstroWeather-Data/main/noche.json`,
que sirve con `access-control-allow-origin: *` — o sea, legible desde
cualquier página sin servidor, sin VPS y sin CORS (verificado: HTTP 200,
`max-age=300`).

Se descartaron las otras dos: commitear el JSON en el repo de la web funcionaba
hoy pero mete en producción un fichero que cambia cada 6 h, y un asset de
Release es público pero con URL incómoda. Ésta además **desbloquea el scheduler
24/7**: si el producto vive en su propio repo, quien lo genera puede ser
cualquier máquina.

- **El historial es el `git log`.** El fichero se sobrescribe SIEMPRE en la
  misma ruta, así que el log de `noche.json` es el registro diffeable de lo que
  dijimos y cuándo. Una previsión que se puede reescribir sin dejar rastro no
  se puede verificar, y verificarnos es medio proyecto.
- **Un commit por CAMBIO, no por pasada.** `generado_utc` cambia siempre; si
  contara para el diff, el historial sería un latido de cuatro commits diarios
  diciendo lo mismo. Se compara la previsión sin él.
- **El raw de terceros NO se republica.** Lo que sale es nuestra previsión.
  AstroCamp, STARS4ALL, AEMET, Open-Meteo y CAMS tienen cada uno sus
  condiciones, y §9 deja escrito que el permiso por escrito de AstroCamp y
  STARS4ALL para uso continuado sigue **pendiente**. Publicar sus series
  crudas es una decisión aparte y no está tomada.
- **Licencia de lo nuestro:** CC BY 4.0, con la atribución de las fuentes de
  partida dentro del propio JSON y en el README del repo de datos.

# 8. REFERENCIAS ESENCIALES

Krisciunas & Schaefer 1991 (PASP 103) · Falchi+ 2016 · Hufnagel-Valley (Roddier 1981) · Masciadri+ 2020 (MNRAS 492, AR) · Lyman+ 2020 (Maunakea WRF) · Milli+ 2019 (nowcasting ML Paranal) · Noll+ 2012 / Jones+ 2013 (SkyCalc) · Leinert+ 1998 (zodiacal) · Patat 2008 (airglow) · Kyba+ 2011 (nubes×LP) · Brunt 1932 (emisividad) · Stull 1988 (Ri) · Prospero & Carlson 1972 (SAL) · Giles+ 2019 (AERONET v3) · Durre+ 2018 (IGRA). Lista completa y URLs: v7 §8.
