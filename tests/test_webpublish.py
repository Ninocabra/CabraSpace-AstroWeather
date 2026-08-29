"""Tests de la publicacion en el repo de datos publico (opcion (a), 29-08-2026).

Lo que protegen no es que sepa hacer `git commit`, es la disciplina del
historial: el fichero se sobrescribe siempre en la misma ruta para que el
`git log` SEA el registro de lo que dijimos y cuando. Ese registro solo vale
si cada commit significa algo, y por eso una pasada que no cambia la prevision
no puede dejar rastro.
"""

import json
import subprocess

import pytest

from astroweather import webpublish


def _prevision(generado, p_abrir=0.88):
    return {
        "generado_utc": generado,
        "sitio": {"nombre": "AstroCamp, Nerpio (Albacete)"},
        "noches": [{"noche": "2026-08-29", "probabilidad_de_abrir": p_abrir}],
        "confianza": {"calibrado": True, "avisos": []},
    }


@pytest.fixture
def repo(tmp_path):
    """Un repo de verdad: lo que se prueba es el comportamiento con git."""
    destino = tmp_path / "datos"
    destino.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(destino)], check=True)
    for clave, valor in (("user.email", "test@example.com"), ("user.name", "Test")):
        subprocess.run(["git", "-C", str(destino), "config", clave, valor], check=True)
    return destino


def _commits(repo):
    salida = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                            capture_output=True, text=True)
    return [linea for linea in salida.stdout.splitlines() if linea]


def test_publishing_leaves_the_forecast_and_a_commit(repo, monkeypatch):
    monkeypatch.setattr(webpublish, "build",
                        lambda *a, **k: _prevision("2026-08-29T09:00:00+00:00"))
    assert webpublish.publish(repo=repo, push=False)

    publicado = json.loads((repo / "noche.json").read_text(encoding="utf-8"))
    assert publicado["noches"][0]["probabilidad_de_abrir"] == 0.88
    assert len(_commits(repo)) == 1


def test_a_pass_that_says_the_same_thing_leaves_no_trace(repo, monkeypatch):
    """La regla del historial: un commit por CAMBIO, no por pasada.

    `generado_utc` cambia siempre, asi que compararlo dentro convertiria el
    `git log` en un latido -- cuatro commits diarios diciendo lo mismo -- y
    enterraria los cambios de verdad. Interesa cuando cambio lo que PENSAMOS,
    no cuando volvimos a pensarlo.
    """
    monkeypatch.setattr(webpublish, "build",
                        lambda *a, **k: _prevision("2026-08-29T09:00:00+00:00"))
    assert webpublish.publish(repo=repo, push=False)

    monkeypatch.setattr(webpublish, "build",
                        lambda *a, **k: _prevision("2026-08-29T15:00:00+00:00"))
    assert webpublish.publish(repo=repo, push=False) is None
    assert len(_commits(repo)) == 1


def test_a_forecast_that_changed_does_leave_one(repo, monkeypatch):
    monkeypatch.setattr(webpublish, "build",
                        lambda *a, **k: _prevision("2026-08-29T09:00:00+00:00"))
    webpublish.publish(repo=repo, push=False)

    monkeypatch.setattr(webpublish, "build",
                        lambda *a, **k: _prevision("2026-08-29T15:00:00+00:00", 0.41))
    assert webpublish.publish(repo=repo, push=False)
    assert len(_commits(repo)) == 2


def test_a_machine_without_the_clone_does_not_fail(tmp_path, monkeypatch):
    """No publicar no es un error: el scheduler corre en maquinas que no publican."""
    monkeypatch.setattr(webpublish, "DEFAULT_REPO", tmp_path / "no-existe")
    monkeypatch.delenv(webpublish.ENV_VAR, raising=False)
    assert webpublish.repo_path() is None
    assert webpublish.publish() is None


def test_a_directory_that_is_not_a_repo_is_not_a_repo(tmp_path, monkeypatch):
    """Un directorio suelto no sirve: sin `.git` no hay historial, y el
    historial es la mitad del producto."""
    suelto = tmp_path / "suelto"
    suelto.mkdir()
    monkeypatch.setenv(webpublish.ENV_VAR, str(suelto))
    assert webpublish.repo_path() is None
