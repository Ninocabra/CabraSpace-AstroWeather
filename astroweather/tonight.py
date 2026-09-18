"""The operational answer for one night (SPEC v8 sections 1.1 and 6.2).

This is the MVP the first three slices were building towards. It answers one
question end to end -- "will I be able to observe tonight, safely, and when?" --
and it answers it the way the specification insists: three separate outputs,
each with its reason and its state of calibration, and nothing published
without saying how much to trust it.

It deliberately does NOT answer "what should I photograph": that needs the sky
background, transparency and seeing slices, and promising it now would be the
recommender MVP dressed up as the operational one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .roofcalibration import load_calibrator
from .ephem import moon_illumination, moon_positions, night_for
from .verification import wind_correction
from .gates import (SafetyVerdict, evaluate, opening_probability, opening_window,
                    probability_usable)
from .queries import connect
from .site import NERPIO, Site


@dataclass
class HourForecast:
    t_utc: dt.datetime
    cloud_total: float | None
    clear_probability: float | None
    usable_probability: float
    moon_altitude: float
    safety: SafetyVerdict
    calibrated: bool

    @property
    def limiting(self) -> str:
        """What is holding this hour back -- the 'why' behind the number."""
        if self.safety.blocking:
            return self.safety.blocking[0].name
        watching = [r for r in self.safety.risks if r.level == "watch"]
        if watching:
            return watching[0].name
        if self.clear_probability is not None and self.clear_probability < 0.5:
            return "nubes"
        if self.moon_altitude > 20:
            return "luna"
        return "-"


@dataclass
class NightReport:
    night: dt.date
    site: str
    hours: list[HourForecast]
    moon_illumination: float
    dark_hours: float
    calibration_note: str

    @property
    def usable_hours(self) -> float:
        return sum(h.usable_probability for h in self.hours)

    @property
    def best_window(self) -> tuple[dt.datetime, dt.datetime, float] | None:
        """Longest run of hours above even odds, with its mean probability."""
        best, current = None, []
        for hour in self.hours + [None]:
            if hour and hour.usable_probability >= 0.5:
                current.append(hour)
                continue
            if len(current) >= 2:
                mean = sum(h.usable_probability for h in current) / len(current)
                if best is None or len(current) > best[2]:
                    best = (current[0].t_utc, current[-1].t_utc, len(current), mean)
            current = []
        return (best[0], best[1], best[3]) if best else None

    @property
    def step_hours(self) -> float:
        """Spacing of the hourly grid, so the window is measured in hours and
        not in slots whatever the forecast cadence."""
        if len(self.hours) < 2:
            return 1.0
        delta = (self.hours[1].t_utc - self.hours[0].t_utc).total_seconds() / 3600
        return delta if delta > 0 else 1.0

    @property
    def verdict_window(self) -> tuple[dt.datetime, dt.datetime, float] | None:
        """The two hours the verdict rests on, so "Abierto" names its window."""
        found = opening_window([h.usable_probability for h in self.hours],
                               step_hours=self.step_hours)
        if found is None:
            return None
        first, last, floor = found
        # El final es el FINAL de la ultima casilla: "02:00-04:00" son dos horas.
        return (self.hours[first].t_utc,
                self.hours[last].t_utc + dt.timedelta(hours=self.step_hours), floor)

    @property
    def opening_probability(self) -> float:
        return opening_probability([h.usable_probability for h in self.hours],
                                   step_hours=self.step_hours)


def _safety_rows(connection, site: Site, start: dt.datetime, end: dt.datetime):
    """Racha, CAPE y lluvia por hora, de CUALQUIER modelo que las publique.

    POR QUE ESTO NO VA CON EL FILTRO DE MODELO DE LA NUBE. La nube se filtra al
    modelo con el que se entreno el calibrador, y con razon: la curva es de una
    malla concreta y aplicarla a otra es corregir numeros que no son suyos. Pero
    esa razon vale para lo que pasa por una CURVA AJUSTADA, no para lo que pasa
    por un umbral fisico. La puerta de viento pregunta si la racha supera 11,1
    m/s; ese umbral no se midio contra ninguna malla, asi que la racha de otro
    modelo responde a la pregunta igual de bien.

    Y NO ES ACADEMICO. El 28-08-2026 la consulta se filtro a `ecmwf_ifs025`
    porque el calibrador venia de ahi -- correcto -- pero `ecmwf_ifs025` NO
    publica racha, ni CAPE, ni probabilidad de precipitacion: las publica
    `ecmwf_ifs`. Desde ese dia las tres puertas de seguridad recibian None en
    todas las horas y devolvian "ok" sin mirar nada. Medido la noche del
    01-09-2026: 13 horas de 13 sin un solo dato de seguridad, mientras el
    archivo tenia rachas de hasta 8,0 m/s PARA ESAS MISMAS HORAS.

    `arg_max(col, run_utc)` ignora los NULL del argumento, asi que cada columna
    se queda con su valor mas reciente que existe, sin que una pasada de solo
    nubes -- que corre en cada recogida -- desplace a la de superficie completa,
    que corre una vez al dia.
    """
    filas = connection.execute("""
        SELECT valid_utc,
               arg_max(wind10m,       run_utc),
               arg_max(gust10m,       run_utc),
               arg_max(precipitation, run_utc),
               arg_max(prob_precip,   run_utc),
               arg_max(cape,          run_utc),
               arg_max(model,         run_utc) FILTER (WHERE gust10m IS NOT NULL)
        FROM forecasts
        WHERE site_id = ? AND valid_utc BETWEEN ? AND ?
          AND member IS NULL AND model NOT LIKE '%prev%'
        GROUP BY valid_utc
    """, [site.site_id, start, end]).fetchall()
    return {f[0]: f[1:] for f in filas}


def _forecast_rows(site: Site, start: dt.datetime, end: dt.datetime, root=None,
                   model: str | None = None):
    """Las horas de la noche: la NUBE de un modelo, la SEGURIDAD de quien la dé.

    El filtro por modelo no es cosmetico para la nube. Sin el, la consulta se
    queda con la pasada mas reciente de CUALQUIER proveedor, hora por hora: ICON
    corre cada 3 h y GFS cada 6, asi que pueden desplazar al ECMWF en unas horas
    y no en otras y dejar una noche cosida con tres modelos distintos. Y encima
    la curva de calibracion es de uno solo, asi que aplicarla al resto es
    corregir numeros que no son suyos. El propio marcador avisa de que los
    proveedores no entienden lo mismo por "cobertura nubosa".

    Las variables de SEGURIDAD son otra cosa y van por su camino: ver
    `_safety_rows`.
    """
    connection = connect(root)
    # El plan B (sin el modelo de la curva) no puede coger un PERCENTIL: la
    # media de WeatherNext y sus p10..p90 son la misma pasada, empatan en
    # `run_utc`, y hasta el 18-09-2026 DuckDB elegia una al azar en cada
    # consulta -- tres llamadas seguidas dieron 0,52, 0,61 y 0,66 la misma
    # noche en un PC sin ECMWF. Y el desempate por modelo hace el resultado
    # repetible cuando dos proveedores comparten hora de pasada.
    condicion = ("AND model = ?" if model else
                 "AND model NOT LIKE '%prev%' AND NOT regexp_matches(model, '_p[0-9]{2}$')")
    parametros = [site.site_id, start, end] + ([model] if model else [])
    nubes = connection.execute(f"""
        SELECT valid_utc, cloud_total, lead_h
        FROM forecasts
        WHERE site_id = ? AND valid_utc BETWEEN ? AND ?
          AND member IS NULL AND cloud_total IS NOT NULL
          {condicion}
        QUALIFY row_number() OVER (PARTITION BY valid_utc
                                   ORDER BY run_utc DESC, model) = 1
        ORDER BY valid_utc
    """, parametros).fetchall()
    seguridad = _safety_rows(connection, site, start, end)
    salida = []
    for valid, cloud, lead in nubes:
        viento, racha, precip, pprec, cape, _modelo = seguridad.get(
            valid, (None, None, None, None, None, None))
        salida.append((valid, cloud, viento, racha, precip, pprec, cape, lead))
    return salida


def safety_source(site: Site, start: dt.datetime, end: dt.datetime, root=None):
    """De que modelo salio la racha, para poder declararlo. None si no hay."""
    modelos = {f[5] for f in _safety_rows(connect(root), site, start, end).values()
               if f[5]}
    return sorted(modelos)


def report(day: dt.datetime | None = None, site: Site = NERPIO, root=None) -> NightReport:
    """Build the operational report for the night beginning on `day`."""
    day = day or dt.datetime.now(dt.timezone.utc)
    night = night_for(day, site)
    calibrator = load_calibrator()
    now = dt.datetime.now(dt.timezone.utc)

    # Primero el modelo con el que se calibro. Hasta el 2026-08-28 esto no se
    # comprobaba y el producto llevaba consumiendo `ecmwf_ifs` mientras la curva
    # venia del retroanalisis de `ecmwf_ifs025`: dos mallas distintas del mismo
    # proveedor, y una correccion medida sobre la otra.
    wanted = calibrator.trained_on_model if calibrator else None
    rows = _forecast_rows(site, night.window_start, night.window_end, root, wanted)
    mismatch = None
    if wanted and not rows:
        rows = _forecast_rows(site, night.window_start, night.window_end, root)
        if rows:
            mismatch = wanted
    times = [r[0] for r in rows]
    moon = {p.t_utc: p for p in moon_positions(times, site)} if times else {}

    # LA CORRECCION DE VIENTO, APLICADA. Se medía desde el 29-08-2026 y se
    # publicaba como aviso al pie del panel, pero la puerta seguia comiendose la
    # racha CRUDA: el modelo prevé el 55 % del viento medio real en este sitio
    # -- terreno de montania contra una celda de 25 km -- asi que una puerta de
    # seguridad estaba decidiendo con un numero corto y el aviso solo servia
    # para que la culpa fuera del lector.
    #
    # Aplicar al racheado el factor medido sobre el viento MEDIO es un supuesto,
    # y se declara: no hay verdad terreno de racha en ninguna fuente (la pagina
    # del sitio publica media, no racha). Lo que lo sostiene es que las dos
    # salen de la MISMA celda y el mismo esquema de capa limite, asi que el
    # defecto de representatividad del terreno las afecta igual. Y falla del
    # lado seguro: sube la racha, o sea cierra antes.
    #
    # No se aplica hasta que hay pares suficientes (`wind_correction` decide), y
    # mientras tanto el factor es 1,0 y el aviso sigue en pie.
    correccion = wind_correction(root)
    factor = float(correccion.get("factor", 1.0) or 1.0)

    hours: list[HourForecast] = []
    for valid, cloud, wind, gust, precipitation, probability, cape, lead in rows:
        hours_ahead = max(0.0, (valid - now).total_seconds() / 3600)
        verdict = evaluate(
            t_utc=valid, hours_ahead=hours_ahead,
            gust=(gust * factor if gust is not None else None),
            precipitation=precipitation, precipitation_probability=probability,
            cape=cape,
        )
        clear = (calibrator.probability(cloud, hours_ahead) if calibrator else None)
        hours.append(HourForecast(
            t_utc=valid,
            cloud_total=cloud,
            clear_probability=clear,
            usable_probability=probability_usable(clear if clear is not None else 0.0,
                                                  verdict),
            moon_altitude=moon[valid].altitude if valid in moon else 0.0,
            safety=verdict,
            calibrated=calibrator is not None,
        ))

    if calibrator is None:
        note = "SIN CALIBRAR: probabilidad no disponible"
    elif getattr(calibrator, "truth", "fotometro") == "techo":
        # La verdad terreno es el TECHO REAL, y el BSS es mensual: la curva se
        # puntua por lo que el observatorio abrio cada mes, no por el fotometro.
        note = (f"probabilidad calibrada contra el techo real: {calibrator.months} "
                f"meses y {calibrator.nights} noches (BSS mensual sobre climatología: "
                f"{(1 - calibrator.brier / calibrator.brier_climatology) * 100:+.1f} %)")
    else:
        note = (f"probabilidad calibrada con {calibrator.trained_on} pares "
                f"(BSS sobre climatología: "
                f"{(1 - calibrator.brier / calibrator.brier_climatology) * 100:+.1f} %)")
    if mismatch:
        # Se dice, no se esconde: una probabilidad calibrada sobre otro modelo
        # sigue siendo mejor que ninguna, pero deja de ser lo que promete.
        note += ("\n  AVISO: se calibró con " + mismatch + " y esta noche no hay "
                 "nubes de ese modelo, así que la curva se está aplicando a "
                 "otro. La probabilidad vale menos de lo que dice el BSS.")

    return NightReport(
        night=night.night.date(),
        site=site.name,
        hours=hours,
        moon_illumination=moon_illumination(night.dusk_astronomical or night.night),
        dark_hours=night.dark_hours,
        calibration_note=note,
    )


def render(night_report: NightReport) -> str:
    """The operational answer as text. The canvas comes later; the data is here."""
    lines = [
        f"NOCHE DEL {night_report.night} — {night_report.site}",
        "=" * 64,
        f"  oscuridad astronomica: {night_report.dark_hours:.1f} h"
        f"   ·   Luna al {night_report.moon_illumination * 100:.0f} %",
        f"  horas utilizables esperadas: {night_report.usable_hours:.1f}",
        f"  probabilidad de condiciones para abrir: "
        f"{night_report.opening_probability:.0%}",
    ]
    window = night_report.best_window
    if window:
        lines.append(f"  mejor ventana: {window[0]:%H:%M} a {window[1]:%H:%M} UTC "
                     f"(media {window[2]:.0%})")
    else:
        lines.append("  mejor ventana: ninguna por encima del 50 %")

    lines += ["", "  hora   nubes  P(despejado)  P(utilizable)  limita   seguridad"]
    for hour in night_report.hours:
        clear = f"{hour.clear_probability:.0%}" if hour.clear_probability is not None else "  -"
        lines.append(
            f"  {hour.t_utc:%H:%M}   {hour.cloud_total:3.0f}%      {clear:>5}"
            f"         {hour.usable_probability:5.0%}      {hour.limiting:8s} "
            f"{'OK' if hour.safety.safe_to_open else 'CERRADO'}"
        )

    blocked = [h for h in night_report.hours if not h.safety.safe_to_open]
    if blocked:
        lines += ["", "  motivos de cierre:"]
        for hour in blocked[:4]:
            lines.append(f"    {hour.t_utc:%H:%M}  {hour.safety.summary}")

    # Medido el 2026-08-28 sobre 33.799 pares, contra el modelo de cielo con la
    # ley de dispersion lunar del sitio. Antes de arreglarla estas dos cifras
    # eran 40 % y 75 %, y la de arriba estaba mal por la verdad, no por el
    # forecast: una noche despejada con Luna se archivaba como nublada.
    lines += ["", f"  {night_report.calibration_note}"]
    if "techo real" in night_report.calibration_note:
        window = night_report.verdict_window
        lines += ["  la probabilidad de la noche es la hora mas floja de la mejor",
                  "  ventana de dos horas seguidas"
                  + (f": {window[0]:%H:%M}-{window[1]:%H:%M} UTC" if window else "")
                  + "."]
    else:
        lines += ["  cubierto total previsto -> 42 % de horas despejadas; cielo limpio",
                  "  previsto -> 90 %, sobre una tasa base del 74 %. Sigue avisando",
                  "  mejor de las noches malas, pero ya promete las buenas."]
    lines += ["  seeing y calidad por dimension: pendientes de las rodajas 4-6."]
    return "\n".join(lines)


if __name__ == "__main__":
    print(render(report()))
