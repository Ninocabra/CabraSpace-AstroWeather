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
reciente anterior al instante, y `AsOf.issued(hora, d)` la que habia a las
`ISSUE_CUTOFF_HOUR_UTC` de d dias antes de que empezara su noche. Lo usan el
retroanalisis (`hindcast.py`), la curva del techo (`roofcalibration.py`), que
se entrena con la nube que habia al corte de la tarde y no con la de la noche
ya empezada, y desde M16 (27-10-2026) el marcador (`scoreboard.py`). La vista
de `queries.connect(as_of=...)` aplica la misma regla de disponibilidad
(`AVAILABLE_AT_SQL`) para quien pregunte en SQL.
"""

from __future__ import annotations

import datetime as dt
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

UTC = dt.timezone.utc
# Una pasada 00Z de los modelos globales esta en Open-Meteo unas 6-7 h despues.
NWP_LATENCY = dt.timedelta(hours=7)
# La hora de la tarde a la que se pregunta "que se sabia de esta noche": la de
# la pasada de la tarde del producto. Supuesta, NO medida (seccion 9 del
# informe del 26-10). Vivia en `roofcalibration.py`; desde M16 (27-10-2026) la
# comparten la curva del techo y el marcador.
ISSUE_CUTOFF_HOUR_UTC = 15

# CUANDO ESTUVO DISPONIBLE UNA FILA, en SQL: la misma regla para la lectura del
# Parquet crudo de aqui abajo y para la vista de `queries.connect(as_of=...)`.
# Fuera de WeatherNext, la recogida; WeatherNext, por su pasada mas el retraso
# (agosto y septiembre llegaron con el relleno del 17-09, y su recogida no dice
# cuando se pudo saber).
AVAILABLE_AT_SQL = (
    "CASE WHEN starts_with(model, 'weathernext') "
    f"THEN run_utc + INTERVAL {int(NWP_LATENCY.total_seconds() // 3600)} HOUR "
    "ELSE retrieved_at END")


def night_of(hour: dt.datetime) -> dt.date:
    """La noche de una hora: la fecha del dia en que EMPEZO (antes de las 12
    UTC, la de la vispera). La misma cuenta que `queries.nights`."""
    return (hour - dt.timedelta(hours=12)).date()


def issue_cutoff(night: dt.date, lead_days: int = 0,
                 hour: int = ISSUE_CUTOFF_HOUR_UTC) -> dt.datetime:
    """El instante al que se pregunta por la noche `night` con `lead_days` de
    antelacion: las `hour` UTC de `lead_days` dias antes de que empiece."""
    day = night - dt.timedelta(days=lead_days)
    return dt.datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


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

    def issued(self, hour: dt.datetime, lead_days: int = 0):
        """Lo que habia de `hour` al corte de la tarde de su noche, `lead_days`
        dias antes: la pregunta "la pasada anterior a las 15 UTC"."""
        return self.at(hour, issue_cutoff(night_of(hour), lead_days))


def raw_forecast_asof(connection, root: Path, model: str, since: dt.datetime) -> AsOf:
    """Nube total y capas de `model`, recogida a recogida, desde el Parquet crudo.

    Cada valor es (total, baja, media, alta, run_utc) y la clave, la hora
    prevista como datetime UTC naive. La disponibilidad de cada fila es
    `AVAILABLE_AT_SQL`: fuera de WeatherNext, las filas sin `retrieved_at` no
    se pueden fechar y no entran.
    """
    base = (root / "forecasts").as_posix()
    rows = connection.execute(f"""
        SELECT DISTINCT {AVAILABLE_AT_SQL} AS available_at, run_utc, valid_utc,
               cloud_total, cloud_low, cloud_mid, cloud_high
        FROM read_parquet('{base}/**/*.parquet', union_by_name=true)
        WHERE model = ? AND member IS NULL AND cloud_total IS NOT NULL AND valid_utc >= ?
    """, [model, since]).fetchall()
    return AsOf((_aware(at), _hour_key(v), (float(t), lo, mi, hi, _aware(run)))
                for at, run, v, t, lo, mi, hi in rows if at is not None)


def raw_ensemble_asof(connection, root: Path, model: str, since: dt.datetime,
                      min_members: int = 40) -> AsOf:
    """La media del EPS de `model`, recogida a recogida: cada valor es
    (P(despejado) media = 1 - nube media, fraccion de miembros con < 20 % de
    nube). Una recogida con menos de `min_members` miembros (una bajada a
    medias) no entra. Lo usan el retroanalisis y el marcador."""
    base = (root / "forecasts").as_posix()
    rows = connection.execute(f"""
        SELECT {AVAILABLE_AT_SQL} AS available_at, valid_utc,
               avg(1 - cloud_total / 100.0),
               avg(CASE WHEN cloud_total < 20 THEN 1.0 ELSE 0.0 END),
               count(DISTINCT member)
        FROM read_parquet('{base}/**/*.parquet', union_by_name=true)
        WHERE model = ? AND member IS NOT NULL AND cloud_total IS NOT NULL
          AND valid_utc >= ?
        GROUP BY 1, 2
    """, [model, since]).fetchall()
    return AsOf((_aware(at), _hour_key(v), (float(m), float(f)))
                for at, v, m, f, n in rows if at is not None and n >= min_members)
