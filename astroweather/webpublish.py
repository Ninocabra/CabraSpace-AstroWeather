"""Sacar la noche del disco duro: publicarla en el repo de datos publico.

Decidido el 29-08-2026, y es la opcion (a) de las tres que habia. `webexport`
ya generaba el JSON, pero lo dejaba en `data/web/noche.json`, que esta ignorado
por git: el producto existia y no se podia leer desde ningun sitio. Las otras
dos opciones eran commitear el JSON en el repo de la web (funciona hoy y ensucia
la web con un fichero que cambia cada 6 h) y colgarlo de una Release (publico,
con URL incomoda). Esta es la que ademas desbloquea el scheduler 24/7: si el
producto vive en su propio repo publico, quien lo genera puede ser cualquier
maquina.

Lo que se publica es la PREVISION, no el archivo. El raw de terceros --
AstroCamp, STARS4ALL, AEMET, Open-Meteo, CAMS -- no se republica: cada fuente
tiene sus condiciones y SPEC 8 deja escrito que el permiso por escrito de
AstroCamp y STARS4ALL para uso continuado esta pendiente. Lo que sale de aqui
es nuestro: nuestra prevision, con nuestra incertidumbre dentro.

El historial no se guarda aparte. El fichero se sobrescribe SIEMPRE en la misma
ruta, asi que el `git log` de `noche.json` es el registro de lo que dijimos y
cuando, diffeable y sin poder reescribirse en silencio. Una prevision que se
puede cambiar sin dejar rastro no se puede verificar, y verificarnos es medio
proyecto.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from .site import NERPIO, Site
from .webexport import build

# Donde esta clonado el repo publico. Configurable porque cada maquina lo tiene
# en un sitio; por defecto, al lado del repo del motor.
ENV_VAR = "ASTROWEATHER_DATA_REPO"
DEFAULT_REPO = Path(__file__).resolve().parent.parent.parent / "AstroWeather-Data"
FILENAME = "noche.json"

# Lo unico que cambia en cada pasada aunque no cambie la prevision. Comparar
# con esto dentro convertiria el historial en un latido: un commit cada 6 h
# diciendo lo mismo. Lo que interesa que quede en el `git log` es cuando cambio
# lo que PENSAMOS, no cuando volvimos a pensarlo.
VOLATILE = ("generado_utc",)


def repo_path() -> Path | None:
    """El clon del repo de datos, o None si esta maquina no lo tiene."""
    candidate = Path(os.environ.get(ENV_VAR, "")) if os.environ.get(ENV_VAR) else DEFAULT_REPO
    return candidate if (candidate / ".git").is_dir() else None


def _significant(payload: dict) -> str:
    return json.dumps({k: v for k, v in payload.items() if k not in VOLATILE},
                      sort_keys=True, ensure_ascii=False)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(("git", "-C", str(repo)) + args,
                          capture_output=True, text=True, check=False)


def publish(site: Site = NERPIO, *, nights: int = 2, root=None,
            repo: Path | None = None, push: bool = True) -> str | None:
    """Genera la noche y la publica. Devuelve el commit, o None si no hizo falta.

    No lanza si el repo no esta clonado: el scheduler corre en maquinas que no
    publican, y no publicar no es un error.
    """
    repo = repo or repo_path()
    if repo is None:
        return None

    payload = build(site, nights=nights, root=root)
    destination = repo / FILENAME
    if destination.exists():
        try:
            previous = json.loads(destination.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = None
        if previous is not None and _significant(previous) == _significant(payload):
            return None

    destination.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                           encoding="utf-8")
    _git(repo, "add", FILENAME)
    if _git(repo, "diff", "--cached", "--quiet").returncode == 0:
        return None

    generated = payload.get("generado_utc", "")
    _git(repo, "commit", "-m", f"Prevision de {generated}")
    if push:
        pushed = _git(repo, "push")
        if pushed.returncode != 0:
            # Se queda commiteado en local y la proxima pasada lo reintenta:
            # perder el commit por un fallo de red seria peor que no publicar.
            raise RuntimeError(f"publicado en local pero sin subir: {pushed.stderr.strip()}")
    return _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=None)
    parser.add_argument("--nights", type=int, default=2)
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    repo = args.repo or repo_path()
    if repo is None:
        print(f"[webpublish] sin repo de datos ({ENV_VAR} o {DEFAULT_REPO}): no se publica")
        return
    commit = publish(nights=args.nights, repo=repo, push=not args.no_push)
    print(f"[webpublish] {commit}" if commit
          else "[webpublish] la prevision no ha cambiado: nada que publicar")


if __name__ == "__main__":
    main()
