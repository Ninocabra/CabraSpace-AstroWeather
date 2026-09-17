# -*- coding: utf-8 -*-
"""El trabajo de WeatherNext: donde corre, y que no repite lo ya archivado.

El acceso de la cuenta de servicio esta PEDIDO (weathernext@google.com,
17-09-2026). Hasta que llegue, el runner no puede recogerlo y este PC si, asi
que lo que hay que proteger es que la ausencia de credenciales se SALTE el
trabajo diciendolo, en vez de fallar cada media hora en el runner.
"""

import datetime as dt

from astroweather import scheduler


def test_without_credentials_the_job_skips_instead_of_failing(monkeypatch, caplog):
    from astroweather.workers import fetch_weathernext
    monkeypatch.setattr(fetch_weathernext, "credentials_source", lambda: None)

    def no_deberia(*a, **k):
        raise AssertionError("sin credenciales no se toca la red")
    monkeypatch.setattr(fetch_weathernext, "run", no_deberia)
    monkeypatch.setattr(fetch_weathernext, "latest_complete_init", no_deberia)

    with caplog.at_level("INFO"):
        assert scheduler._job_weathernext() == 0
    assert "credenciales" in caplog.text


def test_an_initialization_already_archived_is_not_fetched_again(monkeypatch, caplog):
    from astroweather.workers import fetch_weathernext
    init = dt.datetime(2026, 9, 17, 12, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(fetch_weathernext, "credentials_source", lambda: "user")
    monkeypatch.setattr(fetch_weathernext, "initialize", lambda ee, project: "user")
    monkeypatch.setattr(fetch_weathernext, "latest_complete_init",
                        lambda *a, **k: init)
    monkeypatch.setattr(scheduler, "_weathernext_archivada", lambda: init)
    monkeypatch.setattr(fetch_weathernext, "run",
                        lambda **k: (_ for _ in ()).throw(AssertionError("repetida")))

    with caplog.at_level("INFO"):
        assert scheduler._job_weathernext() == 0
    assert "ya archivada" in caplog.text


def test_a_new_initialization_is_fetched_with_the_exam_shape(monkeypatch):
    from astroweather.workers import fetch_weathernext
    pedido = {}
    monkeypatch.setattr(fetch_weathernext, "credentials_source", lambda: "user")
    monkeypatch.setattr(fetch_weathernext, "initialize", lambda ee, project: "user")
    def _busqueda(ee, hours, now, allowed_hours=None):
        pedido["busqueda"] = (hours, allowed_hours)
        return dt.datetime(2026, 9, 17, 12, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(fetch_weathernext, "latest_complete_init", _busqueda)
    monkeypatch.setattr(scheduler, "_weathernext_archivada",
                        lambda: dt.datetime(2026, 9, 17, tzinfo=dt.timezone.utc))
    def _run(**k):
        pedido["run"] = k
        return {"weathernext3": 144}
    monkeypatch.setattr(fetch_weathernext, "run", _run)

    assert scheduler._job_weathernext() == 144
    assert pedido["busqueda"] == (144, (0, 12))
    assert pedido["run"]["hours"] == 144
    assert pedido["run"]["init_time"] == dt.datetime(2026, 9, 17, 12, tzinfo=dt.timezone.utc)
