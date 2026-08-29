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
        # Cuando se va el Sol de verdad y cuando vuelve. La duracion sola no
        # sirve para planificar: lo que se planifica es a que hora empieza.
        "crepusculo": {
            "astronomico_desde": (night.dusk_astronomical.isoformat(timespec="minutes")
                                  if night.dusk_astronomical else None),
            "astronomico_hasta": (night.dawn_astronomical.isoformat(timespec="minutes")
                                  if night.dawn_astronomical else None),
            "ocaso": (night.sunset.isoformat(timespec="minutes")
                      if night.sunset else None),
            "orto": (night.sunrise.isoformat(timespec="minutes")
                     if night.sunrise else None),
        },
        "luna_iluminacion": round(night_report.moon_illumination, 3),
        # La respuesta a la pregunta 1, que es la que manda.
        "probabilidad_de_abrir": round(night_report.opening_probability, 3),
        # OJO a la diferencia, que confunde y con razon: esto es la suma de la
        # probabilidad utilizable sobre TODA la ventana de producto (del ocaso
        # menos una hora al orto mas una), asi que incluye el crepusculo. Por
        # eso puede pasar de la oscuridad astronomica: 11,4 contra 7,8 la misma
        # noche. Lo que se puede hacer en crepusculo es planetaria y objetos
        # brillantes, no cielo profundo, y por eso va tambien la cuenta
        # restringida a la oscuridad de verdad.
        "horas_utilizables_esperadas": round(night_report.usable_hours, 1),
        "horas_utilizables_oscuras": _horas_oscuras(night_report, night),
        # Que significa EXACTAMENTE la probabilidad de arriba, dentro del JSON:
        # sin esto, "probabilidad de que la noche sea utilizable" se lee como
        # "probabilidad de que abran el techo", y no es eso.
        "probabilidad_definicion": (
            "Probabilidad de que haya CONDICIONES para abrir, no de que abran: "
            "el techo lo abren personas y un techo cerrado no significa mal "
            "tiempo. Por hora es la probabilidad de cielo despejado multiplicada "
            "por 0,85 por cada riesgo en vigilancia, y cero si una puerta de "
            "seguridad la bloquea. Para la noche se toma la MEJOR hora, siempre "
            "que se esperen al menos 4 h utilizables en total; si no, se penaliza "
            "en proporcion."),
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
        "objetos": ([_objeto(t) for t in sorted(
            targets, key=lambda t: (-t.q_expected, t.target.name))]
            if with_catalogue else []),
        # Lo que un astrofotografo te diria al oido, con los numeros dentro.
        "consejos": evaluate_advice(_condiciones(
            night_report, turbulence, transparency, darkest, times, site)),
    }


LIMITA_CODIGO = {"cielo": "c", "luna": "l", "fondo": "f", "altura": "a",
                 "ninguno": "n"}


def _horas_oscuras(night_report, night) -> float | None:
    """Horas utilizables esperadas DENTRO de la oscuridad astronomica.

    La cuenta grande incluye el crepusculo porque la ventana de producto va del
    ocaso menos una hora al orto mas una, y en crepusculo se puede hacer
    planetaria y objetos brillantes. Pero para cielo profundo lo que cuenta es
    esta, y publicar solo la grande hacia parecer que la noche da mas de lo que
    da.
    """
    if not (night.dusk_astronomical and night.dawn_astronomical):
        return 0.0
    total = sum(h.usable_probability for h in night_report.hours
                if night.dusk_astronomical <= h.t_utc <= night.dawn_astronomical)
    return round(total, 1)


def _objeto(resultado) -> dict:
    """Un objeto del catalogo, con lo que hace falta para buscarlo y pintarlo.

    La traza sale de `resultado.trace`, que ya trae altura y azimut de CADA
    muestra -- tambien de las que estan bajo el minimo -- y esta sobre la misma
    rejilla adaptativa que usa `nightview` para los marcos de la cupula. Asi el
    punto `i` de la traza ES el marco `i` del deslizador, sin remuestrear nada
    y sin una segunda pasada de efemerides.

    Es lo que estaba roto hasta hoy: la traza venia de otra rejilla y recortada
    a los tramos sobre el horizonte, el marcador se buscaba "por tiempo mas
    cercano" y se quedaba clavado media noche.
    """
    alt, az, rend, limita = [], [], [], []
    for paso in resultado.trace:
        alt.append(paso.get("alt"))
        az.append(paso.get("az"))
        rend.append(round(float(paso.get("yield") or 0.0), 3))
        limita.append("-" if paso.get("too_low")
                      else LIMITA_CODIGO.get(paso.get("limiting"), "n"))

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
        # Arrays paralelos y no una lista de objetos: son 44 objetos por 59
        # marcos, y con claves repetidas el fichero se iba al triple.
        "alt": alt, "az": az, "rend": rend, "limita": "".join(limita),
        # Los tres instantes que se marcan en el perfil. El paso por el
        # meridiano se detecta por el azimut cruzando el sur (o el norte para
        # circumpolares altas), que es la definicion, y no se confunde con el
        # maximo cuando el objeto culmina fuera de la ventana de la noche.
        **_hitos(alt, az),
    }


def _hitos(alt: list, az: list) -> dict:
    """Maximo, minimo y paso por el meridiano, en indices de marco."""
    validos = [i for i, a in enumerate(alt) if a is not None]
    if not validos:
        return {"i_max": None, "i_min": None, "i_meridiano": None}
    i_max = max(validos, key=lambda i: alt[i])
    # El minimo UTIL es el mas bajo estando por encima del horizonte: marcar
    # -4 grados no ayuda a planificar, solo dice que el objeto se ha puesto.
    arriba = [i for i in validos if alt[i] > 0]
    i_min = min(arriba, key=lambda i: alt[i]) if arriba else None
    meridiano = None
    for j in range(1, len(validos)):
        a, b = validos[j - 1], validos[j]
        if alt[a] is None or alt[b] is None:
            continue
        # Distancia angular al sur, con el signo: cambia de lado al cruzar.
        antes = (az[a] - 180.0 + 180.0) % 360.0 - 180.0
        ahora = (az[b] - 180.0 + 180.0) % 360.0 - 180.0
        if antes <= 0 < ahora or ahora <= 0 < antes:
            if abs(antes) < 90 and abs(ahora) < 90:      # cruce por el SUR
                meridiano = b if abs(ahora) < abs(antes) else a
                break
    if meridiano is None and alt[i_max] is not None:
        # Circumpolar o culminacion al norte: el maximo ES el paso.
        meridiano = i_max
    return {"i_max": i_max, "i_min": i_min, "i_meridiano": meridiano}


def _condiciones(night_report, turbulence, transparency, darkest, times, site):
    """Traduce la noche al vocabulario de la base de consejos."""
    lunas = moon_positions(times, site)
    ahora = None
    try:
        ahora = site_now.fetch(site=site)
    except Exception:
        pass                       # la pagina de AstroCamp se cae a ratos
    altura_maxima = max((m.altitude for m in lunas), default=None)
    # Que planetas suben de verdad esta noche. Sin esto, la fila "Planetas y
    # Luna" solo podia hablar de condiciones, y la primera pregunta no es como
    # esta el cielo sino si hay algo a lo que apuntar.
    arriba = []
    try:
        from .skyobjects import planet_positions
        from .targets import Target
        for planeta in planet_positions(times[len(times) // 2], site):
            alturas, _, _ = Target(planeta["name"], planeta["ra"], planeta["dec"],
                                   "planet", 1.0, "normal").positions(times, site)
            pico = float(alturas.max())
            if pico >= 30.0:
                arriba.append((planeta["name"], round(pico, 1)))
    except Exception:
        arriba = []
    arriba.sort(key=lambda p: -p[1])
    return Conditions(
        moon_illumination=night_report.moon_illumination,
        moon_altitude_max=altura_maxima,
        # Una Luna llena que no sale no molesta a nadie, y el semaforo lo tiene
        # que saber: la fase sola manda a rojo noches que estan limpias.
        moon_up=bool(altura_maxima is not None and altura_maxima > 0),
        sky_mag=darkest,
        seeing_tercile=turbulence.tercile,
        seeing_range=turbulence.range_arcsec,
        aod=transparency.aod_site,
        k_total=transparency.k_total,
        dust_alert=transparency.dust_alert,
        open_probability=night_report.opening_probability,
        dark_hours=night_report.dark_hours,
        humidity=getattr(ahora, "rh_out", None),
        dew_margin=getattr(ahora, "dew_margin", None),
        wind=getattr(ahora, "wind_ms", None),
        planets_up=tuple(arriba),
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


# Lo que la cupula necesita y lo que NO. `nightview.build()` da 623 kB: de
# ellos, `lanes` son 536 -- las trazas de solo diez objetos, que aqui ya viajan
# en `objetos` para los 44 y alineadas con los marcos -- y `galaxy` son 58 de la
# version antigua de la Via Lactea, la de 2.600 puntos a mano que el 28-08 se
# sustituyo por la panoramica. Ninguno de los dos se publica.
CUPULA_FUERA = ("lanes", "galaxy", "roof", "advice")


def _cupula(site: Site) -> dict | None:
    """La noche entera, lista para pintar. None si algo no responde.

    Sale de `nightview`, que es exactamente lo que come el mockup de la cupula:
    asi la web pinta la MISMA noche que la vista interna, con la misma fisica y
    los mismos numeros, en vez de una version parecida que acabaria
    discrepando. Lo que se pinta por pixel -- el fondo natural, la Via Lactea,
    la dispersion lunar, el crepusculo, la nube iluminada -- se calcula en el
    navegador a partir de estos datos, y esa es la unica fisica que corre ahi:
    es un RENDER, no una segunda opinion sobre la noche.
    """
    try:
        from .nightview import build as build_nightview
        cupula = build_nightview(site=site)
    except Exception:
        return None
    return {clave: valor for clave, valor in cupula.items()
            if clave not in CUPULA_FUERA}


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
