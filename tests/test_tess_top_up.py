"""`top_up` rellena los huecos entre pasadas del vivo (retroanalisis 26-10-2026, M12).

Pedir desde la ULTIMA medida archivada dejaba sin pedir todo lo que caia entre
dos pasadas de `tess_vivo`: en octubre de 2026, 103 de 245 horas oscuras con
fotometro. Aqui STARS4ALL es un doble que sirve una muestra por minuto de lo
que se le pida, sin red.
"""

import re
from datetime import datetime, timedelta, timezone

import pytest

from astroweather.archive import write_canonical
from astroweather.queries import connect
from astroweather.site import NERPIO
from astroweather.workers import fetch_tess

NOW = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)
STATION = "stars289"


def _minutes(start, end):
    t = start
    while t < end:
        yield t
        t += timedelta(minutes=1)


def _series(instants):
    epochs = [int(t.timestamp()) for t in instants]
    return [
        {"name": "mqtt_consumer.mag", "points": [[21.5, e] for e in epochs]},
        {"name": "mqtt_consumer.tamb", "points": [[0, e] for e in epochs]},
        {"name": "mqtt_consumer.tsky", "points": [[0, e] for e in epochs]},
    ]


class FakeStars4All:
    """Sirve una muestra por minuto en [desde, hasta) y apunta cada peticion."""

    PATTERN = re.compile(r"time >= '([^']+)' AND time < '([^']+)'")

    def __init__(self, desde, hasta):
        self.desde, self.hasta = desde, hasta
        self.peticiones = []

    def __call__(self, sql, **_):
        a, b = (datetime.strptime(x, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                for x in self.PATTERN.search(sql).groups())
        self.peticiones.append((a, b))
        served = [t for t in _minutes(self.desde, self.hasta) if a <= t < b]
        return _series(served) if served else []


@pytest.fixture
def archivo(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_tess, "utcnow", lambda: NOW)
    fake = FakeStars4All(NOW - timedelta(days=30), NOW)
    monkeypatch.setattr(fetch_tess, "query", fake)
    # `connect` monta tambien la vista de previsiones y sin ningun parquet
    # falla; el archivo real siempre lo tiene, asi que se siembra una fila.
    write_canonical([{"site_id": NERPIO.site_id, "model": "ecmwf_ifs",
                      "run_utc": NOW, "valid_utc": NOW, "cloud_low": 10.0}],
                    dataset="forecasts", site_id=NERPIO.site_id, root=tmp_path)
    return tmp_path, fake


def _vivo(root, fin, minutos=90):
    """Lo que escribe una pasada de `tess_vivo`: la ultima hora y media."""
    instants = list(_minutes(fin - timedelta(minutes=minutos), fin))
    rows = fetch_tess._to_rows(_series(instants), NERPIO, STATION, fin)
    write_canonical(rows, dataset="measurements", site_id=NERPIO.site_id, root=root)


def _archived(root, desde, hasta):
    return connect(root).execute(
        "SELECT count(*) FROM measurements WHERE instrument_id = ? "
        "AND t_utc >= ? AND t_utc < ?", [STATION, desde, hasta]).fetchone()[0]


def _raw_rows(root):
    return connect(root).execute(
        f"SELECT count(*) FROM read_parquet('{root.as_posix()}/measurements/**/*.parquet')"
    ).fetchone()[0]


def test_hueco_entre_dos_pasadas_del_vivo_acaba_rellenado(archivo):
    root, fake = archivo
    # Dos pasadas del vivo con la noche de en medio sin nadie: de 20:00 a 04:30.
    _vivo(root, NOW - timedelta(hours=10))
    _vivo(root, NOW)
    noche = (NOW - timedelta(hours=10), NOW - timedelta(hours=1, minutes=30))
    assert _archived(root, *noche) == 0

    escritas = fetch_tess.top_up(NERPIO, station=STATION, root=root, pause=0)

    # Con el criterio viejo (desde la ultima archivada) esto quedaba a cero.
    assert _archived(root, *noche) == 510
    # La ventana entera queda completa: 4 dias enteros hasta ahora, minuto a minuto.
    inicio = datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert _archived(root, inicio, NOW) == int((NOW - inicio).total_seconds() // 60)
    # Y no se ha reescrito lo que ya estaba: solo se escribe lo que faltaba.
    assert escritas == _raw_rows(root) - 2 * 90


def test_segunda_pasada_sin_huecos_no_escribe_nada(archivo):
    root, fake = archivo
    _vivo(root, NOW - timedelta(hours=10))
    _vivo(root, NOW)
    fetch_tess.top_up(NERPIO, station=STATION, root=root, pause=0)
    filas_antes = _raw_rows(root)
    fake.peticiones.clear()

    assert fetch_tess.top_up(NERPIO, station=STATION, root=root, pause=0) == 0
    assert _raw_rows(root) == filas_antes
    assert fake.peticiones == []


def test_apagon_mas_largo_que_la_ventana_se_pide_desde_la_ultima(archivo):
    root, fake = archivo
    _vivo(root, NOW - timedelta(days=10))

    fetch_tess.top_up(NERPIO, station=STATION, root=root, pause=0)

    desde = NOW - timedelta(days=10)
    assert _archived(root, desde, NOW) == int((NOW - desde).total_seconds() // 60)


def test_huecos_con_bordes_exclusivos_y_fusion():
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    m = lambda n: t0 + timedelta(minutes=n)
    # Muestras en 0..9, hueco, 100..109, hueco corto (3 min, jitter), 113..119.
    instantes = [m(i) for i in [*range(10), *range(100, 110), *range(113, 120)]]
    tramos = fetch_tess.huecos(instantes, t0, m(120), fusionar=timedelta(0))
    assert tramos == [(m(9) + timedelta(seconds=1), m(100))]
    # Ventana sin nada: un solo tramo, la ventana entera.
    assert fetch_tess.huecos([], t0, m(120)) == [(t0, m(120))]
    # Dos huecos a menos de una hora se piden juntos.
    instantes = [m(i) for i in [*range(10), *range(30, 40), *range(60, 70)]]
    assert fetch_tess.huecos(instantes, t0, m(70)) == [
        (m(9) + timedelta(seconds=1), m(60))]
