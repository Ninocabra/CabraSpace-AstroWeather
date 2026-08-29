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

from .calibration import Calibrator
from .ephem import moon_illumination, night_for
from .extinction import evaluate as evaluate_transparency
from .queries import DATA_ROOT, night_inputs
from .seeing import integrate
from .site import NERPIO, Site
from .skymodel import predict
from .targets import CATALOGUE, evaluate_night
from .tonight import report

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


def _night(day: dt.datetime, site: Site, root=None) -> dict | None:
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
    }


def build(site: Site = NERPIO, *, nights: int = 2, root=None) -> dict:
    calibrator = Calibrator.load()
    now = dt.datetime.now(dt.timezone.utc)

    salidas = []
    for offset in range(nights):
        noche = _night(now + dt.timedelta(days=offset), site, root)
        if noche:
            salidas.append(noche)

    avisos = []
    if calibrator is None:
        avisos.append("La probabilidad NO esta calibrada: es la salida cruda "
                      "del modelo, que aqui es peor que la climatologia.")
    else:
        # El aviso del desajuste de modelo viaja tal cual: si la curva se esta
        # aplicando a otro proveedor, quien lea el JSON tiene que enterarse.
        for linea in report(now, site, root).calibration_note.splitlines():
            if "AVISO" in linea:
                avisos.append(linea.strip())
    avisos.append("El seeing es experimental y sin calibrar con FWHM local: se "
                  "publica en terciles, nunca en arcosegundos sueltos.")
    if salidas and salidas[0]["cielo"]["seeing"]["origen"] == "supuesto":
        avisos.append("No hay prevision de viento en altura para esta noche: "
                      "el seeing sale de un valor supuesto.")
    if salidas and salidas[0]["cielo"]["transparencia"]["origen"] == "supuesto":
        avisos.append("No hay prevision de aerosol para esta noche: la "
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
