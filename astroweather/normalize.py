"""Saneado en la frontera de los adaptadores, ANTES de `write_canonical`.

El validador de `archive.write_canonical` existe para parar errores de
unidades: un -5 mm de lluvia es casi seguro una conversion que no se hizo, y
tiene que seguir reventando alli. Pero Open-Meteo devuelve de vez en cuando
-0.1 mm en algun miembro del ensemble (artefacto numerico de la interpolacion;
visto el 11-11-2026, rompio seis pasadas seguidas del archivo del EPS porque
UN valor en ~18.000 tumbaba el lote entero). Eso no es un error de unidades,
es redondeo, y se corrige AQUI, en el adaptador, sin aflojar el validador:
lo que se desvia mas de `NEGATIVE_ARTIFACT_SLACK` sigue llegando a la frontera
de escritura y falla alli.

El raw nunca se toca: `store_raw` guarda la respuesta tal cual; solo la fila
canonica lleva el valor recortado, y marcado en `quality_flags`.
"""

from __future__ import annotations

# Un negativo de hasta esta magnitud (en la unidad del campo, mm para la
# precipitacion) es un artefacto numerico; por debajo, es un error de origen.
NEGATIVE_ARTIFACT_SLACK = 0.5

PRECIP_CLAMPED_FLAG = "precip_clamped_to_zero"


def clamp_small_negative(value: float | None, *,
                         slack: float = NEGATIVE_ARTIFACT_SLACK) -> tuple[float | None, bool]:
    """Devuelve (valor, recortado) para una magnitud fisicamente no negativa.

    `-slack <= value < 0` pasa a 0.0 y se avisa con `True`. Todo lo demas
    (None, positivos, y negativos mas alla del margen) vuelve intacto: el
    ultimo caso lo rechaza el validador de escritura, que es su trabajo.
    """
    if value is None:
        return None, False
    number = float(value)
    if -slack <= number < 0.0:
        return 0.0, True
    return number, False
