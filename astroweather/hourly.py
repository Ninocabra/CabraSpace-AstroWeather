# -*- coding: utf-8 -*-
"""Poner una serie horaria del archivo sobre la rejilla de la noche.

`queries.night_series` devuelve el archivo tal cual: una muestra por hora. La
rejilla con la que se planifica la noche NO es esa -- `night.adaptive_hours` da
diez minutos dentro de la oscuridad astronomica y treinta a los lados -- asi que
hay que evaluar la serie DONDE se le pregunta.

POR QUE INTERPOLAR Y NO ESCALONAR. La tentacion es asignar a cada instante el
valor de su hora y ya, pero eso mete un salto artificial en cada cambio de hora
que el fenomeno no tiene: el aerosol es un campo suave y sus muestras horarias
son eso, muestras. Y no es una preferencia estetica. El 01-09-2026, probando
esta misma hipotesis contra los datos de CabraTransit, la version escalonada
(dos mitades de noche, un valor cada una) EMPEORO el coeficiente un 19 % y
cuadruplico el rms, mientras la version lineal sobre los mismos datos lo mejoro.
La diferencia entera era la discontinuidad que metia el test, no la fisica
([[la-refutacion-aplastante-tambien-se-audita]]).

FUERA DEL TRAMO CUBIERTO SE SOSTIENE EL EXTREMO, no se extrapola: prolongar una
recta de aerosol se va a valores negativos en cuanto la pendiente baja, y un AOD
negativo no significa nada. Sostener dice "lo mas fresco que sabemos", que es
verdad. `np.interp` ya hace justo esto en los dos bordes.
"""

from __future__ import annotations

import datetime as dt

import numpy as np

UTC = dt.timezone.utc


def _epoch(marca: dt.datetime) -> float:
    """Segundos Unix, tratando lo ingenuo como UTC y no como hora local.

    `datetime.timestamp()` sobre un naive asume la zona de la maquina: en Nerpio
    son dos horas de desfase en verano, que sobre una serie horaria es dos
    muestras enteras de deslizamiento.
    """
    return (marca if marca.tzinfo else marca.replace(tzinfo=UTC)).timestamp()


def at_times(serie: dict, campo: str, times: list[dt.datetime],
             *, fallback: float | None = None) -> list[float | None]:
    """El valor de `campo` en cada instante de `times`, interpolado en el tiempo.

    Si la serie no trae ni una muestra del campo, devuelve `fallback` en todas
    las horas -- que es la mediana de la noche, es decir, exactamente lo que se
    publicaba antes. La serie horaria AÑADE resolucion, no sustituye a nada:
    cuando no la hay, el informe sale igual que salia.
    """
    muestras = sorted((marca, punto[campo]) for marca, punto in serie.items()
                      if punto.get(campo) is not None)
    if not muestras:
        return [fallback] * len(times)
    xs = np.array([_epoch(marca) for marca, _ in muestras], dtype=float)
    ys = np.array([float(valor) for _, valor in muestras], dtype=float)
    return [float(v) for v in np.interp([_epoch(t) for t in times], xs, ys)]
