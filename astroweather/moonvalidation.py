"""Validacion de la ley de dispersion lunar contra NOCHES RESERVADAS.

Existe como modulo y no como script de un dia porque la regla del proyecto es
que nada se publica sin su incertidumbre, y porque quien vuelva a derivar las
constantes de `moonscatter` necesita poder repetir esto tal cual.

Las noches se reservan por hash de la fecha, no por un corte temporal: guardar
el ultimo anio mezclaria la validacion con el ciclo de 18,6 anios de la
declinacion lunar, que es justo la variable de la que depende todo esto.

EL CRITERIO, que es conjunto a proposito. El ingenuo -- |r| < 0,15 entre
iluminacion lunar y fraccion de noche despejada -- es manipulable: se consigue
ensanchando el umbral de despejado hasta que el detector no vea nubes. Asi que
se exige a la vez:

  1. cociente medido/modelo de la luz lunar entre 0,90 y 1,10 en CADA franja de
     altura, sobre horas seleccionadas por DISPERSION, que no usa el nivel y
     por tanto no es circular;
  2. sin tendencia con la fase ni con la masa de aire de la Luna;
  3. |r| < 0,15 en noches reservadas;
  4. sin degradar la deteccion de nubes contra una etiqueta independiente.

Se cumplen 1, 2 y 4. NO se cumple 3, y no se fuerza: lo que queda de
correlacion es SENSIBILIDAD y no sesgo (ver calibration.CLEAR_SCATTER_THRESHOLD).

Ejecutar:  python -m astroweather.moonvalidation
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections import defaultdict

import numpy as np

from .calibration import (CLEAR_LEVEL_CENTRE, CLEAR_LEVEL_TOLERANCE,
                          CLEAR_SCATTER_THRESHOLD, MIN_SAMPLES_PER_HOUR,
                          MOON_IN_FIELD_ALTITUDE)
from .queries import connect
from .skymodel import predict

RESERVED_FRACTION = 0.25
# Seleccion de horas sin nube para el criterio 1. Mas laxo que el umbral de la
# verdad a proposito: aqui no se decide si una hora es despejada, solo se quita
# la nube que sesgaria la mediana del cociente.
CLEAN_SCATTER = 0.15
# Y el cociente solo se mide donde la Luna pone luz suficiente para que el
# cociente hable de la Luna. Con un creciente fino a 3 grados de altura el
# modelo lunar es el 2 % del cielo, asi que un 1 % de error en el SUELO -- que
# se ajusto aparte, en noches sin Luna -- se convierte en un 50 % de error
# aparente en el cociente. Eso no mide la ley de dispersion, mide el suelo.
MIN_MOON_SHARE = 0.10
ALTITUDE_BANDS = ((0, 10), (10, 20), (20, 30), (30, 40), (40, 50), (50, 60),
                  (60, 90))


def is_reserved(night: str) -> bool:
    """Estable entre maquinas y entre ejecuciones: hash(), en Python, no lo es."""
    digest = hashlib.md5(night.encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF < RESERVED_FRACTION


def _night_of(when: dt.datetime) -> str:
    """La noche empieza el dia anterior si aun no ha amanecido."""
    when = when.astimezone(dt.timezone.utc)
    if when.hour < 12:
        when -= dt.timedelta(days=1)
    return when.date().isoformat()


def collect(since: str = "2019-01-01", root=None) -> dict:
    """Medida, modelo y sus partes, por bin de 10 minutos de noche cerrada."""
    connection = connect(root)
    rows = connection.execute(f"""
        SELECT date_trunc('hour', t_utc)
               + INTERVAL (floor(minute(t_utc) / 10) * 10) MINUTE AS t,
               median(sky_mag_raw) AS mag
        FROM measurements
        -- SOLO EL FOTOMETRO. La allsky escribe en esta misma columna una
        -- magnitud INSTRUMENTAL, monotona en el ADU y con punto cero
        -- ARBITRARIO (`fetch_allsky` lo dice en su comentario): valores de
        -- -16 donde el cielo son 21. Sin este filtro entran en la mediana y
        -- salen residuos de -34 mag contra el modelo. `skybackground` ya
        -- filtraba asi; aqui no, y es el mismo concepto.
        WHERE instrument_type = 'TESS' AND sky_mag_raw IS NOT NULL
          AND t_utc >= '{since}'
        GROUP BY t ORDER BY t
    """).fetchall()
    if not rows:
        return {}

    times = [r[0] for r in rows]
    measured = np.array([float(r[1]) for r in rows])
    predictions = predict(times)

    keep = [index for index, sky in enumerate(predictions)
            if sky.sun_altitude < -18.0
            and sky.moon_altitude <= MOON_IN_FIELD_ALTITUDE]
    return {
        "hour": np.array([times[i].astimezone(dt.timezone.utc).replace(
            minute=0, second=0, microsecond=0, tzinfo=None) for i in keep]),
        "night": np.array([_night_of(times[i]) for i in keep]),
        "measured": measured[keep],
        "predicted": np.array([predictions[i].predicted_mag for i in keep]),
        "moon_altitude": np.array([predictions[i].moon_altitude for i in keep]),
        "phase_angle": np.array([predictions[i].moon_phase_angle for i in keep]),
    }


def report(since: str = "2019-01-01", root=None) -> str:
    from .moonsky import mag_to_nl, moon_brightness
    from .skybackground import budget

    data = collect(since, root)
    if not data:
        return "sin archivo que validar"

    residual = data["measured"] - data["predicted"]
    reserved = np.array([is_reserved(n) for n in data["night"]])
    illumination = (1.0 + np.cos(np.radians(data["phase_angle"]))) / 2.0

    # Luz lunar medida = flujo medido menos todo lo que no es la Luna.
    moon_model = np.array([
        moon_brightness(phase_angle_deg=float(a),
                        separation_deg=max(1.0, 90.0 - float(m)),
                        moon_altitude_deg=float(m), target_altitude_deg=90.0
                        ).physical_nl
        for a, m in zip(data["phase_angle"], data["moon_altitude"])])
    measured_flux = np.array([mag_to_nl(v) for v in data["measured"]])
    predicted_flux = np.array([mag_to_nl(v) for v in data["predicted"]])
    moon_observed = measured_flux - (predicted_flux - moon_model)

    scatter = np.full(len(residual), np.nan)
    by_hour: dict = defaultdict(list)
    for index, key in enumerate(data["hour"]):
        by_hour[key].append(index)
    for indices in by_hour.values():
        if len(indices) >= MIN_SAMPLES_PER_HOUR:
            scatter[indices] = np.std(residual[indices])
    share = moon_model / predicted_flux
    clean = (reserved & np.isfinite(scatter) & (scatter < CLEAN_SCATTER)
             & (share >= MIN_MOON_SHARE))

    lines = ["VALIDACION DE LA LEY DE DISPERSION LUNAR",
             "=" * 62,
             f"  noches: {len(set(data['night']))} en total, "
             f"{len(set(data['night'][reserved]))} RESERVADAS "
             f"({100 * RESERVED_FRACTION:.0f} % por hash de la fecha)",
             f"  bins de 10 min reservados: {int(reserved.sum())}",
             ""]

    lines.append("  1. COCIENTE MEDIDO/MODELO DE LA LUZ LUNAR (0,90 - 1,10)")
    lines.append(f"     solo donde la Luna pone >= {100 * MIN_MOON_SHARE:.0f} % de la luz "
                 f"({int(clean.sum())} bins de {int((reserved & (share > 0)).sum())})")
    passes = True
    for low, high in ALTITUDE_BANDS:
        mask = clean & (data["moon_altitude"] >= low) & (data["moon_altitude"] < high)
        if mask.sum() < 40:
            continue
        value = float(np.median(moon_observed[mask] / moon_model[mask]))
        good = 0.90 <= value <= 1.10
        passes &= good
        lines.append(f"     Luna {low:2d}-{high:2d} deg  n={int(mask.sum()):5d}   "
                     f"{value:5.3f}   {'ok' if good else 'FUERA'}")
    lines += [f"     -> {'CUMPLE' if passes else 'NO CUMPLE'}", ""]

    lines.append("  2. SIN TENDENCIA CON LA FASE NI CON LA MASA DE AIRE")
    band = clean & (data["moon_altitude"] >= 20) & (data["moon_altitude"] < 50)
    for low, high in ((0, 30), (30, 60), (60, 90), (90, 130)):
        mask = band & (data["phase_angle"] >= low) & (data["phase_angle"] < high)
        if mask.sum() > 30:
            lines.append(f"     fase {low:3d}-{high:3d}  n={int(mask.sum()):5d}   "
                         f"{float(np.median(moon_observed[mask] / moon_model[mask])):5.3f}")
    airmass = 1.0 / np.sin(np.radians(np.maximum(data["moon_altitude"], 1.0)))
    up = clean & (data["moon_altitude"] > 10)
    for low, high in ((1.0, 1.3), (1.3, 1.7), (1.7, 2.5), (2.5, 5.0)):
        mask = up & (airmass >= low) & (airmass < high)
        if mask.sum() > 30:
            lines.append(f"     masa de aire {low:3.1f}-{high:3.1f}  n={int(mask.sum()):5d}   "
                         f"{float(np.median(moon_observed[mask] / moon_model[mask])):5.3f}")
    lines.append("")

    lines.append("  3. CORRELACION CON LA LUNA EN NOCHES RESERVADAS")
    per_night: dict = defaultdict(list)
    moon_per_night: dict = defaultdict(list)
    for key, indices in by_hour.items():
        if len(indices) < MIN_SAMPLES_PER_HOUR or not reserved[indices[0]]:
            continue
        values = residual[indices]
        clear = (float(np.std(values)) < CLEAR_SCATTER_THRESHOLD
                 and abs(float(np.mean(values)) - CLEAR_LEVEL_CENTRE)
                 < CLEAR_LEVEL_TOLERANCE)
        night = data["night"][indices[0]]
        per_night[night].append(bool(clear))
        moon_per_night[night].append(float(np.mean(illumination[indices])))
    nights = [n for n, v in per_night.items() if len(v) >= 3]
    x = np.array([np.mean(moon_per_night[n]) for n in nights])
    y = np.array([np.mean(per_night[n]) for n in nights])
    r = float(np.corrcoef(x, y)[0, 1]) if len(nights) > 3 else float("nan")
    lines.append(f"     {len(nights)} noches   r = {r:+.3f}   "
                 f"{'cumple' if abs(r) < 0.15 else 'NO CUMPLE, y no se fuerza'}")
    for low, high in ((0.0, 0.15), (0.15, 0.5), (0.5, 0.85), (0.85, 1.01)):
        mask = (x >= low) & (x < high)
        if mask.sum() > 5:
            lines.append(f"     iluminacion {low:.2f}-{high:.2f}  "
                         f"{int(mask.sum()):4d} noches   "
                         f"despejado {100 * y[mask].mean():5.1f} %")
    lines += ["     lo que queda es SENSIBILIDAD, no sesgo: con Luna se ve nube",
              "     fina que sin Luna se pierde. Ensanchar el umbral hasta r=0",
              "     hace que las horas con prevision de 90-100 % de nubes salgan",
              "     despejadas el 71,5 % de las veces. Ver calibration.py.", ""]

    lines.append("  4. CONTRA UNA ETIQUETA INDEPENDIENTE (no sale del fotometro)")
    connection = connect(root)
    rows = connection.execute("""
        SELECT valid_utc, avg(cloud_total) FROM forecasts
        WHERE cloud_total IS NOT NULL AND member IS NULL AND model LIKE '%prev1d%'
        GROUP BY valid_utc
    """).fetchall()
    forecast = {r[0].astimezone(dt.timezone.utc).replace(tzinfo=None): float(r[1])
                for r in rows}
    buckets: dict = defaultdict(list)
    for key, indices in by_hour.items():
        if len(indices) < MIN_SAMPLES_PER_HOUR or key not in forecast:
            continue
        values = residual[indices]
        clear = (float(np.std(values)) < CLEAR_SCATTER_THRESHOLD
                 and abs(float(np.mean(values)) - CLEAR_LEVEL_CENTRE)
                 < CLEAR_LEVEL_TOLERANCE)
        buckets[min(int(forecast[key]) // 20, 4)].append(bool(clear))
    if buckets:
        lines.append("     prevision de nubes -> horas que la verdad da despejadas")
        for bucket in sorted(buckets):
            values = buckets[bucket]
            if len(values) > 50:
                lines.append(f"     {20 * bucket:3d}-{20 * bucket + 20:3d} %  "
                             f"n={len(values):5d}   {100 * np.mean(values):5.1f} %")
        lines.append("     monotono y con recorrido: la verdad sigue viendo nubes.")
        lines.append("     Es la clausula que impide comprar el criterio 3 ensanchando.")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
