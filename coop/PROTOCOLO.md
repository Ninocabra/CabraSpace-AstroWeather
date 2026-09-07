# Protocolo de co-trabajo Claude ↔ Gemini en Astroweather Cabraspace

Instancia del protocolo v2 (modelo: `CabraEclipse/coop/`). **Todas las rutas,
absolutas.** Aquí se usa para bancos de pruebas y trabajo de método; en un repo
de proyecto se copia esta carpeta cambiando la raíz.

## Ficheros y dueño único

| Fichero (ruta absoluta bajo `C:\Users\ninoc\Documents\PixInsight\CabraSpace\Astroweather Cabraspace\coop\`) | Dueño |
|---|---|
| `turno.txt` — `CLAUDE` o `GEMINI` | quien termina, lo cede |
| `TAREA_ACTIVA.md` — el encargo | escribe Claude |
| `ENTREGA.md` — el informe | escribe Gemini |
| `APRENDIZAJES.md` — balance por entrega | escribe Claude |
| `estado.json` — presupuesto | escribe Claude |
| `gemini\` — ficheros nuevos que produzca Gemini | escribe Gemini |

Nadie escribe en el fichero de otro. Gemini **nunca** edita ficheros existentes
del repo.

## Las dos fases (esto es lo que evita que Nino haga de recadero)

**PRIMERA vez de la sesión de Antigravity — y solo la primera:** Claude enseña a
Nino el bloque de `GEMINI_WATCHER_PROMPT.md` para que lo pegue. Ese prompt monta
en Gemini su propio monitor sobre `turno.txt`.

**A partir de ahí, monitor contra monitor:** Claude arma su Monitor sobre
`turno.txt` esperando `CLAUDE`; Gemini tiene el suyo esperando `GEMINI`. Los
encargos siguientes se entregan escribiendo `TAREA_ACTIVA.md` y poniendo
`GEMINI` en `turno.txt`. **Nino no vuelve a pegar nada** salvo que Antigravity
se cierre o mate el bucle.

## Apretón de manos (obligatorio, las dos partes)

1. Claude escribe `TAREA_ACTIVA.md` → pone `GEMINI` en `turno.txt` → **arma su
   Monitor en el mismo paso** → sigue con otra cosa.
2. Gemini toma el turno → **acuse**: sobrescribe `ENTREGA.md` con
   `EN CURSO desde HH:MM`.
3. Gemini trabaja → informe completo en `ENTREGA.md` (primera línea
   `TIEMPO: empecé HH:MM — terminé HH:MM`) → pone `CLAUDE` en `turno.txt`.
4. Gemini **relee ambos ficheros del disco** y verifica. Una entrega no escrita
   NO existe, diga lo que diga el chat.
5. El monitor de Claude salta; Claude procesa con el checklist de integración.

## Reglas del monitor de Claude

- Se arma **en el mismo paso** en que se cede el turno, nunca después.
- Vigila el éxito **y el fracaso**: sale cuando `turno.txt` dice `CLAUDE`, y
  avisa también si `ENTREGA.md` lleva N minutos sin cambiar sin cerrar. El
  silencio no es éxito.
- PASO 0 de Gemini es leer el turno al arrancar: un monitor solo ve *cambios*,
  y un turno ya puesto se perdería.

## Presupuesto

10 interacciones por sesión (`estado.json`). Al llegar al límite Claude **para y
pregunta**; «sigue» reinicia la cuenta. Los monitores no se apagan nunca.
