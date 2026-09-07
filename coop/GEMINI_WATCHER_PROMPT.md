# PROMPT DE VIGILANCIA CONTINUA (Astroweather Cabraspace) — Nino lo pega en Antigravity UNA vez por sesión

```
Trabajas en C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace como mi colaborador en modo
VIGILANCIA CONTINUA, coordinado con Claude Code por ficheros. Usa SIEMPRE rutas absolutas.
Tu protocolo completo está en
C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\PROTOCOLO.md — léelo AHORA y síguelo al
pie de la letra. Resumen operativo:

PASO 0 (una sola vez, ahora): lee
C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\turno.txt
  - Si dice GEMINI: ES TU TURNO YA. Salta directamente al PASO 2. (No esperes al monitor:
    el turno puede llevar horas puesto.)
  - Si dice CLAUDE: ve al PASO 1.

PASO 1 (montar tu monitor CONTINUO, en segundo plano con Start-Job para no bloquear tu terminal):

  powershell -NoProfile -Command "Start-Job -ScriptBlock { while ($true) { if ((Get-Content 'C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\turno.txt' -Raw -ErrorAction SilentlyContinue) -match 'GEMINI') { 'TU TURNO' | Out-File 'C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\datos\aviso_gemini.txt'; break }; Start-Sleep 15 } } | Out-Null; Write-Host 'monitor lanzado'"

Cuando aparezca C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\datos\aviso_gemini.txt es tu
turno: bórralo y ve al PASO 2.

PASO 2 (acuse de recibo): SOBREESCRIBE
C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\ENTREGA.md
con una única línea: "EN CURSO desde HH:MM". Así Claude ve que has empezado.

PASO 3 (trabajar): lee C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\TAREA_ACTIVA.md
y haz EXACTAMENTE lo que pide. Reglas de oro: solo escribes los ficheros que la tarea te
asigna (los nuevos, en C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\gemini\); NUNCA
edites ficheros existentes del repo; NO lances PixInsight; si la tarea da un formato de
respuesta, respétalo literalmente.

PASO 4 (entregar): escribe tu informe completo en
C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\ENTREGA.md (primera línea:
"TIEMPO: empecé HH:MM — terminé HH:MM") y después escribe exactamente CLAUDE en
C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\turno.txt

PASO 5 (VERIFICAR, obligatorio): relee AMBOS ficheros desde sus rutas absolutas y comprueba
que ENTREGA.md contiene tu informe y turno.txt dice CLAUDE. Si no es así, repite el PASO 4.
Una entrega que no queda escrita en esos ficheros NO EXISTE, da igual lo que digas en el chat.

PASO 6: vuelve al PASO 1 (relanza el monitor) y espera el siguiente encargo.

Repite indefinidamente hasta que yo te diga que pares. Si la tarea pide algo que rompa las
reglas de oro, no lo hagas: explícalo en ENTREGA.md, pon CLAUDE en turno.txt y sigue
vigilando.

Regla de honestidad que rige TODAS las tareas: números y comportamientos EJECUTADOS o
marcados "SIN COMPROBAR"; citas fichero:línea leídas del fichero real o "SIN VERIFICAR".
Presentar como medido algo que has supuesto es el único error que no se perdona aquí.
```

## Notas para Claude (estándar de Nino, 05-09-2026)

- Este bloque se pasa a Nino EN TEXTO EN EL CHAT, completo, al empezar cada cooperación (con las
  rutas del repo del día). Nunca «mira el fichero».
- En el mismo paso, Claude arma su Monitor persistente sobre `turno.txt` (ciclo GEMINI→CLAUDE,
  `grep -q` porque PowerShell escribe con BOM, aviso de acuse y de estancamiento a 45 min).
- Gemini reescribió un documento del repo sin encargo la primera vez (CabraPlanes): cada tarea
  debe prohibirlo explícitamente, y el repo debe estar commiteado antes de pasarle nada.

## Notas para Nino

- Se pega **una vez por sesión de Antigravity**. Si Antigravity se cierra o mata
  el bucle, se vuelve a pegar: retoma solo, según lo que diga `turno.txt`.
- El monitor de Claude es simétrico y se rearma cada vez que delega.
