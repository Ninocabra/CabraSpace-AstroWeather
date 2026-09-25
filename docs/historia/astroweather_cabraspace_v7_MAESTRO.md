# CabraSpace AstroWeather — DOCUMENTO MAESTRO (v7): especificación técnica + plan de implementación
## Sistema de predicción y evaluación de condiciones para observación de cielo profundo
### Localización de referencia: AstroCamp, Nerpio (Albacete) — 38.16° N, 2.32° W, ~1650 m — extrapolable a cualquier lat/lon

> **SUSTITUIDO (27-08-2026): la especificacion vigente es `astroweather_cabraspace_v8_SPEC.md` (+ `CHANGELOG.md`). Este fichero queda como archivo historico — contiene texto normativo obsoleto detectado en auditoria; NO implementar desde aqui.**
>
> **v7 — DOCUMENTO MAESTRO para auditoría cruzada (27-08-2026):** consolida la especificación v6 (íntegra, capítulos 1–8) con el **plan de implementación (§9)** acordado: catálogo de parámetros de cara al usuario, desarrollo por **rodajas verticales** sobre un **tronco común de datos** (Fase 0), el trío *fuente fiable + backup + aprendizaje* por parámetro, la especificación del **sistema visual** (lienzo nocturno sobre la orografía de AstroCamp), la **predicción de apertura del techo**, y la regla *"se muestra la noche, se archiva el día"*. Añade la Guía para el auditor externo (§0). **Este documento es la base de todo lo demás: se somete a auditoría cruzada (Gemini + ChatGPT) y se cierran las objeciones ANTES de escribir código.**
>
> **v6 — rediseño de producto tras auditoría externa cruzada (ChatGPT, 27-08-2026), contrastada y verificada empíricamente ANTES de aplicar (las afirmaciones de la auditoría se comprobaron contra las APIs reales; dos se confirmaron, una se corrigió):**
>
> *Correcciones factuales (verificadas 27-08-2026):*
> 1. **§6 corregido:** el Historical Forecast de Open-Meteo es una **serie continua construida con los leads cortos** de pasadas sucesivas, NO "el forecast tal como se emitió". El backfill por lead time real es **Previous Runs** (verificado: ≥2,5 años para ecmwf_ifs025, day-3 completo en 2024-03/2025-08/2026-06). Y **los miembros del EPS solo se conservan ~4–5 días** (verificado: member25 nulo hace 5 días, con dato hace 2) → **los 51 valores crudos se archivan desde la primera ejecución; no son reconstruibles**.
> 2. La "probabilidad real de despejado" pasa a llamarse **probabilidad BRUTA del ensemble** (P_raw); se añade el módulo de **calibración** (isotónica o logística, por lead time y estación, contra allsky/TESS) → P_calibrada, evaluada con Brier y curva de fiabilidad. Metadatos `native_timestep` / `interpolated` obligatorios.
> 3. **ECMWF en modo híbrido (verificado):** `ecmwf_ifs` (~9 km) existe en Open-Meteo y **sirve BLH** (¡resuelve el hueco "BLH solo GFS"!) pero **NO tiene niveles de presión** → 9 km para nubes/superficie/BLH + `ecmwf_ifs025` para perfiles de seeing. (La auditoría proponía migrar todo al 9 km; eso rompería el pipeline de seeing.)
> 4. **v_eff definido** (faltaba): `v_eff = [∫Cn²·V^{5/3}dh / ∫Cn²dh]^{3/5}` — ponderado por el MISMO perfil de turbulencia de r0; jamás media simple, rms ni V250.
>
> *Rediseño de producto:*
> 5. **El score único 0–100 desaparece como salida principal.** Tres salidas: **gates de seguridad** (binarios) · **P_usable** (probabilidad calibrada de poder observar) · **calidad científica por dimensión con incertidumbre**. Las **horas-SNR-equivalentes por target (§3.3.3b) pasan a ser EL producto central** («qué merece la pena fotografiar esta noche y con qué eficiencia»). f_dark trabaja en flujo (interpretable como "% de integración útil"); CAPE eleva riesgo, no cierra por sí solo.
> 6. **Separación de bandas fotométricas (§1.3.5 nueva):** V, banda del sensor (TESS ≠ SQM) y tasa de electrones por filtro/cámara como representaciones internas; la magnitud única queda solo para la UI.
> 7. **Seeing sin precisión falsa:** se publica tercil (favorable/normal/desfavorable) + rango experimental; el arcosegundo puntual solo cuando el FWHM local demuestre MAE/sesgo. Los **β de Richardson salen del núcleo físico**: Ri se publica como *features* + flag `orographic_turbulence_risk`; la variante multiplicativa queda como **challenger en sombra** (§6.4).
> 8. **Luna:** B_moon físico = 0 bajo el horizonte; el rolloff −6°…0° se reclasifica como `smoothing_UI`, no física. Factores lunares NB fijos → **LUT generada con SkyCalc** (fase × separación × alturas × AOD × filtro); los fijos quedan como fallback `approximation_quality=low`.
> 9. **Rocío:** T_sky MEDIDA (CloudWatcher) como entrada primaria y el modelo radiativo como fallback; la salida pasa de "riesgo 0.6" a **"margen lente–rocío = +X °C"**, con `dew_heater=true/false` configurable por usuario.
> 10. **Almacenamiento en dos niveles:** raw comprimido y versionado (respuesta original del proveedor, nunca se sobrescribe) + tabla **canónica ANCHA** tipada (una fila por site×model×run×valid×member) con metadatos de procedencia — sustituye al esquema EAV. Cadena explícita raw → física → calibrado → observación → error de verificación. **Sin Redis/Celery en v1** (FastAPI + scheduler + SQLite + Parquet/DuckDB bastan para un sitio).
> 11. **Roadmap con Fase 0 de instrumentación de datos** (archivo de runs y miembros EPS, sensores, y el **pipeline FWHM adelantado a la semana 1** — cada noche sin archivar es irrecuperable). Regla rectora: *archivar y medir antes de sofisticar*. **Presupuesto añadido (§7)**.
> 12. **Licencias afinadas:** 7Timer permite uso incluso en software de pago (as-is); EUMETSAT L1 <1 h exige aclarar licencia para uso comercial (fuera del MVP); BDRC descarga numérica sujeta a registro/credenciales; DarkSkySites es endpoint sin contrato (solo benchmark). **Auditoría formal de licencias como gate previo a comercializar** (incl. permiso escrito de AstroCamp/STARS4ALL).
>
> **v5 — verificación empírica de la ingesta (3ª revisión; TODAS las APIs llamadas en real el 26-08-2026) + correcciones derivadas:**
> 1. **Bug P1 de ingesta:** Open-Meteo Air Quality **NO da AOD por especies** — su variable `dust` es concentración en superficie (µg/m³), no AOD; la corrección por especies de la v4 (§1.2.1) se queda sin fuente síncrona. Rutas nuevas: prefetch diario de CAMS ADS + **WMO Barcelona Dust Regional Center** (DOD 550 nm, 72 h, gratis) + heurística de emergencia. Marcado el OBJETIVO REAL pendiente.
> 2. **Ensemble API de Open-Meteo** (los **51 miembros del EPS de ECMWF**, verificados sobre Nerpio): la "confianza" pasa de fórmula ad-hoc sobre 4 deterministas a probabilidad del ensemble (§2.2). *(Matizado en v6: es probabilidad BRUTA, requiere calibración; y los miembros solo se conservan ~4–5 días.)*
> 3. **Historical Forecast API + Previous Runs API** (verificadas): permiten backfill de años de histórico. *(CORREGIDO en v6 punto 1: el Historical es serie de leads cortos, no "tal como se emitió"; el backfill por lead es Previous Runs, y solo determinista.)*
> 4. **Unidades:** Open-Meteo devuelve vientos en **km/h por defecto** — todas las fórmulas de seeing son en m/s; añadir `&wind_speed_unit=ms` a TODAS las llamadas o error silencioso ×3.6 (verificado: 127.7 "de viento" a 250 hPa que eran 35 m/s).
> 5. **Presión del sitio desde la cota real** (no `surface_pressure` del modelo): la celda de Open-Meteo en Nerpio tiene elevation=1302 m frente a 1650 reales (Δ=348 m, verificado) — el filtro §1.4.3d y la corrección de AOD usan ahora la barométrica sobre `pressure_msl`.
> 6. **Endpoint de sondeos actualizado:** el `cgi-bin` de Wyoming está muerto (404); el nuevo es `/wsgi/sounding` con salida CSV (verificado con Murcia 08430). Alternativa bulk: IGRA v2 (NOAA).
> 7. **Coherencia interna:** el score usa por fin el modelo radiativo de rocío v3 (`T_equipo`, no `T−Td`); factor lunar de banda estrecha **por filtro** (no un 0.15 único); rama de K&S para ρ<10°; término de crepúsculo en B_sky; corrección del paréntesis de θ0.
> 8. **Validación externa nueva:** ESO SkyCalc (`skycalc_cli`) como banco de pruebas del módulo de fondo de cielo; sanity-check numérico del perfil HV ejecutado (HV5/7 → r0=4.9 cm, ε=2.07″ — reproduce los valores canónicos).
> 9. **Sesgo de censura del dataset de calibración** (solo hay FWHM las noches que se observó → el ML aprendería un mundo optimista): nueva contramedida en §6.4.
> 10. **Fase 1 recortada:** el GeoTIFF de Falchi sale del MVP (para Nerpio mag_sky base es una constante); la verificación y el archivado entran en la Fase 1. Airglow con término F10.7 (máximo solar del ciclo 25). Atlas de D. Lorenz (2022) como contraste moderno de Falchi.
> 11. **(añadido 27-08) Inventario in situ de AstroCamp verificado (§2.1.2):** la capa de sensores "futura" ya existe — fotómetro TESS stars289 con serie pública desde ~2019 (API Grafana abierta; anoche 21.97 mag/arcsec² medidos), allsky indi-allsky, 2 CloudWatchers, 2 estaciones con presión real del sitio (843 hPa), estado de techos por sala (automatiza el flag de censura) y 14 años de horas-de-cúpula-abierta mensuales (la climatología-baseline de §6.3). Y **DarkSkySites/LUMIX analizada** como contraste moderno de Falchi (VIIRS mensual + propagación Garstang-like + correcciones comunitarias LLFC) y referencia de diseño del B_LP direccional (APEX, horizonte 720 bins).
>
> **v4 — blindaje de casos límite (2ª revisión externa, aceptada con correcciones):** filtro dinámico de niveles de presión "subterráneos" (a 1650 m incluso 850 hPa está al nivel del sitio), corrección de AOD por cota **por especies** (la propuesta original de corregir el AOD total habría subestimado la calima: el polvo sahariano viaja a 1,5–5 km, encima del sitio no hay "aire limpio"), acoplamiento convectivo del viento en el rocío, umbrales de Richardson reescalados para capas gruesas (Ri_bulk), apantallamiento de LP por mar de nubes bajo el observatorio, truncado y rolloff lunar bajo el horizonte (nota: X(Z) de K&S está acotada en 5.0 por diseño — no hay singularidad, solo un gate que implementar), peso analítico de seeing por morfología (α_comp) en vez de gate booleano, e ingesta: SDK FlatBuffers de Open-Meteo, cdsapi siempre asíncrono, particionado Hive de Parquet.
>
> **v3 — revisión por pares externa incorporada:** modelo radiativo de rocío (T_equipo < T_aire bajo cielo despejado), detector de "bad layers" por número de Richardson (limitación orográfica de HV), término de retrodispersión nubosa de la contaminación lumínica, gates de doble zona (núcleo/halo) para objetos extendidos, integración de la trayectoria (masa de aire dinámica) en el motor de targets, almacenamiento columnar Parquet+DuckDB, y política de ML "árboles con lags primero, secuenciales como challenger".
>
> **v2 — segunda pasada en profundidad:** checklist exhaustivo de variables (incl. contaminación lumínica dinámica, partículas en suspensión con distinción AOD/PM y exponente de Ångström, estelas de satélites, contrails, centelleo, dispersión atmosférica…), modelo de seeing Hufnagel–Valley implementable, acceso concreto (endpoints/snippets) a todos los recursos gratuitos verificados, revisión de los sistemas operacionales de los observatorios profesionales (ESO FATE/MOSE, ALTA-LBT, MKWC-Maunakea, nowcasting ML de Paranal) con escalera de replicabilidad, y **motor de recomendación de targets** («esta noche IFN sí, Cruz de Einstein no»).
>
> Documento preparado para implementación con Claude Code. Contiene: (1) análisis exhaustivo de parámetros físicos, (2) modelos matemáticos contrastados con referencias, (3) fuentes de datos y APIs con estrategia de triangulación, (4) índice de observabilidad propuesto, (5) arquitectura de software y roadmap.

---

# 0. GUÍA PARA EL AUDITOR EXTERNO (v7)

Este documento va a ser la base de un sistema real. Pedimos **refutación, no confirmación**. Reglas del encargo:

1. **Qué atacar, por orden de valor para nosotros:** (a) la **semántica de las fuentes** — qué significa exactamente cada dato, cuánto histórico conserva, con qué licencia comercial (aquí vivieron los errores de la ronda anterior); (b) el **marco** — ¿diseñarías otra cosa si empezaras de cero? (no solo bugs dentro del diseño); (c) **lo que FALTA** — variable, riesgo, coste o paso que no esté ni como descartado-con-motivo; (d) la **economía y las licencias** (§7); (e) el **plan de implementación (§9)** — orden de rodajas, definición de terminado, riesgos de cada fuente.
2. **Lo marcado "verificado DD-MM-2026" se comprobó con llamadas reales a las APIs** (no es retórica). Si refutas algo verificado, indica cómo reproducir tu resultado. En la ronda anterior las afirmaciones del auditor también se verificaron: dos se confirmaron y una se corrigió (§8, última referencia).
3. **Escala de honestidad:** marca tus afirmaciones [LO SÉ] / [NO ME CONSTA]. Una afirmación firme equivocada nos cuesta más que diez "no me consta". No puntuamos la seguridad, puntuamos el acierto.
4. **Formato de respuesta:** lista numerada de objeciones con severidad (bloqueante / importante / menor), cada una con su porqué en una frase y, si existe, la alternativa concreta. Sin resumen elogioso inicial.

---

# 1. ANÁLISIS DE PARÁMETROS

La calidad de una noche de cielo profundo es el producto de cinco factores casi independientes. Conviene modelarlos por separado y combinarlos al final, porque cada uno tiene fuentes de datos y física distintas:

1. **Nubosidad** (¿se ve el cielo?)
2. **Transparencia** (¿cuánta luz llega? — aerosoles, calima, vapor de agua, extinción)
3. **Oscuridad / brillo de fondo de cielo** (Luna, contaminación lumínica, twilight, airglow)
4. **Seeing** (turbulencia — resolución alcanzable, FWHM estelar)
5. **Condiciones operativas** (viento, rocío, humedad, precipitación — ¿puede trabajar el equipo?)

## 1.1 Nubosidad

- **Cobertura total no basta.** Usar siempre las tres capas: baja (0–4 km), media (4–8 km), alta (8–15 km). Los cirros altos (frecuentes con jet subtropical sobre el SE peninsular) tienen cobertura "20%" pero destruyen la fotometría y crean halos; las nubes bajas orográficas en Nerpio pueden ser hiperlocales y no aparecer en modelos globales.
- **Solapamiento:** dos capas al 40% pueden bloquear el 100% del cielo. Modelo de solape aleatorio: `C_eff = 1 − (1−c_low)(1−c_mid)(1−c_high)`. Es conservador; mejor que usar `cloud_cover_total` del modelo.
- **Niebla / nubes muy bajas:** los modelos globales las modelan mal. Proxies: RH>95% + viento en calma + depresión de rocío <1°C. Los modelos de mesoescala AROME/ICON-D2/HARMONIE sí resuelven algo de niebla.
- **Ensemble multi-modelo:** la mayor fuente de acierto no es "el mejor modelo" sino el acuerdo entre modelos independientes (ECMWF IFS, ICON, GFS, AROME). Si los 4 dicen despejado → confianza alta. Astrospheric construyó su producto exactamente sobre esta idea (ensemble de nubes).
- **Nowcasting satelital (0–3 h):** imágenes IR de Meteosat (MTG/SEVIRI) cada 10–15 min permiten ver la nube que el modelo no predijo y extrapolar su movimiento. Es el complemento en tiempo real de las cámaras allsky de AstroCamp.

## 1.2 Transparencia (extinción atmosférica)

La extinción en magnitudes por masa de aire: `m_obs = m_0 + k·X`. El coeficiente k (banda V) se descompone en:

```
k_total = k_Rayleigh + k_aerosol + k_ozono
k_Rayleigh(V) ≈ 0.1451 · exp(−h_site / 7996 m)     # ~0.12 a 1650 m
k_ozono(V)    ≈ 0.016
k_aerosol(V)  ≈ 1.086 · AOD_550                     # extinción[mag] = 1.086·τ (profundidad óptica)
```

- **AOD (Aerosol Optical Depth a 550 nm)** es EL parámetro de transparencia. Predicho por CAMS (Copernicus) a 5 días, con especies separadas: **polvo desértico (calima — crítico en el SE de España)**, humo de incendios, sal marina, sulfatos. Un episodio de calima puede subir AOD de 0.05 a >0.5 (≈ +0.5 mag de extinción y fondo de cielo lechoso).
- **PWV (vapor de agua precipitable, mm):** apenas afecta en visible pero domina la extinción en NIR y bandas estrechas rojas (Ha/SII marginalmente, sobre todo trabajo IR y fotometría de precisión). Los observatorios profesionales lo miden con radiómetros (LHATPRO en Paranal). Disponible como `precipitable water` en GFS/ECMWF. Estimación de emergencia desde superficie (Reitan 1963): `ln(PWV) ≈ a + b·Td` (calibrar localmente).
- **Humedad relativa alta** (>85–90%) produce aerosol higroscópico hinchado → halos, pérdida de contraste, aunque no haya "nubes".
- **Humo de incendios:** CAMS lo modela (organic matter/black carbon AOD). Astrospheric lo integra en Norteamérica; en Europa se puede replicar con CAMS.
- **Validación local:** fotometría de la cámara allsky (magnitud instrumental de estrellas de catálogo vs masa de aire → k medido cada noche) y/o un SQM: la transparencia real queda medida, no estimada.

### 1.2.1 Partículas en suspensión: AOD vs PM, y cómo caracterizarlas bien

- **AOD ≠ PM10/PM2.5.** El AOD es la carga de aerosol integrada en TODA la columna atmosférica (lo que atraviesa la luz de la estrella): es la variable astronómica. PM10/PM2.5 son concentraciones a nivel de superficie (variable sanitaria). Una capa sahariana a 3–5 km puede dar AOD 0.4 con PM10 normal en el suelo: si solo miras PM, la noche parece limpia y no lo está. Usar AOD como primaria y PM como confirmación secundaria (ambas están en la Air Quality API).
- **Exponente de Ångström (α):** con AOD a dos longitudes de onda, `α = −ln(AOD_λ1/AOD_λ2)/ln(λ1/λ2)`. Distingue el tipo de partícula: polvo grueso (calima) α ≲ 0.5; humo/contaminación fina α ≳ 1.5. Permite predecir el color de la extinción: `k(λ) ≈ 1.086·AOD_550·(λ/550nm)^(−α)` — el polvo extingue casi igual en R que en B (cielo "lechoso" neutro), el humo castiga mucho más el azul (afecta a la calibración de color y a la banda B/OIII).
- **Verdad terreno de AOD — AERONET (NASA):** red mundial de fotómetros solares con datos públicos nivel 1.5/2.0 (AOD multibanda, α, PWV). Hay estaciones históricas en el SE peninsular (p. ej. Granada-UGR y otras de la red RIMA ibérica): sirven para validar el AOD de CAMS en la región cada día. Acceso por web service (ver §2.1.1).
- **Corrección de AOD por cota del sitio — POR ESPECIES (v4, con enmienda importante):** CAMS integra el AOD desde la topografía de su malla (que en zona montañosa promedia valles y cumbres). Un sitio a 1650 m está por encima de parte de la capa de aerosol de capa límite, así que aplicar `k = 1.086·AOD_columna` tal cual sobreestima la extinción del aerosol *local*. La corrección barométrica `AOD_sitio = AOD·exp(−max(0, h_sitio − h_topo_modelo)/H_a)` con H_a ≈ 1.5–2 km es correcta **solo para el componente no-polvo** (sulfatos, sal, antropogénico, que sí se concentran abajo). **Aplicarla al AOD total sería un error grave en la dirección contraria:** el polvo sahariano viaja sobre Iberia en capas elevadas de 1,5–5 km (Saharan Air Layer) — a 1650 m se está *dentro o debajo* de la calima, no encima, y la corrección le diría al sistema "cielo limpio" en pleno episodio. Implementación: CAMS da las especies por separado →

```
AOD_sitio = AOD_dust                                        # sin corrección (capa elevada)
          + (AOD_total − AOD_dust)·exp(−Δh/H_a)             # resto: corrección de escala de altura
# Δh = max(0, h_sitio − h_topo_modelo);  H_a inicial 1.7 km, calibrable con AERONET/k medido
# Δh verificado para Nerpio el 26-08-2026: elevation de la celda Open-Meteo = 1302 m → Δh = 348 m
```

- **De dónde sale AOD_dust — CORRECCIÓN v5 (bug de ingesta verificado 26-08-2026):** Open-Meteo Air Quality **NO sirve AOD por especies**. Su variable `dust` devuelve `µg/m³` — es la concentración de polvo **en superficie**, exactamente la variable que el primer punto de esta sección descarta como engañosa (en calima elevada el suelo está limpio). Y `dust_aerosol_optical_depth` no existe en esa API (probado: error). **Usar `dust` como proxy de AOD_dust reintroduciría el error que esta corrección pretende evitar**: capa sahariana a 3 km → `dust`≈0 → el sistema aplicaría la corrección barométrica al AOD total → "cielo limpio" en plena calima. Rutas de implementación reales:
  1. **CAMS ADS con prefetch diario** (el snippet de §2.1.1 ya pide `dust_aerosol_optical_depth_550nm`): el forecast llega a 120 h, así que UNA descarga al día en el worker asíncrono cubre todo el horizonte. Para Nerpio y para cualquier sitio ya registrado, esto basta.
  2. **WMO Barcelona Dust Regional Center** (`dust.aemet.es`, AEMET+BSC): DOD a 550 nm multi-modelo (mediana del ensemble SDS-WAS) a 72 h para el Mediterráneo, gratis — el producto operativo de referencia mundial para calima, y redundancia independiente de Copernicus.
  3. **Heurística de emergencia** (si 1 y 2 fallan): si `AOD_total` alto con `dust`(superficie) y PM10 bajos → sospecha de capa elevada → **no aplicar la corrección barométrica** (asumir AOD_dust ≈ AOD_total − AOD_típico_no_polvo del sitio). Falla en seguro: como mucho sobreestima la extinción.
  - **OBJETIVO REAL (pendiente de refinamiento):** una fuente de AOD por especies **horaria, síncrona (<1 s) y global** para el camino caliente de la app mundial en sitios nuevos sin prefetch. Hoy no la hay gratis lista-para-usar; el candidato más cercano es **NASA GEOS-FP** (verificado 27-08-2026: portal NCCS y OPeNDAP accesibles sin registro, especies de aerosol a 0.25°, 5 días, subsetting sin cola — fila 23 de §2.1): un worker que subsetee el punto por OPeNDAP y cachee puede bajar la latencia a segundos. Si eso se implementa, o si Open-Meteo añade las especies de CAMS, sustituir las rutas 1–3 por ello y borrar este aviso.
  - **La solución CORRECTA al problema de la cota (v6, de la auditoría externa — y es mejor que toda esta sección):** si se dispone del **perfil vertical de extinción** (GEOS-FP lo da en 3D), el problema deja de necesitar heurísticas de escala de altura: `τ_sitio(λ) = ∫_{z_sitio}^{TOA} α_ext(λ,z) dz` y `k(λ) = 1.086·τ_sitio(λ)`. Eso elimina de raíz las DOS suposiciones de la fórmula de arriba (que el no-polvo decae con H_a=1.7 km y que todo el polvo está sobre la cota). La corrección exponencial por especies queda como **fallback** cuando solo hay AODs de columna. Prioridad de implementación: probar la integración vertical de GEOS-FP ANTES de invertir en perfeccionar la exponencial.

- **Aerosol estratosférico/volcánico:** tras erupciones grandes, meses de extinción anómala global que CAMS solo captura parcialmente. Señal delatora: crepúsculos violetas/prolongados y k medido persistentemente alto con AOD troposférico bajo. Tratarlo como offset de calibración de k, no como forecast.
- **Higroscopicidad:** con RH>80–85% el aerosol crece (el mismo AOD dispersa más hacia delante → halos). Multiplicador empírico de f_transp cuando RH alta + AOD moderado.

## 1.3 Oscuridad — brillo de fondo de cielo

El fondo de cielo total es la **suma de flujos** (nunca de magnitudes) de: cielo natural sin Luna (~21.9–22.0 mag/arcsec² V en un sitio Bortle 1–2 como Nerpio), luz zodiacal, airglow (variable ±0.3 mag con ciclo solar), contaminación lumínica artificial y Luna.

### 1.3.1 Sol: definición de noche útil
- Crepúsculo astronómico: altura del Sol < −18°. Entre −12° y −18° (náutico) solo banda estrecha con precaución.
- Calcular con astronomía de precisión (astropy/skyfield), no con fórmulas aproximadas: dan la duración de noche útil por fecha.

### 1.3.2 Luna — modelo de Krisciunas & Schaefer (1991, PASP 103, 1033)
Es EL modelo estándar (lo usan planificadores de ESO/Gemini). Da el brillo lunar dispersado en un punto del cielo en función de: fase, separación angular Luna-objeto, alturas de ambos y extinción.

```
# α = ángulo de fase lunar en grados (0 = llena), ρ = separación angular Luna–target
# Z = distancia cenital, k = coef. de extinción V, X(Z) = (1 − 0.96·sin²Z)^(−0.5)

m_moon = −12.73 + 0.026·|α| + 4·10⁻⁹·α⁴                 # magnitud V de la Luna
I*     = 10^(−0.4·(m_moon + 16.57))                       # iluminancia
f(ρ)   = 10^5.36 · (1.06 + cos²ρ) + 10^(6.15 − ρ/40)      # función de dispersión (Rayleigh+Mie)
B_moon = f(ρ) · I* · 10^(−0.4·k·X(Z_moon)) · [1 − 10^(−0.4·k·X(Z_target))]   # en nanoLamberts

# Conversión nL ↔ mag/arcsec² (V):
B[nL] = 34.08 · exp(20.7233 − 0.92104 · V)
V = (20.7233 − ln(B/34.08)) / 0.92104
```

Nótese que k (transparencia) entra en el modelo lunar: **con calima, la Luna molesta MÁS** (más dispersión). Esto acopla las secciones 1.2 y 1.3 — pocos servicios amateur lo hacen.

**Trampa de implementación (v4):** las fórmulas de K&S devuelven valores positivos aunque la Luna esté bajo el horizonte (X(Z) sigue definida para Z>90°), así que el motor debe imponer el gate explícitamente. Precisión importante: **no hay singularidad numérica** — X(Z)=(1−0.96·sin²Z)^(−0.5) está *acotada por diseño* en 5.0 en el horizonte, que es exactamente como Krisciunas y Schaefer modelaron la masa de aire de dispersión (no sustituirla por Kasten-Young, que es la masa de aire fotométrica y cumple otro papel). Implementación:

```
alt_moon > 0°   → B_moon_physical según K&S
alt_moon ≤ 0°   → B_moon_physical = 0        # v6: SIN luz física ficticia bajo el horizonte
# El rolloff exp(alt/1.5°) entre −6° y 0° de versiones anteriores se RECLASIFICA (v6):
#   no es física, es suavizado de presentación → va en la capa de UI como smoothing_UI
#   (evitar el escalón visual en las gráficas), y JAMÁS entra en mag_sky, en los gates
#   de targets ni en las horas-SNR. Dos campos separados: B_moon_physical / B_moon_display.
```

**Límites del modelo K&S (v5 — dos que faltaban):**
- **Separaciones pequeñas: f(ρ) de arriba solo vale para ρ ≥ 10°.** Para ρ < 10° el propio paper da otra expresión (f(ρ) ≈ 6.2·10⁷/ρ², su ec. 21, ρ en grados) — sin esta rama, un target a 5° de la Luna recibe un B_moon absurdo. El motor de targets calcula separaciones continuas, así que la rama es obligatoria (y en la práctica, a <10° de la Luna casi ningún target DSO es viable: documentar el veredicto como "demasiado cerca de la Luna").
- **Precisión declarada y dominio de calibración:** los autores reportan errores del 8–23 %, y el ajuste se hizo en Mauna Kea con k≈0.11–0.12. Con calima (k>0.3) el modelo **extrapola** — otra razón para el SQM local y para la validación externa siguiente.
- **Validación externa gratuita (v5):** contrastar toda la cadena `mag_sky` (Luna + zodiacal + airglow + twilight) contra el **ESO SkyCalc** (`skymodel.eso.org`, modelo de Cerro Paranal: Noll et al. 2012 + Jones et al. 2013, con luz lunar dispersada espectral). Tiene CLI instalable (`pip install skycalc_cli`): generar una parrilla de casos (fase × separación × alturas × k) y usarla como test de regresión del módulo `moonsky.py` antes de publicar nada.

### 1.3.3 Contaminación lumínica
- Fuente estática global: **World Atlas 2015 (Falchi et al. 2016, Science Advances)** — GeoTIFF de 2.9 GB, malla de 30 arcsec, radiancia artificial cenital en mcd/m². Descargable (CC BY-NC 4.0). Muestrear el píxel de cualquier lat/lon con rasterio.
- Conversión documentada (la que usa lightpollutionmap.info):

```
L_total = L_artificial + 0.171168465 mcd/m²        # cielo natural = 22.00 mag/arcsec²
SQM [mag/arcsec²] = −2.5 · log10( L_total[mcd/m²] / 108 000 000 )
NELM ≈ 7.93 − 5·log10( 10^(4.316 − SQM/5) + 1 )    # magnitud límite visual (Bartels/Schaefer)
Bortle: tabla SQM→clase (21.99–22: B1; 21.89–21.99: B2; 21.69–21.89: B3; …)
```

- Datos dinámicos anuales: VIIRS Black Marble (VNP46A4/VJ146A4, NASA) para ver tendencia de contaminación por año.
- **Caducidad del atlas (v5):** los datos de Falchi 2016 son de **2014** — doce años y una transición LED enteros. Y VIIRS es ciego a 400–500 nm, justo donde emite el LED blanco: tanto el atlas como la tendencia Black Marble **subestiman la LP actual y su crecimiento azul**. Contramedidas: (a) el **atlas de David Lorenz** (2022, derivado de VIIRS con metodología pública, gratuito y sin la cláusula NC de Falchi — relevante para la app comercial, ver §4 licencias) como capa de contraste; (b) el SQM local como verdad terreno que absorbe el sesgo en Nerpio; (c) para sitios remotos de la app mundial, declarar la incertidumbre (`data_quality`).
- Validación local: SQM-LE/SQM-LU en AstroCamp → serie temporal real (además detecta nubes: el cielo nublado en sitio oscuro es MÁS oscuro… salvo con Luna/LP).

### 1.3.3b Amplificación de la contaminación lumínica por nubes (v3)

La afirmación "cielo nublado en sitio oscuro = más oscuro" solo es exacta en un Bortle 1 aislado. Las nubes (sobre todo bases bajas y medias; los cirros en menor medida) actúan de espejo de la LP lejana: en entornos urbanos amplifican el fondo hasta ~10× (Kyba et al. 2011), y en un sitio rural con ciudades a 50–100 km el efecto es intermedio y direccional. Modelo:

```
B_LP_efectiva = B_LP_despejado · [1 + γ(h_nube)·c_frac·(R_100km/R_ref)]
# c_frac = cobertura efectiva; R_100km = radiancia artificial integrada del atlas Falchi
# en ~100 km; γ = albedo efectivo de nube, A CALIBRAR — Y CON SIGNO DEPENDIENTE DE LA COTA (v4):
#   nube media/alta (por encima del sitio)                → γ > 0  (espejo: amplifica la LP lejana)
#   nube/niebla baja CONFINADA BAJO EL SITIO ("mar de     → γ < 0  (escudo: tapa Hellín, Caravaca,
#     nubes": c_low alto + BLH < h_sitio + inversión           Murcia → el cenit queda MÁS oscuro
#     térmica en el perfil + allsky local despejada)           que en noche despejada)
```

La calibración es un subproducto gratuito del SQM: cada noche nublada da un par (c_frac y tipo de nube de la allsky+perfil, SQM medido) → los dos γ (espejo y escudo) salen de la regresión en semanas. El caso escudo es además una oportunidad de producto clásica de observatorio de montaña: detectar y anunciar las noches de "mar de nubes" (los valles cerrados, la cumbre despejada y más oscura que nunca). Doble uso del resultado: (a) mag_sky realista en noches parciales, y (b) argumento cuantitativo de protección del cielo de la zona (cómo crece γ·R con los años = informe de LP para la comarca).

### 1.3.4 Suma final de fondo de cielo por hora y por dirección
```
B_sky(t, alt, az) = B_natural + B_zodiacal(Δλ_helioecl, β_ecl) + B_LP(alt, az)
                  + B_moon(t, ρ, …) + B_twilight(alt_sol)
mag_sky = −2.5·log10( Σ 10^(−0.4·mag_i) )          # suma en flujo
```
Para v1 basta el cenit; en v2 se puede mapear por sectores del cielo (la Luna a 30° del target vs 120° cambia todo — relevante para elegir target, que es justo el caso de uso de AstroCamp).

**Tres términos afinados en v5:**
- **B_twilight (término nuevo — antes el crepúsculo náutico puntuaba como noche cerrada):** §1.3.1 permite banda estrecha entre −12° y −18° de Sol, pero la suma anterior no tenía término de crepúsculo → esas horas salían con mag_sky de noche cerrada y el score las premiaba de más. Parametrización operativa: `B_twilight = B_18 · 10^(0.4·s·(−18 − alt_sol))` para −18 < alt_sol < −12 (decae ~s mag por grado de Sol; s≈0.4–0.5 inicial, calibrable con el SQM de las mismas tardes), 0 por debajo de −18°. **OBJETIVO REAL (pendiente de refinamiento):** un modelo de twilight con dependencia espectral y de azimut (Patat et al. 2006, o directamente la parrilla de SkyCalc); la exponencial es un placeholder honesto para que el score no mienta en esa franja.
- **Zodiacal:** depende de la **elongación helioeclíptica Y de β**, no solo de β — con solo la latitud se subestima el fondo al principio y final de la noche apuntando bajo hacia la eclíptica (justo cuando se cazan cometas). Usar las tablas de Leinert et al. (1998) interpoladas (son estáticas: 20 líneas y un CSV).
- **B_LP(alt, az) direccional aún no tiene modelo** — Falchi da solo la cenital. Para el v2 por sectores hace falta la función angular: opción barata, empírica `B_LP(alt, az) = B_LP_cenit · [1 + η·csc(alt)·g(az)]` con g(az) apuntando a las radiancias integradas del atlas por azimut (Hellín/Caravaca/Murcia) y η calibrado con el SQM direccional o la allsky; opción rigurosa, el modelo de Garstang (1989). **Referencia de diseño moderna (v5): el simulador APEX de DarkSkySites** hace exactamente esto en producción — radiancia VIIRS local → volumen voxel con Rayleigh/Mie separados, extinción, curvatura y **máscara de horizonte de 720 bins acimutales desde el DEM** → cúpula del cielo por dirección desde el observador. Dos ideas robables directamente: el horizonte de 720 bins por DEM (barato: SRTM + astropy) y separar "dónde está la luz" de "a dónde va" (mapa propagado vs sin propagar). **OBJETIVO REAL:** mapa B_LP(alt,az) físico por sitio; hasta entonces el cenit + el término §1.3.3b es lo publicable.

### 1.3.5 Bandas fotométricas: la magnitud única de cielo es UI, no física (v6)

La cadena actual mezcla magnitudes que NO son la misma cosa: K&S está en V, el AOD en 550 nm, el TESS y el SQM tienen cada uno su respuesta espectral propia (y distinta entre sí), VIIRS apenas ve el azul LED, y OIII (500,7 nm) ve un cielo distinto que Ha (656,3 nm). Sumarlo todo en un solo `mag_sky` funciona como primera aproximación visual, pero **no es espectralmente consistente** — y las inconsistencias se pagan justo donde el proyecto quiere ser bueno: comparar modelo con sensor, y decidir entre filtros con Luna.

**Representaciones internas (tres, mínimo):**
```
sky_V_mag_arcsec2                    # la física: banda V Johnson — K&S, zodiacal, airglow viven aquí
sky_sensor_predicted(bandpass_id)    # lo que DEBERÍA leer el sensor concreto (TESS stars289, SQM…):
                                     #   convolución del espectro de cielo modelado con SU respuesta
sky_electron_rate(filter, camera)    # para planificación: e⁻/px/s de fondo con el filtro y cámara
                                     #   del usuario → alimenta directamente las horas-SNR (§3.3.3b)
```
La magnitud única se conserva **solo en la interfaz**. Consecuencias prácticas: (a) la comparación modelo↔TESS del panel §6.3 se hace en `sky_sensor_predicted(TESS)`, no en V — si no, el "error del modelo" incluye el error de banda; (b) la conversión TESS→"escala SQM" es una **calibración explícita y versionada**, no una identidad (§2.1.2); (c) los factores lunares por filtro dejan de ser números sueltos y salen de un espectro (LUT de SkyCalc, §3.1). ESO SkyCalc ya trabaja exactamente así (espectro integrado por respuestas de filtro reales) — es el generador natural de las tres representaciones.

## 1.4 Seeing (turbulencia óptica)

### 1.4.1 Física de referencia (lo que miden los observatorios profesionales)
La turbulencia se caracteriza por el perfil `Cn²(h)` (constante de estructura del índice de refracción). Parámetros integrados:

```
r0 = [ 0.423 · (2π/λ)² · sec(z) · ∫ Cn²(h) dh ]^(−3/5)      # parámetro de Fried
ε_FWHM = 0.98 · λ / r0                                        # seeing (rad → arcsec)
θ0 ≈ [ 2.914·(2π/λ)²·sec(z)^(8/3) · ∫ Cn²(h)·h^(5/3) dh ]^(−3/5) # ángulo isoplanático (v5: el
                                                              #   exponente −3/5 abarca TODO el corchete)
τ0 ≈ 0.314 · r0 / v_eff                                       # tiempo de coherencia (lucky imaging)
v_eff = [ ∫ Cn²(h)·V(h)^(5/3) dh / ∫ Cn²(h) dh ]^(3/5)        # v6 (antes sin definir): viento efectivo
                                                              #   ponderado por el MISMO perfil Cn² de r0.
                                                              #   JAMÁS media simple, rms ni V250 a secas.

# Escalados imprescindibles para la app:
ε(z) = ε_cenital · [sec(z)]^(3/5)  = ε_cenital · X^0.6        # el seeing empeora con la masa de aire
ε(λ) ∝ λ^(−1/5)                                               # ligeramente mejor en rojo/IR
```

### 1.4.2 De dónde sale la turbulencia (y qué se puede predecir con datos de modelos NWP)
Tres contribuciones, cada una con su proxy en datos de modelo:

| Capa | Causa | Proxy en modelo NWP |
|---|---|---|
| Capa límite (0–500 m) | Convección, cizalladura orográfica, enfriamiento radiativo | Viento 10 m, cizalladura V10 vs primer nivel VÁLIDO sobre el sitio (700–800 hPa en Nerpio; ver §1.4.3d), altura de capa límite (BLH), gradiente T2m vs 700 hPa |
| Atmósfera libre (1–10 km) | "Bad layers": gradientes térmicos > 0.5 K/100 m, cizalladura entre niveles | Perfiles T y viento en niveles de presión (850, 700, 500, 300 hPa) |
| Jet stream (9–12 km, 200–300 hPa) | Cizalladura vertical enorme en los bordes del chorro | **Velocidad de viento a 250/300 hPa**: >30–35 m/s → seeing malo; <5 m/s también puede ser malo (aire estancado); 8–20 m/s suele ser lo mejor |

Meteoblue construye sus índices exactamente así (gradientes >0.5 K/100 m = "bad layers"; jet >35 m/s = malo). Los modelos profesionales (Astro-Meso-Nh de Masciadri en ESO, WRF en Maunakea — Lyman, Cherubini & Businger 2020, MNRAS 496, 4734) resuelven Cn² con parametrizaciones de Trinquet-Vernin/Dewan alimentadas por mesoescala; y la frontera actual es ML sobre datos históricos del sitio (Milli et al. 2019, arXiv:1910.13767; Turchi/Masciadri; OTCliM con gradient boosting, 2025).

### 1.4.3 Estimador práctico calibrable (v1) — "modelo de dos términos"
Para una web amateur no se puede correr WRF, pero sí un estimador honesto con datos gratuitos de niveles de presión (Open-Meteo los da):

```
# Entradas por hora: V250 (viento 250 hPa, m/s), ΔT = T2m − T700 (proxy estabilidad;
# ¡nunca contra 850/925 hPa en un sitio a 1650 m — ver §1.4.3d!),
# V10 (viento superficie), shear_low = |V_700..800hPa − V10|
seeing_free  = a0 + a1·max(0, (V250 − 12)/25)^1.5          # término de jet
seeing_bl    = b0 + b1·(V10/8)^1.2 + b2·max(0, ΔT_conv)     # término de capa límite
ε_est = sqrt(seeing_free² + seeing_bl²)                      # suma cuadrática de capas independientes
# Valores iniciales sugeridos (a calibrar): a0=0.8", a1=1.5, b0=0.4", b1=0.5, b2=0.1/K
# ΔT_conv (v5, antes sin definir) = (T2m − T700) − 9.8·Δz_km   # exceso sobre el gradiente adiabático
#   seco (K); Δz_km = (geopotential_height_700hPa − h_sitio)/1000. >0 = capa inestable/convectiva.
```

**AVISO DE UNIDADES (v5, verificado 26-08-2026):** Open-Meteo devuelve TODOS los vientos en **km/h por defecto** (la llamada de §2.1.1 dio `wind_speed_250hPa: 127.7`, que son 35 m/s). Todas las fórmulas de este capítulo son en m/s: añadir **`&wind_speed_unit=ms`** a cada llamada, y un assert de cordura en la ingesta (`V250 > 90 → probablemente km/h`). Sin esto, el término de jet predice seeing catastrófico permanente y el error no lanza ninguna excepción.

**Clave del proyecto:** AstroCamp genera cada noche cientos de subexposiciones con FWHM medible (PixInsight/ASTAP logs, o plate-solve + análisis automático). Guardar `(hora, FWHM_mediana, altura del target, filtro)` → corregir a cenit y 500 nm con los escalados de 1.4.1 → **dataset de calibración propio**. Con 2–3 meses de datos, ajustar (a0..b2) por mínimos cuadrados o directamente un gradient boosting (sklearn) con los predictores del modelo NWP. Esto es exactamente lo que hace ESO a escala profesional, y es lo que convertiría CabraSpace en algo mejor que meteoblue para Nerpio.

### 1.4.3b Modelo v2 recomendado: perfil Hufnagel–Valley alimentado con vientos del NWP

El estimador de dos términos (§1.4.3) es un ajuste empírico. Un paso más riguroso —y aún perfectamente implementable con datos gratuitos— es usar el **perfil analítico Hufnagel–Valley**, estándar de la comunidad de óptica atmosférica, cuyo término de alta atmósfera depende explícitamente del viento en la capa del jet (que es justo el dato que falta en las apps convencionales):

```
# h en metros, W en m/s, Cn² en m^(−2/3)
Cn²(h) = 5.94e−53 · (W/27)² · h^10 · e^(−h/1000)     # atmósfera libre alta: ∝ W² (¡jet stream!)
       + 2.7e−16 · e^(−h/1500)                        # troposfera media
       + A · e^(−h/100)                               # capa límite (A calibrable por sitio)

W = [ (1/15000) · ∫_{5km}^{20km} V(h)² dh ]^(1/2)     # viento rms 5–20 km
  ≈ [ Σ V_i²·Δh_i / Σ Δh_i ]^(1/2)  con niveles 500…100 hPa   # ← v5: ponderar por el espesor Δh_i
# de cada capa (geopotential_height por nivel), NO rms simple de los niveles: los niveles de
# presión no equidistan en altura y el rms plano sobrepondera la estratosfera baja.

# Con el perfil integrado, todo lo demás sale de §1.4.1:
∫Cn²dh → r0 → ε = 0.98·λ/r0 ; θ0 ; τ0 (usando además v_eff del propio perfil de vientos)
```

- **Sanity-check numérico ejecutado (v5, 26-08-2026):** integrando el perfil tal como está escrito arriba: W=27, A=1.7e−14 (HV5/7) → r0=4.9 cm, ε=2.07″, θ0=1.09″ — reproduce los valores canónicos de la literatura, y el barrido W=10–40 m/s con A=1.7e−15 da ε=0.78–1.29″: rango dinámico realista para un buen sitio. Las constantes del documento son correctas y el modelo es implementable tal cual.
- Valores canónicos: A = 1.7e−14 (perfil "HV5/7", condiciones fuertes). Para un sitio de montaña tranquilo A baja 1–2 órdenes de magnitud: **A es EL parámetro a calibrar con los FWHM medidos en AstroCamp** (un solo escalar, ajustable con pocas semanas de datos; añadir dependencia de A con V10 y ΔT superficie si mejora).
- Ventajas sobre el modelo de dos términos: dimensionalmente correcto, da también θ0 y τ0 (útil para lucky/planetaria), degrada con el jet de forma física (W²·h^10 concentra la turbulencia en 9–12 km) y es directamente comparable con la literatura.
- Escalera de sofisticación documentada en la literatura profesional, por si se quiere ir más lejos: HV(W) → **Dewan/AFGL** (Cn² desde cizalladura y estabilidad N² por capas, requiere perfiles completos T/V) → **Trinquet–Vernin (2007)** (estadístico sobre radiosondeos) → **Astro-Meso-Nh / WRF con parametrización de OT** (lo que corren ESO y Maunakea; fuera de alcance de un servidor web, pero es la referencia de validación). Relación termodinámica de base para todas: **Gladstone**: `Cn² = (79e−6 · P/T²)² · CT²` (P en hPa, T en K, CT² de fluctuaciones térmicas).
- **Validación gratuita con radiosondeos reales:** la Universidad de Wyoming publica el archivo mundial de sondeos; el más cercano a Nerpio es **Murcia (WMO 08430, 00 y 12 UTC)**: perfiles reales de T y viento para (a) comprobar los perfiles del modelo NWP y (b) calcular Cn² por Dewan/Trinquet-Vernin offline y comparar con la HV. Acceso en §2.1.1.

### 1.4.3c Detector de "bad layers" por número de Richardson (v3 — corrige la ceguera orográfica de HV)

Limitación reconocida del perfil HV: su término alto depende solo de W² (jet), pero en un entorno de montaña como Nerpio buena parte del seeing catastrófico viene de **ondas de gravedad orográficas** (lee waves) y cizalladuras en la troposfera media que HV no ve. Con los mismos perfiles de presión ya descargados (925–100 hPa) se calcula por capa:

```
# Entre cada par de niveles adyacentes (z1,z2), con θ = temperatura potencial:
N² = (g/θ̄)·(Δθ/Δz)                      # frecuencia de Brunt–Väisälä al cuadrado (estabilidad)
S² = (ΔU/Δz)² + (ΔV/Δz)²                 # cizalladura vertical al cuadrado
Ri = N²/S²                               # número de Richardson de la capa
# v5 — dos detalles de ingesta: (1) Open-Meteo da wind_speed + wind_direction por nivel, no U/V:
#   U = −speed·sin(dir·π/180), V = −speed·cos(dir·π/180) (convención meteorológica "de dónde viene").
#   S² es cizalladura VECTORIAL: dos capas con 20 m/s que giran 90° cizallan tanto como 0→20.
# (2) Δz REAL de geopotential_height por nivel, no atmósfera estándar (en dorsal/vaguada difieren >100 m).

# Umbral clásico Ri<0.25: válido para capas finas (Δz→0). Con niveles de modelo separados
# 1.5–3 km, el promedio suaviza los gradientes y un Ri_bulk de 0.6 esconde subcapas con
# Ri_local<0.25. El umbral crítico ~0.25 es del Ri GRADIENTE; para Ri_bulk no existe una
# traducción universal → cualquier umbral/multiplicador aquí es heurístico y debe tratarse
# como tal (v6).
```

**Integración v6 — Ri como FEATURES y flag de riesgo, no como física del núcleo.** La v4 multiplicaba el Cn² de HV por unos β (3.0 / 1.8 / 1.0) según umbrales de Ri_bulk. La auditoría externa tiene razón en que esos números no tienen respaldo físico directo (el 0.25 crítico es del Ri local, y la equivalencia umbral→multiplicador es inventada). Rediseño:

```
# Lo que se calcula y ARCHIVA cada hora (features):
ri_min                      # el Ri_bulk mínimo del perfil válido
frac_capas_ri_lt_05         # fracción de capas con Ri_bulk < 0.5
max_shear                   # cizalladura vectorial máxima (m/s por km)
viento_cruzado_cordillera   # componente 700–500 hPa perpendicular a la sierra del Segura
# Lo que se PUBLICA:
orographic_turbulence_risk = low / medium / high     # reglas simples sobre las features
# Lo que compite en sombra (champion/challenger, §6.4):
variante_beta: HV con Cn²·β(Ri_bulk) [β iniciales 3.0/1.8/1.0]  →  solo se promociona al
modelo publicado si bate al HV puro contra el FWHM medido fuera de muestra
```

Así el detector conserva todo su valor (el régimen "viento fuerte del NO contra el Segura + estabilidad media = noche sospechosa" se sigue detectando y avisando), pero deja de inyectar números inventados en la predicción numérica publicada — y el FWHM local decidirá con datos si los β merecen entrar. Honestidad sobre los límites (se mantiene de v3): las lee waves finas requieren mesoescala de ~1 km; Ri sobre perfiles de 9–25 km detecta el régimen, no localiza la onda.

### 1.4.3d Filtro de niveles de presión "subterráneos" (v4 — bug corregido)

A 1650 m la presión en superficie es P_sfc ≈ 830–840 hPa: los niveles 1000 y 925 hPa de un modelo global sobre Nerpio son **extrapolaciones matemáticas bajo tierra**, y 850 hPa (~1450 m en atmósfera estándar) está al nivel del propio sitio o por debajo — usarlo como "capa baja" produce gradientes de espesor casi nulo y cizalladuras ficticias. Regla de implementación, dinámica para que valga en cualquier sitio del mundo (app global):

```
# v5 — CORRECCIÓN sobre la v4: surface_pressure de la API es la presión EN LA TOPOGRAFÍA DEL
# MODELO, no en el sitio real. Verificado en Nerpio: la celda tiene elevation = 1302 m (no 1650)
# → su surface_pressure ronda 865 hPa cuando en el observatorio hay ~830. Con ese valor, el
# filtro dejaría pasar 850 hPa en situaciones de alta presión — el nivel que este parche
# existe para excluir. La presión de referencia se calcula a la COTA REAL:
P_sitio = pressure_msl · (1 − 2.25577e−5 · h_real)^5.25588      # barométrica estándar, h_real en m
niveles_validos = [p for p in niveles if p <= P_sitio − 25 hPa]  # margen de seguridad
# En Nerpio (h_real=1650) → P_sitio ≈ 830 hPa → primer nivel útil = 800 hPa, SIEMPRE.
# Estabilidad baja: T2m vs T700; cizalladura de capa límite: V10 vs V(primer nivel válido).
# En un sitio a nivel del mar → 1000/925 hPa vuelven a ser perfectamente válidos.
```

Corolario para el Ri (§1.4.3c) y para HV: los perfiles empiezan en el primer nivel válido; el tramo sitio→primer nivel se cubre con las variables de superficie + BLH, no con niveles interpolados. Dos notas de ingesta (verificadas): la `elevation` que devuelve la API se registra como metadato del sitio junto a la cota real (Nerpio: 1302 vs 1650, Δ=348 m — el mismo Δh de la corrección de AOD §1.2.1); y sobre la BLH, **actualización v6 (verificada 27-08-2026): el modelo `ecmwf_ifs` (~9 km) SÍ sirve `boundary_layer_height`** (valores nocturnos plausibles de 40–50 m sobre Nerpio) — en el esquema híbrido v6 (9 km superficie + 0.25° perfiles, §2.1.1) la BLH sale del ECMWF 9 km como primaria y de GFS como contraste; la estimación propia desde el perfil (altura donde θ supera θ_superficie + 1 K) queda como tercera vía.

### 1.4.4 Importancia relativa según el uso — y cómo se PUBLICA el seeing (v6)
- Cielo profundo con escalas de imagen de 1–2 "/px: seeing importa poco salvo que sea >3–4".
- Galaxias a foco largo / doble estrellas / planetaria: seeing es EL factor.
- El índice final debe ponderar el seeing según el "perfil de target" (ver §3).
- **Regla de honestidad v6:** que HV5/7 reproduzca los valores canónicos (§1.4.3b) demuestra que la implementación no está rota, **no** que tenga skill predictivo en Nerpio. Hasta que el FWHM local demuestre MAE/sesgo/terciles fuera de muestra, el producto publica **"seeing favorable / normal / desfavorable" + rango** (p. ej. "estimación experimental: 1.4–2.1″"), NUNCA "1.63″". Internamente el valor puntual se calcula y archiva siempre (es lo que se verifica); lo que se restringe es la presentación. Coherente con §6.3 (terciles en el panel público).

## 1.5 Condiciones operativas

- **Viento:** medio y rachas a 10 m. Umbrales típicos: <15 km/h ideal; 15–30 degrada guiado en focales largas; >35–40 km/h cierre de observatorio. Comparar rachas, no solo medio.
- **Rocío:** fórmula de Magnus (usar constantes Sonntag):

```
γ = ln(RH/100) + 17.62·T / (243.12 + T)
Td = 243.12·γ / (17.62 − γ)                     # °C
riesgo_rocio: alto si (T − Td) < 2 °C con viento < 5 km/h; los tubos radían al cielo y
se enfrían por debajo de T ambiente → usar (T_sky ≈ T − 20…25 °C en despejado) como criterio fino.
```

- **Modelo radiativo de rocío (v3 — el criterio T−Td>2 °C es insuficiente):** la óptica expuesta al cenit despejado NO está a T_aire: radia contra un cielo cuya temperatura efectiva IR es de −40…−60 °C y se enfría 3–5 °C por debajo del ambiente. El riesgo real es `T_equipo < Td`, no `T_aire < Td`. Modelo implementable:

```
# Emisividad de cielo despejado (Brunt; e = presión de vapor en hPa, de T y RH):
ε_clear = 0.605 + 0.048·√e
# Las nubes actúan de manta térmica (c_frac = cobertura efectiva, §1.1):
ε_sky = ε_clear + (1 − ε_clear)·0.85·c_frac
# Depresión de equilibrio de una superficie expuesta (parametrización operativa):
ΔT_rad(V) = ΔT_max · [(1 − ε_sky)/(1 − ε_clear_ref)] / (1 + κ·V10^0.8)   # v4: el viento acopla
T_equipo  = T_aire − ΔT_rad                                                #   el equipo al aire
# ΔT_max ≈ 4 °C óptica expuesta (≈2 °C tubo cerrado); κ ≈ 0.15–0.20 (V10 en m/s); ambos calibrables.
# Física: la convección forzada anula el subenfriamiento radiativo con viento ≳20 km/h; el máximo
# riesgo de rocío es la combinación despejado total + calma — exactamente la "noche perfecta".
riesgo_rocio = (T_equipo − Td) < 1 °C                 # y "escarcha" si además T_equipo < 0
```

  **Jerarquía v6 (el sensor manda):** los CloudWatchers de AstroCamp YA miden T_sky (§2.1.2) — **no se estima lo que se puede medir**. Prioridad: (1) `T_sky` medida fresca → modelo principal; (2) modelo radiativo de arriba → fallback (sitios sin sensor de la app mundial). ΔT_max, κ y el 0.85 de nube son parametrización operativa a calibrar — con T_sky medida y un termómetro IR sobre la lente, la calibración es directa.
  **Salida v6 — un margen en grados, no un índice:** el producto no publica "riesgo_rocio = 0.6" sino **`margen_rocio = T_equipo − Td` en °C** ("margen previsto lente–rocío: +0.7 °C a las 03:00"), con signo y evolución horaria — accionable de verdad. Config de usuario `dew_heater = true/false`: con cintas, el margen se recalcula con ΔT_max reducido y el gate cambia por completo. Corolario que se mantiene: el margen se hunde justo cuando la noche se despeja del todo — el forecast avisa, no premia solo el "despejado". El score usa este margen (§3.1), no T−Td.
- **Precipitación / nieve / tormenta:** probabilidad y CAPE (cierre preventivo).
- **Temperatura:** gradiente de enfriamiento nocturno (aclimatación de óptica, foco); mínima (baterías, escarcha).
- **Kp / auroras:** irrelevante para DSO en Nerpio salvo tormentas extremas (airglow/SAR); opcional.

## 1.6 Checklist exhaustivo de variables (incluidas las que casi todos olvidan)

Tabla de control para la implementación: si una variable no está en el JSON de salida o declarada como descartada-con-motivo, falta trabajo. P = prioridad (1 imprescindible, 2 recomendada, 3 refinamiento).

| Variable | Por qué importa | Fuente | P |
|---|---|---|---|
| Nubes baja/media/alta + total | Gate principal | Open-Meteo multi-modelo | 1 |
| Niebla / nubes <2 km | Modelos globales no la ven | AROME/ICON-D2, proxy RH+calma, allsky | 2 |
| Precipitación, prob., tipo | Gate duro, cierre | Open-Meteo | 1 |
| CAPE / índice de disparo | Tormenta nocturna | Open-Meteo | 2 |
| T2m, RH, punto de rocío | Rocío, aclimatación | Open-Meteo + estación local | 1 |
| dT/dt nocturno | Deriva de foco, corrientes de tubo | derivado de T2m horaria | 3 |
| Viento 10 m medio + rachas + dirección | Guiado; dirección: polvo local y cierzo del valle | Open-Meteo + estación | 1 |
| Perfiles de presión (T, V: 925–100 hPa) | Seeing (HV/Dewan), jet, bad layers | Open-Meteo pressure levels | 1 |
| Viento 200–300 hPa (jet) | Seeing de atmósfera libre | ídem | 1 |
| Altura de capa límite (BLH) | Seeing de capa baja, inversiones | Open-Meteo **solo GFS** (resto `null` — verificado v5); o estimada del perfil | 2 |
| AOD 550 total | Transparencia | Open-Meteo AQ / CAMS | 1 |
| AOD polvo (calima) | Alerta específica SE peninsular | **CAMS ADS (prefetch diario) + Barcelona Dust RC** — ¡Open-Meteo AQ NO la da: su `dust` es µg/m³ en superficie! (v5, §1.2.1) | 1 |
| AOD humo / α Ångström | Incendios; color de la extinción | CAMS ADS, AERONET | 2 |
| PM10/PM2.5 | Confirmación superficie | Open-Meteo AQ | 3 |
| PWV | NIR, fotometría precisión | GFS/ECMWF (PWAT) | 2 |
| Ozono columnar | k menor (~0.016 mag) | constante o CAMS | 3 |
| k medido (fotometría allsky) | Verdad terreno de transparencia | allsky AstroCamp | 2 |
| Contaminación lumínica estática | mag_sky base del sitio | Atlas Falchi (GeoTIFF) | 1 |
| Tendencia LP anual | ¿Empeora el sitio? | VIIRS Black Marble | 3 |
| SQM en vivo | Verdad terreno de fondo de cielo | sensor local | 2 |
| Sol: crepúsculos (−12°, −18°) | Ventana útil | astropy (local) | 1 |
| Luna: fase, altura, orto/ocaso | B_moon | astropy (local) | 1 |
| Separación Luna–target | K&S direccional | astropy + target | 2 |
| Luz zodiacal / gegenschein | +fondo cerca de eclíptica (mañana/tarde) | tablas Leinert 1998 (elongación Y β — v5) | 3 |
| Airglow | ±0.3 mag con ciclo solar (Patat 2008); estamos tras el máximo del ciclo 25 | término F10.7 (SWPC, ya integrado para Kp) + SQM (v5) | 3 |
| **P_raw / P_calibrada del EPS** | 51 miembros ECMWF → fracción bruta → calibrador (v6); mejor que acuerdo de 4 deterministas | Open-Meteo **Ensemble API** (verificada) + archivo propio de miembros | 1 |
| **Twilight −18…−12°** | Sin término, esas horas puntúan como noche cerrada | B_twilight §1.3.4 (v5) | 2 |
| Fondo galáctico | Targets en Vía Láctea tienen fondo alto propio | lat. galáctica del target | 3 |
| Kp / arcos SAR | Solo tormentas extremas | NOAA SWPC | 3 |
| **Estelas de satélites** | Densidad de trazas (Starlink) en crepúsculos; mitigable apilando con rechazo sigma | TLEs CelesTrak + cálculo local | 3 |
| **Contrails persistentes** | Cirros artificiales en corredores aéreos; predictor: RH alta a 250–300 hPa | RH en niveles + observación | 3 |
| **Centelleo (scintillation)** | Ruido en fotometría rápida/exoplanetas: σ ∝ X^1.75·Cn²(alto) | derivado del perfil HV | 3 |
| **Dispersión atmosférica / refracción diferencial** | Targets <30° de altura: elongación cromática; limitar altura mínima o ADC | cálculo local (astropy) | 2 |
| Seeing local (suelo/pilar/cúpula) | Hormigón caliente, térmicas: no lo ve ningún modelo | diseño del observatorio + FWHM | 2 |
| Nieve en suelo | Refleja LP local, sube fondo | snow_depth + albedo flag | 3 |
| Estabilidad fotométrica (σ ensemble) | Noches "fotométricas" para ciencia | dispersión multi-modelo | 2 |

---

# 2. FUENTES DE DATOS Y APIs (con estrategia de triangulación)

## 2.1 Tabla maestra

| # | Fuente | Qué da | Cobertura | Coste/licencia | Uso en CabraSpace |
|---|---|---|---|---|---|
| 1 | **Open-Meteo** (`api.open-meteo.com`) | Nubes por capas, T/RH/viento/rachas, **niveles de presión (1000–30 hPa: T, viento, RH, geopotencial)**, BLH, y selección de modelo: **`ecmwf_ifs` ~9 km (verificado v6: superficie+nubes+BLH, SIN niveles de presión) + `ecmwf_ifs025` (con niveles) → uso HÍBRIDO**, ICON, GFS, AROME/ARPEGE, UKMO… **Una sola llamada sirve varios modelos** (`&models=a,b,c` — verificado; sufijo por variable) | Global (AROME: Francia + borde, llega a Nerpio por poco — verificado) | Gratis no comercial (CC-BY); **comercial: plan de pago (Professional ~99 €/mes, a confirmar condiciones — §7)** | **Columna vertebral.** Un solo proveedor da el multi-modelo, el EPS y los niveles de presión. ¡`&wind_speed_unit=ms` SIEMPRE! Riesgo de proveedor único: fleco 17 |
| 2 | **Open-Meteo Air Quality** (`air-quality-api.open-meteo.com`) | **AOD 550 nm TOTAL**, PM10/PM2.5 y `dust` **en superficie (µg/m³ — NO es AOD de polvo; v5 verificado)** desde CAMS (11 km Europa / global) | Global | Gratis no comercial | Transparencia (AOD total). La alerta de calima por especies sale de #3 y #17, NO de aquí |
| 3 | **CAMS / Atmosphere Data Store (ADS)** directo | AOD total y por especie (dust, smoke), 5 días, netCDF/GRIB vía `cdsapi` | Global | Gratis (registro Copernicus) | Triangular #2; mapas de calima; datos por especie |
| 4 | **meteoblue** (web + API de pago) | Seeing index 1/2, arcsec, **bad layers**, jet stream; su paquete "Astronomy Seeing" | Global | Web gratis 3 días; API de pago | Referencia de contraste (no integrar sin licencia; fórmulas propietarias no publicadas) |
| 5 | **7Timer! ASTRO** (`7timer.info/bin/astro.php`) | Seeing y transparencia por clases, nubes; derivado de GFS | Global | Gratis; **licencia v6: su web permite explícitamente el uso en software gratuito O de pago, "as-is"** — el riesgo real no es legal sino SLA/continuidad/metodología GFS antigua | Challenger/benchmark barato; nunca fuente operacional esencial |
| 6 | **Clear Outside** (First Light Optics) | Web de previsión astro por capas | Global | Gratis (sin API oficial) | Solo contraste manual |
| 7 | **Astrospheric** | El mejor producto astro (ensemble, seeing de Rahill, humo) | **SOLO Norteamérica (dominio RDPS)** | API para miembros Pro | **No sirve para Nerpio** — pero es el modelo de producto a imitar |
| 8 | **AEMET OpenData** | Observaciones de estaciones, predicción municipal, radar, avisos | España | Gratis (API key) | Validación con observaciones reales cercanas; avisos |
| 9 | **EUMETSAT (Meteosat)** — v6: en 2026 el operativo es **MTG con FCI** (disco completo ~10 min); SEVIRI es la generación anterior (MSG) — no agruparlos | Imagen IR/visible de alta cadencia vía Data Store/EUMDAC | Europa/África | **Matiz de licencia v6:** buena parte de los datos "Core" es reutilizable gratis, pero los productos L1 con **latencia <1 h** tienen condiciones específicas — una licencia personal/investigación NO equivale a permiso para servicio comercial operacional. **Aclarar por escrito antes de que sea dependencia comercial** | Nowcasting 0–3 h — **fuera del MVP**: para 0–60 min en AstroCamp, allsky+CloudWatcher+radar locales son probablemente superiores y sin fricción de licencia |
| 10 | **ECMWF Open Data** / **NOAA NOMADS (GFS)** | GRIB2 crudos: PWAT (agua precipitable), niveles, jet | Global | Gratis | PWV real; backup de #1 si hace falta control total |
| 11 | **World Atlas 2015 (Falchi et al. 2016)** GeoTIFF | Brillo artificial cenital, 30" de resolución | Global | CC BY-NC 4.0 | Capa estática de contaminación lumínica → SQM/Bortle de cualquier punto |
| 12 | **lightpollutionmap.info** — re-analizada v5 (27-08-2026) | Visor con capas **SB_2012–SB_2025** (brillo de cielo anual en MPSAS derivado de VIIRS — serie propia más actual que Falchi), VIIRS anuales, WA2015, **puntos SQM/SQC comunitarios**, y **Radiance Light Trends** (app hermana del GFZ: serie temporal DMSP+VIIRS por punto, gratis) | Global | Web gratis; **API `QueryRaster` SOLO con key** (verificado: sin key rechaza; se solicita al autor, gratuita para uso amateur, de pago comercial); su help.html documenta las conversiones de §1.3.3 (verificado: mismas constantes) | Contraste de #11 y de la fila 21; su serie SB anual es el punto medio entre Falchi (2014) y DSS (mensual) |
| 13 | **NASA Black Marble (VNP46A4/VJ146A4)** | Radiancia nocturna anual VIIRS | Global | Gratis (Earthdata) | Tendencia anual de LP |
| 14 | **astropy + astroplan + skyfield** (librerías) | Efemérides precisas: Sol, Luna (fase, separación, salida/puesta), twilights, masa de aire, visibilidad de targets | — | Open source | **Todo el cálculo astronómico se hace en local, sin API** |
| 15 | **NOAA SWPC** | Kp actual y previsto; **F10.7 diario en JSON** (`services.swpc.noaa.gov/json/f107_cm_flux.json` — verificado: 131 sfu el 26-08-2026) | Global | Gratis | Kp opcional; **F10.7 alimenta el término de airglow** (§1.3.4 v5, fleco 6) |
| 16 | **Sensores AstroCamp** — inventario VERIFICADO 26-08-2026 (§2.1.2): fotómetro **TESS stars289** (mag/arcsec² + T_sky IR, serie pública desde ~2019), **allsky** (indi-allsky, imagen pública), 2 CloudWatchers, 2 estaciones meteo (con **presión real del sitio**: 843 hPa medidos), estado de techos por sala, estadística mensual de horas abiertas 2012–2026 | Verdad terreno en tiempo real Y retrospectiva | Nerpio | Propio/público | **Capa de verdad terreno + calibración ML — disponible YA, no "hardware futuro"** |
| 17 | **WMO Barcelona Dust Regional Center** (`dust.aemet.es`, AEMET+BSC) — **v5** | **DOD 550 nm** (AOD de polvo) multi-modelo SDS-WAS, 72 h | N. África, O. Medio, **Europa/Mediterráneo** | Gratis | **La fuente operativa de la alerta de calima** (sustituye el papel que la v4 asignaba por error a #2); redundante e independiente de Copernicus |
| 18 | **Open-Meteo Ensemble API** (`ensemble-api.open-meteo.com`) — verificada | **51 miembros del EPS de ECMWF** (también ICON-EPS, GFS ENS) por hora y variable. **Retención de miembros individuales: solo ~4–5 días (verificado v6)** | Global | Gratis no comercial | **P_raw de despejado** = fracción de miembros con nubes<20 % → calibrador → P_calibrada (§2.2). **Archivar los 51 valores crudos en CADA descarga: no son reconstruibles** |
| 19 | **Open-Meteo Historical Forecast + Previous Runs APIs** — verificadas, **descripción corregida v6** | Historical Forecast: **serie continua construida con los leads cortos** de pasadas sucesivas (¡NO el forecast completo emitido!). Previous Runs: valores **por lead time** (`_previous_day1…7`) — verificado ≥2,5 años para ecmwf_ifs025; profundidad menor en otros modelos (comprobar por modelo) | Global | Gratis no comercial | **Backfill DETERMINISTA por lead time** (Bucle 2/MOS desde el día 1). La calibración PROBABILÍSTICA no se puede backfillear (miembros no archivados) → depende del archivo propio |
| 20 | **ESO SkyCalc** (`skymodel.eso.org`, CLI `skycalc_cli`) — **v5** | Modelo avanzado de cielo de Paranal (Luna espectral, zodiacal, airglow, twilight) | — (herramienta) | Gratis | **Banco de pruebas de `moonsky.py`/mag_sky**: parrilla de casos como test de regresión |
| 21 | **DarkSkySites.com** (motor LUMIX + simulador APEX) — **v5, analizada 26-08-2026** | SQM/Bortle modelado por punto desde **VIIRS VNP46A3 MENSUAL** con propagación tipo Garstang/Cinzano-Falchi (Rayleigh/Mie separados, oclusión por terreno, curvatura), correcciones comunitarias por SQM (LLFC), API JSON abierta (`/api/site-intelligence?lat&lng`) | Global | Web gratis; **v6: el endpoint JSON se descubrió por ingeniería inversa — NO es un contrato**: sin SLA, sin licencia publicada, y su modelo sigue en calibración. Para integrarlo en producto, acordar API/términos con ellos por escrito | **Solo inspiración, benchmark y contraste** (para Nerpio da SQM 21.71 con dataset jul-2026 vs 21.97 medidos por el TESS) + **referencia de diseño** para el B_LP(alt,az) direccional (§1.3.4). Jamás dependencia operacional en este estado |
| 22 | **Red STARS4ALL / TESS** — v5, censada y SONDEADA 27-08-2026 | **658 fotómetros con dashboard público** en el mismo Grafana que stars289 (§2.1.2). **Conector confirmado con muestra de 19 estaciones: base InfluxDB COMPARTIDA** — la misma llamada `tsdb/query` con `datasourceId:2` vale para todas cambiando solo el tag `name='starsNNN'` | Mundial (fuerte en Iberia) | Gratis | **Verdad terreno de mag_sky para la app mundial** → Bucles 2–4 regionales sin hardware propio. Realidad medida de la red: ~8/19 de la muestra emitiendo al minuto; el resto paradas de días a años, y algunas vivas emiten basura (mag=0, mag 13 fijo) → el conector FILTRA por frescura (<15 min) Y por rango físico (14≤mag≤23, el mismo del histograma oficial). Coordenadas exactas: NO están en el dashboard (solo tags "sitio, país") — geocodificar tags o usar el catálogo tessdb de STARS4ALL |
| 23 | **NASA GEOS-FP** (portal NCCS + **OPeNDAP**, verificado accesible 27-08-2026) | Forecast 5 días con **aerosoles POR ESPECIES** (dust, sea salt, BC/OC, sulfatos) a 0.25°, subsetting por OPeNDAP sin cola | Global | Gratis, sin registro | **Plan B del AOD_dust** (§1.2.1): a diferencia de CAMS ADS no encola — candidato a cerrar el OBJETIVO REAL del AOD por especies casi-síncrono |
| 24 | **DEM para el horizonte 720-bins** (§1.3.4): **IGN MDT05** (España, 5 m, descarga libre) / SRTM global; **OpenTopoData** (`api.opentopodata.org`, verificado) como API rápida de prototipo | España/Global | Gratis (OpenTopoData: ~1000 llamadas/día) | Máscara de horizonte por azimut para B_LP direccional y para `alt_min` real de targets (mejor que el corte fijo de 30°) |
| 25 | **RainViewer API** (`api.rainviewer.com/public/weather-maps.json`, verificado) | Mosaico mundial de **radar de precipitación** actualizado cada 10 min, tiles listos para web | Global (con huecos) | Gratis sin key (tier público) | Capa de nowcast de precipitación para el frontend — complementa el IR de Meteosat con "¿me llueve en 20 min?" |

### 2.1.1 Acceso concreto a cada recurso gratuito (endpoints, snippets, avisos)

**Open-Meteo — forecast multi-modelo con niveles de presión** (sin API key; **URL verificada 26-08-2026, con las dos correcciones v5**: `wind_speed_unit=ms` y `pressure_msl`+`geopotential_height` para §1.4.3c/d):
```
https://api.open-meteo.com/v1/forecast?latitude=38.16&longitude=-2.32
  &hourly=cloud_cover_low,cloud_cover_mid,cloud_cover_high,temperature_2m,relative_humidity_2m,
  dew_point_2m,wind_speed_10m,wind_gusts_10m,precipitation_probability,cape,boundary_layer_height,
  pressure_msl,temperature_850hPa,temperature_700hPa,wind_speed_500hPa,wind_speed_300hPa,
  wind_speed_250hPa,wind_speed_200hPa,geopotential_height_700hPa,geopotential_height_500hPa
  &models=ecmwf_ifs025,icon_seamless,gfs_seamless,meteofrance_seamless
  &wind_speed_unit=ms&timezone=UTC
# Verificado: una sola llamada devuelve los 4 modelos (variables con sufijo _ecmwf_ifs025, etc.);
# la celda de Nerpio tiene elevation=1302 m (registrar como metadato).

# v6 — LLAMADA COMPLEMENTARIA (esquema híbrido ECMWF, verificado 27-08-2026): el modelo
# ecmwf_ifs (~9 km, celda 38.137/-2.305 — malla más fina) da superficie, nubes y BLH
# (¡que el 0.25° no trae!) pero NO niveles de presión (temperature_700hPa → null, probado):
https://api.open-meteo.com/v1/forecast?latitude=38.16&longitude=-2.32
  &hourly=cloud_cover_low,cloud_cover_mid,cloud_cover_high,temperature_2m,relative_humidity_2m,
  dew_point_2m,wind_speed_10m,wind_gusts_10m,precipitation_probability,cape,boundary_layer_height,
  pressure_msl&models=ecmwf_ifs&wind_speed_unit=ms&timezone=UTC
# Regla: nubes/superficie/BLH del 9 km; perfiles (seeing, Ri) del 0.25°; y MEDIR en el panel
# §6.3 si el 9 km realmente mejora las nubes en Nerpio antes de darlo por superior.
```
(Confirmar variables por modelo en el configurador de open-meteo.com/en/docs. Licencia CC-BY: atribución; uso comercial → plan API de pago, barato.)

**Open-Meteo Air Quality (CAMS)** (v5: `dust` es µg/m³ EN SUPERFICIE — confirmación secundaria, jamás proxy de AOD_dust; ver §1.2.1):
```
https://air-quality-api.open-meteo.com/v1/air-quality?latitude=38.16&longitude=-2.32
  &hourly=aerosol_optical_depth,dust,pm10,pm2_5&timezone=UTC
```

**Open-Meteo Ensemble API (verificada; retención de miembros ~4–5 días — verificado v6):**
```
https://ensemble-api.open-meteo.com/v1/ensemble?latitude=38.16&longitude=-2.32
  &hourly=cloud_cover&models=ecmwf_ifs025&timezone=UTC
# → cloud_cover, cloud_cover_member01 … cloud_cover_member50
# P_raw(t) = #{miembros con cloud_cover(t) < 20%} / 51        ← BRUTA; calibrar (§2.2)
# ARCHIVAR SIEMPRE los 51 valores crudos por (run, valid, member) — probado con past_days=35:
#   los miembros devuelven null más allá de ~4-5 días; lo no archivado se pierde para siempre.
# Metadatos obligatorios: native_timestep del EPS e interpolated=true/false (interpolar a la
#   hora no crea información meteorológica nueva).
```

**Open-Meteo Historical Forecast + Previous Runs (verificadas; descripción CORREGIDA v6):**
```
https://historical-forecast-api.open-meteo.com/v1/forecast?latitude=38.16&longitude=-2.32
  &start_date=2024-03-01&end_date=2024-03-02&hourly=cloud_cover_low&models=ecmwf_ifs025
https://previous-runs-api.open-meteo.com/v1/forecast?latitude=38.16&longitude=-2.32
  &start_date=2024-03-01&end_date=2024-03-02
  &hourly=temperature_2m_previous_day1,temperature_2m_previous_day3&models=ecmwf_ifs025
# Historical Forecast = serie CONTINUA hecha con los leads cortos de pasadas sucesivas — vale
#   como "mejor estimación a posteriori", NO como el forecast emitido a +48/+72 h.
# Previous Runs = el valor que cada pasada anterior predijo para la misma hora (lead real).
#   Verificado: day-3 completo para ecmwf_ifs025 en 2024-03, 2025-08 y 2026-06 (≥2,5 años);
#   la profundidad varía por modelo — comprobarla ANTES de prometer backfill de ese modelo.
# OJO: comparten el cupo de 10k llamadas/día del plan gratuito — el backfill masivo, despacio o de pago.
```

**WMO Barcelona Dust Regional Center (AOD de polvo operativo, §1.2.1):** productos públicos de DOD 550 nm (mediana multi-modelo SDS-WAS y modelos individuales, 72 h) en `https://dust.aemet.es/products/daily-dust-products`. **Matiz v6:** la visualización es pública, pero la **descarga numérica automatizada está sujeta a registro/credenciales y a condiciones operativas que hay que confirmar** — no escribir "gratis, worker automático" hasta tener el acceso concedido. En cualquier caso NUNCA en el hot path (worker diario desacoplado, como CAMS).

**7Timer! ASTRO** (gratis, sin key; servicio personal mantenido "as-is" → tolerar caídas y JSON malformado ocasional con retry):
```
http://www.7timer.info/bin/astro.php?lat=38.16&lon=-2.32&output=json
http://www.7timer.info/bin/api.pl?lon=-2.32&lat=38.16&product=astro&output=json
# Escalas: seeing clases 1–8 (<0.5" … >2.5"); transparency 1–8 (~mag de extinción <0.3 … >1);
# cloudcover 1–9 (<6% … >94%). Documentación: wiki de github.com/Yeqzids/7timer-issues
```

**CAMS ADS directo** (registro gratuito en ads.atmosphere.copernicus.eu; librería `cdsapi`):
```python
import cdsapi
cdsapi.Client().retrieve("cams-global-atmospheric-composition-forecasts", {
  "variable": ["total_aerosol_optical_depth_550nm","dust_aerosol_optical_depth_550nm"],
  "date": "2026-08-26/2026-08-26", "time": "00:00",
  "leadtime_hour": [str(h) for h in range(0,121,3)],
  "type": "forecast", "data_format": "netcdf_zip"}, "aod.zip")
```

**AERONET web service** (verdad terreno AOD/α/PWV, sin key; **verificado activo 26-08-2026** — Granada operativa, PI Alados-Arboledas, datos de esta semana):
```
https://aeronet.gsfc.nasa.gov/cgi-bin/print_web_data_v3?site=Granada&year=2026&month=8&day=1
  &year2=2026&month2=8&day2=26&AOD15=1&AVG=20
# Listado de estaciones y parámetros en aeronet.gsfc.nasa.gov (elegir la activa más próxima al SE)
```

**AEMET OpenData** (opendata.aemet.es; API key gratuita; flujo en 2 pasos: la primera llamada devuelve una URL `datos` temporal que hay que seguir):
```
GET https://opendata.aemet.es/opendata/api/observacion/convencional/todas?api_key=...  → {"datos": url}
GET url → JSON de observaciones de estaciones (elegir las más cercanas a Nerpio: Hellín, Caravaca…)
```

**Radiosondeos reales (validación de perfiles y de Cn²):** archivo de la Universidad de Wyoming, estación **Murcia 08430** (00/12 UTC). **v5: el endpoint `cgi-bin` de la v4 está MUERTO (404, verificado)** — el servicio migró a `wsgi`, y además ofrece CSV directo (mejor que parsear el HTML):
```
https://weather.uwyo.edu/wsgi/sounding?datetime=2026-08-25%2012:00:00&id=08430&type=TEXT:CSV&src=BUFR
# type=TEXT:LIST (tabla clásica), TEXT:CSV (parseo trivial), PNG:SKEWT (diagrama);
# verificado 26-08-2026 con datos reales de Murcia. Una fecha por petición (iterar para series).
# Alternativa bulk para histórico largo: IGRA v2 (NOAA, ncei.noaa.gov) — misma estación, ficheros planos.
```

**ESO Ambient Conditions Database** (¡archivo público!): formularios de consulta de DIMM/MASS-DIMM/SLODAR/meteo de Paranal y La Silla en `archive.eso.org/cms/eso-data/ambient-conditions/paranal-ambient-query-forms.html` (DIMM nuevo desde 2016, manual ASM incluido). No sirve para operar Nerpio, pero es el **banco de pruebas perfecto**: descargar seeing medido + reanálisis/GFS de esas fechas y comprobar que nuestro estimador HV reproduce las estadísticas antes de fiarnos de él en Nerpio.

**NOAA SWPC** (Kp, sin key): `https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json`

**Atlas Falchi 2016**: GeoTIFF (≈2.9 GB, 30", mcd/m²) vía GFZ Data Services (DOI 10.5880/GFZ.1.4.2016.001), licencia CC BY-NC. Conversiones a SQM/Bortle en §1.3.3 (las mismas que documenta lightpollutionmap.info/help.html). Tendencias anuales: NASA Black Marble VNP46A4/VJ146A4 vía Earthdata (login gratuito).

**Meteosat (nowcasting nubes)**: registro gratuito en EUMETSAT Data Store + librería `eumdac` para imágenes IR cada 10–15 min; para visualización rápida en la web, servicios WMS públicos de EUMETView (revisar condiciones de uso para embed).

**TLEs para densidad de estelas** (CelesTrak, sin key): `https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle` + skyfield para contar pases iluminados sobre el campo durante la sesión.

**Catálogo de targets (para el motor de recomendación, §3.3):** **OpenNGC** (github.com/mattiaverga/OpenNGC, CSV libre: tipo, V mag, brillo superficial, dimensiones de NGC/IC) + listas curadas (Messier, Herschel 400, "Top" de Gary Imm). Prior art open-source directamente estudiable: **UpTonight** (github.com/mawinkler/uptonight — calcula los mejores targets de la noche con astroplan; leer su código antes de escribir el nuestro) y **pyastroweatherio/AstroWeather** (integración Home Assistant sobre 7Timer + Met.no, con pesos nubes:seeing:transparencia = 3:2:1 — un prior razonable).

**Librerías de cálculo local:** `pip install astropy astroplan skyfield rasterio` (efemérides, restricciones de observabilidad, muestreo del GeoTIFF).

### 2.1.2 Sensores de AstroCamp: inventario verificado y endpoints (v5, 26-08-2026)

La página de estado `http://com1.astrocamp.es:81/cloud.html` (HTML estático, refresco 60–120 s, parseable con regex/BeautifulSoup) agrega TODO esto — y cambia el arranque del proyecto, porque la "capa de sensores futura" del roadmap **ya existe en gran parte**:

| Recurso | Qué da | Acceso verificado |
|---|---|---|
| **Fotómetro TESS stars289 (red STARS4ALL)** | `mag` (mag/arcsec², cadencia ~1 min), `tamb`, `tsky` (T de cielo IR = detector de nubes), **serie desde ~2019** | Grafana anónimo: dashboard `tess.dashboards.stars4all.eu/d/datasheet_stars289`; **datos crudos por POST a `/api/tsdb/query` con `datasourceId:2`** e InfluxQL sobre `mqtt_consumer` con `name='stars289'` (probado: anoche máx **21.97 mag/arcsec²**) |
| **Allsky (indi-allsky)** | Imagen actual + GIF + vídeos | `https://com2.astrocamp.es:85/allsky/public/AllSkyCurrentImage.JPG` (+ `/indi-allsky/videoviewer`) |
| **2 × CloudWatcher** | SkyTemp IR (−15.8 °C anoche = despejado), índice de cielo | Tabla "Cloud" de cloud.html |
| **2 × estación meteo** | T, RH, punto de rocío, viento km/h, dirección, **presión 843.3 hPa** (¡la presión real del sitio para §1.4.3d!), lluvia | Tabla "Meteo" de cloud.html |
| **Estado de techos** (S1R1…S2R4, Open/Closed) | **Verdad terreno de "se está observando"** → automatiza el flag `censored` de §6.4.6 | Tabla de estados de cloud.html |
| **Horas de cúpula abierta por mes, 2012–2026** | ~14 años de climatología de noche útil real (2.100–2.500 h/año típico) | Tabla "Open Dark Time Statistics" de cloud.html |
| Efemérides + código MPC **I79** | Lat/lon exactos del observatorio (38:09:56.4, −2:19:36.2) | Bloque "Ephemerids" de cloud.html |

**Sondeo del conector TESS multi-estación (27-08-2026, 19 estaciones probadas):** la base InfluxDB del Grafana es **compartida** — `POST https://tess.dashboards.stars4all.eu/api/tsdb/query` con `datasourceId:2` y `SELECT "mag" FROM "mqtt_consumer" WHERE ("name"='starsNNN') ORDER BY time DESC LIMIT 1` funciona para cualquier estación cambiando solo el tag. Resultado de la muestra: **8/19 en vivo** (dato ≤1 min: stars1, stars4, stars222, stars289, stars715, stars999, stars1000, stars1365; cadencia ~1/min, mag 16.7–18.9 plausibles con Luna), 3 enfermas (stars60 parada 3 días; stars530 emite mag=0; stars1412 mag 13.25 fijo) y 8 muertas (stars90 desde jul-2024) → **~40 % de red viva ≈ 250–300 fotómetros útiles en el mundo**. Reglas del conector derivadas: (1) filtrar por **frescura** (<15 min) Y por **rango físico** (14 ≤ mag ≤ 23, el corte de los paneles oficiales); (2) estación muerta = caso normal, no error; (3) **las coordenadas no viajan con los datos** — el dashboard solo lleva tags de texto (`api/dashboards/uid/datasheet_starsNNN` → `tags: [sitio, región, país]`): geocodificar tags o usar el catálogo tessdb de STARS4ALL. Primer entregable de implementación: `fetch_tess.py` — descarga histórica a Parquet de stars289 + las ibéricas activas (misma función, lista de nombres).

Consecuencias (aplicadas en §2.3.1, §4.1 y §6): el TESS **cumple el papel operativo** del SQM del roadmap (con ~7 años de histórico, no cero) — **matiz v6: fotométricamente TESS ≠ SQM** (respuestas espectrales distintas): en `measurements` se guarda `instrument_type=TESS, instrument_id=stars289, bandpass_id, raw_mag` y, si se quiere escala "equivalente SQM", `calibrated_mag` con una calibración explícita y versionada (§1.3.5) — nunca identificarlos sin más; el CloudWatcher ES el sensor de nubes IR; los Bucles 1–3 dejan de esperar a compras de hardware; la tabla de horas abiertas es la **climatología-baseline** del panel §6.3; y la calibración γ espejo/escudo de §1.3.3b tiene años de pares (SQM, nubes) ya grabados. Cautelas: el puerto 81 es HTTP sin SLA (scraper defensivo, tolerar caídas); hay sensores muertos mezclados en las tablas con su timestamp viejo (meteo 3 congelada desde 2025) → **filtrar SIEMPRE por frescura del timestamp, no por posición en la tabla**; y confirmar con AstroCamp el permiso de uso continuado de estos endpoints públicos.

## 2.2 Estrategia de triangulación (el "fleco" que pediste)

Tres niveles de confianza, de menor a mayor horizonte:

1. **Ahora (0–1 h): sensores locales mandan.** Allsky (detección de estrellas → nubes reales y extinción medida), SQM (fondo real), T/RH (rocío). Si el sensor dice nublado, la predicción se anota como fallida (dato de verificación).
2. **Nowcast (0–3 h): satélite IR Meteosat** + extrapolación de movimiento. Detecta la banda de cirros que el GFS no vio.
3. **Forecast (3 h–7 días): ensemble en DOS dimensiones.** (a) **Probabilística (v6 — con nombre honesto)**: los 51 miembros del EPS de ECMWF vía la Ensemble API → `P_raw(t) = #{miembros con nubes < 20%}/51`. Es la **probabilidad BRUTA del ensemble** — una fracción de miembros, NO una garantía de frecuencia observada en AstroCamp. La cadena completa es `P_raw → calibrador (regresión isotónica o logística, por lead time y estación, entrenado contra allsky/TESS) → P_calibrada`, evaluada con Brier score y curva de fiabilidad en el panel §6.3; hasta que el calibrador exista, la UI etiqueta el valor como "prob. del modelo (sin calibrar)". Sustituye a la fórmula ad-hoc `1 − σ/50` de versiones anteriores. (b) **Acuerdo entre familias**: nubes de ECMWF, ICON, GFS y AROME (una sola llamada con `&models=a,b,c,d` — verificado) → la dispersión entre modelos independientes detecta el caso "todo el EPS de ECMWF se equivoca junto" que (a) no puede ver. Mostrar SIEMPRE ambas: mediana + P + acuerdo.

Reglas de coherencia cruzada (confirmación de la información):
- AOD de Open-Meteo AQ vs CAMS ADS directo → deben coincidir (misma fuente, distinto pipeline); si divergen, hay bug.
- Seeing estimado propio vs meteoblue (visual) vs 7Timer → si los tres coinciden en "malo", fiable; si divergen, mostrar incertidumbre.
- Predicción de anoche vs FWHM/allsky medidos → métrica de acierto pública por fuente (¡gran contenido para la web!: "en Nerpio, ECMWF acierta el 84% de horas despejadas, GFS el 71%").

## 2.3 Qué miden los observatorios profesionales (referencia de diseño)

| Observatorio | Instrumentación de sitio | Lección para CabraSpace |
|---|---|---|
| ESO Paranal (ASM) | DIMM (seeing), MASS (perfil Cn² en 6 capas), SCIDAR, LHATPRO (PWV), SQM, allsky | Separan seeing total vs capa límite vs atmósfera libre; publican todo en archivo. El nowcasting ML (Milli+2019) usa históricos del propio sitio |
| Maunakea (MKWC) | Forecast operacional de seeing con WRF + parametrización Cn² (Lyman+2020) | Es viable predecir seeing con NWP + calibración local |
| Calar Alto (CAHA, Almería — el observatorio profesional más cercano a Nerpio) | RoboDIMM, monitorización de extinción; estudios publicados de su clima: mediana de seeing ~0.9", episodios de calima cuantificados (Sánchez et al. 2007/2008) | **Sus estadísticas de calima y extinción son extrapolables al SE peninsular** — usar como prior climatológico de Nerpio |
| ORM La Palma (ING) | RoboDIMM, SkyProbe extinción, estudios de fondo de cielo (Benn & Ellison 1998) | Metodología de fotometría de extinción con cámara |

### 2.3.1 Sistemas operacionales de predicción en observatorios (estado del arte, revisado)

| Sistema | Sitio | Método | Qué enseña |
|---|---|---|---|
| **ALTA Center** (INAF-Arcetri) | LBT, Mt. Graham | Astro-Meso-Nh mesoescala; opera desde 2016; forecast largo (tarde para la noche) + corto (1–2 h, actualizado cada hora) | La arquitectura de referencia: dos horizontes de predicción distintos, web pública nightly (alta.arcetri.inaf.it) |
| **FATE** | VLT, Paranal | Mismo grupo/método que ALTA para ESO | Estándar actual de ESO junto al ASM |
| **MOSE** (Masciadri+ 2013) | Paranal/Armazones | Estudio de viabilidad que fundó lo anterior | Metodología de validación modelo-vs-DIMM |
| **Método autorregresivo** (Masciadri+ 2020, MNRAS 492) | ALTA/FATE | **Fusión forecast + medidas in situ en tiempo real**: corrige el forecast con la autorregresión de sus errores recientes; precisión sin precedentes a 1–2 h | **La joya replicable**: no requiere mesoescala, solo un forecast base + un sensor local |
| **MKWC** (Lyman, Cherubini & Businger 2020) | Maunakea | WRF + parametrización Cn²; página pública de forecast | Se puede predecir seeing con NWP puro razonablemente |
| **Nowcasting ML Paranal** (Milli+ 2019, 2020) | Paranal | Random forest / ML sobre históricos del ASM + ERA5 | El histórico del propio sitio vale más que el mejor modelo global |
| **Scheduling ML** (arXiv:2407.16049, 2024) | VLT/ELT | Random forest de seeing a 1 h para elegir qué OB ejecutar | Exactamente el caso de uso "decidir qué target acometer" |
| **OTCliM** (2025, gradient boosting) | genérico | Climatología de Cn² superficial por ML | La vía barata para el término de capa límite |
| **Site testing TMT** (Schöck+ 2009) | comparativa mundial | DIMM + MASS + SODAR + escintilómetro lunar | Qué instrumentación mide qué capa |

**Escalera de replicabilidad para CabraSpace** (de gratis-hoy a ambicioso):
1. **Idéntico a los profesionales desde el día 1:** modelo lunar K&S, extinción k(AOD, altitud), efemérides de precisión, definición de noche por crepúsculos — es literalmente lo mismo que usan los planificadores de ESO/Gemini.
2. **"Astro-Meso-Nh de los pobres":** perfil HV(W) con vientos de niveles de presión (§1.4.3b) → ε, θ0, τ0 horarios. Validar contra el archivo público del DIMM de Paranal + contra radiosondeos de Murcia antes de publicarlo.
3. **Método autorregresivo (el mejor ratio esfuerzo/beneficio):** en cuanto haya SQM + sensor de nubes + FWHM en vivo, aplicar la corrección de Masciadri 2020: `pred_corr(t+1h) = pred(t+1h) + AR(errores últimos 60–120 min)`. Es un filtro de ~20 líneas de Python y es lo que ESO considera estado del arte a 1–2 h.
4. **ML del sitio:** gradient boosting (estilo OTCliM/Milli) con 6–12 meses de pares (predictores NWP → FWHM medido). Publicar las métricas.

Hardware local — **actualización v5 tras el inventario §2.1.2: los puntos (1) y (2) YA EXISTEN en AstroCamp** (TESS stars289 con ~7 años de serie pública = el SQM; 2 CloudWatchers = el sensor IR; allsky operativa con indi-allsky). Lo único que falta de verdad es **(3) el pipeline de FWHM sobre las imágenes ya existentes (= 0 € y es software)**, que pasa a ser LA prioridad de hardware/datos del proyecto; (4) DIMM DIY sigue como proyecto futuro.

---

# 3. SALIDAS DE OBSERVABILIDAD (v6 — tres salidas separadas, no un número)

## 3.1 Diseño

**El score único 0–100 de versiones anteriores mezclaba tres conceptos que no deben multiplicarse entre sí**: una probabilidad meteorológica, penalizaciones de calidad científica y riesgos de seguridad del equipo. Además el producto de 7 factores era ininterpretable (siete 0.9 → 48 sin que nada esté "mal"). La v6 separa las tres preguntas, cada una con su matemática propia:

**Salida 1 — Gates de seguridad (¿puedo abrir?):** binarios o casi, evaluados por hora:
```
safe_to_open = rain_ok AND gust_ok AND storm_ok AND hardware_ok
# rain_ok:  sin precipitación en sensor/radar ahora, prob_precip horaria bajo umbral
# gust_ok:  racha < umbral de cierre (35–40 km/h; configurable por equipo)
# storm_ok: CAPE/convección como RIESGO ESCALONADO (v6 — CAPE ya NO cierra por sí solo):
#           0–60 min → sensor lluvia + radar (RainViewer) + allsky mandan
#           1–6 h    → prob. precip + convección + radar extrapolado
#           >6 h     → ensemble de precipitación; CAPE alto = "riesgo elevado", no S=0
```

**Salida 2 — P_usable (¿podré mantener abierto y aprovechar?):** probabilidad **calibrada** por hora de conseguir cielo utilizable: parte de la P_raw del EPS (§2.2) pasada por el calibrador, combinada con nowcast local (allsky/CloudWatcher) en las primeras horas. Se publica con su etiqueta de calibración ("calibrada N meses" / "sin calibrar aún").

**Salida 3 — Calidad científica esperada (¿cómo de buena será la hora?):** cada dimensión POR SEPARADO, con incertidumbre, en unidades interpretables:
```
q_dark   = min(1, B_ref/B_sky)          # EN FLUJO (v6): B ∝ 10^(−0.4·mag_sky); interpretación
                                        #   directa: "esta hora rinde el 43 % de la integración
                                        #   útil de una hora de referencia". Sin exponentes 1.5
                                        #   inventados. B_ref = el fondo de la noche de referencia
                                        #   del sitio (Nerpio: mag 21.9).
q_transp = 10^(−0.4·(k_total − k_ref))  # capado a [0,1]; k_ref se APRENDE por sitio y banda
                                        #   (percentil 20 del k medido, §6 Bucle 3) — el 0.18 es
                                        #   solo el valor de arranque de Nerpio, no una constante
q_seeing = tercil + rango (§1.4.4)      # favorable/normal/desfavorable + "1.4–2.1″ (experimental)"
q_cloud  = ((100 − C_eff)/100)²         # C_eff con solape (§1.1)
margen_rocio = T_equipo − Td [°C]       # §1.5 v6: en grados con signo, con dew_heater configurable
```
Para el usuario que quiera "un número", se publica un agregado **orientativo** `Q = exp[Σ w_i·ln(max(q_i, ε)) / Σ w_i]` (media geométrica ponderada — no se hunde por acumular factores neutros y conserva el cero efectivo si una dimensión colapsa). Pero el agregado es presentación: **para decidir QUÉ observar manda §3.3 (horas-SNR-equivalentes por target), que es el producto central de CabraSpace** — casi todas las apps dicen "cómo estará el cielo"; esta dice *qué fotografía produce más información esta noche*.

**Regla de producto (se mantiene y refuerza): el JSON publica SIEMPRE el desglose** `{gates, P_usable, q: {...}, limitante}` — el factor limitante es el "porqué" de cada hora. Los pesos w_i por perfil de target son los antiguos w_s reinterpretados; todo calibrable en §6 Bucle 3.

**Perfiles de target** (cambian los pesos w_i del agregado y algún umbral):
- `DSO banda ancha`: peso de seeing bajo, q_dark estricto.
- `DSO banda estrecha`: q_dark relajado con Luna — el fondo con Luna se calcula **por filtro**. **v6: los factores fijos (Ha 3 nm ≈ 0.05 · OIII ≈ 0.20 · SII ≈ 0.08) se degradan a fallback `approximation_quality=low`** — son intuición plausible, no coeficientes establecidos (dependen del espectro lunar con la fase, la extinción, la anchura y forma real del filtro, las fugas fuera de banda y la QE del sensor). **La fuente buena es una LUT generada con SkyCalc** (§1.3.5): `moon_phase × moon_sep × moon_alt × target_alt × AOD/PWV × filtro (centro, FWHM)` → degradación del fondo para Ha/OIII/SII en 3/5/7 nm y L/RGB, precalculada una vez y consultada en O(1). Consecuencia de producto que se mantiene: hay noches de Luna "solo Ha" que un factor único jamás distinguirá. Transparencia sigue importando (halos en OIII).
- `Planetaria/Luna/dobles`: peso de seeing dominante (τ0 del perfil HV), q_dark ≈ irrelevante, q_cloud tolera nubes de paso.
- `Fotometría/exoplanetas`: q_transp estricto (k estable, sin cirros: exigir además σ entre modelos baja).

## 3.2 Salidas por noche (agregados que la gente quiere ver)

- **Horas útiles (v6):** Σ de horas con `safe_to_open` y `P_usable > 0.6` entre crepúsculos astronómicos (umbral configurable).
- Mejor ventana continua (inicio, fin, y sus q por dimensión).
- "Semáforo" de noche: mejor bloque de 3 h.
- **La frase estrella (v6, del motor §3.3):** «Entre las 00:40 y las 04:10 tendrás 3,5 h reales que equivalen a **2,9 h de una noche de referencia para M51**; para IFN solo 0,8 h equivalentes (la Luna sube el fondo); para Q2237, 2,4 h (el fondo importa poco, el seeing previsto sí)».
- Confianza: P_calibrada + acuerdo entre familias (§2.2) — mostrar SIEMPRE junto a cualquier valor.
- Extras de planificación (ya con astropy/astroplan): altura de la Luna toda la noche, % iluminación, separación Luna-target si el usuario indica target, tránsito por el meridiano.

## 3.3 Motor de targets: «esta noche IFN sí, Cruz de Einstein no» — EL PRODUCTO CENTRAL (v6)

Las salidas de §3.1 dicen si la noche es utilizable; esta capa dice **para qué** es buena — y en la v6 deja de ser un refinamiento tardío: **las horas-SNR-equivalentes (§3.3.3b) son el diferencial competitivo del proyecto** y se implementan con el MVP. Dos ejes físicos independientes clasifican cualquier target:

- **Eje contraste** — brillo superficial del target (SB, mag/arcsec²) frente al fondo de cielo previsto `mag_sky` (§1.3.4). En imagen limitada por fondo: `SNR ∝ F_obj·√t/√B_sky` → el tiempo para alcanzar una SNR fija escala con el brillo del fondo: **cada magnitud de cielo perdida multiplica el tiempo necesario ×2.5** (21.0 vs 22.0 → ×2.5; 20.0 → ×6.3; 19.0 → ×16). Regla operativa: interesa la **diferencia Δ = SB_target − mag_sky**; con Δ ≳ 4–5 mag el target es "de cielo excelente" (solo sale con fondo oscuro y muchas horas).
- **Eje resolución** — escala angular del detalle buscado frente al FWHM previsto (ε del §1.4). Detalle mínimo recuperable ≈ 1×FWHM (≈0.8×FWHM con buen muestreo y deconvolución). La Luna y la LP no afectan a este eje; el jet stream sí.

### 3.3.1 Matriz de decisión (gates por clase de target)

**Estatus v6 de estos umbrales: heurísticas editoriales, no fronteras físicas.** Los cortes (21.5, 20.8, 1.8″…) son criterio curado razonable para arrancar y comunicar — pero el motor fino decide con las horas-SNR continuas (§3.3.3b), y la matriz queda como corte de cordura y como lenguaje para el usuario. El Bucle 3 (§6) los revisa contra sesiones reales.

| Clase (ejemplos) | Gate de cielo (mag_sky cenit) | Gate de seeing (FWHM) | Luna | Transparencia | Nota |
|---|---|---|---|---|---|
| **IFN, colas de marea, halos (SB ≳ 26)** (IFN de M81/82, Arp loops) | ≥ 21.5 | indiferente | < 5% o bajo horizonte | AOD ≤ 0.08, sin cirros | El target más exigente en contraste que existe |
| Nebulosas débiles banda ancha (LBN, SNR tenues) | ≥ 21.2 | ≤ 4" | < 20% y separación > 90° | AOD ≤ 0.12 | |
| Galaxias con detalle / brazos (NGC 891, M51) | ≥ 20.8 | ≤ 2.5" | < 40%, sep > 60° | media | Ejes contraste y resolución a la vez |
| **PN pequeñas, detalles finos, lentes: Cruz de Einstein (Q2237+030, componentes a ~1.8")** | ≥ 20 basta | **≤ 1.5–1.8"** + escala ≤ 0.8"/px | tolera | media | Casi solo eje resolución: noche de jet en calma |
| Nebulosas en banda estrecha (Ha/OIII/SII 3–7 nm) | ≥ 19 (aplicar el factor lunar POR FILTRO de §3.1 — v5: Ha ~0.05, OIII ~0.20, SII ~0.08) | ≤ 3.5" | hasta ~60% si sep > 60° | **sin cirros** (halos en OIII) | La noche "con Luna" bien aprovechada; con Luna alta puede ser "solo Ha" |
| Cúmulos (globulares/abiertos) | ≥ 19.5 | ≤ 2.5" ayuda | tolerante | media | Poco exigente: target de noche mediocre |
| Planetaria / Luna / dobles | irrelevante | **τ0 y ε lo son todo** | irrelevante | alta ayuda | Lucky imaging: mirar τ0 del perfil HV |
| Fotometría / exoplanetas / ocultaciones | estable > oscuro | moderado | tolera | **k constante**: σ ensemble baja, sin cirros, centelleo bajo (X < 1.5) | "Noche fotométrica" ≠ "noche bonita" |
| Cometas / targets bajos (< 30°) | según SB | dispersión atmosférica → limitar altura o ADC | según SB | transparencia de horizonte | Extinción y refracción diferencial crecen rápido |

### 3.3.2 Implementación del motor

```python
# catálogo: OpenNGC (CSV libre: tipo, Vmag, surf_bright, dimensiones) + lista curada propia
# condiciones por hora: mag_sky(t), fwhm(t), aod(t), moon(alt, phase, sep(target)), sigma_ensemble(t)

def feasibility(target, cond):
    gates = GATES[classify(target)]          # tabla 3.3.1 en un dict
    ok_sky   = cond.mag_sky_eff(target) >= gates.sky      # con B_moon direccional (K&S, §1.3.2)
    ok_see   = cond.fwhm <= gates.fwhm
    ok_moon  = moon_rule(cond, gates, target)
    ok_trans = cond.aod <= gates.aod and cond.cirrus_ok
    alt_ok   = target.max_alt_tonight >= 30               # astroplan: airmass, tránsito
    return verdict(ok_sky, ok_see, ok_moon, ok_trans, alt_ok)   # ✔ / ~ (marginal) / ✘ + motivo

# salida /api/v1/tonight → lista ordenada por (feasibility, altura de tránsito, horas útiles)
```

Ejemplo de salida para la web (el "guideline fácil" pedido):

> **Esta noche (pred.: SQM 21.7, FWHM 2.4", AOD 0.05, Luna 3% bajo el horizonte):**
> ✔ IFN y colas de marea (cielo excelente y sin Luna — noche rara: aprovéchala para SB extremo)
> ✔ Banda ancha profunda, LBN
> ✘ Cruz de Einstein / PN pequeñas (seeing 2.4" > 1.8" — jet a 42 m/s)
> ~ Galaxias con detalle (posible, resolución limitada)
> ✔ Banda estrecha (innecesario hoy: guárdala para noches con Luna)

Reglas de producto: cada veredicto lleva SIEMPRE su motivo en una frase; ordenar la lista empezando por lo que SOLO se puede hacer esta noche (coste de oportunidad: si hay SQM 21.7 sin Luna, recomendar IFN antes que un globular que sale cualquier noche).

### 3.3.3 Refinamientos v3 del motor de targets

**a) Doble zona de brillo superficial (la trampa del SB promedio).** El SB de catálogo de OpenNGC es un promedio que miente en objetos con alto rango dinámico: M51 tiene núcleo/brazos brillantes pero puente de marea y halo debilísimos. Con gate único, el motor recomendaría M51 en noche mediocre y sacrificaría justo lo que la hace especial. Solución: dos gates por objeto extendido:

```
targets.sb_core       # estructura principal (catálogo)     → gate estándar de su clase
targets.sb_faint      # estructura débil (halo, colas, IFN circundante) — SOLO para los ~50
                      #   objetos top curados a mano (v5); v6: el default "sb_core + 3" para el
                      #   resto SE ELIMINA — ese +3 era inventado y generaba veredictos con
                      #   apariencia de dato. En su lugar:
targets.difficulty    # v6 — categoría curada de dificultad para el resto del catálogo:
                      #   'extremo_contraste' | 'bajo_contraste' | 'normal' |
                      #   'compacto_resolucion' | 'narrowband'
                      #   (una etiqueta honesta comunica mejor que un número falso)
veredicto = { 'completo' si cumple gate_faint (solo objetos con sb_faint REAL),
              'parcial'  si solo cumple gate_core,           # "M51 sí, pero sin marea"
              'no'       si ninguno }
# OBJETIVO REAL (pendiente): derivar sb_faint semiautomáticamente de fotometría de surveys
# profundos (p. ej. perfiles de brillo de DESI Legacy Surveys) y validar a mano — hasta
# entonces, 50 objetos bien curados > 20.000 objetos con números incomparables.
```

El veredicto "parcial" con su motivo es información de planificación de primera: "esta noche M51 core ✔ / halo ✘ — guarda el halo para la ventana sin Luna del día 12".

**b) Masa de aire dinámica (no evaluar en el cenit lo que se fotografía a 45°).** Un target que culmina a 45° sufre X≈1.41 toda la noche: seeing ×X^0.6 (+23%), extinción +k·(X−1) y fondo distinto. El motor debe evaluar la trayectoria real, no el cenit:

```
horas_snr_equivalentes(target) = Σ_h  Q(h)
Q(h) = 10^(−0.8·k·(X_h−1))                        # pérdida de SNR² por extinción (señal²)
       · [B_ref / B_sky(alt_h, az_h, luna)]        # fondo real en la posición del target
       · (FWHM_ref / (fwhm_cenital·X_h^0.6))^(2·α) # v4: peso ANALÍTICO de seeing por morfología
# α_comp (por target o por zona sb_core/sb_faint del §3.3.3a):
#   α = 0    fuentes extensas ≫ seeing (IFN, nebulosas gigantes): el FWHM no cambia la SNR/px
#   α = 0.5  galaxias y estructura intermedia
#   α = 1    fuentes compactas (PN pequeñas, Cruz de Einstein, fotometría estelar): el ruido de
#            fondo en la apertura escala con FWHM² → seeing 1.5" vs 3" = ×4 en velocidad de SNR
# se reporta "3.2 h equivalentes-cenit" en vez de "5 h sobre 30°": comparable entre targets
```

Consecuencia práctica: dos targets con la misma ventana nominal dejan de empatar — el que transita alto gana; el seeing degradado a 40° penaliza cuadráticamente a los targets compactos (α=1) y nada a la IFN (α=0); y el gate duro de la matriz 3.3.1 queda solo como corte de cordura, porque la ponderación fina ya es continua. `astroplan` da alt/az por hora; el resto es la línea de arriba.

---

# 4. ARQUITECTURA PARA CABRASPACE (global por diseño)

Todas las fuentes elegidas son globales → la misma API sirve para Nerpio y para cualquier lat/lon (la app "para todo el mundo" sale gratis). Lo único local es la capa de sensores, que se declara opcional por sitio.

```
┌────────────────────────────────────────────────────────────┐
│ FRONTEND (CabraSpace)                                      │
│  - Widget "Esta noche en Nerpio" (score horario, semáforo) │
│  - Gráfica 7 días: nubes 3 capas + seeing + mag_sky + luna │
│  - Animación satélite IR + allsky en vivo                  │
│  - Selector lat/lon o mapa → misma vista para cualquier    │
│    sitio  (modo "app mundial")                             │
├────────────────────────────────────────────────────────────┤
│ API propia (FastAPI, Python)                               │
│  GET /api/v1/astroweather?lat&lon&days&profile=dso_broad   │
│  → JSON horario: raw + derivados + score + confianza       │
├────────────────────────────────────────────────────────────┤
│ WORKERS (scheduler simple cada 1–3 h) + CACHE (SQLite)     │
│  fetch_openmeteo(models=[ecmwf,icon,gfs,arome], plevels)   │
│  fetch_airquality(aod, dust)                               │
│  fetch_aemet(), fetch_swpc() [opc], fetch_meteosat_frames()│
│  sample_falchi_geotiff(lat,lon)  [una vez por sitio, cache]│
├────────────────────────────────────────────────────────────┤
│ CORE de cálculo (sin red, puro Python)                     │
│  ephem.py      → astropy/astroplan: sol, luna, twilights   │
│  moonsky.py    → Krisciunas–Schaefer completo (§1.3.2)     │
│  extinction.py → k_total desde AOD+altitud (§1.2)          │
│  seeing.py     → estimador 2 términos (§1.4.3)             │
│  score.py      → índice (§3) por perfil                    │
├────────────────────────────────────────────────────────────┤
│ VERDAD TERRENO + CALIBRACIÓN                               │
│  ingest allsky/SQM/estación AstroCamp (MQTT o ficheros)    │
│  ingest FWHM de las sesiones (parser de logs / FITS headers)│
│  tabla measurements(t, fwhm_zenith_500nm, sqm, clouds_real)│
│  job mensual: reajusta coeficientes de seeing.py y publica │
│  métricas de acierto por modelo                            │
└────────────────────────────────────────────────────────────┘
```

**Regla de arquitectura v6 (de la auditoría externa, adoptada como norma):** el flujo es
```
PROVEEDORES → RAW ARCHIVE → NORMALIZACIÓN → QC → MODELOS FÍSICOS → CALIBRACIÓN LOCAL → PRODUCTOS
```
y **el raw nunca se sobrescribe ni se sustituye por el valor corregido**. Cada predicción archivada lleva simultáneamente `raw_proveedor`, `derived_physics`, `locally_calibrated`, y después `observation` y `verification_error`. Es lo que permite responder dentro de dos años: "¿fallamos porque ECMWF falló, porque la fórmula era mala, o porque el sensor estaba roto?".

**Simplicidad v6:** para un solo sitio, **nada de Redis/Celery/ARQ** — FastAPI + un scheduler embebido (APScheduler) + SQLite (estado/caché operativa) + Parquet/DuckDB (histórico/analítica) + filesystem o R2 (raw). Redis y colas entran solo cuando haya concurrencia multiusuario real. Cuantos menos componentes con estado, más tiempo para comprobar la ciencia en vez de mantener infraestructura.

Notas de implementación para Claude Code:
1. **Open-Meteo, UNA llamada para todos los modelos** (v5, verificado: `&models=a,b,c,d` devuelve las variables con sufijo `_modelo`), **siempre con `&wind_speed_unit=ms`**. Variables sugeridas: `cloud_cover_low,cloud_cover_mid,cloud_cover_high,temperature_2m,relative_humidity_2m,dew_point_2m,wind_speed_10m,wind_gusts_10m,precipitation_probability,cape,boundary_layer_height,pressure_msl` + niveles `temperature_850hPa,temperature_700hPa,wind_speed_250hPa,wind_speed_300hPa,geopotential_height_*` (verificar nombres exactos por modelo; no todos publican todos los niveles; BLH solo GFS).
2. Air Quality API: `aerosol_optical_depth,dust,pm10` — recordando que `dust` es superficie (µg/m³): el AOD de polvo viene del worker CAMS/BDRC (§1.2.1 v5).
3. El GeoTIFF de Falchi (2.9 GB) no va al servidor web: preprocesar una vez → o bien recortar España/Europa, o bien muestrear bajo demanda desde un objeto S3 con rasterio en un worker, y cachear por celda de 30". **(v5) Y no va al MVP en absoluto**: para Nerpio la LP base es una constante del sitio (~21.9 medible con SQM); el atlas entra con la página "cualquier localización" (Fase 2).
4. Zona horaria y crepúsculos: todo en UTC internamente; presentar en hora local del sitio.
5. Respetar licencias: Open-Meteo CC-BY (atribución en el footer), Falchi CC BY-NC (ojo si la app mundial se monetiza — para uso comercial, contactar, sustituir por el atlas de Lorenz 2022 —libre— o calcular desde VIIRS), CAMS/Copernicus atribución obligatoria, meteoblue NO se escrapea. (v5) Las APIs de Open-Meteo (forecast, AQ, ensemble, históricas) comparten el cupo gratuito de ~10k llamadas/día: el backfill histórico masivo se hace despacio o con un mes del plan de pago.
6. Empezar simple: v1 = un solo modelo (ECMWF) + su EPS (probabilidad de despejado — v5: es una llamada más, no una fase más) + AOD + efemérides + score. El multi-modelo, satélite y ML después.

**Notas v5/v6 de ingesta:**
- **Worker nuevo `fetch_dust_aod()`** (diario): CAMS ADS (`dust_aerosol_optical_depth_550nm`, 120 h) + Barcelona Dust RC (72 h, cuando el acceso numérico esté concedido) → tabla propia consultada por `extinction.py`. Es el sustituto del rol que la v4 asignaba por error a la Air Quality API (§1.2.1). Explorar GEOS-FP para el perfil vertical (§1.2.1 v6).
- **Worker `fetch_ensemble()`** (cada 6 h): los 51 miembros de nubes del EPS → **archiva los 51 valores crudos por (run, valid, member)** — retención upstream ~4–5 días (v6, verificado), lo no archivado se pierde — y calcula P_raw por hora (§2.2).
- **Worker `fetch_openmeteo()` en modo híbrido (v6):** `ecmwf_ifs` (9 km: superficie/nubes/BLH) + `ecmwf_ifs025` (perfiles) + ICON/GFS/AROME (§2.1.1).
- **Job único de arranque `backfill_history()`**: Historical Forecast + Previous Runs APIs → puebla `forecasts` con 2+ años de histórico por modelo y lead time, respetando el rate limit (§6).
- **Asserts de unidades en la ingesta**: vientos en m/s (si V250>90, algo devolvió km/h), presiones en hPa, alturas en m. Los dos bugs silenciosos encontrados en la revisión v5 (km/h y surface_pressure del modelo) se cazan aquí.

**Notas v4 de ingesta y rendimiento:**
- **Open-Meteo por SDK binario:** usar la librería oficial `openmeteo-requests` (+`openmeteo-sdk`, serialización FlatBuffers → arrays NumPy directos, con caché y retry incorporados) en lugar de parsear JSON. Dimensionado honesto: para un solo sitio el JSON es irrelevante; el SDK importa por comodidad hoy y por rendimiento cuando la app mundial pida cientos de celdas.
- **cdsapi (CAMS ADS) JAMÁS en el camino caliente:** sus peticiones se encolan en Copernicus (de 2 a >45 min). Open-Meteo Air Quality es la fuente primaria síncrona (<1 s) **del AOD total**; el worker de cdsapi corre desacoplado en background (v6: un job del scheduler con retry basta — sin ARQ/Celery, ver regla de simplicidad) y sus GRIB/NetCDF alimentan la tabla diaria de AOD_dust que consulta `extinction.py` (§1.2.1 — el forecast de 120 h hace que un prefetch al día baste) además del archivo Parquet de reconciliación y reanálisis.

Endpoints ampliados (v2):
```
GET /api/v1/astroweather?lat&lon&days&profile=...   # horario: raw + derivados + score + confianza
GET /api/v1/tonight?lat&lon[&targets=M81,Q2237]     # agregados de la noche + matriz de clases §3.3
GET /api/v1/verification?site=nerpio                # métricas de acierto por fuente (transparencia del sistema)
```
Módulos nuevos en el core: `hv_profile.py` (Cn² HV(W) → ε, θ0, τ0), `targets.py` (OpenNGC + gates §3.3, astroplan), `arcorrect.py` (corrección autorregresiva con sensores, escalera §2.3.1-3), `scintillation.py` y `refraction.py` (flags de fotometría y de targets bajos).

## 4.1 Roadmap (v6 — con Fase 0 de instrumentación; regla rectora: *archivar y medir antes de sofisticar*)

- **Fase 0 — Instrumentación de datos (la primera semana, ANTES de cualquier frontend):** esquema canónico versionado (§6.1 v6) + archivo raw; `backfill_history()` (Previous Runs, determinista); **archivo de cada run y de CADA MIEMBRO EPS desde la primera ejecución** (no reconstruibles, §2.1.1); ingesta TESS (histórico ~7 años a Parquet con `fetch_tess.py`) + scraper de cloud.html (CloudWatcher, meteo, techos) con QC de frescura; y el **pipeline de FWHM científico**: FITS → detección de estrellas → FWHM robusta → plate-solve/altura → corrección a cenit y λ → QC → Parquet. Cada noche sin este pipeline es dato irrecuperable — corre aunque no exista web.
- **Fase 1 — Nerpio fiable (MVP):** ECMWF híbrido 9 km + 0.25° **+ EPS (P_raw, archivando miembros)** + Air Quality (AOD total) + astropy/Krisciunas-Schaefer + mag_sky base de Nerpio como constante del sitio + **gates de seguridad + P_usable + calidad por dimensión (§3.1 v6)** + **horas-SNR-equivalentes para la lista curada de targets (§3.3 — el producto central entra AQUÍ, no en fase avanzada)**. Seeing solo como tercil+rango experimental. Widget de 72 h en CabraSpace. Tests de `moonsky.py` contra SkyCalc antes de publicar.
- **Fase 2 — Calidad científica:** multi-modelo 4 familias con acuerdo; worker CAMS/BDRC/GEOS-FP de aerosol por especies (y perfil vertical si GEOS-FP responde); LUT SkyCalc de filtros (§3.1); DEM horizonte 720 bins; **calibrador de P_raw → P_calibrada** (ya hay meses de pares propios); Bucles 1–3 activos con TESS/CloudWatcher; página "cualquier localización" (Falchi/Lorenz muestreado).
- **Fase 3 — Nowcasting:** optical flow/detección de nubes en la allsky + radar (RainViewer) + corrección autorregresiva (Masciadri) con sensores en vivo; **Meteosat solo cuando la licencia comercial esté aclarada por escrito** (§2.1 fila 9). Panel de verificación pública completo.
- **Fase 4 — Modelos aprendidos:** MOS por variable/lead; seeing híbrido NWP+FWHM (gradient boosting) con validación walk-forward; challenger β-Richardson contra HV puro; mapa por sectores de cielo con Luna (requiere el B_LP direccional §1.3.4).

---

# 5. FLECOS REVISADOS (segunda pasada, cosas que suelen olvidarse)

1. **Cirros finos sub-detectables**: cobertura "0%" con velo de cirro; mitigación: SQM (el fondo sube) + fotometría allsky (k sube). Por eso los sensores locales no son opcionales para la verificación.
2. **Calima nocturna sin aviso**: CAMS a veces subestima la llegada; añadir alerta si AOD_dust > 0.15 en cualquier hora de las próximas 48 h (v5: AOD_dust del worker CAMS/Barcelona Dust RC — la mediana multi-modelo del BDRC es menos propensa al fallo de un solo modelo).
3. **Viento en altura vs superficie desacoplados**: noches de calma en suelo con jet fuerte → seeing malo con "buen tiempo"; es exactamente el hueco de información que motivó este proyecto y lo cubre el término V250.
4. **Orografía de Nerpio**: los 9–11 km de malla de ECMWF/ICON no ven el valle; el sesgo local (niebla de valle, drenaje catabático) solo se corrige con la serie histórica local (fase 3–4).
5. **Luna cerca del target ≠ Luna en el cielo**: el modelo K&S con separación angular evita descartar noches útiles (p. ej. Luna al 40% a 100° del target con banda estrecha = noche perfectamente aprovechable).
6. **Airglow**: no predecible noche a noche, pero su componente lenta SÍ (v5): correlaciona con la actividad solar (~0.25 mag V en Paranal a lo largo del ciclo, Patat 2008) y estamos justo tras el máximo del ciclo 25 — para el gate de IFN (mag_sky ≥ 21.5) es la diferencia entre ✔ y ✘. Término `B_airglow(F10.7)` con el flujo de SWPC (fuente ya integrada para Kp); el residuo rápido lo captura el SQM.
7. **Nieve en el suelo**: aumenta el fondo por reflexión de LP local; flag informativo con `snow_depth`.
8. **Degradación elegante**: cada fuente puede caerse; el score debe calcularse con lo disponible y declarar qué falta (campo `data_quality` en el JSON).
9. **Rate limits y caché**: Open-Meteo gratuito ~10k llamadas/día; con caché por celda de 0.1° y 3 h de TTL, la app mundial escala sin coste.
10. **Verificación como producto**: publicar el % de acierto por fuente convierte la web en referencia (nadie más lo hace en Europa a nivel amateur).
11. **Estelas de satélites**: en las 1–2 h tras el crepúsculo y antes del alba los trenes Starlink iluminados cruzan el campo; informativo (se mitiga apilando con rechazo sigma) pero conviene el flag para fotometría y timelapses.
12. **Contrails persistentes**: en corredores aéreos, RH alta a 250–300 hPa genera cirros artificiales que ningún modelo de nubes predice como tales; proxy: RH250 > 90% + ruta aérea → aviso "posibles estelas".
13. **Dispersión atmosférica**: por debajo de 30° de altura la refracción diferencial elonga las estrellas en el eje vertical (≈1" entre 400–700 nm a 30°); el motor de targets debe penalizar targets que culminan bajos.
14. **Centelleo**: para exoplanetas/ocultaciones el ruido de centelleo (σ ∝ X^1.75, dominado por el Cn² alto) puede superar al ruido de fondo; derivarlo del mismo perfil HV ya calculado.
15. **Microclima del propio observatorio**: hormigón/metal que radía, cúpula mal ventilada o un pilar al sol de la tarde añaden 0.5–2" que ningún forecast contiene; se detecta comparando FWHM propio vs estimador en noches de jet en calma.
16. **Validación de perfiles**: contrastar mensualmente los perfiles del NWP con el radiosondeo real de Murcia 08430 (sesgos sistemáticos de viento a 250 hPa se trasladan directos al seeing estimado).
17. **(v5) Proveedor único**: forecast, AQ, ensemble e histórico cuelgan TODOS de Open-Meteo — un proyecto open-source con un mantenedor principal. El plan B (ECMWF Open Data + NOMADS en GRIB2 crudo) está en la tabla §2.1 pero es un salto de complejidad real (mallas, GRIB, sin multi-modelo unificado). El seguro barato es el ya previsto: archivar TODO en Parquet propio desde el día 1 — si el proveedor cae, se pierde el futuro, no el pasado.
18. **(v5) AROME en el borde**: Nerpio (38.16°N) está a ~0.7° del límite sur del dominio AROME — devuelve datos (verificado) pero los bordes de los modelos de área limitada heredan condiciones de contorno del global: tratar su voto en el ensemble con un peso inicial menor y dejar que el Bucle 4 decida con datos.
19. **(v5) El score no debe premiar la franja −18°…−12°** sin el término B_twilight de §1.3.4 activado; si en el MVP no está implementado, gate binario (noche = Sol < −18°) y punto — mentir en esa franja estropea la verificación de "horas útiles".

---

# 6. SISTEMA DE APRENDIZAJE CONTINUO

El principio que lo gobierna todo: **un sistema solo puede aprender de lo que archiva**. La primera pieza no es ningún algoritmo: es archivar cada predicción junto a cada medida.

**Matiz v6 a la premisa (la v5 exageró; verificado 27-08-2026):** Open-Meteo SÍ conserva parte del pasado, pero **menos de lo que la v5 afirmaba**. Lo que hay de verdad: la **Historical Forecast API** sirve una serie CONTINUA construida con los leads cortos de pasadas sucesivas (una "mejor estimación a posteriori" — útil, pero NO el forecast emitido a +48/+72 h), y la **Previous Runs API** sí da los valores por lead time (verificado: ≥2,5 años para ecmwf_ifs025; profundidad variable por modelo). Lo que NO hay: **los miembros individuales del EPS solo viven ~4–5 días** (verificado) — la distribución probabilística del pasado es irrecuperable. Consecuencias estructurales: (a) `backfill_history()` puebla `forecasts` con años de pares DETERMINISTAS (predicción, lead) el primer día → el **Bucle 2 (MOS) arranca sin espera**, cruzando con observaciones AEMET, sondeos de Murcia y el archivo DIMM de Paranal; (b) la **calibración probabilística (P_raw→P_calibrada) y el Bucle 4 probabilístico solo pueden crecer con archivo propio** — razón de más para la Fase 0; (c) el archivado propio es obligatorio también para lo que ninguna API histórica cubre: miembros EPS, CAMS por especies, 7Timer, las predicciones derivadas propias (fwhm_est, mag_sky_est) y toda la verdad terreno.

## 6.1 El almacenamiento que funda el sistema (v6 — dos niveles, esquema ANCHO)

La v5 proponía una tabla EAV (`source, variable, value`) — funciona, pero a decenas de millones de filas/año es la representación menos eficiente para DuckDB y para ML. La v6 usa **dos niveles**:

**Nivel RAW:** la respuesta original de cada proveedor, comprimida y versionada (JSON/GRIB tal cual), nunca sobrescrita — es la garantía forense de la regla de arquitectura de §4.

**Nivel CANÓNICO:** una fila por `site × model × run × valid_time × member`, con **columnas tipadas** (Parquet es extraordinariamente eficiente en formato ancho: solo lee las columnas pedidas):

```sql
forecasts_canonical(
  site_id, model, run_utc, valid_utc, lead_h, member,   -- member=NULL para deterministas
  cloud_low, cloud_mid, cloud_high, t2m, rh, td, wind10, gust10, prob_precip, cape,
  blh, pressure_msl, t850, t700, v500, v300, v250, v200, gph700, gph500,
  aod_total, aod_dust, pwv,
  fwhm_est, mag_sky_est, p_raw,                          -- derivados propios (versionados)
  -- metadatos de procedencia (v6 — los que permiten explicar un cambio dentro de 3 años):
  provider_model_version, provider_grid_lat, provider_grid_lon, provider_elevation,
  native_resolution_km, native_timestep, interpolated, retrieved_at, license_tag,
  quality_flags, pipeline_version
)

-- Verdad terreno, venga de donde venga, en las mismas unidades y hora UTC.
measurements(
  site_id, t_utc,
  fwhm_zenith_500nm,     -- de los subs reales (corregido por masa de aire y filtro, §1.4.1)
  sky_mag_raw,           -- lectura del fotómetro EN SU BANDA (v6, §1.3.5)
  instrument_type, instrument_id, bandpass_id, sky_mag_calibrated,  -- TESS ≠ SQM (§2.1.2)
  clear_flag, cloud_frac,-- fotometría de la allsky (nº estrellas detectadas vs catálogo)
  t_sky_ir,              -- CloudWatcher (entrada primaria del rocío v6, §1.5)
  k_extinction,          -- fotometría de la allsky (pendiente mag vs masa de aire)
  t2m, rh, wind, gust, roof_open, censored,   -- estación local + techos (§6.4.6)
  quality_flags
)
```

Con esto, cada noche genera automáticamente cientos de pares (predicción, realidad) por variable y por horizonte. Todo lo demás son consumidores de estas dos tablas.

**Nota de dimensionado (v3, actualizada v6):** en el esquema ancho, 5 modelos × 120 h × 4–8 pasadas/día + 51 miembros de nubes ≈ decenas de miles de filas/día (mucho menos que los 60–120k del EAV, porque las ~30 variables van en columnas de la misma fila). SQLite queda SOLO como caché operativa de servicio (últimas 72 h); **todo el histórico y la analítica en Parquet particionado + DuckDB embebido** (agregaciones 10–100× más rápidas, compresión ~10×, cero mantenimiento; Parquet ancho solo lee las columnas pedidas). Sin Redis (v6, regla de simplicidad §4); TimescaleDB/Influx solo si algún día hay escritura concurrente multi-sitio masiva.

Esquema de particionado Hive (v4) para que DuckDB pode particiones y las consultas de verificación no hagan full scan:
```
data/forecasts/site=nerpio/year=2026/month=08/model=ecmwf.parquet
data/measurements/site=nerpio/year=2026/measurements_2026-08.parquet
```

## 6.2 Los cinco bucles de aprendizaje (de días a años)

Ordenados por horizonte temporal y por coste. Cada uno es independiente: se pueden activar por fases.

### Bucle 1 — Corrección autorregresiva (horizonte: 1–3 h; activo desde que haya un sensor)
El método de Masciadri 2020 (MNRAS 492, 140), operativo en ALTA/FATE, replicado en pequeño:
```
error(t) = medido(t) − predicho(t)                  # con SQM, allsky o FWHM en vivo
pred_corr(t+Δ) = pred(t+Δ) + Σ φ_i · error(t−i)     # AR(p) sobre los últimos 60–120 min, p≈2–4
```
Los φ_i se reajustan cada noche por mínimos cuadrados sobre la propia noche anterior. ~20 líneas
de Python, y es el estado del arte a 1–2 h reconocido por ESO. Aplicable a seeing (FWHM en vivo),
fondo de cielo (SQM) y nubes (allsky).

### Bucle 2 — MOS / corrección de sesgo por variable (horizonte: días; refit semanal)
Lo que hacen los servicios meteorológicos nacionales con sus propios modelos (Model Output
Statistics): sobre una ventana rodante de 60–90 días, ajustar por variable, por modelo y por
lead time una corrección simple:
```
y_corr = a + b·y_modelo            # o quantile mapping si la distribución está sesgada
```
Es donde se corrige "ECMWF siempre subestima la humedad del valle de Nerpio" o "GFS ve
despejado cuando hay cirros". Requisito: ~2 meses de tabla forecasts+measurements — **o cero
días para la parte DETERMINISTA con el backfill de Previous Runs** (v6: ≥2,5 años verificados
para ecmwf_ifs025; observaciones AEMET como verdad terreno inicial, la de Nerpio la sustituye
al crecer). La parte probabilística NO se puede backfillear (miembros no archivados upstream).
Riesgo a vigilar: ventanas cortas aprenden la estación del año, no el sitio — comparar siempre
contra la corrección nula.

### Bucle 3 — Calibración de los modelos físicos (mensual)
- Coeficiente **A** del perfil HV (§1.4.3b): mínimos cuadrados de FWHM_estimado vs FWHM_medido
  del último mes. Un escalar: converge con pocas semanas de datos.
- Curvas del score (§3.1) y gates de targets (§3.3.1): revisar contra veredictos reales
  ("dijimos IFN ✔ y la sesión fracasó por fondo alto → subir el gate de SQM 0.1").
- k_ref de f_transp: percentil 20 del k medido por la allsky en el último trimestre.

### Bucle 4 — Pesos del ensemble (estacional)
Aprender qué modelo gana EN NERPIO por variable y por régimen: tasa de acierto de cada modelo
(Brier score para despejado/nublado) por estación del año y, si se quiere afinar, por situación
sinóptica (advección africana vs frente atlántico vs anticiclón). El ensemble pasa de mediana
simple a media ponderada por skill:  `w_m ∝ exp(−BS_m/τ)`. Publicar los pesos: es contenido
divulgativo de primera ("en verano ICON gana; con calima, ECMWF").

### Bucle 5 — ML del sitio (anual; el escalón OTCliM/Milli)
Con 6–12 meses de pares: gradient boosting (sklearn/LightGBM) con predictores NWP
(V250, V500, ΔT, BLH, cizalladuras, RH por capas, hora desde el ocaso, día del año) → FWHM
medido. Validación estrictamente temporal (walk-forward: entrenar en meses pasados, validar en
el siguiente; JAMÁS validación aleatoria, que filtra el futuro al pasado y da métricas
fantasía). La importancia de variables (SHAP) revela la física local: si "hora desde el ocaso"
domina, el problema es térmico local, no el jet — y eso se arregla con hormigón, no con ML.

**Política de modelos v3 (inercia temporal sin caer en la moda):** la crítica de que un GBM "instantáneo" no ve la inercia (la capa límite tarda horas en estabilizarse tras el ocaso) es correcta; la solución de primera línea NO es una LSTM sino **features de memoria en el propio GBM**: lags de 1–6 h de las variables clave, medias móviles, tendencia de T (dT/dt), horas desde el ocaso (ya incluida) y el error reciente del propio forecast (que además ya explota el Bucle 1 autorregresivo, diseñado exactamente para la memoria corta). Con el volumen de datos de un solo sitio (miles, no millones de muestras), los modelos secuenciales (LSTM/Transformer) tienden a sobreajustar y a perder contra árboles+lags — y la referencia profesional apunta igual: ESO opera random forest y autorregresión, OTCliM usa gradient boosting. Dicho esto, el marco champion/challenger (§6.4) es agnóstico: cuando la app mundial acumule datos multi-sitio (decenas de miles de noches), un modelo secuencial entrenado globalmente y afinado por sitio es un challenger legítimo — que entre en sombra y gane el panel de verificación como todos los demás.

## 6.3 Verificación pública: el marcador que obliga a mejorar

Panel `/api/v1/verification` permanente, por fuente, variable, lead time y mes:
- **Nubes:** Brier score y tasa de acierto de "noche útil" (≥3 h despejadas), curva de fiabilidad
  (cuando decimos 80% despejado, ¿ocurre el 80% de las veces?).
- **Seeing:** MAE y sesgo de FWHM — y **en el panel público, terciles** (bueno/medio/malo bien clasificados), no arcosegundos (v5): ni ALTA/FATE con mesoescala dedicada bajan del ~20–30 % de error; prometer décimas de arcosegundo con NWP global es perder la credibilidad que este panel existe para construir. El MAE fino queda como métrica interna de calibración.
- **Fondo:** MAE de mag_sky vs SQM.
- Siempre contra dos **baselines humillantes**: persistencia ("mañana como hoy") y climatología
  ("lo normal en agosto"). Si el sistema no bate a la persistencia a 24 h, no está aportando
  nada y el panel lo dirá — esa honestidad es la que construye reputación.
- **(v5) La climatología-baseline ya existe y es del propio sitio:** la tabla "Open Dark Time
  Statistics" de cloud.html (§2.1.2) da las horas reales de cúpula abierta POR MES desde 2012
  (~2.100–2.500 h/año). Es la probabilidad climatológica de noche útil de Nerpio con 14 años de
  muestra — mejor baseline que cualquier reanálisis, y gratis. El TESS aporta además la
  climatología fina de fondo de cielo y nubes desde 2019 (% despejado por mes, ya calculado en
  los paneles del propio dashboard).

## 6.4 Higiene de MLOps (lo que evita que el aprendizaje se pudra)

1. **Champion/challenger:** toda corrección o modelo nuevo corre en sombra (se archiva su
   predicción sin publicarla) y solo se promociona si bate al campeón en el panel de 6.3
   durante ≥1 mes. Nada se despliega por intuición.
2. **Versionado:** tabla `model_registry(version, tipo, coeficientes, fecha, métricas)`. Cada
   predicción archivada apunta a la versión que la generó — sin esto la verificación histórica
   es imposible de interpretar.
3. **Deriva de los datos de entrada:** los modelos NWP cambian de ciclo (ECMWF actualiza el IFS
   ~2 veces/año, resoluciones cambian) → monitorizar el residual medio mensual; un salto brusco
   = re-entrenar el MOS, no es que el cielo haya cambiado.
4. **Deriva de los sensores:** los SQM envejecen (~0.03–0.05 mag/año), las cúpulas de allsky se
   degradan, un vecino instala un LED. Contramedida: noches patrón (sin Luna, AOD bajo) como
   auto-calibración cruzada SQM ↔ fotometría allsky ↔ atlas Falchi.
5. **Datos sucios fuera del entrenamiento:** flags de calidad en measurements (Luna alta
   contamina el SQM como proxy de nubes; viento >40 km/h contamina el FWHM como proxy de
   seeing). Entrenar solo con filas limpias; evaluar con todas.
6. **(v5) Sesgo de censura — el riesgo ML más serio del proyecto:** el pipeline de FWHM solo
   genera filas **las noches en que alguien observó**, y nadie observa las noches malas → el
   dataset queda censurado hacia el buen tiempo y el Bucle 5 aprendería un mundo
   sistemáticamente optimista (el modelo nunca "ve" un seeing de 5"). Contramedidas: (a)
   registrar CADA noche el motivo de no-observación (nublado / viento / no se intentó) como
   fila de measurements con flag `censored` — **(v5) automatizable YA: el estado de techos
   S1R1…S2R4 de cloud.html (§2.1.2) dice cada minuto si se estaba observando, y el TESS mide
   el cielo aunque todos los techos estén cerrados**; (b) la allsky como verdad terreno incondicional
   — funciona también las noches que nadie abre la cúpula (nubes reales, extinción relativa,
   brillo, movimiento). **Matiz v6 sobre el "FWHM de allsky": una fisheye tiene PSF muy variable
   por posición, submuestreo brutal y aberraciones — su FWHM vale como indicador RELATIVO
   calibrado por posición (mejor/peor que su propia mediana), JAMÁS como seeing atmosférico
   absoluto ni como sustituto del pipeline de imágenes científicas**; (c) evaluar el modelo SOLO sobre el subconjunto con verdad
   terreno independiente de la decisión de observar; (d) además el FWHM de subs tiene suelo
   instrumental (muestreo, óptica, guiado): registrar el setup y usar percentil bajo por
   franja, no la mediana bruta, como estimador del seeing atmosférico.

## 6.5 Aprendizaje para la app mundial (donde no hay sensores)

Nerpio tiene verdad terreno; el resto del mundo no. Estrategia jerárquica:
- **Modelo global por defecto** (física + MOS de Nerpio como prior razonable de sitio de montaña).
- **Feedback de un toque:** al acabar la noche, la app pregunta "¿cómo estuvo?" (despejado /
  parcial / nublado; seeing bueno/malo). Es exactamente el mecanismo comunitario de Astrospheric
  y de la validación de meteoblue (2830 observaciones en 5 continentes). Cada respuesta es una
  fila de measurements de baja calidad pero volumen alto → suficiente para el Bucle 4 (pesos de
  ensemble regionales) aunque no para el 5.
- **Sitios con hardware se auto-promocionan:** cualquier usuario con SQM/allsky puede subir su
  serie (formato documentado) y su sitio pasa a tener Bucles 1–3 propios. Red de sitios
  calibrados = el foso competitivo del producto. **(v5) Prior art directo: el sistema LLFC de
  DarkSkySites** — lecturas SQM comunitarias con delta frente al modelo, radio de corrección
  (5–20 km), peso por tipo de dispositivo, tope de corrección (2 mag) y cola de aprobación
  (`/api/llfc/corrections` es público: estudiar su esquema antes de diseñar el nuestro). Y
  **la red STARS4ALL/TESS ya es una red mundial de fotómetros con dashboards públicos** — el
  conector genérico "TESS → measurements" da verdad terreno gratuita en cientos de sitios,
  no solo en Nerpio (**censado 27-08-2026: 660 fotómetros** con dashboard en el mismo Grafana,
  fila 22 de §2.1). Para el feedback visual de baja calidad/alto volumen existe además
  **Globe at Night** (NELM ciudadano con descargas anuales) como dataset de arranque.
- Frontera regional: un modelo por clúster climático (Mediterráneo seco, Atlántico, tropical…)
  antes que un modelo por sitio — evita sobreajustar sitios con 10 noches de feedback.

## 6.6 Cadencia operativa resumida

| Frecuencia | Tarea | Bucle |
|---|---|---|
| Cada hora (noche) | AR-correction con sensores en vivo | 1 |
| Cada noche | Ingesta FWHM/allsky/SQM del día; verificación de la predicción de ayer | — |
| Semanal | Refit MOS por variable y lead; refresco del panel público | 2 |
| Mensual | Recalibrar A (HV), gates de targets, k_ref; chequeo de deriva de sensores | 3 |
| Estacional | Pesos del ensemble por régimen | 4 |
| Anual | Re-entrenamiento ML walk-forward; challenger vs champion | 5 |


---

# 7. PRESUPUESTO Y ESFUERZO (v6 — de la auditoría externa, adaptado a nuestro caso)

Órdenes de magnitud, no cotizaciones. La conclusión que importa: **la infraestructura no es el coste; el coste es construir, verificar y mantener los modelos.**

## 7.1 Infraestructura — prototipo Nerpio (no comercial)

| Concepto | Rango mensual |
|---|---|
| VPS pequeño (Hetzner o similar) | 6–20 € |
| Almacenamiento (Parquet local / R2 con franquicia gratuita) | 0–5 € |
| Open-Meteo (uso no comercial) + CAMS/AEMET/7Timer/SWPC/GEOS-FP | 0 € |
| Dominio, monitorización, backups | 5–20 € |
| **Total** | **≈10–45 €/mes** |

## 7.2 Infraestructura — explotación comercial

| Concepto | Rango mensual |
|---|---|
| Open-Meteo Professional (los usos comerciales avanzados lo requieren; **~99 €/mes, condiciones a confirmar en su pricing antes de lanzar**) | ~99 € |
| VPS de producción + storage/backups + observabilidad + redundancia | 25–110 € |
| **Base mensual** | **≈125–210 €/mes** |

Partidas **deliberadamente sin número** hasta tener términos por escrito: licencia EUMETSAT de baja latencia (si algún día se integra Meteosat operacional), cualquier integración con DarkSkySites, y la formalización del uso de los endpoints de AstroCamp/STARS4ALL. **La auditoría formal de licencias es un gate previo al lanzamiento comercial**, no un trámite posterior.

## 7.3 Esfuerzo de desarrollo

La auditoría externa estima 490–830 h para las cuatro fases con ingeniería externa a 50–80 €/h (≈24–66 k€), con confianza media-baja declarada. **Para nuestro caso (desarrollo propio con Claude Code) los euros no aplican; las horas valen como ESCALA RELATIVA entre fases:**

| Fase | Peso relativo del esfuerzo |
|---|---|
| Fase 0 + MVP Nerpio (Fase 1) | ~25 % |
| Multi-modelo + aerosol + targets (Fase 2) | ~20 % |
| Nowcast + FWHM + verificación (Fase 3) | ~25 % |
| ML + refinamientos (Fase 4) | ~30 % |

El "MVP en 1–2 semanas" de versiones anteriores sigue siendo plausible para el alcance recortado de la Fase 1 v6 — pero la lección de la estimación externa se acepta: **la robustez (QC, tests contra SkyCalc, archivado completo, verificación) es la mayor parte del trabajo real**, y está repartida por todas las fases, no concentrada al final.

---

# 8. REFERENCIAS

- Krisciunas, K. & Schaefer, B. (1991). *A model of the brightness of moonlight*. PASP 103, 1033. — Modelo lunar estándar.
- Falchi, F. et al. (2016). *The new world atlas of artificial night sky brightness*. Science Advances 2, e1600377. — Atlas de contaminación lumínica (GeoTIFF público).
- Roddier, F. (1981). *The effects of atmospheric turbulence in optical astronomy*. Progress in Optics 19. — Base de Cn², r0.
- Kasten, F. & Young, A. (1989). Fórmula de masa de aire. — `X = 1/(cos z + 0.50572·(96.07995−z)^−1.6364)`.
- Sarazin, M. & Roddier, F. (1990). *The ESO differential image motion monitor*. A&A 227, 294. — DIMM.
- Milli, J. et al. (2019). *Nowcasting the turbulence at the Paranal Observatory*. arXiv:1910.13767. — ML de seeing con datos de sitio.
- Milli, J. et al. (2020). *Turbulence nowcast for Cerro Paranal/Armazones*. arXiv:2012.05674.
- Lyman, R., Cherubini, T. & Businger, S. (2020). *Forecasting seeing for the Maunakea Observatories*. MNRAS 496, 4734. — Predicción operacional de seeing con WRF.
- Masciadri, E., Turchi, A. et al. (serie Astro-Meso-Nh, MNRAS 2017–2023). — Forecast operacional de Cn²/PWV en ESO.
- Osborn, J. et al. (2018). Perfiles S-SCIDAR en Paranal. — Estructura vertical de turbulencia.
- Sánchez, S.F. et al. (2007, 2008). *Night-sky at Calar Alto* (PASP/AN). — Clima, extinción y calima del observatorio profesional más próximo a Nerpio.
- Benn, C. & Ellison, S. (1998). *La Palma night-sky brightness*. — Metodología de fondo de cielo.
- Reitan, C. (1963). Relación PWV–punto de rocío en superficie.
- Schaefer, B. (1990) / Bartels: conversión SQM↔NELM.
- Docs de APIs: open-meteo.com/en/docs (+ /ecmwf-api, /air-quality-api), ads.atmosphere.copernicus.eu, opendata.aemet.es, lightpollutionmap.info/help.html, EUMETSAT Data Store.
- Hufnagel, R. (1974) / Valley, G. (1980). Perfil analítico Cn² Hufnagel–Valley. — Base del modelo §1.4.3b.
- Dewan, E. et al. (1993). *A model for Cn² profiles using radiosonde data* (AFGL). — Cn² por cizalladura/estabilidad.
- Trinquet, H. & Vernin, J. (2007). Modelo estadístico de Cn² desde radiosondeos.
- Masciadri, E., Lascaux, F. & Fini, L. (2013). *MOSE: operational forecast… at ESO sites — I*. MNRAS 436, 1968.
- Masciadri, E., Martelloni, G. & Turchi, A. (2020). *Filtering techniques to enhance optical turbulence forecast at short time-scales*. MNRAS 492, 140. — Método autorregresivo (escalón 3 de la escalera §2.3.1).
- Masciadri, E., Turchi, A. & Martelloni, G. (2019). *New achievements in OT forecast systems in operational mode* (ALTA). arXiv:1911.02819.
- Turchi, A., Masciadri, E. & Veillet, C. (2022). *Characterization of LBT atmospheric and turbulence conditions (ALTA)*. arXiv:2210.11247.
- arXiv:2407.16049 (2024). *Optimisation of short-term scheduling at Paranal (VLT/ELT)* — random forest de seeing para elegir OB.
- Schöck, M. et al. (2009). *TMT site testing*. PASP 121, 384. — Instrumentación de caracterización de sitio.
- Giles, D. et al. (2019). *AERONET Version 3 database*. AMT 12, 169.
- Duriscoe, D. (2013). Modelo de brillo natural de cielo (NPS). — Componentes zodiacal/galáctica.
- Crumey, A. (2014). *Human contrast threshold and astronomical visibility*. MNRAS 442, 2600. — Visibilidad visual vs fondo.
- ESO Ambient Conditions Database: archive.eso.org/cms/eso-data/ambient-conditions/ (DIMM/MASS públicos, manual ASM).
- Recursos verificados: 7timer.info (wiki Yeqzids/7timer-issues), aeronet.gsfc.nasa.gov (print_web_data_v3), weather.uwyo.edu (sondeos 08430), alta.arcetri.inaf.it, mkwc.ifa.hawaii.edu, celestrak.org, github.com/mattiaverga/OpenNGC, github.com/mawinkler/uptonight.
- Kyba, C. et al. (2011). *Cloud coverage acts as an amplifier for ecological light pollution*. PLoS ONE 6, e17307. — Base del término de retrodispersión §1.3.3b.
- Noll, S. et al. (2012). *An atmospheric radiation model for Cerro Paranal*. A&A 543, A92; y Jones, A. et al. (2013). *An advanced scattered moonlight model*. A&A 560, A91. — El modelo detrás de ESO SkyCalc; validación de `moonsky.py` (v5, §1.3.2).
- Leinert, C. et al. (1998). *The 1997 reference of diffuse night sky brightness*. A&AS 127, 1. — Tablas de luz zodiacal (elongación, β) para §1.3.4 (v5).
- Patat, F. (2008). *The dancing sky: 6 years of night-sky observations at Cerro Paranal*. A&A 481, 575. — Variación del airglow con el ciclo solar (~0.25 mag V), base del término F10.7 (v5, fleco 6).
- Patat, F. et al. (2006). *UBVRI twilight sky brightness at ESO-Paranal*. A&A 455, 385. — Objetivo real del término B_twilight §1.3.4 (v5).
- Garstang, R. (1989). *Night-sky brightness at observatories and sites*. PASP 101, 306. — Objetivo real del B_LP(alt,az) direccional §1.3.4 (v5).
- Lorenz, D. (2022). *Light pollution atlas 2006–2022* (djlorenz.github.io/astronomy/lp). — Contraste moderno y de licencia libre del atlas Falchi (v5, §1.3.3).
- WMO Barcelona Dust Regional Center (dust.aemet.es). — Fuente operativa de DOD 550 nm para la alerta de calima (v5, §1.2.1).
- Durre, I. et al. (2018). *IGRA v2* (Integrated Global Radiosonde Archive, NOAA NCEI). — Alternativa bulk al servidor de Wyoming para radiosondeos históricos (v5, §2.1.1).
- Auditoría externa cruzada (ChatGPT, 27-08-2026): *Auditoría técnica, científica, económica y de implementación de CabraSpace AstroWeather v5* (docx local). — Origen de las correcciones y del rediseño de producto de la v6; sus afirmaciones factuales sobre Open-Meteo fueron verificadas empíricamente antes de aplicarse (2 confirmadas, 1 corregida: el ECMWF 9 km no tiene niveles de presión).
- Brunt, D. (1932) / Idso, S. (1981). Emisividad efectiva del cielo despejado. — Modelo radiativo de rocío §1.5.
- Stull, R. (1988). *An Introduction to Boundary Layer Meteorology*. — Número de Richardson, N², criterio Ri<0.25 (§1.4.3c).
- Prospero, J. & Carlson, T. (1972). Transporte del polvo sahariano en capas elevadas (Saharan Air Layer). — Base de la corrección por especies §1.2.1.
- Papayannis, A. et al. (2008). *Systematic lidar observations of Saharan dust over Europe (EARLINET)*. JGR 113. — Perfiles verticales de calima 1.5–5 km sobre Europa.

---

# 9. PLAN DE IMPLEMENTACIÓN (v7 — cómo se construye esto)

## 9.0 Principios del plan

1. **Rodajas verticales sobre un tronco común.** El tronco (Fase 0, §9.2) — archivado, esquema, sensores — se construye UNA vez; después, cada parámetro se implementa completo de punta a punta (fuente → backup → visual → aprendizaje) antes de pasar al siguiente. Nada de capas horizontales que no se ven hasta el final.
2. **Cada parámetro llega con su trío obligatorio:** ficha de fuente (la mejor forma de adquirir, con su significado verificado), backup con degradación declarada, y enchufe al sistema de aprendizaje común siempre que exista verdad terreno.
3. **El visual se pacta antes que el backend** (mockup con datos falsos, §9.5) — rehacer un SVG es barato; rehacer el frontend tres veces, no.
4. **Se muestra la noche, se archiva el día** (§9.7).
5. **El usuario siempre sabe a qué atenerse:** cada dato visible lleva su influencia ("qué permite / qué impide"), su incertidumbre y su estado de calibración. Un número sin su porqué no se publica.

## 9.1 Catálogo de parámetros de cara al usuario

La versión completa y viva será `CATALOGO_PARAMETROS.md` en el repo (primer entregable, §9.8); esta tabla es su esqueleto normativo. Columnas: influencia en lenguaje de usuario · qué permite/impide (de la matriz §3.3.1) · fuente primaria · backup · verdad terreno.

| # | Parámetro | Cómo influye (usuario) | Permite / impide | Fuente primaria | Backup | Verdad terreno |
|---|---|---|---|---|---|---|
| 1 | **Noche útil y crepúsculos** | El marco: solo importa de ocaso−1h a orto+1h | Define la ventana de todo | astropy local (sin red) | — (determinista) | — |
| 2 | **Luna** (fase, altura, separación) | Cada mag de fondo perdida = ×2.5 de tiempo de integración | Con Luna alta: NB sí (por filtro, LUT §3.1), IFN/banda ancha profunda no | astropy + K&S (§1.3.2) | — (determinista) + SkyCalc como test | TESS (mag por banda, §1.3.5) |
| 3 | **Nubes** (baja/media/alta, niebla) | EL gate: bloquean o degradan todo; los cirros "20 %" matan fotometría y crean halos | Despejado: todo; cirros: nada de fotometría/OIII; huecos: tolerable para planetaria | EPS ECMWF (P_raw→P_calibrada) + híbrido 9 km (§2.1.1) | Familias ICON/GFS/AROME + CloudWatcher/allsky local (0–1 h) + Meteosat (fase 3) | TESS+CloudWatcher+allsky (§2.1.2) |
| 4 | **Lluvia / tormenta** | Gate de seguridad: cierre | Impide abrir; riesgo escalonado 0–60 min / 1–6 h / >6 h (§3.1) | EPS precipitación + prob. horaria | Radar RainViewer + sensor de lluvia local | Estación local |
| 5 | **Viento / rachas** | >35–40 km/h cierre; 15–30 degrada guiado; agita la capa límite (seeing) | Impide focales largas antes que cortas | Open-Meteo híbrido (rachas) | 2 estaciones AstroCamp | Estación local |
| 6 | **Calima (AOD polvo)** | +0.1 AOD ≈ +0.1 mag perdidas y cielo lechoso; la Luna molesta MÁS (§1.3.2) | Impide IFN/banda ancha profunda; NB sobrevive; alerta si AOD_dust>0.15 a 48 h | CAMS ADS prefetch diario (§1.2.1) | Barcelona Dust RC → heurística superficie-limpia → (GEOS-FP, objetivo) | AERONET Granada + k de la allsky |
| 7 | **Transparencia / partículas / humo** (AOD total, α, k) | Pérdida de contraste y color de la extinción (el humo castiga el azul/OIII) | Impide fotometría de precisión si k inestable | Open-Meteo AQ (AOD total) | CAMS ADS | k medido (allsky) |
| 8 | **Fondo de cielo** (LP + zodiacal + airglow + twilight) | El presupuesto de oscuridad del sitio; mag_sky por hora | Gates de §3.3.1 por clase de target | Modelo local (§1.3.4: constante Nerpio + K&S + Leinert + F10.7) | — (modelo local; Falchi/Lorenz para otros sitios) | TESS (con banda, §1.3.5) |
| 9 | **Seeing** (jet + capa límite + orografía) | Decide PN pequeñas, dobles, lucky, Cruz de Einstein; casi irrelevante para IFN | Tercil + rango (§1.4.4); flag riesgo orográfico (Ri, §1.4.3c) | Perfiles 0.25° → HV(W) + features Ri | 7Timer/meteoblue SOLO contraste (nunca etiqueta) | **Pipeline FWHM (Fase 0)** + archivo DIMM Paranal como banco externo |
| 10 | **Rocío / escarcha** | La lente se enfría bajo el aire; margen en °C accionable | `dew_heater` cambia el gate; escarcha si T_equipo<0 | **T_sky medida (CloudWatcher)** (§1.5 v6) | Modelo radiativo Brunt (fallback) | Propia (T_sky + evento de rocío observado) |
| 11 | **Techo: ¿se abrirá?** | El resumen ejecutivo de la noche para quien no quiere detalles | — (es un compuesto) | gates × P_usable × prior climatológico 14 años (§9.6) | — | Estado real de techos (etiqueta asimétrica, §9.6) |
| 12 | Extras (estelas, contrails, centelleo, nieve) | Flags informativos (§1.6 P3) | Afectan fotometría/timelapses | CelesTrak / RH250 / HV / snow_depth | — | — |

## 9.2 El tronco común (Fase 0) — UNA vez, antes de la primera rodaja

Es la Fase 0 de §4.1, ordenada como dependencia dura del plan: **el aprendizaje por parámetro (§9.3.4) no puede existir sin esto**, y lo no archivado (miembros EPS, noches de FWHM) es irrecuperable.

1. Esquema canónico ancho + nivel raw (§6.1 v6), versionado, con Parquet/DuckDB.
2. Workers base: `fetch_openmeteo` (híbrido), `fetch_ensemble` (**archivando los 51 miembros**), `fetch_airquality`, `fetch_swpc`.
3. `backfill_history()` determinista (Previous Runs) — el MOS arranca con ≥2,5 años.
4. `fetch_tess.py` (histórico ~7 años → Parquet) + scraper de cloud.html (CloudWatcher, meteo, **techos**) con QC de frescura.
5. **Pipeline FWHM científico** (FITS → estrellas → FWHM robusta → plate-solve → cenit/λ → QC → Parquet) — corre desde la semana 1 aunque el seeing sea la rodaja 6.
6. Panel de verificación mínimo (aunque feo): predicción vs realidad por variable y lead — es el juez de todas las rodajas.

## 9.3 La rodaja vertical tipo (plantilla por parámetro)

Cada parámetro del catálogo se implementa con estos cinco pasos, en este orden:

1. **Ficha de fuente** (lección de la auditoría v5→v6): qué significa EXACTAMENTE el dato según el proveedor, retención/histórico, resolución y timestep nativos, licencia comercial, SLA. Se verifica con llamadas reales y se archiva la ficha en el repo.
2. **Ingesta + backup**: fuente primaria y cadena de fallback con degradación DECLARADA (`data_quality` en el JSON, fleco 8) — el sistema dice qué le falta, nunca inventa. El backup se prueba apagando la primaria (test de caos barato).
3. **Lane visual** en el lienzo nocturno (§9.5) con su "qué permite / qué impide" tocable.
4. **Enchufe al aprendizaje común**: la predicción se archiva en `forecasts_canonical`, la verdad terreno en `measurements`, y el parámetro aparece en el panel §6.3 contra persistencia y climatología. Si no hay verdad terreno posible, se declara (columna del catálogo vacía = decisión consciente).
5. **Tests + registro**: contra SkyCalc/valores canónicos donde aplique; entrada en `model_registry` si hay coeficientes.

**Definición de terminado (DoD):** la rodaja está acabada cuando (a) el parámetro se pinta en el lienzo con datos reales de Nerpio, (b) su backup ha servido datos con la primaria apagada, y (c) su verificación aparece en el panel. Sin las tres, no se empieza la siguiente.

## 9.4 Orden de las rodajas (y por qué)

1. **Noche + Luna** — el marco de todo (eje del lienzo), cero dependencia externa, valor visible inmediato.
2. **Nubes** — el gate principal, la mejor verdad terreno disponible (TESS/CloudWatcher/allsky), y donde estrena P_raw→P_calibrada.
3. **Lluvia + viento + techo** — completan los gates de seguridad; habilitan la predicción de techo (§9.6).
4. **Calima** — la triple fuente ya diseñada (§1.2.1); alerta de producto diferencial en el SE peninsular.
5. **Fondo de cielo completo** — LP + zodiacal + twilight sobre la Luna ya hecha; validación SkyCalc.
6. **Seeing** — el último a propósito: máxima incertidumbre, y su aprendizaje necesita meses de FWHM que ya se estarán acumulando desde la Fase 0.
7. **Rocío** — rodaja corta (el sensor ya mide T_sky).
8. **Extras** — flags P3 (§1.6) según sobre tiempo.

## 9.5 El sistema visual: lienzo nocturno sobre la orografía de AstroCamp

Concepto: **profundo pero muy visual** — nada de tablas como primera cara. Especificación:

- **El lienzo ES la noche**: eje X de ocaso−1h a orto+1h (§9.7). Abajo, la **silueta real de la sierra** vista desde AstroCamp (generable desde el DEM MDT05/SRTM con el horizonte de 720 bins de §1.3.4 — la misma máscara sirve para el motor de targets y para el dibujo).
- **El cielo del lienzo se pinta con mag_sky** (de negro profundo a gris azulado) y sobre él el **arco real de la Luna** con su % de iluminación.
- **Nubes**: tres franjas horizontales (baja/media/alta) en grises — más oscura cuanto más alta — con la cobertura por hora; **la opacidad/trama codifica la certeza** (P_calibrada): un 40 % inseguro NO se pinta igual que un 40 % seguro.
- **Iconos por parámetro**: gota azul (precipitación prevista), icono naranja/rojo con el número de AOD (calima/partículas), estrella más o menos emborronada (tercil de seeing), termómetro/gota en lente con el margen de rocío en °C, manga de viento con racha, candado/cúpula para los gates.
- **Cada icono es tocable** → "qué permite / qué impide ahora" (la matriz §3.3.1 en lenguaje llano) + el factor limitante de la hora.
- **Reglas duras del lenguaje visual**: (1) seguridad inequívoca (rojo/candado, nunca ambiguo); (2) **la incertidumbre SIEMPRE se pinta** — si el dibujo no sabe dudar, miente con confianza y el panel de verificación nos lo echará en cara; (3) todo número visible lleva su porqué a un toque.
- **Entregable previo al backend**: mockup SVG estático con TRES noches inventadas (buena / regular / mala) para pactar el lenguaje visual. Es la primera cosa que se enseña y se corrige.

## 9.6 Predicción de apertura del techo

Producto: «¿Se abrirá el techo esta noche?» con probabilidad. Composición: `gates de seguridad × P_usable × prior climatológico mensual` (la tabla 2012–2026 de horas de cúpula abierta, §2.1.2 — 14 años de prior real del propio sitio).

**Trampa estadística (pactada de antemano):** el techo lo abren humanos — techo cerrado ⇏ mala noche (puede no haber nadie), pero techo abierto ⇒ casi seguro noche útil. **Etiqueta asimétrica**: el modelo se entrena contra "cielo útil" medido por TESS/CloudWatcher (que miran aunque no haya nadie) y se valida asimétricamente contra el techo; el estado del techo además automatiza el flag de censura (§6.4.6). Al usuario se le dice "¿se abrirá?"; por debajo se predice cielo, no comportamiento humano.

## 9.7 Se muestra la noche, se archiva el día

- **UI, score, catálogo, lienzo**: exclusivamente ocaso−1h → orto+1h. Lo diurno no aparece (en AstroCamp no se hace solar).
- **Ingesta y archivo: las 24 h.** Parte del día condiciona la noche y es predictor: el CAPE de la tarde es la tormenta de las 22:00, la calima llega de día, y "horas desde el ocaso" (relajación térmica) es de los mejores predictores de seeing de capa límite (§6.2 Bucle 5). Archivar el día cuesta cero; sus predictores valen mucho.

## 9.8 Primeros entregables concretos (en orden)

1. **Repo `CabraSpace-AstroWeather`** separado (el motor+API; la web CabraSpace solo consume `GET /api/v1/…` — la frontera del repo calca la frontera de la arquitectura §4). La página que sustituye a Astrofotografía → Astro Forecast vive en CabraSpace-Web y no se fusiona hasta que la API sirva en real (sin ventana rota).
2. **`CATALOGO_PARAMETROS.md`** — la tabla §9.1 extendida a documento vivo (una sección por parámetro con su ficha de fuente).
3. **Mockup SVG** del lienzo nocturno, tres estados (§9.5) → se pacta el visual.
4. **Fase 0** completa (§9.2) — desde ese día, todo se archiva.
5. **Rodaja 1 (Noche+Luna)** y sucesivas por el orden de §9.4, cada una con su DoD.

**Gate previo a todo lo anterior: la auditoría cruzada de ESTE documento** (Gemini + ChatGPT, con la guía §0), cierre de objeciones bloqueantes, y entonces — solo entonces — código.
