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
    """cloud.html only shows 'now', so a slow poll means no history at all."""
    trigger, _ = scheduler.JOBS["sensores"]
    assert trigger.interval.total_seconds() <= 600


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
    assert any("AVISO A NINO" in record.message for record in caplog.records)


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
