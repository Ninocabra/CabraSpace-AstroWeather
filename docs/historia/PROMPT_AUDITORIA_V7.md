# Encargo: auditoría adversarial del documento maestro CabraSpace AstroWeather (v7)

Te adjunto el documento maestro (v7) de un sistema real de predicción de condiciones de observación astronómica que vamos a construir sobre el observatorio AstroCamp (Nerpio, España, 1650 m). Este documento será la base de TODO el desarrollo: tu trabajo es encontrar sus errores ahora, antes de que cuesten meses.

## Tu papel

Auditor adversarial independiente con triple perfil: (a) física atmosférica y astronomía observacional, (b) ingeniería de datos y ML operacional, (c) producto, economía y licencias. NO eres un colaborador amable: eres el revisor que intenta tumbar el documento. Ya ha pasado dos revisiones externas y una verificación empírica; lo fácil ya está cazado — busca lo difícil.

## Reglas del encargo (incumplirlas invalida la auditoría)

1. **Lee el documento COMPLETO antes de escribir una sola objeción** (~1.300 líneas; el capítulo §0 es la guía del auditor y forma parte del encargo). Si tu contexto no te permite procesarlo entero, DILO explícitamente y pide auditarlo por capítulos en varios turnos — eso es mejor que fingir que lo leíste.
2. **Escala de honestidad:** etiqueta cada afirmación tuya como **[LO SÉ]** (puedes sostenerla con fuente o experiencia directa) o **[NO ME CONSTA]** (plausible, sin verificar). Puntuamos: acierto firme +2 · "no me consta" honesto 0 · **afirmación firme equivocada −3**. Aviso: en la ronda anterior el auditor hizo 3 afirmaciones factuales fuertes; las verificamos las 3 contra las APIs reales — 2 correctas y 1 equivocada (recomendó migrar el modelo principal al ECMWF de 9 km sin saber que ese producto no sirve niveles de presión, lo que habría roto el pipeline de seeing). **Todo lo que afirmes con seguridad será verificado ejecutando.** Responde en consecuencia.
3. **Lo marcado "verificado DD-MM-2026" en el documento se comprobó con llamadas reales a las APIs** (no es retórica). Puedes refutarlo — pero entonces indica exactamente cómo reproducir tu resultado (endpoint, parámetros, qué se debe observar).
4. **No propongas nada que ya esté en el documento.** Cita siempre la sección (§X.Y) que atacas. Una objeción a algo que el documento ya resuelve dos páginas más abajo cuenta como afirmación equivocada.
5. **Prohibido el resumen elogioso.** Si una sección está bien, no la menciones. Sin introducción diplomática, sin conclusión de cortesía.

## Qué atacar, por orden de valor para nosotros

1. **Semántica de las fuentes (§2):** para cada fuente que conozcas — ¿el dato significa lo que el documento cree que significa? ¿La retención/histórico real es la que se afirma? ¿La licencia permite el uso comercial descrito? ¿Qué SLA real tiene? *Aquí vivieron los errores caros de las rondas anteriores.*
2. **El marco:** si diseñaras este sistema desde cero, ¿qué harías estructuralmente distinto y por qué? No bugs — arquitectura de producto, elección de salidas, prioridades.
3. **Lo que FALTA:** variables, riesgos, costes, pasos legales u operativos que no aparezcan ni siquiera como "descartado con motivo".
4. **Física y modelos (§1, §3):** errores de fórmula, dominios de validez, supuestos no declarados, unidades, casos límite, y toda precisión aparente sin evidencia detrás.
5. **ML y aprendizaje (§6):** fugas de datos, sesgos adicionales al de censura ya tratado, métricas mal elegidas, planes de calibración irreales con el volumen de datos de un solo sitio.
6. **El plan de implementación (§9):** orden de rodajas, definición de terminado, la especificación visual, la predicción de apertura del techo — ¿dónde se va a atascar esto en la práctica? ¿Qué rodaja está mal dimensionada o mal ordenada?
7. **Economía y licencias (§7):** partidas ausentes, supuestos de coste equivocados, riesgos legales no contemplados.

## Cinco preguntas que queremos respondidas sí o sí

1. Los **5 riesgos con más probabilidad de matar el proyecto**, ordenados de mayor a menor, con una frase de por qué cada uno.
2. ¿Qué parte del documento tiene la **mayor distancia entre la confianza con la que está escrita y la evidencia real** que la sostiene?
3. ¿Qué **verificarías tú PRIMERO con llamadas reales** antes de escribir código? Lista concreta: fuente → qué comprobar → qué resultado te haría cambiar el diseño.
4. ¿Qué **eliminarías o simplificarías** por completo? (No añadir — quitar. El documento ya es grande.)
5. Si solo pudiéramos implementar **3 de las 8 rodajas** de §9.4, ¿cuáles, en qué orden, y por qué esas?

## Formato de respuesta (obligatorio)

Lista numerada de objeciones ordenada por severidad, cada una así:

```
N. [BLOQUEANTE | IMPORTANTE | MENOR] §X.Y — enunciado en una frase.
   Por qué: 1–3 frases.
   Alternativa concreta: (si existe)
   Etiqueta: [LO SÉ] / [NO ME CONSTA]
   Cómo verificarlo: (si aplica: endpoint/fuente/cálculo reproducible)
```

Después de las objeciones: las respuestas a las cinco preguntas, y una sección final **"Lo que yo haría distinto desde cero"** (máximo 300 palabras).
