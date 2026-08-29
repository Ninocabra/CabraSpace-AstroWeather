"""La noche, en un JSON que la web puede leer sola (SPEC v8 seccion 6.2).

Decidido el 28-08-2026: exportacion ESTATICA. Sin servidor, sin VPS y sin CORS
-- un fichero que se genera aqui y que la pagina lee. La alternativa era montar
una API para un sitio con un observatorio y un usuario, y no sale a cuenta.

Lo que sale es el PRODUCTO, no el archivo: las dos preguntas que el sistema
existe para responder.

    1. Estaran abiertos los techos esta noche, o la siguiente?
    2. Que condiciones tengo, y que merece la pena apuntar?

Y una regla que no se salta: **nada se publica sin su incertidumbre**. Por eso
cada bloque lleva de donde sale su numero -- si el aerosol viene de CAMS o de un
supuesto, si el seeing viene del ECMWF o de una constante -- y hay una lista de
avisos que la pagina debe poder enseñar. Un numero sin su porque no se publica,
y la unica forma de que eso sobreviva a la exportacion es que viaje DENTRO de
ella.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from .advice import Conditions, evaluate as evaluate_advice
from .calibration import Calibrator
from .ephem import moon_illumination, moon_positions, night_for
from .extinction import evaluate as evaluate_transparency
from .queries import DATA_ROOT, night_inputs
from .seeing import integrate
from . import site_now
from .site import NERPIO, Site
from .skymodel import predict
from .targets import CATALOGUE, evaluate_night
from .tonight import report
from .verification import wind_correction

# Cuantos objetos se publican. La lista curada tiene diez; sacar los tres
# mejores es una recomendacion, sacar los diez es un listado.
TOP_TARGETS = 3
DEFAULT_PATH = DATA_ROOT / "web" / "noche.json"


def _hour(hour) -> dict:
    return {
        "t_utc": hour.t_utc.isoformat(),
        "nubes_previstas": hour.cloud_total,
        "p_despejado": (round(hour.clear_probability, 3)
                        if hour.clear_probability is not None else None),
        "p_utilizable": round(hour.usable_probability, 3),
        "limita": hour.limiting,
        "seguro_para_abrir": hour.safety.safe_to_open,
        "porque": hour.safety.summary,
    }


def _night(day: dt.datetime, site: Site, root=None, *,
           with_catalogue: bool = True) -> dict | None:
    night_report = report(day, site, root)
    if not night_report.hours:
        return None

    night = night_for(day, site)
    # La MISMA rejilla que la vista de la cupula (`nightview`), y por la misma
    # razon: fina donde se planifica, gruesa donde no se observa. Con una hora
    # de paso el recomendador esta limitado por la discretizacion, no por el
    # modelo -- M13 salia a 2,74 h de SNR contra 3,14 con cualquier paso mas
    # fino, un 13 % (medido el 2026-08-29 sobre la noche del 11-09). Y sobre
    # todo: el numero que se PUBLICA y el que se DIBUJA salen ahora de la misma
    # rejilla, asi que no pueden discrepar.
    times = night.adaptive_hours(10, 30, site)
    forecast = night_inputs(times[0], times[-1], site.site_id, root)
    turbulence = integrate(forecast["jet_speed"])
    transparency = evaluate_transparency(
        aod_total=forecast["aod_total"], aod_dust=forecast["aod_dust"],
        elevation_m=site.site_elevation, delta_h_m=site.delta_h)

    # El informe viene con marcas EN PUNTO (las del proveedor) y la rejilla del
    # recomendador arranca en el ocaso, que no cae en punto: buscar por igualdad
    # exacta fallaba siempre y dejaba E[Q] en cero. Se empareja por la hora.
    usable = {h.t_utc.replace(minute=0, second=0, microsecond=0):
              h.usable_probability for h in night_report.hours}
    targets = evaluate_night(
        CATALOGUE, times,
        usable_probability=[
            usable.get(t.replace(minute=0, second=0, microsecond=0), 0.0)
            for t in times],
        turbulence=turbulence, aod_total=forecast["aod_total"],
        aod_dust=forecast["aod_dust"], site=site)

    window = night_report.best_window
    skies = predict([t for t in times if night.window_start], site)
    darkest = min((s.predicted_mag for s in skies if s.is_night), default=None)

    return {
        "noche": night_report.night.isoformat(),
        "oscuridad_astronomica_h": round(night_report.dark_hours, 1),
        "luna_iluminacion": round(night_report.moon_illumination, 3),
        # La respuesta a la pregunta 1, que es la que manda.
        "probabilidad_de_abrir": round(night_report.opening_probability, 3),
        "horas_utilizables_esperadas": round(night_report.usable_hours, 1),
        "mejor_ventana": ({"desde": window[0].isoformat(),
                           "hasta": window[1].isoformat(),
                           "p_media": round(window[2], 3)} if window else None),
        "horas": [_hour(h) for h in night_report.hours],
        # Y la pregunta 2.
        "cielo": {
            "mag_cenit_mas_oscuro": round(darkest, 2) if darkest else None,
            "seeing": {
                "tercil": turbulence.tercile,
                "rango_arcsec": [round(turbulence.range_arcsec[0], 1),
                                 round(turbulence.range_arcsec[1], 1)],
                "jet_ms": round(turbulence.jet_speed, 0),
                "origen": forecast["jet_source"],
                # Se publica en terciles y NUNCA en arcosegundos sueltos: el
                # modelo predijo 1,21" donde se midieron 3,16".
                "calibrado": False,
            },
            "transparencia": {
                "aod": round(transparency.aod_site, 3),
                "k_total": round(transparency.k_total, 3),
                "veredicto": transparency.verdict,
                "origen": forecast["aod_source"],
            },
        },
        # `evaluate_night` devuelve en orden de CATALOGO, no de calidad: cortar
        # los primeros publicaba los peores. Y un objeto con cero horas no es
        # una recomendacion, asi que si no hay ninguno la lista va vacia y el
        # motivo se dice aparte -- mejor que fingir tres.
        "recomendaciones": [
            {
                "objeto": t.target.name,
                "clase": t.target.difficulty,
                "horas_si_despeja": round(t.q_conditional, 1),
                "horas_esperadas": round(t.q_expected, 1),
                "veredicto": t.verdict,
                "porque": t.reason,
            }
            for t in sorted(targets,
                            key=lambda t: (t.q_expected, t.q_conditional),
                            reverse=True)[:TOP_TARGETS]
            if t.q_conditional > 0
        ],
        # El CATALOGO entero, para que la web pueda buscar en el y dibujar la
        # cupula de cualquier objeto sin recalcular nada. La traza va a un punto
        # de cada tres -- suficiente para trazar el arco y una tercera parte del
        # peso -- y solo mientras el objeto esta por encima del horizonte.
        # Solo en la primera noche: el buscador y la cupula son de HOY, y las
        # trazas de la segunda duplicaban el peso del fichero (38 kB de 44) sin
        # que nadie las mire.
        "objetos": ([_objeto(t, times) for t in sorted(
            targets, key=lambda t: (-t.q_expected, t.target.name))]
            if with_catalogue else []),
        # Lo que un astrofotografo te diria al oido, con los numeros dentro.
        "consejos": evaluate_advice(_condiciones(
            night_report, turbulence, transparency, darkest, times, site)),
    }


def _objeto(resultado, times) -> dict:
    """Un objeto del catalogo con lo que hace falta para buscarlo y pintarlo."""
    traza = [
        {"t": t.isoformat(timespec="minutes"),
         "alt": round(paso["alt"], 1), "az": round(paso["az"], 1)}
        for i, (t, paso) in enumerate(zip(times, resultado.trace))
        if i % 3 == 0 and not paso.get("too_low")
    ]
    return {
        "nombre": resultado.target.name,
        "tipo": resultado.target.kind,
        "clase": resultado.target.difficulty,
        "ra": resultado.target.ra_deg,
        "dec": resultado.target.dec_deg,
        "nota": resultado.target.note,
        # Otros nombres por los que alguien lo buscaria. La pagina en ingles
        # buscaba "Veil" y no encontraba el Velo.
        "alias": list(resultado.target.aliases),
        "horas_si_despeja": round(resultado.q_conditional, 1),
        "horas_esperadas": round(resultado.q_expected, 1),
        "horas_sobre_minimo": round(resultado.hours_above_minimum, 1),
        "altura_maxima": round(resultado.max_altitude, 1),
        "separacion_luna": round(resultado.min_moon_separation, 1),
        "mejor_hora": (resultado.best_hour.isoformat(timespec="minutes")
                       if resultado.best_hour else None),
        "veredicto": resultado.verdict,
        "porque": resultado.reason,
        "traza": traza,
    }


def _condiciones(night_report, turbulence, transparency, darkest, times, site):
    """Traduce la noche al vocabulario de la base de consejos."""
    lunas = moon_positions(times, site)
    ahora = None
    try:
        ahora = site_now.fetch(site=site)
    except Exception:
        pass                       # la pagina de AstroCamp se cae a ratos
    viento = verificacion_viento()
    return Conditions(
        moon_illumination=night_report.moon_illumination,
        moon_altitude_max=max((m.altitude for m in lunas), default=None),
        sky_mag=darkest,
        seeing_tercile=turbulence.tercile,
        seeing_range=turbulence.range_arcsec,
        aod=transparency.aod_site,
        k_total=transparency.k_total,
        dust_alert=transparency.dust_alert,
        transparency_verdict=transparency.verdict,
        open_probability=night_report.opening_probability,
        dark_hours=night_report.dark_hours,
        humidity=getattr(ahora, "rh_out", None),
        dew_margin=getattr(ahora, "dew_margin", None),
        wind=getattr(ahora, "wind_ms", None),
        wind_uncalibrated=not viento.get("applied", False),
    )


def verificacion_viento() -> dict:
    """Envoltorio para poder sustituirlo en los tests sin tocar la BD."""
    try:
        return wind_correction()
    except Exception:
        return {"factor": 1.0, "applied": False}


def _sensores(site: Site) -> dict | None:
    """El sitio ahora mismo. None si la pagina de AstroCamp no responde."""
    try:
        ahora = site_now.fetch(site=site)
    except Exception:
        return None
    return {
        "leido_utc": ahora.retrieved_at.isoformat(timespec="seconds"),
        "medido_utc": ahora.t_utc.isoformat(timespec="seconds") if ahora.t_utc else None,
        "antiguedad_min": (round(ahora.age_minutes, 1)
                           if ahora.age_minutes is not None else None),
        "fresco": ahora.meteo_fresh,
        "temperatura": ahora.t_out,
        "humedad": ahora.rh_out,
        "punto_rocio": ahora.dew_point,
        "margen_rocio": (round(ahora.dew_margin, 1)
                         if ahora.dew_margin is not None else None),
        "riesgo_rocio": ahora.dew_risk,
        "viento_ms": ahora.wind_ms,
        "viento_dir": ahora.wind_dir,
        "presion": ahora.pressure,
        # El CloudWatcher es verdad de nubes INDEPENDIENTE del modelo, y lleva
        # vivo desde el 27-08-2026 tras cuatro anios muerto.
        "cielo_ir": ahora.sky_temp,
        "cielo_indice": ahora.sky_index,
        "cielo_fresco": ahora.sky_fresh,
        "cielo_despejado": ahora.sky_clear,
        "techos_abiertos": ahora.roofs_open,
        "techos_total": ahora.roofs_total,
        "estado_seguridad": ahora.safety_status,
        "incompleto": ahora.missing_note,
    }


def _cupula(site: Site) -> dict | None:
    """La cupula de esta noche. Sin nubes si la rejilla no contesta."""
    try:
        from .dome import build as build_dome
        cupula = build_dome(site=site)
    except Exception:
        return None
    return {
        "noche": cupula["night"],
        "luna": cupula["moon"],
        "marcos": [{"t": f["t"], "etiqueta": f["label"],
                    "sol_alt": f["sun_alt"], "luna_alt": f["moon_alt"],
                    "luna_az": f["moon_az"], "cielo_mag": f["sky_mag"],
                    "nubes": f["clouds"]}
                   for f in cupula["frames"]],
    }


def build(site: Site = NERPIO, *, nights: int = 2, root=None) -> dict:
    calibrator = Calibrator.load()
    now = dt.datetime.now(dt.timezone.utc)

    salidas = []
    for offset in range(nights):
        noche = _night(now + dt.timedelta(days=offset), site, root,
                       with_catalogue=(offset == 0))
        if noche:
            salidas.append(noche)

    avisos = []
    if calibrator is None:
        avisos.append("La probabilidad NO está calibrada: es la salida cruda "
                      "del modelo, que aquí es peor que la climatología.")
    else:
        # El aviso del desajuste de modelo viaja tal cual: si la curva se esta
        # aplicando a otro proveedor, quien lea el JSON tiene que enterarse.
        for linea in report(now, site, root).calibration_note.splitlines():
            if "AVISO" in linea:
                avisos.append(linea.strip())
    avisos.append("El seeing es experimental y sin calibrar con FWHM local: se "
                  "publica en terciles, nunca en arcosegundos sueltos.")
    # La racha alimenta una puerta de SEGURIDAD y no tiene verdad terreno en
    # ninguna fuente. Lo que si se mide es el viento medio, y sale corto: eso
    # es exactamente el sentido en el que una puerta no puede fallar en
    # silencio, asi que se publica.
    viento = wind_correction()
    if not viento.get("applied") and viento.get("bias") is not None:
        avisos.append(
            f"La puerta de viento NO está calibrada y el modelo se queda CORTO: "
            f"sobre {viento['n']} horas medidas contra la estacion del sitio "
            f"prevé el {viento['ratio']*100:.0f} % del viento medio real "
            f"({viento['bias']:+.1f} m/s). La racha, que es la que decide, no "
            f"tiene verdad terreno en ninguna fuente. Trata el veredicto de "
            f"abrir como optimista en viento.")
    if salidas and salidas[0]["cielo"]["seeing"]["origen"] == "supuesto":
        avisos.append("No hay previsión de viento en altura para esta noche: "
                      "el seeing sale de un valor supuesto.")
    if salidas and salidas[0]["cielo"]["transparencia"]["origen"] == "supuesto":
        avisos.append("No hay previsión de aerosol para esta noche: la "
                      "transparencia sale de un valor supuesto.")

    return {
        "generado_utc": now.isoformat(timespec="seconds"),
        "sitio": {
            "nombre": site.name,
            "mpc": "I79",
            "lat": site.latitude,
            "lon": site.longitude,
            "altitud_m": site.site_elevation,
        },
        "noches": salidas,
        # Lo que los sensores del sitio dicen AHORA. No es prevision y no se
        # mezcla con ella: va con su antiguedad delante para que la pagina
        # pueda apagar un numero viejo en vez de ensenarlo como si fuera de
        # este minuto.
        "sensores": _sensores(site),
        # La cupula, con la Luna y las nubes proyectadas: lo que hace falta
        # para dibujar CUALQUIER objeto del catalogo sin recalcular nada.
        "cupula": _cupula(site),
        "confianza": {
            "calibrado": calibrator is not None,
            "pares_de_entrenamiento": calibrator.trained_on if calibrator else 0,
            "bss_sobre_climatologia": (
                round(1 - calibrator.brier / calibrator.brier_climatology, 3)
                if calibrator else None),
            "modelo_calibrado": calibrator.trained_on_model if calibrator else None,
            "curva_ajustada_el": calibrator.fitted_at if calibrator else None,
            "avisos": avisos,
        },
    }


def write(path: Path | None = None, site: Site = NERPIO, *, nights: int = 2,
          root=None) -> Path:
    path = path or DEFAULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build(site, nights=nights, root=root)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                    encoding="utf-8")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nights", type=int, default=2)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    destino = write(args.out, nights=args.nights)
    print(f"[webexport] {destino} ({destino.stat().st_size} bytes)")
