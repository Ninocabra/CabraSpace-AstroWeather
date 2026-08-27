# CabraSpace AstroWeather — Especificación técnica completa (v4)
## Sistema de predicción y evaluación de condiciones para observación de cielo profundo
### Localización de referencia: AstroCamp, Nerpio (Albacete) — 38.16° N, 2.32° W, ~1650 m — extrapolable a cualquier lat/lon

> **v4 — blindaje de casos límite (2ª revisión externa, aceptada con correcciones):** filtro dinámico de niveles de presión "subterráneos" (a 1650 m incluso 850 hPa está al nivel del sitio), corrección de AOD por cota **por especies** (la propuesta original de corregir el AOD total habría subestimado la calima: el polvo sahariano viaja a 1,5–5 km, encima del sitio no hay "aire limpio"), acoplamiento convectivo del viento en el rocío, umbrales de Richardson reescalados para capas gruesas (Ri_bulk), apantallamiento de LP por mar de nubes bajo el observatorio, truncado y rolloff lunar bajo el horizonte (nota: X(Z) de K&S está acotada en 5.0 por diseño — no hay singularidad, solo un gate que implementar), peso analítico de seeing por morfología (α_comp) en vez de gate booleano, e ingesta: SDK FlatBuffers de Open-Meteo, cdsapi siempre asíncrono, particionado Hive de Parquet.
>
> **v3 — revisión por pares externa incorporada:** modelo radiativo de rocío (T_equipo < T_aire bajo cielo despejado), detector de "bad layers" por número de Richardson (limitación orográfica de HV), término de retrodispersión nubosa de la contaminación lumínica, gates de doble zona (núcleo/halo) para objetos extendidos, integración de la trayectoria (masa de aire dinámica) en el motor de targets, almacenamiento columnar Parquet+DuckDB, y política de ML "árboles con lags primero, secuenciales como challenger".
>
> **v2 — segunda pasada en profundidad:** checklist exhaustivo de variables (incl. contaminación lumínica dinámica, partículas en suspensión con distinción AOD/PM y exponente de Ångström, estelas de satélites, contrails, centelleo, dispersión atmosférica…), modelo de seeing Hufnagel–Valley implementable, acceso concreto (endpoints/snippets) a todos los recursos gratuitos verificados, revisión de los sistemas operacionales de los observatorios profesionales (ESO FATE/MOSE, ALTA-LBT, MKWC-Maunakea, nowcasting ML de Paranal) con escalera de replicabilidad, y **motor de recomendación de targets** («esta noche IFN sí, Cruz de Einstein no»).
>
> Documento preparado para implementación con Claude Code. Contiene: (1) análisis exhaustivo de parámetros físicos, (2) modelos matemáticos contrastados con referencias, (3) fuentes de datos y APIs con estrategia de triangulación, (4) índice de observabilidad propuesto, (5) arquitectura de software y roadmap.

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
```

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
alt_moon > 0°          → B_moon según K&S
−6° ≤ alt_moon ≤ 0°    → B_moon(alt=0) · exp(alt_moon/1.5)   # rolloff "crepúsculo lunar":
alt_moon < −6°         → B_moon = 0                           #   evita un escalón en el score
```

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
B_sky(t, alt, az) = B_natural + B_zodiacal(β_ecl) + B_LP(alt, az) + B_moon(t, ρ, …)
mag_sky = −2.5·log10( Σ 10^(−0.4·mag_i) )          # suma en flujo
```
Para v1 basta el cenit; en v2 se puede mapear por sectores del cielo (la Luna a 30° del target vs 120° cambia todo — relevante para elegir target, que es justo el caso de uso de AstroCamp).

## 1.4 Seeing (turbulencia óptica)

### 1.4.1 Física de referencia (lo que miden los observatorios profesionales)
La turbulencia se caracteriza por el perfil `Cn²(h)` (constante de estructura del índice de refracción). Parámetros integrados:

```
r0 = [ 0.423 · (2π/λ)² · sec(z) · ∫ Cn²(h) dh ]^(−3/5)      # parámetro de Fried
ε_FWHM = 0.98 · λ / r0                                        # seeing (rad → arcsec)
θ0 ≈ 2.914·(2π/λ)²·sec(z)^(8/3) · ∫ Cn²(h)·h^(5/3) dh)^(−3/5) # ángulo isoplanático
τ0 ≈ 0.314 · r0 / v_eff                                       # tiempo de coherencia (lucky imaging)

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
```

**Clave del proyecto:** AstroCamp genera cada noche cientos de subexposiciones con FWHM medible (PixInsight/ASTAP logs, o plate-solve + análisis automático). Guardar `(hora, FWHM_mediana, altura del target, filtro)` → corregir a cenit y 500 nm con los escalados de 1.4.1 → **dataset de calibración propio**. Con 2–3 meses de datos, ajustar (a0..b2) por mínimos cuadrados o directamente un gradient boosting (sklearn) con los predictores del modelo NWP. Esto es exactamente lo que hace ESO a escala profesional, y es lo que convertiría CabraSpace en algo mejor que meteoblue para Nerpio.

### 1.4.3b Modelo v2 recomendado: perfil Hufnagel–Valley alimentado con vientos del NWP

El estimador de dos términos (§1.4.3) es un ajuste empírico. Un paso más riguroso —y aún perfectamente implementable con datos gratuitos— es usar el **perfil analítico Hufnagel–Valley**, estándar de la comunidad de óptica atmosférica, cuyo término de alta atmósfera depende explícitamente del viento en la capa del jet (que es justo el dato que falta en las apps convencionales):

```
# h en metros, W en m/s, Cn² en m^(−2/3)
Cn²(h) = 5.94e−53 · (W/27)² · h^10 · e^(−h/1000)     # atmósfera libre alta: ∝ W² (¡jet stream!)
       + 2.7e−16 · e^(−h/1500)                        # troposfera media
       + A · e^(−h/100)                               # capa límite (A calibrable por sitio)

W = [ (1/15000) · ∫_{5km}^{20km} V(h)² dh ]^(1/2)     # viento rms 5–20 km
  ≈ rms de V a 500, 400, 300, 250, 200, 150, 100 hPa  # ← niveles de presión de Open-Meteo/GFS

# Con el perfil integrado, todo lo demás sale de §1.4.1:
∫Cn²dh → r0 → ε = 0.98·λ/r0 ; θ0 ; τ0 (usando además v_eff del propio perfil de vientos)
```

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

# Umbral clásico Ri<0.25: válido para capas finas (Δz→0). Con niveles de modelo separados
# 1.5–3 km, el promedio suaviza los gradientes y un Ri_bulk de 0.6 esconde subcapas con
# Ri_local<0.25 → reescalar el criterio para malla gruesa (v4):
Ri_bulk < 0.5        → bad layer severa        (β = 3.0)
0.5 ≤ Ri_bulk < 1.0  → moderada / orográfica   (β = 1.8)
Ri_bulk ≥ 1.0        → capa estable             (β = 1.0)
```

Integración con el estimador: multiplicar el Cn² de HV en cada capa marcada por su factor `β` de la tabla anterior (valores iniciales; calibrables con FWHM como el resto). Es exactamente la lógica de los "bad layers" de meteoblue (§1.4.2) pero con criterio físico explícito y auditable. Honestidad sobre los límites: las lee waves finas requieren mesoescala de ~1 km que un perfil de modelo global no resuelve; Ri sobre perfiles de 9–25 km es un *proxy* — bueno para detectar el régimen (viento fuerte del NO contra el Segura + estabilidad media = noche sospechosa), no para localizar la onda. El patrón sinóptico "viento perpendicular a la cordillera a 700–500 hPa" merece su propio flag.

### 1.4.3d Filtro de niveles de presión "subterráneos" (v4 — bug corregido)

A 1650 m la presión en superficie es P_sfc ≈ 830–840 hPa: los niveles 1000 y 925 hPa de un modelo global sobre Nerpio son **extrapolaciones matemáticas bajo tierra**, y 850 hPa (~1450 m en atmósfera estándar) está al nivel del propio sitio o por debajo — usarlo como "capa baja" produce gradientes de espesor casi nulo y cizalladuras ficticias. Regla de implementación, dinámica para que valga en cualquier sitio del mundo (app global):

```
P_sfc(sitio) = presión en superficie del punto (Open-Meteo la da: surface_pressure)
niveles_validos = [p for p in niveles if p <= P_sfc − 25 hPa]     # margen de seguridad
# En Nerpio → primer nivel útil ≈ 800 hPa; estabilidad baja: T2m vs T700;
# cizalladura de capa límite: V10 vs V(primer nivel válido).
# En un sitio a nivel del mar → 1000/925 hPa vuelven a ser perfectamente válidos.
```

Corolario para el Ri (§1.4.3c) y para HV: los perfiles empiezan en el primer nivel válido; el tramo sitio→primer nivel se cubre con las variables de superficie + BLH, no con niveles interpolados. Nota adicional: la topografía del modelo (malla 9–25 km) no es la real — comparar la `elevation` que devuelve la API con la cota real del sitio y registrar el offset como metadato del sitio.

### 1.4.4 Importancia relativa según el uso
- Cielo profundo con escalas de imagen de 1–2 "/px: seeing importa poco salvo que sea >3–4".
- Galaxias a foco largo / doble estrellas / planetaria: seeing es EL factor.
- El índice final debe ponderar el seeing según el "perfil de target" (ver §3).

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

  El sensor de nubes IR (AAG CloudWatcher y similares) **mide T_sky directamente**: en cuanto exista, sustituye a ε_sky estimada y calibra ΔT_max con un termómetro IR sobre la propia lente. Corolario útil: el riesgo de rocío se dispara justo cuando la noche se despeja del todo — el forecast debe avisar de ello, no premiar solo el "despejado".
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
| Altura de capa límite (BLH) | Seeing de capa baja, inversiones | Open-Meteo | 2 |
| AOD 550 total | Transparencia | Open-Meteo AQ / CAMS | 1 |
| AOD polvo (calima) | Alerta específica SE peninsular | ídem | 1 |
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
| Luz zodiacal / gegenschein | +fondo cerca de eclíptica (mañana/tarde) | modelo estático (lat. eclíptica) | 3 |
| Airglow | ±0.3 mag no predecible | asumir cte; lo captura el SQM | 3 |
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
| 1 | **Open-Meteo** (`api.open-meteo.com`) | Nubes por capas, T/RH/viento/rachas, **niveles de presión (1000–30 hPa: T, viento, RH)**, BLH, y selección de modelo: ECMWF IFS 9 km, ICON, GFS, AROME/HARMONIE, UKMO… | Global | Gratis no comercial (CC-BY); plan API comercial barato | **Columna vertebral.** Un solo proveedor da el multi-modelo para el ensemble y los niveles de presión para el estimador de seeing |
| 2 | **Open-Meteo Air Quality** (`air-quality-api.open-meteo.com`) | **AOD 550 nm, polvo (calima), PM10/PM2.5** desde CAMS (11 km Europa / global) | Global | Gratis no comercial | Transparencia + alerta de calima |
| 3 | **CAMS / Atmosphere Data Store (ADS)** directo | AOD total y por especie (dust, smoke), 5 días, netCDF/GRIB vía `cdsapi` | Global | Gratis (registro Copernicus) | Triangular #2; mapas de calima; datos por especie |
| 4 | **meteoblue** (web + API de pago) | Seeing index 1/2, arcsec, **bad layers**, jet stream; su paquete "Astronomy Seeing" | Global | Web gratis 3 días; API de pago | Referencia de contraste (no integrar sin licencia; fórmulas propietarias no publicadas) |
| 5 | **7Timer! ASTRO** (`7timer.info/bin/astro.php`) | Seeing y transparencia por clases, nubes; derivado de GFS | Global | Gratis | Triangulación barata; API JSON trivial (verificar SLA — servicio personal) |
| 6 | **Clear Outside** (First Light Optics) | Web de previsión astro por capas | Global | Gratis (sin API oficial) | Solo contraste manual |
| 7 | **Astrospheric** | El mejor producto astro (ensemble, seeing de Rahill, humo) | **SOLO Norteamérica (dominio RDPS)** | API para miembros Pro | **No sirve para Nerpio** — pero es el modelo de producto a imitar |
| 8 | **AEMET OpenData** | Observaciones de estaciones, predicción municipal, radar, avisos | España | Gratis (API key) | Validación con observaciones reales cercanas; avisos |
| 9 | **EUMETSAT (Meteosat MTG/SEVIRI)** vía Data Store/EUMDAC o proxies (Sat24, Windy) | Imagen IR/visible cada 10–15 min | Europa/África | Gratis (registro) | **Nowcasting de nubes 0–3 h**; animación en la web |
| 10 | **ECMWF Open Data** / **NOAA NOMADS (GFS)** | GRIB2 crudos: PWAT (agua precipitable), niveles, jet | Global | Gratis | PWV real; backup de #1 si hace falta control total |
| 11 | **World Atlas 2015 (Falchi et al. 2016)** GeoTIFF | Brillo artificial cenital, 30" de resolución | Global | CC BY-NC 4.0 | Capa estática de contaminación lumínica → SQM/Bortle de cualquier punto |
| 12 | **lightpollutionmap.info** | Visor de Atlas + VIIRS + SQM comunitarios | Global | Web gratis; conversiones documentadas en su help | Referencia de validación de #11 |
| 13 | **NASA Black Marble (VNP46A4/VJ146A4)** | Radiancia nocturna anual VIIRS | Global | Gratis (Earthdata) | Tendencia anual de LP |
| 14 | **astropy + astroplan + skyfield** (librerías) | Efemérides precisas: Sol, Luna (fase, separación, salida/puesta), twilights, masa de aire, visibilidad de targets | — | Open source | **Todo el cálculo astronómico se hace en local, sin API** |
| 15 | **NOAA SWPC** | Kp actual y previsto | Global | Gratis | Opcional |
| 16 | **Sensores AstroCamp** (allsky, T/RH, y idealmente SQM + sensor nube IR tipo AAG CloudWatcher) | Verdad terreno en tiempo real | Nerpio | Propio | **Capa de verdad terreno + calibración ML** |

### 2.1.1 Acceso concreto a cada recurso gratuito (endpoints, snippets, avisos)

**Open-Meteo — forecast multi-modelo con niveles de presión** (sin API key):
```
https://api.open-meteo.com/v1/forecast?latitude=38.16&longitude=-2.32
  &hourly=cloud_cover_low,cloud_cover_mid,cloud_cover_high,temperature_2m,relative_humidity_2m,
  dew_point_2m,wind_speed_10m,wind_gusts_10m,precipitation_probability,cape,boundary_layer_height,
  temperature_850hPa,temperature_700hPa,wind_speed_500hPa,wind_speed_300hPa,wind_speed_250hPa,wind_speed_200hPa
  &models=ecmwf_ifs025,icon_seamless,gfs_seamless,meteofrance_seamless&timezone=UTC
```
(Confirmar el nombre exacto de cada variable/modelo en el configurador de open-meteo.com/en/docs — cambian entre modelos. Licencia CC-BY: atribución; uso comercial → plan API de pago, barato.)

**Open-Meteo Air Quality (CAMS)**:
```
https://air-quality-api.open-meteo.com/v1/air-quality?latitude=38.16&longitude=-2.32
  &hourly=aerosol_optical_depth,dust,pm10,pm2_5&timezone=UTC
```

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

**AERONET web service** (verdad terreno AOD/α/PWV, sin key):
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

**Radiosondeos reales (validación de perfiles y de Cn²):** archivo de la Universidad de Wyoming, estación **Murcia 08430** (00/12 UTC), respuesta HTML/texto parseable:
```
https://weather.uwyo.edu/cgi-bin/sounding?region=europe&TYPE=TEXT%3ALIST&YEAR=2026&MONTH=08&FROM=2600&TO=2612&STNM=08430
```

**ESO Ambient Conditions Database** (¡archivo público!): formularios de consulta de DIMM/MASS-DIMM/SLODAR/meteo de Paranal y La Silla en `archive.eso.org/cms/eso-data/ambient-conditions/paranal-ambient-query-forms.html` (DIMM nuevo desde 2016, manual ASM incluido). No sirve para operar Nerpio, pero es el **banco de pruebas perfecto**: descargar seeing medido + reanálisis/GFS de esas fechas y comprobar que nuestro estimador HV reproduce las estadísticas antes de fiarnos de él en Nerpio.

**NOAA SWPC** (Kp, sin key): `https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json`

**Atlas Falchi 2016**: GeoTIFF (≈2.9 GB, 30", mcd/m²) vía GFZ Data Services (DOI 10.5880/GFZ.1.4.2016.001), licencia CC BY-NC. Conversiones a SQM/Bortle en §1.3.3 (las mismas que documenta lightpollutionmap.info/help.html). Tendencias anuales: NASA Black Marble VNP46A4/VJ146A4 vía Earthdata (login gratuito).

**Meteosat (nowcasting nubes)**: registro gratuito en EUMETSAT Data Store + librería `eumdac` para imágenes IR cada 10–15 min; para visualización rápida en la web, servicios WMS públicos de EUMETView (revisar condiciones de uso para embed).

**TLEs para densidad de estelas** (CelesTrak, sin key): `https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle` + skyfield para contar pases iluminados sobre el campo durante la sesión.

**Catálogo de targets (para el motor de recomendación, §3.3):** **OpenNGC** (github.com/mattiaverga/OpenNGC, CSV libre: tipo, V mag, brillo superficial, dimensiones de NGC/IC) + listas curadas (Messier, Herschel 400, "Top" de Gary Imm). Prior art open-source directamente estudiable: **UpTonight** (github.com/mawinkler/uptonight — calcula los mejores targets de la noche con astroplan; leer su código antes de escribir el nuestro) y **pyastroweatherio/AstroWeather** (integración Home Assistant sobre 7Timer + Met.no, con pesos nubes:seeing:transparencia = 3:2:1 — un prior razonable).

**Librerías de cálculo local:** `pip install astropy astroplan skyfield rasterio` (efemérides, restricciones de observabilidad, muestreo del GeoTIFF).

## 2.2 Estrategia de triangulación (el "fleco" que pediste)

Tres niveles de confianza, de menor a mayor horizonte:

1. **Ahora (0–1 h): sensores locales mandan.** Allsky (detección de estrellas → nubes reales y extinción medida), SQM (fondo real), T/RH (rocío). Si el sensor dice nublado, la predicción se anota como fallida (dato de verificación).
2. **Nowcast (0–3 h): satélite IR Meteosat** + extrapolación de movimiento. Detecta la banda de cirros que el GFS no vio.
3. **Forecast (3 h–7 días): ensemble multi-modelo.** Para cada hora, obtener nubes de ECMWF, ICON, GFS y AROME (4 llamadas a Open-Meteo con `&models=`). Mostrar la mediana Y la dispersión: `confianza = 1 − σ(modelos)/50`. Un "despejado" con los 4 modelos de acuerdo vale infinitamente más que el de un solo modelo.

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

Hardware local recomendado (barato, orden de impacto): (1) SQM-LE ~200 €, (2) sensor de nubes por IR (ΔT cielo-ambiente; despejado ≈ −20 °C o menos) ~300 €, (3) pipeline de FWHM sobre las imágenes ya existentes = 0 €, (4) DIMM DIY con un tubo pequeño (proyecto futuro).

---

# 3. ÍNDICE DE OBSERVABILIDAD (scoring por hora)

## 3.1 Diseño

Puntuación 0–100 por hora, como **producto de factores 0–1** (no suma ponderada: una noche nublada con seeing perfecto debe puntuar ~0, y el producto lo garantiza; la suma no).

```
S(t) = 100 · f_cloud · f_dark · f_transp · f_seeing^w_s · f_wind · f_dew · f_precip
```

Curvas propuestas (todas calibrables; formas elegidas para penalizar fuerte lo inaceptable y poco lo tolerable):

```
f_cloud  = ((100 − C_eff)/100)²                       # C_eff con solape (§1.1); cuadrática: 30% nubes → 0.49
f_dark   = clip( (mag_sky − 18.0) / (22.0 − 18.0), 0, 1 )^1.5    # mag_sky del modelo §1.3.4 (cenit v1)
f_transp = 10^(−0.4·(k_total − k_ref)) capado a [0,1]  # k_ref = 0.18 (noche fotométrica en Nerpio)
f_seeing = clip( (4.0 − ε_est) / (4.0 − 1.0), 0, 1 )   # 1" → 1.0 ; 4" → 0
f_wind   = 1 si racha<15 km/h; lineal a 0 entre 15 y 45
f_dew    = 1 si (T−Td)>3 °C; 0.6 si 1–3; 0.3 si <1 (mitigable con cintas → configurable)
f_precip = 0 si prob_precip > 30% o CAPE alto (gate duro)
```

**Perfiles de target** (el exponente w_s y algún umbral cambian):
- `DSO banda ancha`: w_s = 0.5 (seeing poco crítico), f_dark estricto.
- `DSO banda estrecha`: f_dark relajado con Luna (multiplicar B_moon por 0.15 antes de calcular mag_sky — un filtro de 3–7 nm rechaza la mayor parte del continuo lunar), transparencia sigue importando.
- `Planetaria/Luna/dobles`: w_s = 2, f_dark ≈ 1, f_cloud tolera nubes de paso.
- `Fotometría/exoplanetas`: f_transp estricto (k estable, sin cirros: exigir además σ entre modelos baja).

## 3.2 Salidas por noche (agregados que la gente quiere ver)

- Horas útiles: Σ de horas con S > 60 entre crepúsculos astronómicos.
- Mejor ventana continua (inicio, fin, S medio).
- "Semáforo" de noche: mejor bloque de 3 h.
- Confianza: dispersión del ensemble (§2.2) — mostrar SIEMPRE junto al score.
- Extras de planificación (ya con astropy/astroplan): altura de la Luna toda la noche, % iluminación, separación Luna-target si el usuario indica target, tránsito por el meridiano.

## 3.3 Guía de observabilidad por target: «esta noche IFN sí, Cruz de Einstein no»

El score global (§3.1) dice si la noche es buena; esta capa dice **para qué** es buena. Dos ejes físicos independientes clasifican cualquier target:

- **Eje contraste** — brillo superficial del target (SB, mag/arcsec²) frente al fondo de cielo previsto `mag_sky` (§1.3.4). En imagen limitada por fondo: `SNR ∝ F_obj·√t/√B_sky` → el tiempo para alcanzar una SNR fija escala con el brillo del fondo: **cada magnitud de cielo perdida multiplica el tiempo necesario ×2.5** (21.0 vs 22.0 → ×2.5; 20.0 → ×6.3; 19.0 → ×16). Regla operativa: interesa la **diferencia Δ = SB_target − mag_sky**; con Δ ≳ 4–5 mag el target es "de cielo excelente" (solo sale con fondo oscuro y muchas horas).
- **Eje resolución** — escala angular del detalle buscado frente al FWHM previsto (ε del §1.4). Detalle mínimo recuperable ≈ 1×FWHM (≈0.8×FWHM con buen muestreo y deconvolución). La Luna y la LP no afectan a este eje; el jet stream sí.

### 3.3.1 Matriz de decisión (gates por clase de target)

| Clase (ejemplos) | Gate de cielo (mag_sky cenit) | Gate de seeing (FWHM) | Luna | Transparencia | Nota |
|---|---|---|---|---|---|
| **IFN, colas de marea, halos (SB ≳ 26)** (IFN de M81/82, Arp loops) | ≥ 21.5 | indiferente | < 5% o bajo horizonte | AOD ≤ 0.08, sin cirros | El target más exigente en contraste que existe |
| Nebulosas débiles banda ancha (LBN, SNR tenues) | ≥ 21.2 | ≤ 4" | < 20% y separación > 90° | AOD ≤ 0.12 | |
| Galaxias con detalle / brazos (NGC 891, M51) | ≥ 20.8 | ≤ 2.5" | < 40%, sep > 60° | media | Ejes contraste y resolución a la vez |
| **PN pequeñas, detalles finos, lentes: Cruz de Einstein (Q2237+030, componentes a ~1.8")** | ≥ 20 basta | **≤ 1.5–1.8"** + escala ≤ 0.8"/px | tolera | media | Casi solo eje resolución: noche de jet en calma |
| Nebulosas en banda estrecha (Ha/OIII/SII 3–7 nm) | ≥ 19 (la NB rechaza ~85% del continuo lunar → aplicar B_moon×0.15) | ≤ 3.5" | hasta ~60% si sep > 60° | **sin cirros** (halos en OIII) | La noche "con Luna" bien aprovechada |
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
targets.sb_core   # estructura principal (catálogo)         → gate estándar de su clase
targets.sb_faint  # estructura débil de interés (halo, colas, IFN circundante; campo curado
                  #   manualmente para los ~200 objetos top; NULL → usar sb_core + 3 mag)
veredicto = { 'completo' si cumple gate_faint,               # "noche para el halo de M51"
              'parcial'  si solo cumple gate_core,           # "M51 sí, pero sin marea"
              'no'       si ninguno }
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
│ WORKERS (cron cada 1–3 h) + CACHE (SQLite/Redis)           │
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

Notas de implementación para Claude Code:
1. **Open-Meteo, una llamada por modelo**, variables sugeridas: `cloud_cover_low,cloud_cover_mid,cloud_cover_high,temperature_2m,relative_humidity_2m,dew_point_2m,wind_speed_10m,wind_gusts_10m,precipitation_probability,cape,boundary_layer_height` + niveles `temperature_850hPa,temperature_700hPa,wind_speed_250hPa,wind_speed_300hPa,geopotential_height_*` (verificar nombres exactos por modelo en la doc; no todos los modelos publican todos los niveles).
2. Air Quality API: `aerosol_optical_depth,dust,pm10`.
3. El GeoTIFF de Falchi (2.9 GB) no va al servidor web: preprocesar una vez → o bien recortar España/Europa, o bien muestrear bajo demanda desde un objeto S3 con rasterio en un worker, y cachear por celda de 30".
4. Zona horaria y crepúsculos: todo en UTC internamente; presentar en hora local del sitio.
5. Respetar licencias: Open-Meteo CC-BY (atribución en el footer), Falchi CC BY-NC (ojo si la app mundial se monetiza — para uso comercial, contactar o sustituir por cálculo propio desde VIIRS), CAMS/Copernicus atribución obligatoria, meteoblue NO se escrapea.
6. Empezar simple: v1 = un solo modelo (ECMWF) + AOD + efemérides + score. El ensemble, satélite y ML después.

**Notas v4 de ingesta y rendimiento:**
- **Open-Meteo por SDK binario:** usar la librería oficial `openmeteo-requests` (+`openmeteo-sdk`, serialización FlatBuffers → arrays NumPy directos, con caché y retry incorporados) en lugar de parsear JSON. Dimensionado honesto: para un solo sitio el JSON es irrelevante; el SDK importa por comodidad hoy y por rendimiento cuando la app mundial pida cientos de celdas.
- **cdsapi (CAMS ADS) JAMÁS en el camino caliente:** sus peticiones se encolan en Copernicus (de 2 a >45 min). Open-Meteo Air Quality es la fuente primaria síncrona (<1 s); el worker de cdsapi corre desacoplado en background (cola propia con retry: ARQ/Celery) y sus GRIB/NetCDF van solo al archivo Parquet de reconciliación y reanálisis.

Endpoints ampliados (v2):
```
GET /api/v1/astroweather?lat&lon&days&profile=...   # horario: raw + derivados + score + confianza
GET /api/v1/tonight?lat&lon[&targets=M81,Q2237]     # agregados de la noche + matriz de clases §3.3
GET /api/v1/verification?site=nerpio                # métricas de acierto por fuente (transparencia del sistema)
```
Módulos nuevos en el core: `hv_profile.py` (Cn² HV(W) → ε, θ0, τ0), `targets.py` (OpenNGC + gates §3.3, astroplan), `arcorrect.py` (corrección autorregresiva con sensores, escalera §2.3.1-3), `scintillation.py` y `refraction.py` (flags de fotometría y de targets bajos).

## 4.1 Roadmap

- **Fase 1 (MVP, 1–2 semanas):** Open-Meteo ECMWF + Air Quality + astropy/Krisciunas-Schaefer + Falchi muestreado + score DSO. Widget de 72 h en CabraSpace.
- **Fase 2:** Ensemble 4 modelos con banda de confianza; perfiles de target; página "cualquier localización".
- **Fase 3:** Nowcast Meteosat + integración de sensores AstroCamp (allsky/SQM) + panel de verificación pública de aciertos.
- **Fase 4:** Calibración ML del seeing con el archivo de FWHM propio (gradient boosting, sklearn); mapa de score por sectores de cielo con Luna.

---

# 5. FLECOS REVISADOS (segunda pasada, cosas que suelen olvidarse)

1. **Cirros finos sub-detectables**: cobertura "0%" con velo de cirro; mitigación: SQM (el fondo sube) + fotometría allsky (k sube). Por eso los sensores locales no son opcionales para la verificación.
2. **Calima nocturna sin aviso**: CAMS a veces subestima la llegada; añadir alerta si AOD_dust > 0.15 en cualquier hora de las próximas 48 h.
3. **Viento en altura vs superficie desacoplados**: noches de calma en suelo con jet fuerte → seeing malo con "buen tiempo"; es exactamente el hueco de información que motivó este proyecto y lo cubre el término V250.
4. **Orografía de Nerpio**: los 9–11 km de malla de ECMWF/ICON no ven el valle; el sesgo local (niebla de valle, drenaje catabático) solo se corrige con la serie histórica local (fase 3–4).
5. **Luna cerca del target ≠ Luna en el cielo**: el modelo K&S con separación angular evita descartar noches útiles (p. ej. Luna al 40% a 100° del target con banda estrecha = noche perfectamente aprovechable).
6. **Airglow**: variable no predecible a nivel amateur; asumir constante y dejar que el SQM capture su variación.
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

---

# 6. SISTEMA DE APRENDIZAJE CONTINUO

El principio que lo gobierna todo: **un sistema solo puede aprender de lo que archiva**. La predicción de anoche desaparece de todas las APIs en cuanto se actualiza el modelo; si no la guardaste tú, no existe dataset de entrenamiento. Por tanto la primera pieza no es ningún algoritmo: es archivar cada predicción junto a cada medida.

## 6.1 Las dos tablas que fundan el sistema

```sql
-- Cada vez que un worker descarga un forecast, se guarda TAL CUAL se predijo,
-- con su hora de emisión y su horizonte (lead time). Nunca se sobreescribe.
forecasts(
  site_id, issued_at_utc, valid_at_utc, lead_h,        -- lead_h = valid − issued
  source,            -- 'ecmwf' | 'icon' | 'gfs' | 'arome' | 'cams' | '7timer' | 'propio_hv'
  variable,          -- 'cloud_low' | 'fwhm_est' | 'aod' | 'mag_sky_est' | ...
  value, meta_json
)

-- Verdad terreno, venga de donde venga, en las mismas unidades y hora UTC.
measurements(
  site_id, t_utc,
  fwhm_zenith_500nm,     -- de los subs reales (corregido por masa de aire y filtro, §1.4.1)
  sqm_mag,               -- SQM
  clear_flag, cloud_frac,-- fotometría de la allsky (nº estrellas detectadas vs catálogo)
  k_extinction,          -- fotometría de la allsky (pendiente mag vs masa de aire)
  t2m, rh, wind, gust,   -- estación local
  quality_flags
)
```

Con esto, cada noche genera automáticamente cientos de pares (predicción, realidad) por variable y por horizonte. Todo lo demás son consumidores de estas dos tablas.

**Nota v3 de almacenamiento (dimensionado honesto):** 4 modelos × ~30 variables × 120 h × 4–8 pasadas/día ≈ 60–120k filas/día solo en `forecasts` (~40M/año). SQLite lo *soporta* como almacén operativo, pero las consultas analíticas de los bucles 2–5 (cruces forecast×measurements por lead time y ventana) piden formato columnar. Arquitectura recomendada, sin añadir operación de servidores: **SQLite/Redis solo como caché operativa de servicio** (últimas 72 h) + **archivo histórico en Parquet particionado por mes y fuente + DuckDB embebido** para toda la analítica y el entrenamiento (consultas de agregación 10–100× más rápidas, compresión ~10×, cero mantenimiento). TimescaleDB/Influx solo si algún día hay escritura concurrente multi-sitio masiva; para esta escala serían complejidad gratuita.

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
despejado cuando hay cirros". Requisito: ~2 meses de tabla forecasts+measurements. Riesgo a
vigilar: ventanas cortas aprenden la estación del año, no el sitio — comparar siempre contra
la corrección nula.

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
- **Seeing:** MAE y sesgo de FWHM; % de noches "≤2"" bien clasificadas.
- **Fondo:** MAE de mag_sky vs SQM.
- Siempre contra dos **baselines humillantes**: persistencia ("mañana como hoy") y climatología
  ("lo normal en agosto"). Si el sistema no bate a la persistencia a 24 h, no está aportando
  nada y el panel lo dirá — esa honestidad es la que construye reputación.

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
  calibrados = el foso competitivo del producto.
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

# 7. REFERENCIAS

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
- Brunt, D. (1932) / Idso, S. (1981). Emisividad efectiva del cielo despejado. — Modelo radiativo de rocío §1.5.
- Stull, R. (1988). *An Introduction to Boundary Layer Meteorology*. — Número de Richardson, N², criterio Ri<0.25 (§1.4.3c).
- Prospero, J. & Carlson, T. (1972). Transporte del polvo sahariano en capas elevadas (Saharan Air Layer). — Base de la corrección por especies §1.2.1.
- Papayannis, A. et al. (2008). *Systematic lidar observations of Saharan dust over Europe (EARLINET)*. JGR 113. — Perfiles verticales de calima 1.5–5 km sobre Europa.
