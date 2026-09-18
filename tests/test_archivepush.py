"""Tests de la copia del archivo (29-08-2026).

Lo que protegen es el criterio, no la mecanica de copiar: que lo irrecuperable
sube, que lo recuperable y pesado no, y que un dia sin terminar no se agrupa.
Esa ultima es la importante -- un bundle es inmutable, y uno hecho a mediodia
se quedaria para siempre con media jornada dentro.
"""

import gzip
import io
import lzma
import subprocess
import tarfile

import pytest

from astroweather import archivepush


def _escribir(ruta, contenido=b"<html>estado del sitio</html>"):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(gzip.compress(contenido))


@pytest.fixture
def datos(tmp_path):
    """Un `data/` de mentira con las cuatro clases de contenido."""
    raiz = tmp_path / "data"
    (raiz / "measurements" / "site=nerpio" / "year=2026" / "month=08").mkdir(parents=True)
    (raiz / "measurements" / "site=nerpio" / "year=2026" / "month=08"
     / "20260828T101010Z_abc.parquet").write_bytes(b"PAR1parquet")

    for dia, veces in (("20260827", 3), ("20260828", 4), ("20260829", 2)):
        for i in range(veces):
            _escribir(raiz / "raw" / "site=nerpio" / "source=astrocamp_cloudhtml"
                      / "year=2026" / "month=08" / f"{dia}T{i:02d}0000Z_h{i}.html.gz",
                      b"<html>fila " + str(i).encode() + b"</html>")

    _escribir(raiz / "raw" / "site=nerpio" / "source=openmeteo_ensemble"
              / "year=2026" / "month=08" / "20260828T161000Z_eps.json.gz", b"{}")
    _escribir(raiz / "raw" / "site=nerpio" / "source=tess_stars289"
              / "year=2026" / "month=08" / "20260828T161000Z_tess.json.gz", b"{}" * 5000)
    _escribir(raiz / "raw" / "site=nerpio" / "source=weathernext_ee"
              / "year=2026" / "month=09" / "20260917T093948Z_wn.json.gz", b"{}" * 5000)
    return raiz


@pytest.fixture
def repo(tmp_path):
    destino = tmp_path / "archivo"
    destino.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(destino)], check=True)
    for clave, valor in (("user.email", "test@example.com"), ("user.name", "Test")):
        subprocess.run(["git", "-C", str(destino), "config", clave, valor], check=True)
    return destino


def test_the_photometer_raw_is_the_one_thing_left_behind(datos, repo):
    """93 % del peso, y es lo unico grande que se puede volver a pedir entero."""
    archivepush.collect(datos, repo, today="20260829")

    assert (repo / "measurements").exists()
    assert (repo / "raw" / "site=nerpio" / "source=openmeteo_ensemble").exists()
    assert not (repo / "raw" / "site=nerpio" / "source=tess_stars289").exists()
    # Y la exclusion lleva su motivo escrito, no es un olvido.
    assert "fetch_tess" in archivepush.SKIPPED["tess_stars289"]


def test_weathernext_raw_stays_home_and_its_rows_travel(datos, repo):
    """Nino, 17-09-2026: de WeatherNext sube solo lo canonico."""
    archivepush.collect(datos, repo, today="20260829")

    assert not (repo / "raw" / "site=nerpio" / "source=weathernext_ee").exists()
    # The exclusion is by source, not a blanket one on raw.
    assert (repo / "raw" / "site=nerpio" / "source=openmeteo_ensemble").exists()
    assert "fetch_weathernext" in archivepush.SKIPPED["weathernext_ee"]


def test_a_day_that_has_not_finished_is_not_bundled(datos, repo):
    """Un bundle es inmutable: agrupar hoy congelaria media jornada."""
    archivepush.collect(datos, repo, today="20260829")
    bundles = repo / "raw" / "site=nerpio" / "source=astrocamp_cloudhtml" / "year=2026" / "month=08"

    assert (bundles / "20260827.tar.xz").exists()
    assert (bundles / "20260828.tar.xz").exists()
    assert not (bundles / "20260829.tar.xz").exists()


def test_the_bundle_carries_every_scrape_of_its_day_in_the_clear(datos, repo):
    """Descomprimidos dentro: encadenar gzip con xz tira el 35,8x por la borda,
    que es todo lo que hace viable subir esto."""
    archivepush.collect(datos, repo, today="20260829")
    ruta = (repo / "raw" / "site=nerpio" / "source=astrocamp_cloudhtml"
            / "year=2026" / "month=08" / "20260828.tar.xz")

    with tarfile.open(fileobj=io.BytesIO(lzma.decompress(ruta.read_bytes()))) as tar:
        nombres = tar.getnames()
        primero = tar.extractfile(nombres[0]).read()

    assert len(nombres) == 4
    assert all(not n.endswith(".gz") for n in nombres)
    assert primero.startswith(b"<html>")


def test_collecting_twice_copies_nothing_the_second_time(datos, repo):
    """Los nombres llevan marca de tiempo y hash: falta == no existe."""
    primera = archivepush.collect(datos, repo, today="20260829")
    segunda = archivepush.collect(datos, repo, today="20260829")

    assert sum(primera.values()) > 0
    assert sum(segunda.values()) == 0


def test_a_machine_without_the_clone_does_not_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(archivepush, "DEFAULT_REPO", tmp_path / "no-existe")
    monkeypatch.delenv(archivepush.ENV_VAR, raising=False)
    assert archivepush.repo_path() is None
    assert archivepush.push() is None


def test_pushing_commits_what_it_collected(datos, repo):
    commit = archivepush.push(root=datos, repo=repo, push_remote=False, today="20260829")
    assert commit

    mensaje = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%B"],
                             capture_output=True, text=True).stdout
    assert "measurements" in mensaje          # el commit dice QUE entro
    assert archivepush.push(root=datos, repo=repo, push_remote=False,
                            today="20260829") is None


def _pasada(raiz, mes, stamp, horas, tag="weathernext3"):
    """Una pasada canonica de verdad, escrita por la frontera de escritura."""
    import datetime as dt
    from astroweather.archive import write_canonical
    run = dt.datetime(2026, mes, 3, 0, tzinfo=dt.timezone.utc) + dt.timedelta(hours=stamp)
    filas = [{"site_id": "nerpio", "model": "weathernext3", "run_utc": run,
              "valid_utc": run + dt.timedelta(hours=h), "lead_h": h, "member": None,
              "cloud_total": 12.3} for h in range(1, horas + 1)]
    return write_canonical(filas, dataset="forecasts", site_id="nerpio", root=raiz, tag=tag)


def test_weathernext_runs_travel_as_one_file_per_closed_month(tmp_path, repo):
    """Nino, 17-09-2026: sueltas en este disco, un fichero por mes cerrado."""
    import pyarrow.parquet as pq
    raiz = tmp_path / "data"
    _pasada(raiz, 8, 0, 6)
    _pasada(raiz, 8, 12, 6)
    _pasada(raiz, 9, 0, 6)                       # mes en curso
    sin_etiqueta = _pasada(raiz, 8, 24, 3, tag=None)

    resumen = archivepush.collect(raiz, repo, today="20260917")

    agosto = repo / "forecasts" / "site=nerpio" / "year=2026" / "month=08"
    assert sorted(p.name for p in agosto.iterdir()) == sorted(
        [sin_etiqueta.name, "202608_weathernext3_month.parquet"])
    assert pq.read_table(agosto / "202608_weathernext3_month.parquet").num_rows == 12
    assert not (repo / "forecasts" / "site=nerpio" / "year=2026" / "month=09").exists()
    assert resumen["forecasts/mensual"] == 1

    # Idempotente: una segunda pasada no rehace ni duplica el mes.
    assert archivepush.collect(raiz, repo, today="20260918")["forecasts/mensual"] == 0


def test_a_stale_machine_does_not_overwrite_the_state_of_the_one_collecting(tmp_path, repo):
    """El caso real del 17-09-2026: este PC piso `open_hours` del runner.

    Su copia era del 31-08 y cambio 258 -> 254, 155 -> null y el total de 1635
    a 1476. Lo canonico es de solo-anadir, pero estos sueltos SE REESCRIBEN.
    """
    import os, time
    raiz = tmp_path / "data"
    (raiz / "measurements").mkdir(parents=True)
    viejo, nuevo = b'{"total": 1476}', b'{"total": 1635}'
    (raiz / "open_hours_nerpio.json").write_bytes(viejo)
    (repo / "open_hours_nerpio.json").write_bytes(nuevo)
    antiguo = time.time() - 86400
    os.utime(raiz / "open_hours_nerpio.json", (antiguo, antiguo))

    archivepush.collect(raiz, repo, today="20260918")
    assert (repo / "open_hours_nerpio.json").read_bytes() == nuevo

    # ...y al reves: la maquina que SI recoge sigue subiendo lo suyo.
    (raiz / "open_hours_nerpio.json").write_bytes(b'{"total": 1700}')
    archivepush.collect(raiz, repo, today="20260918")
    assert (repo / "open_hours_nerpio.json").read_bytes() == b'{"total": 1700}'


def test_the_collector_state_is_merged_between_machines(tmp_path, repo):
    """Cada maquina escribe solo sus trabajos; un trabajo sin fecha esta vencido."""
    import json
    raiz = tmp_path / "data"
    (raiz / "measurements").mkdir(parents=True)
    (repo / "estado_colector.json").write_text(json.dumps({
        "escrito_utc": "2026-09-17T20:01:44+00:00",
        "trabajos": {"forecast": "2026-09-17T16:01:34+00:00",
                     "weathernext": "2026-09-16T20:00:00+00:00"}}), encoding="utf-8")
    (raiz / "estado_colector.json").write_text(json.dumps({
        "escrito_utc": "2026-09-17T20:08:47+00:00",
        "trabajos": {"weathernext": "2026-09-17T20:08:42+00:00"}}), encoding="utf-8")

    archivepush.collect(raiz, repo, today="20260918")

    estado = json.loads((repo / "estado_colector.json").read_text(encoding="utf-8"))
    assert estado["trabajos"]["forecast"] == "2026-09-17T16:01:34+00:00"   # no se pierde
    assert estado["trabajos"]["weathernext"] == "2026-09-17T20:08:42+00:00"  # gana el nuevo


def test_a_run_born_inside_the_repo_is_not_versioned(tmp_path, repo):
    """En el runner `data/forecasts` ES el clon del archivo: no se copia, nace dentro.

    Sin la regla, el `git add -A` de `push` subiria las dos pasadas diarias de
    WeatherNext una a una y la agrupacion mensual no llegaria a actuar.
    """
    raiz = tmp_path / "data"
    (raiz / "measurements").mkdir(parents=True)
    archivepush.collect(raiz, repo, today="20260918")

    suelta = repo / "forecasts" / "site=nerpio" / "year=2026" / "month=09"
    suelta.mkdir(parents=True, exist_ok=True)
    for nombre, ignorado in (("20260918T000000Z_abcd1234_weathernext3.parquet", True),
                             ("202609_weathernext3_month.parquet", False),
                             ("20260918T000000Z_abcd1234_weathernext3_backfill.parquet", False),
                             ("20260918T000000Z_abcd1234.parquet", False)):
        ruta = suelta / nombre
        ruta.write_bytes(b"PAR1")
        comprobado = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "-q", str(ruta.relative_to(repo))])
        assert (comprobado.returncode == 0) is ignorado, nombre


# --- dos maquinas contra el mismo remoto (18-09-2026) -----------------------

def _g(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout


def _dos_clones(tmp_path):
    """Un remoto y dos clones: `runner` y `pc`, con el mismo estado inicial."""
    import json
    remoto = tmp_path / "remoto.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remoto)], check=True)
    clones = {}
    for nombre in ("runner", "pc"):
        clon = tmp_path / nombre
        subprocess.run(["git", "clone", "-q", str(remoto), str(clon)], check=True,
                       capture_output=True)
        for clave, valor in (("user.email", "t@example.com"), ("user.name", nombre)):
            _g(clon, "config", clave, valor)
        clones[nombre] = clon
    estado = {"escrito_utc": "2026-09-17T20:00:00+00:00",
              "trabajos": {"forecast": "2026-09-17T16:00:00+00:00",
                           "weathernext": "2026-09-17T08:00:00+00:00"}}
    (clones["runner"] / "estado_colector.json").write_text(json.dumps(estado, indent=2) + "\n")
    _g(clones["runner"], "add", "-A"); _g(clones["runner"], "commit", "-qm", "base")
    _g(clones["runner"], "push", "-q", "origin", "main")
    _g(clones["pc"], "pull", "-q", "origin", "main")
    _g(clones["pc"], "branch", "--set-upstream-to=origin/main", "main")
    return clones


def _estado(ruta, **trabajos):
    import json
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps({"escrito_utc": max(trabajos.values()),
                                "trabajos": trabajos}, indent=2) + "\n")


def test_the_state_race_of_the_18th_is_resolved_by_merging(tmp_path):
    """Lo que paso a las 07:43: el PC tenia un commit de estado sin subir, el
    runner subio el suyo tocando las mismas lineas, y el `pull --rebase` se
    quedo a medias sin que nadie lo mirara. Ahora se funde por trabajo."""
    import json
    clones = _dos_clones(tmp_path)
    # El PC deja un commit local con SU estado (atrasado en `forecast`)...
    _estado(clones["pc"] / "estado_colector.json",
            forecast="2026-09-17T16:00:00+00:00", weathernext="2026-09-17T20:08:00+00:00")
    _g(clones["pc"], "commit", "-qam", "pc"); 
    # ...y el runner sube el suyo, mas nuevo en `forecast`.
    _estado(clones["runner"] / "estado_colector.json",
            forecast="2026-09-18T04:31:00+00:00", weathernext="2026-09-17T08:00:00+00:00")
    _g(clones["runner"], "commit", "-qam", "runner"); _g(clones["runner"], "push", "-q")

    datos = tmp_path / "data"
    (datos / "measurements").mkdir(parents=True)
    _estado(datos / "estado_colector.json",
            forecast="2026-09-17T16:00:00+00:00", weathernext="2026-09-18T07:10:00+00:00")
    assert archivepush.push(root=datos, repo=clones["pc"])

    _g(clones["runner"], "pull", "-q")
    subido = json.loads((clones["runner"] / "estado_colector.json").read_text())
    assert subido["trabajos"] == {"forecast": "2026-09-18T04:31:00+00:00",
                                  "weathernext": "2026-09-18T07:10:00+00:00"}
    assert not archivepush._rebase_en_curso(clones["pc"])


def test_a_conflict_that_is_not_the_state_aborts_and_says_so(tmp_path):
    """Cualquier otro choque es un problema de verdad: no se resuelve a ciegas,
    el clon vuelve a como estaba y el fallo SE VE (antes: codigo 0)."""
    clones = _dos_clones(tmp_path)
    (clones["pc"] / "open_hours_nerpio.json").write_text('{"total": 1476}\n')
    _g(clones["pc"], "add", "-A"); _g(clones["pc"], "commit", "-qm", "pc")
    (clones["runner"] / "open_hours_nerpio.json").write_text('{"total": 1635}\n')
    _g(clones["runner"], "add", "-A"); _g(clones["runner"], "commit", "-qm", "runner")
    _g(clones["runner"], "push", "-q")
    antes = _g(clones["pc"], "rev-parse", "HEAD")

    with pytest.raises(RuntimeError, match="al dia"):
        archivepush.ponerse_al_dia(clones["pc"])
    assert not archivepush._rebase_en_curso(clones["pc"])
    assert _g(clones["pc"], "rev-parse", "HEAD") == antes     # su commit, intacto
