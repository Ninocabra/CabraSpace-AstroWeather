"""Tests for the unattended collector (SPEC v8 sections 5 and 6.1)."""

import pytest

from astroweather import scheduler


@pytest.fixture(autouse=True)
def clean_tally():
    scheduler.TALLY.clear()
    yield
    scheduler.TALLY.clear()


def test_a_failing_job_does_not_stop_the_others():
    """One dead source must never silence the whole collection."""
    def explodes():
        raise ConnectionError("el proveedor esta caido")

    ran = []
    scheduler.guarded("roto", explodes)()
    scheduler.guarded("sano", lambda: ran.append(1) or 7)()

    assert ran == [1]
    assert scheduler.TALLY["roto"]["failed"] == 1
    assert scheduler.TALLY["sano"]["ok"] == 1


def test_failures_are_counted_not_swallowed():
    """Silence is not success: a broken job has to show up in the tally."""
    def explodes():
        raise TimeoutError

    job = scheduler.guarded("lento", explodes)
    job()
    job()
    assert scheduler.TALLY["lento"] == {"ok": 0, "failed": 2}


def test_every_expiring_source_is_scheduled():
    """These three cannot be rebuilt afterwards, so they must have a job."""
    for name in ("sensores", "ensemble", "forecast"):
        assert name in scheduler.JOBS


def test_the_photometer_and_fits_are_not_paced_like_live_sensors():
    """FITS sit on disk; there is no FWHM job, it is run on demand."""
    assert "fwhm" not in scheduler.JOBS


def test_sensor_polling_is_fast_enough_to_build_a_series():
    """cloud.html only shows "now", so a slow poll means no history at all.

    Desde que la recogida vive en GitHub Actions (30-08-2026) el ritmo REAL lo
    marca el cron del workflow, no este periodo: lo que aqui se afirma es que
    el trabajo nunca sea el cuello de botella, o sea que este vencido en
    cualquier pasada que arranque. Si alguien le pusiera media hora, las
    pasadas del cuarto de hora de la ventana de decision rasparian una de cada
    dos y el archivo saldria plano sin que nada fallara.
    """
    import datetime as dt

    assert scheduler.JOBS["sensores"].periodo <= dt.timedelta(minutes=10)


def test_jobs_never_overlap_themselves():
    """A slow fetch must not pile up on the next tick."""
    built = scheduler.build(only={"sensores"})
    job = built.get_job("sensores")
    assert job.max_instances == 1
    assert job.coalesce is True


def test_expiring_credentials_are_watched():
    """A key that dies quietly takes a whole source down with it."""
    assert "credenciales" in scheduler.JOBS
    assert "AEMET_API_KEY" in scheduler.CREDENTIAL_DEADLINES


def test_the_warning_arrives_with_time_to_act(monkeypatch, caplog):
    """Two weeks of notice, not a message the morning after it expired."""
    import datetime as dt

    class Soon(dt.date):
        @classmethod
        def today(cls):
            return scheduler.CREDENTIAL_DEADLINES["AEMET_API_KEY"] - dt.timedelta(days=10)

    monkeypatch.setattr(scheduler, "date", Soon)
    with caplog.at_level("WARNING"):
        scheduler._job_credentials()
    assert any("AVISO: " in record.message for record in caplog.records)


def test_an_expired_key_is_an_error_not_a_warning(monkeypatch, caplog):
    import datetime as dt

    class TooLate(dt.date):
        @classmethod
        def today(cls):
            return scheduler.CREDENTIAL_DEADLINES["AEMET_API_KEY"] + dt.timedelta(days=1)

    monkeypatch.setattr(scheduler, "date", TooLate)
    with caplog.at_level("ERROR"):
        scheduler._job_credentials()
    assert any("CADUCADA" in record.message for record in caplog.records)


def test_la_hora_utc_de_un_trabajo_es_utc_de_verdad():
    """Un CronTrigger sin zona usa la de la MAQUINA, no la del scheduler.

    Se descubrio el 30-08-2026 mirando por que el `publicar` de "cada 6 h en el
    minuto 35 UTC" disparaba a las 18:35 locales: el scheduler se construye con
    timezone="UTC" pero los triggers se creaban aparte y APScheduler les pone la
    zona local. Con un campo que se llama `desde_hora_utc`, que la hora dependa
    de donde este el equipo es garantizar que un dia signifique otra cosa.
    """
    built = scheduler.build(only={"verificacion"})
    job = built.get_job("verificacion")
    assert str(job.trigger.timezone) == "UTC", (
        f"el trabajo dispara en {job.trigger.timezone}, no en UTC")
