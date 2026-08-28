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
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .site import NERPIO

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


def _job_forecast():
    from .workers import fetch_forecast
    counts = fetch_forecast.run(NERPIO, days=7)
    return sum(counts.values())


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
    from .workers import fetch_tess
    from datetime import datetime as dt, timedelta
    # Resume from the current month; already-archived months are skipped.
    start = dt.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0,
                                         microsecond=0)
    return fetch_tess.run(NERPIO, start=start, resume=False)


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
            logger.warning("AVISO A NINO: %s caduca el %s (quedan %d dias). "
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


# name -> (trigger, callable). The pace of each job is set by how fast its
# source forgets, not by how often the underlying value changes.
JOBS = {
    # cloud.html only ever shows "now": not scraping is not observing.
    "sensores": (IntervalTrigger(minutes=5), _job_sensors),
    # EPS members expire upstream in ~4 days; twice a day is cheap insurance.
    "ensemble": (CronTrigger(hour="6,18", minute=10), _job_ensemble),
    "forecast": (CronTrigger(hour="*/6", minute=20), _job_forecast),
    # CAMS queues, so it runs once a day well away from anything interactive.
    "aerosol": (CronTrigger(hour=7, minute=30), _job_aerosol),
    "aemet": (CronTrigger(hour=8, minute=0), _job_aemet),
    # The photometer archive is served by month; topping up daily is enough.
    "tess": (CronTrigger(hour=9, minute=0), _job_tess),
    # Late morning, once the previous night is complete.
    "verificacion": (CronTrigger(hour=10, minute=0), _job_verification),
    # Cheap, daily, and it is the only thing standing between a quiet key
    # expiry and a source that stops without anyone noticing.
    "credenciales": (CronTrigger(hour=10, minute=5), _job_credentials),
}


def build(only: set[str] | None = None) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="UTC")
    for name, (trigger, function) in JOBS.items():
        if only and name not in only:
            continue
        scheduler.add_job(
            guarded(name, function), trigger, id=name, name=name,
            max_instances=1,        # never overlap a slow run with the next tick
            coalesce=True,          # after a suspend, catch up once, not N times
            misfire_grace_time=1800,
        )
    scheduler.add_job(_heartbeat, IntervalTrigger(hours=1), id="latido")
    return scheduler


def run_once(only: set[str] | None = None) -> None:
    """Run every job a single time: smoke test, and catch-up after downtime."""
    for name, (_, function) in JOBS.items():
        if only and name not in only:
            continue
        guarded(name, function)()
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
    logger.info("mientras corra en este PC solo se recoge dato con el equipo "
                "encendido; el VPS de la fase 1 lo resuelve")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler detenido a mano")


if __name__ == "__main__":
    main()
