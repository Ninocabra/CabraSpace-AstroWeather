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
import statistics
from pathlib import Path

from .advice import Conditions, evaluate as evaluate_advice
from .calibration import Calibrator
from .ephem import moon_illumination, moon_positions, night_for
from .extinction import evaluate as evaluate_transparency
from .queries import DATA_ROOT, night_inputs, night_series
from .seeing import integrate
from . import site_now
from .site import NERPIO, Site
from .skymodel import predict
from .catalog import candidatos
from .targets import CATALOGUE, MIN_ALTITUDE, evaluate_night
from .tonight import report
from .verification import wind_correction

# Cuantos objetos se publican. La lista curada tiene diez; sacar los tres
# mejores es una recomendacion, sacar los diez es un listado.
TOP_TARGETS = 3
DEFAULT_PATH = DATA_ROOT / "web" / "noche.json"


def _hour(hour, serie: dict | None = None, site: Site | None = None) -> dict:
    """Una hora de la noche. Con seeing y transparencia SUYOS si se tienen.

    Hasta el 31-08-2026 el seeing y la transparencia solo salian por noche:
    un tercil y un AOD, la mediana de la ventana entera. Y las dos cosas se
    mueven dentro de la misma noche -- medido esta noche sobre el archivo, el
    AOD va de 0,12 a las 20:00 a 0,27 al amanecer, MAS DEL DOBLE, y la mediana
    publicada (0,19) no describe ninguno de los dos extremos. Con un solo
    numero, "noche transparente" tapaba una noche que se enturbia segun avanza.

    Las claves nuevas son OPCIONALES a proposito: si no hay serie -- archivo
    ausente, filas que faltan -- la hora sale como salia. Esto aniade
    resolucion, no la sustituye.
    """
    salida = {
        "t_utc": hour.t_utc.isoformat(),
        "nubes_previstas": hour.cloud_total,
        "p_despejado": (round(hour.clear_probability, 3)
                        if hour.clear_probability is not None else None),
        "p_utilizable": round(hour.usable_probability, 3),
        "limita": hour.limiting,
        "seguro_para_abrir": hour.safety.safe_to_open,
        "porque": hour.safety.summary,
    }
    punto = (serie or {}).get(
        hour.t_utc.replace(minute=0, second=0, microsecond=0))
    if not punto or site is None:
        return salida

    jet = punto.get("jet_speed")
    if jet:
        turbulencia = integrate(jet)
        salida["seeing_arcsec"] = round(turbulencia.seeing_arcsec, 2)
        salida["seeing_tercil"] = turbulencia.tercile
    aod_total = punto.get("aod_total")
    if aod_total is not None:
        transparencia = evaluate_transparency(
            aod_total=aod_total, aod_dust=punto.get("aod_dust"),
            elevation_m=site.site_elevation, delta_h_m=site.delta_h)
        salida["aod"] = round(transparencia.aod_site, 3)
        salida["k_total"] = round(transparencia.k_total, 3)
    return salida


# El corte del veredicto binario. Al 50 % por la razon de siempre: es el umbral
# que minimiza los fallos cuando los dos errores cuestan lo mismo, y ninguna
# otra cifra se podria defender sin una matriz de costes que nadie ha medido.
UMBRAL_ABRIR = 0.5


def _veredicto_abrir(probabilidad: float) -> dict:
    """Abierto o cerrado, con la regla dentro.

    La regla viaja con el veredicto porque la palabra sola es mas rotunda que
    el numero del que sale: quien lee "ABIERTO" tiene derecho a saber que eso
    significa "al menos la mitad de las veces", y que sigue siendo una
    prevision de CONDICIONES, no de lo que haran las personas del techo.
    """
    abierto = probabilidad >= UMBRAL_ABRIR
    return {
        "abierto": bool(abierto),
        "umbral": UMBRAL_ABRIR,
        # Con tildes: esto se lee en la pagina, no en el codigo. Y sin repetir
        # lo que ya dice `probabilidad_definicion`, que viaja al lado en el
        # mismo globo.
        "regla": (
            f"Se dice Abierto cuando la probabilidad que va debajo llega al "
            f"{UMBRAL_ABRIR:.0%}, y Cerrado por debajo de esa cifra. Es un "
            f"corte sobre ESE mismo número, no una segunda opinión, así que "
            f"la palabra y el porcentaje no pueden discrepar."),
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
    # La MISMA fuente sin colapsar, para que cada hora publique su seeing y su
    # transparencia. La ventana se toma de las horas del informe y no de la
    # rejilla, porque el informe llega mas lejos por los dos lados.
    horas_informe = [h.t_utc for h in night_report.hours] or list(times)
    serie_horaria = night_series(min(horas_informe), max(horas_informe),
                                 site.site_id, root)
    turbulence = integrate(forecast["jet_speed"])
    transparency = evaluate_transparency(
        aod_total=forecast["aod_total"], aod_dust=forecast["aod_dust"],
        elevation_m=site.site_elevation, delta_h_m=site.delta_h)

    # El informe viene con marcas EN PUNTO (las del proveedor) y la rejilla del
    # recomendador arranca en el ocaso, que no cae en punto: buscar por igualdad
    # exacta fallaba siempre y dejaba E[Q] en cero. Se empareja por la hora.
    usable = {h.t_utc.replace(minute=0, second=0, microsecond=0):
              h.usable_probability for h in night_report.hours}
    # Con catalogo grande solo en la primera noche: la segunda no lleva trazas
    # ni ranking, asi que no hace falta puntuar mil objetos para ella.
    universo = _suben_esta_noche(times, site) if with_catalogue else list(CATALOGUE)
    targets = evaluate_night(
        universo, times,
        usable_probability=[
            usable.get(t.replace(minute=0, second=0, microsecond=0), 0.0)
            for t in times],
        turbulence=turbulence, aod_total=forecast["aod_total"],
        aod_dust=forecast["aod_dust"], serie=serie_horaria, site=site)

    calibracion = _calibracion_fotometro()
    ordenados = sorted(targets, key=lambda t: (-t.q_expected, -t.q_conditional,
                                              t.target.name))
    window = night_report.best_window
    # EL CIELO DE LA NOCHE, EN DOS NUMEROS QUE NO SON EL MISMO, y hasta hoy se
    # confundian en uno solo -- y encima con el signo cambiado.
    #
    # `mag_cenit_mas_oscuro` salia de un `min()` sobre magnitudes, y en
    # magnitudes por segundo de arco al cuadrado MAS GRANDE ES MAS OSCURO: 21,9
    # es cielo negro y 17 es cielo con Luna alta. O sea que el campo llamado "lo
    # mas oscuro" publicaba el instante MAS BRILLANTE de la noche. La noche del
    # 30-08-2026 el panel decia 17,05 cuando el cielo llegaba a 20,19, y el
    # fotometro habia medido 20,30 la noche anterior con MAS Luna: el numero
    # parecia una contradiccion con la medida y era un signo.
    #
    # `nightview.py` calcula esto mismo con `max()` y `higher_is_better=True`
    # desde siempre. Dos ficheros, el mismo concepto, signos opuestos, y el que
    # se publicaba era el malo.
    #
    # Y son dos numeros porque uno solo no describe una noche con Luna: el mejor
    # instante (20,19, justo al cerrar el crepusculo con la Luna aun baja) y el
    # tipico (17,63, con la Luna arriba) se llevan tres magnitudes, que es un
    # factor 16 de fondo. Publicar solo el mejor invita a creer que el cielo
    # esta asi toda la noche.
    skies = predict(times, site) if night.window_start else []
    de_noche = [s.predicted_mag for s in skies if s.is_night]
    darkest = max(de_noche) if de_noche else None
    typical = statistics.median(de_noche) if de_noche else None

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
        # Y la misma respuesta MOJADA. Un 47 % no le dice a nadie si conviene
        # subir al observatorio: obliga a poner el umbral en la cabeza de quien
        # lee, y cada uno lo pone donde quiere. Asi que el motor lo pone, y lo
        # pone donde toca -- al 50 %, que es el corte que minimiza los fallos
        # cuando equivocarse por arriba cuesta lo mismo que por abajo.
        # NO es una segunda opinion: es un corte sobre el numero de arriba, asi
        # que no pueden discrepar nunca.
        "veredicto_abrir": _veredicto_abrir(night_report.opening_probability),
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
        "horas": [_hour(h, serie_horaria, site) for h in night_report.hours],
        # Y la pregunta 2.
        "cielo": {
            "mag_cenit_mas_oscuro": round(darkest, 2) if darkest else None,
            # Lo mismo, corregido contra el fotometro. NO es un retoque
            # cosmetico: el modelo se mide 0,27 mag pesimista en esta epoca, y
            # esa correccion sale de comparar prediccion y medida en el MISMO
            # sitio del que salen los datos. Se publican los dos numeros porque
            # el crudo es lo que usa el recomendador y el corregido es lo que
            # mejor describe el cielo.
            "mag_cenit_corregido": (round(darkest + calibracion["sesgo"] * -1, 2)
                                    if darkest and calibracion.get("aplicable")
                                    else None),
            # Lo TIPICO de la noche, que en noche con Luna es lo que de verdad
            # se va a encontrar: la mediana sobre la oscuridad astronomica.
            "mag_cenit_tipico": round(typical, 2) if typical else None,
            "mag_cenit_tipico_corregido": (round(typical + calibracion["sesgo"] * -1, 2)
                                           if typical and calibracion.get("aplicable")
                                           else None),
            "calibracion_fotometro": calibracion,
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
        # Los mejores, con su traza completa: son los que la cupula dibuja y los
        # carriles detallan.
        "objetos": ([_objeto(t) for t in ordenados[:CON_TRAZA]]
                    if with_catalogue else []),
        # EL CATALOGO ENTERO DE LA NOCHE, y "entero" quiere decir incluyendo lo
        # que puntua cero. Un objeto con cero horas no es un objeto sin
        # respuesta: es un objeto con una respuesta mala Y SU MOTIVO, que es
        # justo lo que alguien quiere leer cuando lo busca. Publicando solo los
        # que pasaban de cero, el buscador de la web no encontraba M31 con Luna
        # llena -- el motor SI lo habia puntuado ("el cielo no baja de 20.0
        # mag/arcsec2: esta clase no cuaja") -- lo mandaba a SIMBAD y lo pintaba
        # como objeto externo diciendo "el motor NO lo ha puntuado". Mentia.
        # Sin trazas: nombre, coordenadas, horas y veredicto es todo lo que hace
        # falta para buscarlo, situarlo en la cupula y decir como le va.
        "catalogo": ([{"nombre": t.target.name, "tipo": t.target.kind,
                       "clase": t.target.difficulty,
                       # Con coordenadas: asi el buscador puede coger cualquiera
                       # y situarlo en la cupula aunque no lleve traza, con la
                       # misma geometria que los objetos de fuera.
                       "ra": t.target.ra_deg, "dec": t.target.dec_deg,
                       # Seis y no cuatro: el orden de `catalog._alias` pone
                       # primero la designacion cruda y las formas Messier, asi
                       # que cortando a cuatro se quedaban fuera "NGC 224" y
                       # "NGC224" justo en los objetos que la gente busca por
                       # su numero.
                       "alias": list(t.target.aliases)[:6],
                       "horas_si_despeja": round(t.q_conditional, 1),
                       "horas_esperadas": round(t.q_expected, 1),
                       "altura_maxima": round(t.max_altitude, 0),
                       "separacion_luna": round(t.min_moon_separation, 0),
                       "veredicto": t.verdict,
                       # El porque viaja con cada uno por la misma regla que
                       # gobierna el resto del fichero: un numero sin su motivo
                       # no se publica, y el motivo de un cero es el mas util.
                       "porque": t.reason}
                      for t in ordenados]
                     if with_catalogue else []),
        "catalogo_evaluado": len(universo) if with_catalogue else 0,
        # Lo que un astrofotografo te diria al oido, con los numeros dentro.
        # Al semaforo va lo TIPICO y no un extremo: sus textos dicen "el fondo de
        # cielo se queda en X", y eso solo es cierto de un valor representativo.
        # Con el mejor instante recomendaria galaxias por veinte minutos de
        # cielo bueno; con el peor, las condenaria por veinte minutos de cielo
        # malo. (Verificado: sobre la noche del 30-08 los semaforos salen los
        # mismos con la mediana que con el minimo de antes, y distintos con el
        # maximo -- o sea que esto arregla el numero que se cita sin cambiar el
        # consejo.)
        "consejos": evaluate_advice(_condiciones(
            night_report, turbulence, transparency, typical, times, site)),
    }


LIMITA_CODIGO = {"cielo": "c", "luna": "l", "fondo": "f", "altura": "a",
                 "ninguno": "n"}


# Cuantos objetos se publican con su traza completa: los que la cupula puede
# dibujar hora a hora y los carriles detallar. El resto de lo evaluado va en
# `catalogo` sin traza -- la altura se recalcula en el navegador con el mismo
# tiempo sidereo, que es geometria -- y por eso ya no hay tope: el buscador
# tiene que poder encontrar cualquier cosa que el motor haya mirado esta noche.
CON_TRAZA = 40


def _suben_esta_noche(times, site) -> list:
    """Los candidatos que pasan del minimo de altura en algun momento.

    Prefiltro puramente GEOMETRICO, y por eso no puede cambiar ningun veredicto:
    lo que nunca sube del minimo puntua cero por definicion. Sirve para no
    pasarle 1.774 objetos al recomendador cuando 800 de ellos estan bajo el
    horizonte toda la noche.

    Y va vectorizado: una sola transformacion de astropy para todo el catalogo
    (1.774 x 59) tarda menos de un segundo, mientras que hacerlo objeto a objeto
    dentro del recomendador serian 35 ms cada uno.
    """
    import numpy as np
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time
    import astropy.units as u

    objetivos = list(candidatos())
    lugar = EarthLocation(lat=site.latitude * u.deg, lon=site.longitude * u.deg,
                          height=site.site_elevation * u.m)
    cuando = Time([t.astimezone(dt.timezone.utc) for t in times])
    coords = SkyCoord(ra=[t.ra_deg for t in objetivos] * u.deg,
                      dec=[t.dec_deg for t in objetivos] * u.deg)
    alturas = np.asarray(coords[:, None].transform_to(
        AltAz(obstime=cuando, location=lugar)).alt.deg)
    pico = alturas.max(axis=1)
    return [objetivo for objetivo, alto in zip(objetivos, pico) if alto >= MIN_ALTITUDE]


def _suelo_publicado() -> dict:
    """El suelo en uso y de donde sale, para que se pueda auditar."""
    try:
        import json
        from .archive import DATA_ROOT
        from .skybackground import FLOOR_FICHERO, NATURAL_FLOOR_MAG, epoch_floor
        ruta = DATA_ROOT / FLOOR_FICHERO
        datos = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
        return {"en_uso": epoch_floor(), "constante": NATURAL_FLOOR_MAG,
                "medido": bool(datos.get("medido")),
                "n": datos.get("n"), "ventana_dias": datos.get("ventana_dias"),
                "ajustado_el": datos.get("ajustado_el")}
    except Exception:
        return {}


def _calibracion_fotometro() -> dict:
    """Envoltorio para poder sustituirlo en los tests sin tocar la BD."""
    try:
        from .verification import photometer_bias
        return photometer_bias()
    except Exception:
        return {"aplicable": False, "porque": "no medible"}


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
    alt, az, rend, limita, sep = [], [], [], [], []
    for paso in resultado.trace:
        alt.append(paso.get("alt"))
        az.append(paso.get("az"))
        rend.append(round(float(paso.get("yield") or 0.0), 3))
        limita.append("-" if paso.get("too_low")
                      else LIMITA_CODIGO.get(paso.get("limiting"), "n"))
        # La separacion a la Luna YA la calculaba el recomendador para cada
        # muestra y se tiraba: solo sobrevivia el minimo de la noche. Y es la
        # que cambia -- la Luna se mueve y el objeto tambien -- asi que el
        # numero util es el de CADA momento, no el peor de todos.
        sep.append(paso.get("moon_sep"))

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
        "sep_luna": sep,
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


def _condiciones(night_report, turbulence, transparency, fondo_tipico, times, site):
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
        sky_mag=fondo_tipico,
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


# Columnas del archivo con las que se puede tapar un hueco de la descarga en
# vivo, y a que campo del panel corresponden. Solo lo que el scraper guarda
# cada 5 minutos: no se rellena nada que no se este midiendo de verdad.
# Rango fisicamente posible de cada lectura. Un sensor que devuelve cero no
# esta diciendo "cero": esta diciendo que no ha leido, y publicarlo tal cual es
# lo que hizo que el panel ensenara "viento 0 km/h" con la pagina a medias.
# SPEC 5 ya lo pedia -- "descartar por frescura y rango fisico" -- y hasta hoy
# solo se aplicaba al fotometro.
RANGOS = {
    "temperatura": (-25.0, 45.0),
    "humedad": (1.0, 100.0),
    "punto_rocio": (-40.0, 30.0),
    "viento_ms": (0.0, 60.0),
    "presion": (700.0, 950.0),
    "cielo_ir": (-60.0, 30.0),
    "cielo_indice": (-50.0, 100.0),
}


def _plausible(campo: str, valor) -> bool:
    """Si el valor cae dentro de lo que ese sensor puede medir de verdad."""
    if valor is None:
        return False
    minimo, maximo = RANGOS.get(campo, (float("-inf"), float("inf")))
    return minimo <= float(valor) <= maximo


DESDE_ARCHIVO = {
    "temperatura": "t2m", "humedad": "rh", "viento_ms": "wind",
    "presion": "station_pressure", "punto_rocio": "dew_point",
    "cielo_ir": "t_sky_ir", "cielo_indice": "sky_index",
}


# Cuanto hacia atras se rescata una lectura cuando la fuente no responde.
#
# ERAN SEIS HORAS Y SE QUEDABAN CORTAS EN EL CASO QUE IMPORTA. El 31-08-2026 la
# pagina de AstroCamp llevaba caida desde las 10:22 UTC; a las 18:02 el rescate
# no alcanzaba, y el panel publico solo temperatura y humedad -- que venian de
# AEMET, otra fuente que sigue viva y que no da viento ni presion ni nubes. O
# sea que el sensor se quedaba en blanco justo cuando la caida se hacia larga,
# que es justo cuando hace falta saber cual fue la ultima lectura.
#
# Siete dias, y no "sin limite", porque un dato de hace un mes ya no describe
# nada aunque se etiquete. Lo que hace honesto ensenar una lectura vieja no es
# el plazo: es que viaje CUANDO se midio y que la pagina lo grite.
RESCATE_HORAS = 24 * 7


def _ultimo_del_archivo(columnas: list[str], root=None) -> dict:
    """El valor mas reciente de cada columna, con su hora. Vacio si no hay."""
    if not columnas:
        return {}
    try:
        from .queries import connect
        conexion = connect(root)
        salida = {}
        for columna in columnas:
            fila = conexion.execute(f"""
                SELECT {columna}, t_utc FROM measurements
                WHERE {columna} IS NOT NULL
                  AND t_utc > now() - INTERVAL {RESCATE_HORAS} HOUR
                ORDER BY t_utc DESC LIMIT 1
            """).fetchone()
            if fila:
                salida[columna] = (fila[0], fila[1])
        return salida
    except Exception:
        return {}


def _sensores(site: Site, root=None) -> dict | None:
    """El sitio ahora mismo. None si la pagina de AstroCamp no responde.

    La pagina VUELVE INCOMPLETA a ratos, y eso no es una hipotesis: el
    29-08-2026 se publico un panel con dos casillas y `incompleto` diciendo que
    faltaban "estaciones meteo, techos y registro de seguridad" -- mientras el
    techo estaba abierto de verdad, 18 de 18. Publicar el hueco tal cual hacia
    que la pagina pintase AUSENCIA DE DATO como si fuera dato: "0/18 techos" no
    es "cero techos abiertos", es "no lo se", y se leen igual.

    Asi que lo que falta se completa desde NUESTRO archivo, que el scraper
    llena cada 5 minutos, y cada valor dice de donde sale y de cuando es. Lo
    que no se pueda completar se queda a None y la pagina tiene que callarse,
    no poner un cero.
    """
    try:
        ahora = site_now.fetch(site=site)
    except Exception:
        ahora = None

    panel = {
        "leido_utc": (ahora.retrieved_at.isoformat(timespec="seconds")
                      if ahora else None),
        "medido_utc": (ahora.t_utc.isoformat(timespec="seconds")
                       if ahora and ahora.t_utc else None),
        "antiguedad_min": (round(ahora.age_minutes, 1)
                           if ahora and ahora.age_minutes is not None else None),
        "fresco": bool(ahora and ahora.meteo_fresh),
        "temperatura": getattr(ahora, "t_out", None),
        "humedad": getattr(ahora, "rh_out", None),
        "punto_rocio": getattr(ahora, "dew_point", None),
        "margen_rocio": (round(ahora.dew_margin, 1)
                         if ahora and ahora.dew_margin is not None else None),
        "riesgo_rocio": getattr(ahora, "dew_risk", "desconocido"),
        "viento_ms": getattr(ahora, "wind_ms", None),
        "viento_dir": getattr(ahora, "wind_dir", None),
        "presion": getattr(ahora, "pressure", None),
        "cielo_ir": getattr(ahora, "sky_temp", None),
        "cielo_indice": getattr(ahora, "sky_index", None),
        "cielo_fresco": bool(ahora and ahora.sky_fresh),
        "cielo_despejado": getattr(ahora, "sky_clear", None),
        # 0/0 NO es "ningun techo abierto": es que no vino el bloque. Se
        # publica None para que la pagina no pueda confundirlos.
        "techos_abiertos": (ahora.roofs_open if ahora and ahora.roofs_total else None),
        "techos_total": (ahora.roofs_total if ahora and ahora.roofs_total else None),
        "estado_seguridad": getattr(ahora, "safety_status", None),
        "incompleto": getattr(ahora, "missing_note", None) if ahora else "sin respuesta",
    }

    # Se sustituye lo que falta Y lo que no puede ser: un cero de viento con la
    # pagina a medias no es calma, es una lectura que no llego.
    faltan = [campo for campo in DESDE_ARCHIVO
              if not _plausible(campo, panel.get(campo))]
    for campo in faltan:
        panel[campo] = None
    archivo = _ultimo_del_archivo([DESDE_ARCHIVO[c] for c in faltan], root)
    completados = {}
    for campo in faltan:
        valor = archivo.get(DESDE_ARCHIVO[campo])
        if valor is None:
            continue
        panel[campo] = valor[0]
        completados[campo] = valor[1].isoformat(timespec="seconds")
    # EL MARGEN DE ROCIO ES UNA RESTA, Y UNA RESTA EXIGE LA MISMA HORA. Con
    # AstroCamp caida el 31-08-2026 la temperatura se rescataba de AEMET (16:00
    # UTC, viva) y el punto de rocio de AstroCamp (10:21, muerta): la resta daba
    # 25,8 grados de margen mezclando dos instantes separados casi seis horas.
    # Un numero perfectamente plausible y sin ningun significado, en un campo
    # que avisa de condensacion.
    #
    # Se deriva solo si las dos partes son del MISMO momento. Si no, se calla:
    # lo que falta aqui no es un dato, es su simultaneidad, y eso no se rescata.
    def _cuando(campo):
        return completados.get(campo) or panel.get("medido_utc")

    if (panel["temperatura"] is not None and panel["punto_rocio"] is not None
            and panel["margen_rocio"] is None
            and _cuando("temperatura") == _cuando("punto_rocio")):
        panel["margen_rocio"] = round(panel["temperatura"] - panel["punto_rocio"], 1)
    # LOS TECHOS TAMBIEN, pero lo que se rescata es el ESTADO y no la cuenta.
    # El archivo guarda `roof_open` como un booleano -- abierto o cerrado -- y no
    # los 18 de 18, asi que no se puede reconstruir "0/18" de ayer sin
    # inventarselo. Se publica lo que hay: abierto o cerrado, y cuando.
    #
    # Y es la casilla que mas pesa de la pagina, asi que la etiqueta de la web
    # importa mas aqui que en ningun otro sitio: un "abierto" de hace ocho horas
    # sin decir que es de hace ocho horas se lee como "el observatorio esta
    # funcionando esta noche".
    if panel["techos_total"] is None:
        historico = _ultimo_del_archivo(["roof_open"], root).get("roof_open")
        if historico is not None:
            panel["techos_estado_archivo"] = bool(historico[0])
            completados["techos"] = historico[1].isoformat(timespec="seconds")

    # Que se completo y de cuando: sin esto el panel diria "18 grados" sin decir
    # que ese 18 es de hace veinte minutos.
    panel["del_archivo"] = completados
    # EL FOTOMETRO MANDA. Es medida y todo lo demas de esta pagina es
    # estimacion, asi que el fondo de cielo de AHORA sale de el y no del modelo.
    panel.update(_fotometro_ahora(root))
    return panel


def _fotometro_ahora(root=None) -> dict:
    """La ultima lectura valida del fotometro, con su hora.

    Hasta hoy la mas reciente que teniamos era de mas de un dia antes, porque
    `fetch_tess` rellena por meses. Ahora hay un trabajo en vivo cada diez
    minutos y esto lo lee del archivo, que es donde ese trabajo lo deja.
    """
    try:
        from .queries import connect
        fila = connect(root).execute("""
            SELECT sky_mag_raw, t_utc FROM measurements
            WHERE instrument_type = 'TESS' AND sky_mag_raw IS NOT NULL
              AND t_utc > now() - INTERVAL 3 HOUR
            ORDER BY t_utc DESC LIMIT 1
        """).fetchone()
    except Exception:
        return {}
    if not fila:
        return {"cielo_mag_medido": None, "cielo_mag_medido_utc": None}
    return {"cielo_mag_medido": round(float(fila[0]), 2),
            "cielo_mag_medido_utc": fila[1].isoformat(timespec="seconds"),
            "cielo_mag_instrumento": "TESS stars289"}


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

    # DE DONDE SALEN RACHA, CAPE Y LLUVIA, que desde el 01-09-2026 pueden venir
    # de una malla distinta de la de la nube. Es correcto -- esas tres pasan por
    # umbrales físicos y no por la curva calibrada, así que la malla no las
    # invalida -- pero no puede ser silencioso: quien lea "NO abrir" tiene
    # derecho a saber qué modelo lo dijo.
    try:
        from .tonight import safety_source
        from .ephem import night_for
        noche_actual = night_for(now, site)
        fuentes = safety_source(site, noche_actual.window_start,
                                noche_actual.window_end, root)
    except Exception:
        fuentes = []
    if not fuentes:
        avisos.append("No hay previsión de racha, CAPE ni precipitación para "
                      "esta noche: las puertas de seguridad no han podido "
                      "comprobar nada y el veredicto de abrir solo mira nubes.")
    elif calibrator and fuentes != [calibrator.trained_on_model]:
        avisos.append("La racha, el CAPE y la lluvia salen de %s, no de la "
                      "malla con la que se calibró la nube (%s): son umbrales "
                      "físicos y no la curva, así que la malla no los invalida."
                      % (", ".join(fuentes), calibrator.trained_on_model))

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
        "sensores": _sensores(site, root),
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
            # El suelo natural que usa el recomendador. Ya no es la constante de
            # siete anios: se remide cada dia contra el fotometro, porque el
            # cielo de aqui se ha oscurecido 0,56 mag desde 2019.
            "suelo_cielo": _suelo_publicado(),
            "avisos": avisos,
        },
    }


def write(path: Path | None = None, site: Site = NERPIO, *, nights: int = 2,
          root=None, payload: dict | None = None) -> Path:
    """Escribe la noche. Con `payload`, la ya construida en vez de rehacerla.

    Construir cuesta 26 segundos -- 983 objetos por 59 instantes -- y hasta hoy
    el trabajo que escribia el fichero y el que lo publicaba llamaban cada uno
    al suyo: 52 segundos para obtener DOS VECES el mismo JSON. En un disco
    propio eso no se nota; en un runner que se paga por minutos, es la mitad de
    la pasada.
    """
    path = path or DEFAULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = payload if payload is not None else build(site, nights=nights, root=root)
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
