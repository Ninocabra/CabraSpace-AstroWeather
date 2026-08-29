"""El archivo, fuera de este disco. Decidido y medido el 29-08-2026.

`data/` son 85 MB que vivian en un solo disco, y parte no se puede volver a
descargar: el scraper de AstroCamp lee una pagina que solo muestra "ahora", y
los 51 miembros del EPS caducan aguas arriba en 4-5 dias. Perder ese disco era
perder eso para siempre.

El criterio no es "subirlo todo", es PESO CONTRA RELEVANCIA, y las tres
decisiones salen de medir, no de opinar:

1. **Lo canonico se copia tal cual.** `measurements`, `forecasts` y
   `daily_observations` son Parquet y -- esto es lo que lo hace viable -- el
   almacen ya es de solo-anadir: cada escritura crea un fichero nuevo con su
   marca de tiempo y NADA se reescribe. En git eso significa que el historial
   pesa lo que pesan los datos, una vez, en vez de una copia entera por cada
   dia que se toque. 36 MB hoy, unos 10 MB al anio.

2. **Los scrapes se agrupan por dia.** Son la MISMA pagina cada 5 minutos, y
   comprimidos de uno en uno no se aprovecha nada: 181 ficheros del 28-08 pesan
   826 kB sueltos y **23 kB** en un solo flujo (tar + xz), 35,8x. Eso convierte
   470 MB al anio en unos 13 MB. Se agrupa por DIA y no por mes a proposito:
   es justo el agujero que encontro la auditoria de Codex -- con cierre mensual,
   lo irrecuperable se pasaba hasta 30 dias sin mas hogar que este PC.

3. **El raw del fotometro NO sube.** Son 37,3 MB, el 93 % de todo el raw, y es
   lo unico grande que se puede volver a pedir entero (`fetch_tess` reanuda por
   meses). Ademas sus valores ya viajan aqui dentro de `measurements`. Subirlo
   seria pagar el 93 % del peso por el 0 % de la relevancia que no este ya.

Un dia solo se agrupa cuando ha TERMINADO. Un bundle a medias seria un fichero
inmutable con la mitad de la verdad dentro, y el dia siguiente no habria forma
de anadirle lo que falta sin reescribirlo.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import gzip
import os
import shutil
import subprocess
import tarfile
import lzma
from pathlib import Path

from .queries import DATA_ROOT

ENV_VAR = "ASTROWEATHER_ARCHIVE_REPO"
DEFAULT_REPO = Path(__file__).resolve().parent.parent.parent / "AstroWeather-Archive"

# Almacenes canonicos: Parquet de solo-anadir, se copian tal cual.
MIRRORED = ("measurements", "forecasts", "daily_observations")

# Raw que se agrupa por dia. Los scrapes porque llegan cada 5 minutos; la
# allsky porque cada consulta trae 5 h de serie y las consultas se solapan
# una hora, asi que entre ficheros hay mucho identico que comprimir junto.
BUNDLED = ("astrocamp_cloudhtml", "astrocamp_allsky")

# Raw que no sube, con el motivo. Se declara aqui y se repite en el README del
# archivo: una exclusion sin motivo escrito es un olvido esperando a que le den
# la razon.
SKIPPED = {
    "tess_stars289": "37 MB recuperables con `python -m astroweather.workers."
                     "fetch_tess`, y sus valores ya viajan en measurements/",
}

# Ficheros sueltos de `data/` que son estado del producto y valen su peso.
LOOSE = ("cloud_calibration.json", "open_hours_nerpio.json", "roof_climatology.json")


def repo_path(repo: Path | None = None) -> Path | None:
    """El clon del archivo, o None si esta maquina no lo tiene."""
    if repo is not None:
        return repo if (repo / ".git").is_dir() else None
    candidate = Path(os.environ[ENV_VAR]) if os.environ.get(ENV_VAR) else DEFAULT_REPO
    return candidate if (candidate / ".git").is_dir() else None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(("git", "-C", str(repo)) + args,
                          capture_output=True, text=True, check=False)


def _day_of(name: str) -> str | None:
    """El dia que lleva el nombre del fichero, `YYYYMMDDTHHMMSSZ_hash.ext`."""
    return name[:8] if len(name) > 8 and name[:8].isdigit() else None


def mirror_tree(source: Path, destination: Path) -> list[Path]:
    """Copia lo que falte. Los nombres son unicos, asi que falta == no existe."""
    copiados = []
    if not source.is_dir():
        return copiados
    for origen in sorted(source.rglob("*")):
        if not origen.is_file():
            continue
        destino = destination / origen.relative_to(source)
        if destino.exists():
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origen, destino)
        copiados.append(destino)
    return copiados


def bundle_days(source: Path, destination: Path, *, today: str) -> list[Path]:
    """Un `.tar.xz` por dia terminado. Descomprime antes de empaquetar.

    Los ficheros llegan ya en gzip individual, y encadenar compresores no sirve
    de nada: lo que hace el 35,8x es que xz vea las 288 paginas casi iguales en
    el MISMO flujo, y para eso tienen que entrar en claro.
    """
    creados = []
    if not source.is_dir():
        return creados
    por_dia: dict[str, list[Path]] = {}
    for fichero in source.rglob("*"):
        if not fichero.is_file() or fichero.suffix == ".xz":
            continue
        dia = _day_of(fichero.name)
        if dia and dia < today:            # solo dias TERMINADOS
            por_dia.setdefault(dia, []).append(fichero)

    for dia, ficheros in sorted(por_dia.items()):
        destino = destination / f"year={dia[:4]}" / f"month={dia[4:6]}" / f"{dia}.tar.xz"
        if destino.exists():
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        crudo = io.BytesIO()
        with tarfile.open(fileobj=crudo, mode="w") as tar:
            for fichero in sorted(ficheros):
                datos = fichero.read_bytes()
                nombre = fichero.name
                if nombre.endswith(".gz"):
                    datos, nombre = gzip.decompress(datos), nombre[:-3]
                info = tarfile.TarInfo(nombre)
                info.size = len(datos)
                tar.addfile(info, io.BytesIO(datos))
        destino.write_bytes(lzma.compress(crudo.getvalue(), preset=9))
        creados.append(destino)
    return creados


def collect(root: Path, repo: Path, *, today: str | None = None) -> dict[str, int]:
    """Deja el archivo al dia dentro del repo. Devuelve el recuento por bloque."""
    today = today or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    resumen: dict[str, int] = {}

    for almacen in MIRRORED:
        resumen[almacen] = len(mirror_tree(root / almacen, repo / almacen))

    raiz_raw = root / "raw"
    if raiz_raw.is_dir():
        for sitio in sorted(p for p in raiz_raw.iterdir() if p.is_dir()):
            for fuente in sorted(p for p in sitio.iterdir() if p.is_dir()):
                nombre = fuente.name.split("=", 1)[-1]
                relativo = fuente.relative_to(root)
                if nombre in SKIPPED:
                    continue
                if nombre in BUNDLED:
                    resumen["raw/" + nombre] = len(
                        bundle_days(fuente, repo / relativo, today=today))
                else:
                    resumen["raw/" + nombre] = len(
                        mirror_tree(fuente, repo / relativo))

    sueltos = 0
    for nombre in LOOSE:
        origen = root / nombre
        if origen.is_file():
            destino = repo / nombre
            if not destino.exists() or destino.read_bytes() != origen.read_bytes():
                shutil.copy2(origen, destino)
                sueltos += 1
    resumen["sueltos"] = sueltos
    return resumen


def push(root: Path | None = None, repo: Path | None = None, *,
         push_remote: bool = True, today: str | None = None) -> str | None:
    """Recoge y sube. Devuelve el commit, o None si no habia nada nuevo."""
    repo = repo_path(repo)
    if repo is None:
        return None
    resumen = collect(Path(root or DATA_ROOT), repo, today=today)

    _git(repo, "add", "-A")
    if _git(repo, "diff", "--cached", "--quiet").returncode == 0:
        return None
    nuevos = sum(resumen.values())
    _git(repo, "commit", "-m", f"Archivo al dia: {nuevos} ficheros nuevos\n\n" +
         "\n".join(f"  {k}: {v}" for k, v in sorted(resumen.items()) if v))
    if push_remote:
        subido = _git(repo, "push")
        if subido.returncode != 0:
            raise RuntimeError(f"archivado en local pero sin subir: {subido.stderr.strip()}")
    return _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=None)
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    repo = repo_path(args.repo)
    if repo is None:
        print(f"[archivepush] sin clon del archivo ({ENV_VAR} o {DEFAULT_REPO})")
        return
    commit = push(repo=repo, push_remote=not args.no_push)
    print(f"[archivepush] {commit}" if commit
          else "[archivepush] el archivo ya estaba al dia")


if __name__ == "__main__":
    main()
