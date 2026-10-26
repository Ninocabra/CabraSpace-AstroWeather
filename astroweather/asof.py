"""Lo que el archivo sabia de cada hora prevista EN CADA INSTANTE.

POR QUE NO VALE LA VISTA DEDUPLICADA. Para los modelos de Open-Meteo,
`run_utc` es el DIA truncado a 00Z (`workers/fetch_forecast.py:101`), pero cada
recogida del dia trae la pasada mas reciente que sirve Open-Meteo (00Z, 06Z,
12Z...), y la vista `forecasts` de `queries.py` se queda con la ULTIMA recogida
de cada etiqueta: la de las 21-23 UTC, ya con la noche empezada. Preguntarle
"la pasada de hoy anterior a las 15 UTC" devuelve la recogida de las 23 UTC.
Medido el 26-10-2026 (`hindcast.py`, M16): para la hora 22 UTC del 17-09 la
"pasada del 17-09" vale 69, 65, 17 y 12 % de nube segun se recogiera a las 02,
10, 16 o 23 UTC, y sobre 1979 horas la vista coincide con la nube que de verdad
uso el producto solo en el 56 %; esta reconstruccion, en el 100 %.

Aqui se lee el Parquet CRUDO y, para cada hora prevista, se guarda la serie de
recogidas en orden de `retrieved_at`: `AsOf.at(hora, instante)` devuelve la mas
reciente anterior al instante. Lo usan el retroanalisis (`hindcast.py`) y la
curva del techo (`roofcalibration.py`), que se entrena con la nube que habia al
corte de la tarde y no con la de la noche ya empezada.
"""

from __future__ import annotations

import datetime as dt
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

UTC = dt.timezone.utc
# Una pasada 00Z de los modelos globales esta en Open-Meteo unas 6-7 h despues.
NWP_LATENCY = dt.timedelta(hours=7)


def _aware(t: dt.datetime) -> dt.datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _hour_key(t: dt.datetime) -> dt.datetime:
    """Hora UTC naive: la misma clave que `hindcast.hour_key`."""
    if t.tzinfo is not None:
        t = t.astimezone(UTC).replace(tzinfo=None)
    return t.replace(minute=0, second=0, microsecond=0)


class AsOf:
    """Lo que el archivo sabia de cada hora en cada instante: la recogida mas
    reciente anterior a `when`."""

    def __init__(self, rows):
        by_hour: dict = defaultdict(list)
        for when, hour, values in rows:
            by_hour[hour].append((when, values))
        self.by_hour = {h: sorted(v, key=lambda x: x[0]) for h, v in by_hour.items()}
        self.times = {h: [x[0] for x in v] for h, v in self.by_hour.items()}

    def at(self, hour: dt.datetime, when: dt.datetime):
        stamps = self.times.get(hour)
        if not stamps:
            return None
        i = bisect_right(stamps, _aware(when)) - 1
        return self.by_hour[hour][i][1] if i >= 0 else None


def raw_forecast_asof(connection, root: Path, model: str, since: dt.datetime) -> AsOf:
    """Nube total y capas de `model`, recogida a recogida, desde el Parquet crudo.

    Cada valor es (total, baja, media, alta, run_utc) y la clave, la hora
    prevista como datetime UTC naive. Fuera de WeatherNext, las filas sin
    `retrieved_at` no se pueden fechar y no entran.
    """
    base = (root / "forecasts").as_posix()
    rows = connection.execute(f"""
        SELECT DISTINCT retrieved_at, run_utc, valid_utc, cloud_total, cloud_low,
               cloud_mid, cloud_high
        FROM read_parquet('{base}/**/*.parquet', union_by_name=true)
        WHERE model = ? AND member IS NULL AND cloud_total IS NOT NULL AND valid_utc >= ?
    """, [model, since]).fetchall()
    if model.startswith("weathernext"):
        # WeatherNext lleva su hora de inicializacion de verdad, pero agosto y
        # septiembre llegaron con el relleno del 17-09: se fecha la
        # disponibilidad por la pasada mas su retraso, no por la recogida.
        return AsOf((_aware(run) + NWP_LATENCY, _hour_key(v),
                     (float(t), lo, mi, hi, _aware(run)))
                    for ret, run, v, t, lo, mi, hi in rows)
    return AsOf((_aware(ret), _hour_key(v), (float(t), lo, mi, hi, _aware(run)))
                for ret, run, v, t, lo, mi, hi in rows if ret is not None)
