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
