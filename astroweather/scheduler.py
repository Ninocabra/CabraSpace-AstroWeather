"""Unattended collection of everything that expires (SPEC v8 sections 5 and 6.1).

The archive only grows while something is running, and parts of it cannot be
rebuilt afterwards:

* the 51 EPS members are kept upstream for about four days;
* cloud.html publishes the present state of the sensors and nothing else, so a
  reading not scraped is a reading that never existed;
* the observatory safety log and roof states scroll away the same way.

Everything here is therefore paced by how fast its source forgets, not by how
often the value changes. FITS are the exception: they sit on disk, so the FWHM
pipeline is deliberately left out and run on demand.

Each job is wrapped so a failing source can never stop the rest: a worker that
raises is logged, counted and retried on its next tick. Silence is not success,
so the heartbeat prints what ran and what did not.
"""

from __future__ import annotations

import argparse
import logging
import os
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, NamedTuple

from .site import NERPIO

# APScheduler se importa DENTRO de `build()`, no aqui: el colector efimero
# (`astroweather.ci`) lee esta misma tabla de trabajos y no tiene por que
# instalar un planificador que no va a usar. En un runner que se paga por
# minutos, cada dependencia que no se instala son segundos.

LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "scheduler.log"

logger = logging.getLogger("astroweather.scheduler")


def _configure_logging(verbose: bool = True) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if verbose:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


# Running tally, so the heartbeat can report health instead of just liveness.
TALLY: dict[str, dict[str, int]] = {}


def guarded(name: str, function, *args, **kwargs):
    """Run one job so that its failure cannot take the others down with it."""

    def job() -> None:
        stats = TALLY.setdefault(name, {"ok": 0, "failed": 0})
        started = datetime.now(timezone.utc)
        try:
            result = function(*args, **kwargs)
            stats["ok"] += 1
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            logger.info("%-18s ok   (%.1fs) %s", name, elapsed,
                        f"filas={result}" if isinstance(result, int) else "")
        except Exception as error:
            stats["failed"] += 1
            logger.error("%-18s FALLO %s: %s", name, type(error).__name__, error)
            logger.debug(traceback.format_exc())

    return job


def _job_sensors():
    from .workers import scrape_astrocamp
    return scrape_astrocamp.run(NERPIO)


def _job_ensemble():
    from .workers import fetch_ensemble
    return fetch_ensemble.run(NERPIO, forecast_days=7)


def _job_weathernext():
    """WeatherNext 3 de las 00Z y 12Z, las pasadas que gano el examen.

    DONDE CORRE Y POR QUE. Hasta el 25-09-2026 solo una cuenta de usuario tenia
    acceso y esto recogia en el escritorio. Ese dia Google concedio acceso a la
    cuenta de servicio `astroweather-runner` (clave en el secreto
    EE_SERVICE_ACCOUNT_KEY) y el runner recoge aunque el PC este apagado. Una
    maquina sin credenciales SE SALTA DICIENDOLO, que no es lo mismo que fallar
    en silencio ni que fallar cada media hora.

    Y no vuelve a pedir una pasada que ya esta archivada: el periodo de 12 h y
    la inicializacion mas reciente completa no van sincronizados, asi que sin
    esta comprobacion la mitad de las pasadas serian una copia.
    """
    from .workers import fetch_weathernext

    if fetch_weathernext.credentials_source() is None:
        logger.info("weathernext  se salta: esta maquina no tiene credenciales "
                    "de Earth Engine (acceso de la cuenta de servicio pedido "
                    "el 17-09-2026, concedido el 25-09-2026)")
        return 0

    import ee

    # An unset GitHub secret arrives as "", not as missing.
    project = os.getenv("GOOGLE_CLOUD_PROJECT") or "astroweather-cabra"
    fetch_weathernext.initialize(ee, project)
    ultima = fetch_weathernext.latest_complete_init(
        ee, WEATHERNEXT_HOURS, datetime.now(timezone.utc),
        allowed_hours=WEATHERNEXT_RUNS)
    if ultima is None:
        logger.info("weathernext  sin inicializacion completa de 00Z/12Z")
        return 0

    archivada = _weathernext_archivada()
    if archivada is not None and ultima <= archivada:
        logger.info("weathernext  %s ya archivada", ultima.strftime("%Y-%m-%dT%HZ"))
        return 0

    counts = fetch_weathernext.run(hours=WEATHERNEXT_HOURS, init_time=ultima,
                                   project=project, ee_module=ee)
    return sum(counts.values())


def _weathernext_archivada():
    """La inicializacion mas reciente que ya esta en el archivo, o None."""
    from .queries import connect
    try:
        fila = connect().execute(
            "SELECT max(run_utc) FROM forecasts WHERE model = 'weathernext3'"
        ).fetchone()
    except Exception:          # archivo vacio o sin la particion todavia
        return None
    if not fila or fila[0] is None:
        return None
    marca = fila[0]
    return marca.astimezone(timezone.utc) if marca.tzinfo else marca.replace(
        tzinfo=timezone.utc)


def _job_ahora():
    """Los sensores solos al repo de datos: 1,5 kB y 0,1 s.

    Es lo que permite que el panel este fresco en la tarde-noche sin rehacer
    los 660 kB de la prevision, que cuestan 26 s de calculo y otro tanto de
    historial en un repo publico.
    """
    from .webpublish import publicar_ahora, repo_path
    if repo_path() is None:
        return "sin repo de datos en esta maquina"
    return publicar_ahora() or "los sensores no han cambiado"


def _job_suelo():
    from .skybackground import fit_epoch_floor
    return fit_epoch_floor()


def _job_techo_curva():
    """La curva del techo solo cambia si la aspirante GANA el examen.

    Hasta el 26-10-2026 esto era `train()` + `save()`: cada manana una curva
    nueva, sin mirar si era mejor que la que sustituia. Ahora la aspirante se
    ajusta sin las ultimas 14 noches puntuables y se mide contra la vigente en
    esas noches, con el Brier del veredicto (`curvecontest.py`); la decision, sea
    la que sea, queda en el registro `curve_decisions` del archivo.
    """
    from .curvecontest import roof_duel, summary
    return summary(roof_duel())


def _job_nota():
    """La nota de la noche de ayer (y el repaso de las tres anteriores).

    Desde las 10 UTC: AstroCamp apunta las horas de la noche en su tabla hacia
    las 08:00 UTC en verano (10:00 de su hora; 26 de 26 dias de septiembre) y
    con el horario de invierno eso pueden ser las 09:00 UTC.
    """
    from .nightscore import run_daily
    out = run_daily()
    return (f"noches {out['noches'][0]} a {out['noches'][-1]}; filas nuevas: "
            f"{', '.join(out['filas_nuevas']) or 'ninguna'}")


def _job_salud():
    """El bloque de salud de cada pasada y, si cambio algo que importa, el
    `marcador.json` del repo de datos (`health.py`). Lo lee `colector` para
    meterlo en `estado_colector.json`."""
    from . import health
    from .webpublish import publicar_marcador, repo_path
    block = health.compute()
    health.write(block)
    resumen = f"salud {block['nivel']}"
    if repo_path() is None:
        return resumen + " (sin repo de datos: no se publica)"
    commit = publicar_marcador(payload=health.marcador(block))
    return f"{resumen}; marcador {commit}" if commit else f"{resumen}; marcador sin cambios"


def _job_tess_live():
    from .workers import fetch_tess
    return fetch_tess.run_live()


def _job_allsky():
    from .workers import fetch_allsky
    return fetch_allsky.run()


def _job_polvo():
    from .workers import fetch_astrocamp_dust
    return fetch_astrocamp_dust.run()


def _job_archive():
    from .archivepush import push, repo_path
    if repo_path() is None:
        return "sin clon del archivo en esta maquina: no se archiva"
    commit = push()
    return commit or "el archivo ya estaba al dia"


def _job_publish():
    """Construye la noche UNA vez, la deja en disco y la publica.

    Eran dos trabajos, `web` y `publicar`, y cada uno llamaba a `build()`: 52
    segundos para obtener dos veces el mismo JSON. Se separaron para que un
    fallo de red al empujar no marcase en rojo la generacion, y eso se sigue
    cumpliendo -- lo que se escribe en disco ya esta escrito cuando falla el
    push -- pero sin pagar el calculo dos veces.
    """
    from .webexport import build, write
    from .webpublish import publish, repo_path

    payload = build()
    local = write(payload=payload)
    if repo_path() is None:
        return f"{local} (sin repo de datos en esta maquina: no se publica)"
    commit = publish(payload=payload)
    if commit:
        # LO QUE SE PUBLICO, guardado para la nota de manana (`nightscore`). El
        # runner clona el repo de datos con profundidad 1: a la manana siguiente
        # la version de las 16 UTC ya no esta en su disco. Solo si hubo commit:
        # lo que no llego al repo no lo vio nadie.
        from .nightscore import record_publication
        record_publication(payload)
    return commit or "la prevision no ha cambiado"


def _job_cloudmap():
    """El campo de nubes del MAPA, aparte de la noche.

    Trabajo propio y no un apendice de `publicar` por dos razones. Una: son
    dos fuentes distintas -- esto son 465 puntos de Open-Meteo y aquello es el
    archivo ya recogido -- asi que un corte del proveedor no tiene por que
    marcar en rojo la generacion de la noche, que no lo necesita para nada.
    Dos: el gasto se mide aqui y se deja en el log. Open-Meteo cobra por PUNTO
    y tiene tope diario; el dia que nos corten, el log tiene que poder decir en
    que se fue el cupo sin tener que reconstruirlo de memoria.
    """
    from .cloudmap import build
    from .webpublish import publicar_nubes, repo_path

    payload, gasto = build()
    resumen = (f"{gasto['puntos']} puntos en {gasto['lotes']} lotes, "
               f"{gasto['segundos']} s, {len(payload['horas'])} horas")
    if repo_path() is None:
        return f"{resumen} (sin repo de datos en esta maquina: no se publica)"
    commit = publicar_nubes(payload=payload)
    return f"{commit} ({resumen})" if commit else f"sin cambios ({resumen})"


def _job_prediction():
    from .workers import archive_prediction
    return archive_prediction.run(NERPIO)


def _job_forecast():
    from .workers import fetch_forecast
    counts = fetch_forecast.run(NERPIO, days=7)
    return sum(counts.values())


def _job_aemet_hourly():
    from .workers import fetch_aemet
    return fetch_aemet.run_hourly()


def _job_aerosol():
    from .workers import fetch_aerosol
    return fetch_aerosol.run(NERPIO, days=5, with_dust=True)


def _job_aemet():
    from .workers import fetch_aemet
    from datetime import date, timedelta
    # Only the recent days: the historical backfill is a one-off, not a job.
    start = date.today() - timedelta(days=7)
    return fetch_aemet.run(NERPIO, start=start)


def _job_tess():
    """Pide SOLO los huecos de los ultimos dias (`fetch_tess.top_up`).

    Antes se re-pedia el MES ENTERO cada dia (`start` = dia 1, `resume=False`).
    Funcionaba, pero reescribia ~40.000 filas repetidas diarias -- que la vista
    deduplicada esconde y el archivo paga igual -- y solo tapaba el hueco si la
    maquina estaba encendida a su hora. La noche del 28-08-2026 no lo estuvo y
    esa noche se quedo sin una sola fila de fotometro, mientras STARS4ALL la
    servia entera.

    Y pedir desde la ultima archivada tampoco bastaba: `tess_vivo` la adelanta
    cada pocos minutos, y lo que caia entre dos pasadas del vivo no se pedia
    nunca (octubre de 2026: 103 de 245 horas oscuras con fotometro).
    """
    from .workers import fetch_tess
    return fetch_tess.top_up(NERPIO)


# Credentials that expire. A key that dies silently takes a source with it,
# so the collector warns well before, every day, until it is renewed.
CREDENTIAL_DEADLINES = {
    "AEMET_API_KEY": date(2026, 12, 5),
}
WARN_DAYS_BEFORE = 14


def _job_credentials():
    """Warn while there is still time to renew, not once it is too late."""
    today = date.today()
    for name, expiry in CREDENTIAL_DEADLINES.items():
        remaining = (expiry - today).days
        if remaining < 0:
            logger.error("CADUCADA: %s vencio el %s. Renovar y actualizar .env",
                         name, expiry)
        elif remaining <= WARN_DAYS_BEFORE:
            logger.warning("AVISO: %s caduca el %s (quedan %d dias). "
                           "Renovar en opendata.aemet.es y actualizar .env",
                           name, expiry, remaining)
        else:
            logger.info("credenciales   ok   %s vence en %d dias", name, remaining)
    return len(CREDENTIAL_DEADLINES)


def _job_verification():
    from .verification import report
    logger.info("panel de verificacion:\n%s", report())
    # The scoreboard is the half that says which provider is winning, and it
    # can only ever score what was archived while it was running. Logged from
    # the day it exists so the multi-model comparison starts accumulating.
    from .scoreboard import report as scoreboard_report
    logger.info("marcador de modelos:\n%s", scoreboard_report())


def _heartbeat():
    if not TALLY:
        logger.info("latido: aun sin trabajos ejecutados")
        return
    summary = ", ".join(f"{name}={stats['ok']}/{stats['ok'] + stats['failed']}"
                        for name, stats in sorted(TALLY.items()))
    failing = [name for name, stats in TALLY.items() if stats["failed"] > 0]
    logger.info("latido: %s%s", summary,
                f"  <-- CON FALLOS: {', '.join(failing)}" if failing else "")


# 144 h y solo 00Z/12Z: exactamente lo que se bajo y se puntuo en el examen.
WEATHERNEXT_HOURS = 144
WEATHERNEXT_RUNS = (0, 12)


class Trabajo(NamedTuple):
    """Un trabajo y CADA CUANTO tiene que correr. Nada mas.

    Antes el ritmo vivia en un `trigger` de APScheduler, y eso ataba la
    recogida a un proceso vivo en una maquina concreta. El periodo es la misma
    informacion sin el planificador dentro: sirve para montar el scheduler
    local (`build`) y sirve para que un runner efimero decida que le toca
    (`astroweather.ci`), y al ser UNA tabla no pueden discrepar.
    """

    funcion: Callable[[], object]
    periodo: timedelta
    # ...o un periodo que depende del MOMENTO. `periodo` sigue siendo el caso
    # normal y el que usa el scheduler local; `ritmo` manda cuando existe.
    ritmo: Callable[[datetime], timedelta] | None = None
    # ...y nunca antes de esta hora UTC. Solo para lo que necesita que la noche
    # anterior este CERRADA: verificar o remedir el suelo sobre una noche a
    # medias no es que sea impreciso, es que es otra cosa.
    desde_hora_utc: int | None = None


# --- El ritmo de los techos ------------------------------------------------
#
# LA PREGUNTA DEL PRODUCTO ES "¿ABRE ASTROCAMP?", y esa pregunta se responde en
# una franja estrecha: la media hora larga alrededor del ocaso astronomico, que
# es cuando el observatorio decide. Fuera de ahi el techo cambia poco; dentro,
# cambia una vez y esa vez es TODA la informacion.
#
# Medido el 01-09-2026: el ocaso astronomico cayo a las 20:11Z y el archivo
# tiene lecturas de techo a las 19:01, 19:31, 20:01 y 20:31. Cinco muestras de
# media hora para cubrir el unico momento que importa: si hubiera abierto a las
# 20:05 no lo habriamos sabido hasta las 20:31.
#
# POR QUE ESTO NO ES SOLO CAMBIAR EL CRON DE FUERA. `sensores` tiene periodo de
# 10 minutos a proposito, para estar vencido en cualquier pasada de un reloj de
# 15. Con un reloj de 5 minutos ese mismo 10 pasa de red de seguridad a TAPON:
# la mitad de las pasadas encontrarian el trabajo no vencido y el muestreo real
# seguiria siendo de 10 minutos con el doble de runners pagados. El ritmo tiene
# que bajar con el reloj o el reloj no sirve de nada.
VENTANA_TECHOS_ANTES = timedelta(hours=1)
VENTANA_TECHOS_LARGO = timedelta(hours=2)
# Por debajo de los 5 minutos del reloj, por el mismo motivo que 10 esta por
# debajo de 15: el trabajo tiene que estar vencido SIEMPRE que el runner
# arranque, y las pasadas no caen en punto.
RITMO_TECHOS_APERTURA = timedelta(minutes=3)


def techo_abierto(root=None) -> bool | None:
    """El ultimo estado conocido del techo. None si no hay ninguno.

    None no es "cerrado": significa que no lo sabemos, y aguas arriba se trata
    como el caso que MAS resolucion necesita, no como el que menos.
    """
    try:
        from .queries import connect
        fila = connect(root).execute("""
            SELECT roof_open FROM measurements
            WHERE instrument_id = 'astrocamp_roofs' AND roof_open IS NOT NULL
            ORDER BY t_utc DESC LIMIT 1
        """).fetchone()
    except Exception:                                   # noqa: BLE001
        return None
    return bool(fila[0]) if fila else None


def ritmo_de_techos(ahora: datetime, root=None) -> timedelta:
    """Cada cuanto mirar el techo, segun donde estemos de la noche.

    Cinco minutos desde una hora ANTES del ocaso astronomico y durante dos
    horas, mientras el techo siga cerrado. En cuanto abre -- o pasadas las dos
    horas -- se vuelve al ritmo de vigilancia, que no se apaga: un techo abierto
    se puede cerrar, y enterarse tarde de eso es el mismo fallo por el otro
    lado.
    """
    if techo_abierto(root):
        return JOBS_PERIODO_SENSORES
    try:
        from .ephem import night_for
        ocaso = night_for(ahora, NERPIO).dusk_astronomical
    except Exception:                                   # noqa: BLE001
        return JOBS_PERIODO_SENSORES
    inicio = ocaso - VENTANA_TECHOS_ANTES
    if inicio <= ahora <= inicio + VENTANA_TECHOS_LARGO:
        return RITMO_TECHOS_APERTURA
    return JOBS_PERIODO_SENSORES


# El periodo de fuera de la ventana. Sigue siendo mas corto que el reloj para
# que ninguna pasada se quede sin raspar.
JOBS_PERIODO_SENSORES = timedelta(minutes=10)


# El ritmo de cada trabajo lo marca lo deprisa que OLVIDA su fuente, no lo a
# menudo que cambia el valor.
JOBS: dict[str, Trabajo] = {
    # cloud.html solo publica el "ahora": no raspar es no observar. El periodo
    # es de 10 minutos para que este vencido en CUALQUIER pasada: quien decide
    # el ritmo de verdad es el cron del workflow, y como el runner ya esta
    # pagado, raspar en cada arranque sale gratis. Asi la resolucion del
    # archivo sigue a la cadencia de la noche -- cuarto de hora en la ventana
    # de decision -- en vez de ser plana.
    "sensores": Trabajo(_job_sensors, JOBS_PERIODO_SENSORES,
                        ritmo=ritmo_de_techos),
    # Los miembros del EPS caducan aguas arriba en ~4 dias.
    "ensemble": Trabajo(_job_ensemble, timedelta(hours=12)),
    # Gano el examen del 17-09-2026 contra el ECMWF a todos los plazos (+0,024
    # de AUC a 1 dia, IC [+0,004, +0,046]). Dos pasadas al dia, 144 h, las
    # mismas que se puntuaron. Todavia NO entra en el veredicto: primero su
    # curva de calibracion.
    "weathernext": Trabajo(_job_weathernext, timedelta(hours=12)),
    "forecast": Trabajo(_job_forecast, timedelta(hours=6)),
    # Justo despues del forecast: lo que NOSOTROS decimos, guardado antes de
    # que pase. Sin esto, cambiar el calibrador cambia retroactivamente lo que
    # dijimos que dijimos, y no hay auditoria posible.
    "prediccion": Trabajo(_job_prediction, timedelta(hours=6)),
    # Y detras, el JSON que lee la web: se genera y se publica en el mismo
    # trabajo porque construirlo cuesta 26 s y hacerlo dos veces era la mitad
    # de la pasada.
    "publicar": Trabajo(_job_publish, timedelta(hours=6)),
    # El campo de nubes del mapa, al mismo ritmo y por la misma razon: cada
    # 6 h es cuando hay pasada nueva del ECMWF. Pedirlo mas a menudo seria
    # gastar cupo para volver a bajar los mismos numeros.
    "nubes": Trabajo(_job_cloudmap, timedelta(hours=6)),
    # Y los SENSORES aparte, en cada pasada. La prevision cambia cada 6 h; los
    # sensores cambian cada minuto y son lo que mira quien entra a las nueve de
    # la noche. Diez minutos de periodo para que este vencido siempre: lo que
    # decide su ritmo de verdad es cada cuanto arranca el runner.
    "ahora": Trabajo(_job_ahora, timedelta(minutes=10)),
    # CAMS encola, asi que una vez al dia y sin prisa.
    "aerosol": Trabajo(_job_aerosol, timedelta(hours=24)),
    "aemet": Trabajo(_job_aemet, timedelta(hours=24)),
    # La observacion HORARIA no tiene historico que pedir: la ventana es de
    # ~12 h y lo que no se sondee hoy no existe manana.
    "aemet_horario": Trabajo(_job_aemet_hourly, timedelta(hours=8)),
    # El archivo del fotometro se sirve por meses; rellenar a diario basta, y
    # es lo que hace que `tess_vivo` no sea la unica copia de nada.
    "tess": Trabajo(_job_tess, timedelta(hours=24)),
    # Y el fotometro EN VIVO, para que el panel pueda ensenar el fondo de cielo
    # medido de ahora en vez de uno de hace un dia.
    "tess_vivo": Trabajo(_job_tess_live, timedelta(minutes=10)),
    # La allsky sirve 19,2 h como mucho (medido) a 15 s de cadencia. Cada 4
    # horas pidiendo 5 deja una hora de solape: una caida corta no abre hueco.
    "allsky": Trabajo(_job_allsky, timedelta(hours=4)),
    # El grafico de polvo abarca 24 h, asi que cada 8 h se recoge cada punto
    # TRES veces y hacen falta dos pasadas seguidas fallidas para abrir un
    # hueco. Mas a menudo no anade dato: solo pesa en el archivo, que son 64 KB
    # de PNG comprimido por pasada y ahi si se nota (~70 MB al anio).
    "polvo": Trabajo(_job_polvo, timedelta(hours=8)),
    "verificacion": Trabajo(_job_verification, timedelta(hours=24), desde_hora_utc=8),
    # El suelo del cielo, remedido cada dia contra el fotometro. Va DESPUES del
    # relleno del TESS: el numero que usa el recomendador tiene que ser el de
    # hoy, no el de hace siete anios.
    "suelo": Trabajo(_job_suelo, timedelta(hours=24), desde_hora_utc=8),
    # La nota de la noche de ayer: lo publicado a las 16 UTC contra lo que paso
    # (`nightscore.py`). Desde las 10 UTC, cuando AstroCamp ya ha apuntado la
    # noche en su tabla, y DESPUES de `tess` y `allsky` (orden de esta tabla), que
    # rellenan las verdades de la noche. Antes de la curva: el examen de la curva
    # se puntua con estas notas.
    "nota": Trabajo(_job_nota, timedelta(hours=24), desde_hora_utc=10),
    # La curva P(techo abierto | nube prevista). Desde el 26-10-2026 no se
    # guarda a ciegas: una aspirante ajustada sin las ultimas 14 noches tiene que
    # ganar a la vigente en esas noches (`curvecontest.py`). A las 10 y no a las
    # 8 para examinar con la nota de anoche ya escrita.
    "techo_curva": Trabajo(_job_techo_curva, timedelta(hours=24), desde_hora_utc=10),
    # Barato, diario, y es lo unico que hay entre una clave que caduca en
    # silencio y una fuente que deja de responder sin que nadie se entere.
    "credenciales": Trabajo(_job_credentials, timedelta(hours=24)),
    # La salud en CADA pasada (periodo por debajo del reloj, como `ahora`): la
    # caida de cadencia del 30-09 (M11) estuvo cuatro semanas sin que nada la
    # dijera. Va justo antes de `archivo` para que el estado que sube la lleve.
    "salud": Trabajo(_job_salud, timedelta(minutes=10)),
    # Y sacar el archivo de este disco. CADA pasada y no una vez al dia: en un
    # runner efimero el disco se destruye al terminar, asi que lo que no se
    # empuje aqui no ha existido. Agrupar por dias sigue esperando a que el dia
    # TERMINE, que es cosa de `archivepush` y no del ritmo.
    "archivo": Trabajo(_job_archive, timedelta(minutes=10)),
}


def build(only: set[str] | None = None):
    """El scheduler de proceso vivo, armado desde la MISMA tabla de periodos.

    Se queda como respaldo -- una maquina con el proceso arriba recoge igual --
    pero la recogida de verdad vive ya en GitHub Actions: mientras dependia de
    este PC, lo que no estuviera encendido no se observaba, y el fotometro de
    la noche del 29-08-2026 lo demostro parandose a las 00:01 locales.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = BlockingScheduler(timezone="UTC")
    for name, trabajo in JOBS.items():
        if only and name not in only:
            continue
        if trabajo.desde_hora_utc is not None:
            # La zona, EXPLICITA. Un CronTrigger creado sin ella no usa la del
            # scheduler -- aunque se le haya puesto UTC -- sino la de la
            # maquina: por eso el `publicar` de "*/6 UTC" del codigo anterior
            # disparaba a las 18:35 LOCALES. Con un campo que se llama
            # `desde_hora_utc`, dejarlo al azar de la zona del equipo es
            # garantizar que un dia signifique otra cosa.
            disparo = CronTrigger(hour=trabajo.desde_hora_utc, minute=20,
                                  timezone="UTC")
        else:
            disparo = IntervalTrigger(seconds=int(trabajo.periodo.total_seconds()))
        scheduler.add_job(
            guarded(name, trabajo.funcion), disparo, id=name, name=name,
            max_instances=1,        # never overlap a slow run with the next tick
            coalesce=True,          # after a suspend, catch up once, not N times
            misfire_grace_time=1800,
        )
    scheduler.add_job(_heartbeat, IntervalTrigger(hours=1), id="latido")
    return scheduler


def run_once(only: set[str] | None = None) -> None:
    """Run every job a single time: smoke test, and catch-up after downtime."""
    for name, trabajo in JOBS.items():
        if only and name not in only:
            continue
        guarded(name, trabajo.funcion)()
    _heartbeat()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true",
                        help="ejecuta todo una vez y sale")
    parser.add_argument("--only", nargs="*", help="limita a estos trabajos")
    parser.add_argument("--quiet", action="store_true", help="solo al fichero de log")
    args = parser.parse_args()

    _configure_logging(verbose=not args.quiet)
    only = set(args.only) if args.only else None

    if args.once:
        run_once(only)
        return

    scheduler = build(only)
    logger.info("scheduler arrancado; trabajos: %s",
                ", ".join(job.id for job in scheduler.get_jobs()))
    logger.info("OJO: esto es el RESPALDO. La recogida vive en GitHub Actions "
                "(.github/workflows/colector.yml); los dos a la vez solo "
                "duplican trabajo, que el deduplicador absorbe")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler detenido a mano")


if __name__ == "__main__":
    main()
