# Aprendizajes de las entregas de Gemini en Astroweather Cabraspace

## 2026-09-07 — fetch_weathernext.py (entrega 1, 6 min)

BUENO: estructura calcada del molde, invariante lead_h como ValueError y la
prueba en rojo vista de verdad, seis supuestos marcados SIN COMPROBAR sin que
se le pidiera uno a uno, y un test de integración con write_canonical que no
estaba en el encargo y era el que más valía.

MALO (responsabilidad del encargo, no de Gemini): derivó dirección del viento
y humedad para los cinco cuantiles; el encargo decía "se deriva" sin decir
"solo de la media". Un cuantil de una función no es la función de los
cuantiles: la próxima vez, decirlo. Y copió `model_grid_elevation` del molde,
que es la celda de Open-Meteo: el encargo decía "como hace el molde". Cuando
el molde tiene un número de OTRO proveedor, el encargo tiene que prohibirlo.

RECURRENTE: BOM UTF-8 en los tres ficheros (PowerShell). Leer siempre con
`utf-8-sig` al integrar; `ast.parse` sobre `utf-8` falla y `pytest` no, así
que el BOM pasa desapercibido hasta que otro lo lee.
