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
import json
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
    # Decidido por Nino el 17-09-2026: de WeatherNext sube solo lo canonico.
    "weathernext_ee": "39 MB del backfill ene-sep 2026 recuperables con `python -m "
                      "astroweather.workers.fetch_weathernext --init-time ...` mientras "
                      "Earth Engine los sirva; sus nubes ya viajan en forecasts/, y si "
                      "Google revoca el acceso hay que borrar cada copia del dato",
}

# Canonico que no sube pasada a pasada sino UN FICHERO POR MES CERRADO
# (decidido por Nino el 17-09-2026). WeatherNext escribe 864 filas por pasada
# con decimales de sobra: 50 kB sueltos, 25,6 MB el backfill de ene-sep 2026.
# Redondeado en el worker y juntado por mes, marzo pasa de 3.043 kB a 443 kB y
# el backfill a 3,6 MB, con el AUC del examen igual hasta el cuarto decimal.
# Las pasadas sueltas se quedan en este disco; el mes en curso no viaja hasta
# cerrarse, igual que un dia de scrapes sin terminar (`bundle_days`).
MONTHLY_TAGS = ("weathernext3",)

# Ficheros sueltos de `data/` que son estado del producto y valen su peso.
#
# `sky_floor.json` entro el 30-08-2026 y faltaba: es el suelo del cielo medido
# cada dia -- la constante de siete anios se jubilo el 29-08 por promediar una
# deriva -- y sin viajar aqui, una maquina que empiece de cero se cae al
# respaldo y el recomendador puntua contra un cielo que no es el de esta epoca.
# `estado_colector.json` entro el mismo dia y es lo que hace posible que la
# recogida viva en un runner que se destruye: sin el, cada pasada creeria que
# no ha corrido nunca nada.
# Lo que el repo del archivo NO debe versionar, escrito por `collect` para que
# no dependa de que alguien se acuerde de crearlo a mano.
#
# EN EL RUNNER `data/forecasts` ES UN ENLACE al clon del archivo (workflow
# `colector.yml`), asi que una pasada de WeatherNext no se "copia" al repo: nace
# dentro. Sin esto, el `git add -A` de `push` subiria las dos pasadas diarias
# una a una -- justo lo que se decidio el 17-09-2026 que no pasara -- y la
# agrupacion mensual no llegaria a actuar. Las sueltas sobreviven entre pasadas
# en la cache del runner, como el raw del dia; si se pierde la cache, se vuelven
# a pedir a Earth Engine, que es donde siguen estando.
IGNORADOS = (
    "# Escrito por astroweather.archivepush: las pasadas sueltas de WeatherNext",
    "# viajan agrupadas por mes cerrado, no una a una.",
    "forecasts/**/*_weathernext3.parquet",
)

ESTADO = "estado_colector.json"

LOOSE = ("cloud_calibration.json", "cloud_calibration_weathernext3.json",
         "roof_calibration.json", "open_hours_nerpio.json",
         "roof_climatology.json", "sky_floor.json", "estado_colector.json")


def repo_path(repo: Path | None = None) -> Path | None:
    """El clon del archivo, o None si esta maquina no lo tiene."""
    if repo is not None:
        return repo if (repo / ".git").is_dir() else None
    candidate = Path(os.environ[ENV_VAR]) if os.environ.get(ENV_VAR) else DEFAULT_REPO
    return candidate if (candidate / ".git").is_dir() else None


# Sin ventana de consola. `pythonw.exe` no tiene consola, asi que Windows le
# CREA una a cada proceso de consola que arranque: con el scheduler local en
# marcha, cada pasada abria un parpadeo de ventanas de git cada diez minutos, y
# el 31-08-2026 acabo siendo lo primero que Nino pregunto al ver la pantalla.
# `getattr` y no `subprocess.CREATE_NO_WINDOW` a secas: la bandera no existe
# fuera de Windows, y esto corre tambien en el runner de Actions, que es Linux.
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(("git", "-C", str(repo)) + args,
                          creationflags=SIN_VENTANA,
                          capture_output=True, text=True, check=False)


def _day_of(name: str) -> str | None:
    """El dia que lleva el nombre del fichero, `YYYYMMDDTHHMMSSZ_hash.ext`."""
    return name[:8] if len(name) > 8 and name[:8].isdigit() else None


def _per_run_tag(name: str) -> str | None:
    """La etiqueta mensual de un fichero por pasada, o None si no lleva."""
    for tag in MONTHLY_TAGS:
        if name.endswith(f"_{tag}.parquet"):
            return tag
    return None


def mirror_tree(source: Path, destination: Path) -> list[Path]:
    """Copia lo que falte. Los nombres son unicos, asi que falta == no existe."""
    copiados = []
    if not source.is_dir():
        return copiados
    for origen in sorted(source.rglob("*")):
        if not origen.is_file() or _per_run_tag(origen.name):
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


def bundle_months(source: Path, destination: Path, *, today: str) -> list[Path]:
    """Un Parquet por mes TERMINADO con las pasadas sueltas de cada etiqueta.

    El mes es el de la particion (`year=/month=`), que `write_canonical` pone
    por la primera hora valida de la pasada. Filas ordenadas por modelo, pasada
    y plazo, y zstd alto: juntas y en orden es donde se comprime.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    creados = []
    if not source.is_dir():
        return creados
    grupos: dict[tuple[Path, str], list[Path]] = {}
    for fichero in source.rglob("*.parquet"):
        tag = _per_run_tag(fichero.name)
        if tag is None:
            continue
        particion = fichero.parent.relative_to(source)
        partes = dict(p.split("=", 1) for p in particion.parts if "=" in p)
        mes = partes.get("year", "") + partes.get("month", "")
        if len(mes) == 6 and mes < today[:6]:          # solo meses TERMINADOS
            grupos.setdefault((particion, tag), []).append(fichero)

    for (particion, tag), ficheros in sorted(grupos.items()):
        partes = dict(p.split("=", 1) for p in particion.parts if "=" in p)
        destino = destination / particion / f"{partes['year']}{partes['month']}_{tag}_month.parquet"
        if destino.exists():
            continue
        tabla = pa.concat_tables([pq.read_table(f) for f in sorted(ficheros)],
                                 promote_options="default")
        tabla = tabla.sort_by([("model", "ascending"), ("run_utc", "ascending"),
                               ("lead_h", "ascending")])
        destino.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(tabla, destino, compression="zstd", compression_level=19)
        creados.append(destino)
    return creados


def fundir_estado(origen: Path, destino: Path) -> bool:
    """El estado del colector es de DOS maquinas: se funde, no se sustituye.

    Cada una corre unos trabajos (el runner casi todos, el escritorio el de
    WeatherNext mientras Google no de acceso a la cuenta de servicio), y cada
    una escribe solo lo suyo. Copiar el fichero entero borraba las fechas de la
    otra, y un trabajo sin fecha esta VENCIDO: el runner rehacia la pasada
    completa. Se queda la marca mas reciente de cada trabajo.
    """
    nuevo = json.loads(origen.read_text(encoding="utf-8"))
    if destino.exists():
        viejo = json.loads(destino.read_text(encoding="utf-8"))
    else:
        viejo = {}
    trabajos = dict(viejo.get("trabajos", {}))
    for nombre, marca in nuevo.get("trabajos", {}).items():
        if nombre not in trabajos or marca > trabajos[nombre]:
            trabajos[nombre] = marca
    fundido = {
        "escrito_utc": max(filter(None, (viejo.get("escrito_utc"),
                                         nuevo.get("escrito_utc"))), default=""),
        "trabajos": dict(sorted(trabajos.items())),
    }
    texto = json.dumps(fundido, indent=2) + chr(10)
    if destino.exists() and destino.read_text(encoding="utf-8") == texto:
        return False
    destino.write_text(texto, encoding="utf-8")
    return True


def asegurar_gitignore(repo: Path) -> bool:
    """Deja las reglas de `IGNORADOS` en el .gitignore del archivo."""
    destino = repo / ".gitignore"
    lineas = destino.read_text(encoding="utf-8").splitlines() if destino.exists() else []
    faltan = [linea for linea in IGNORADOS if linea not in lineas]
    if not faltan:
        return False
    destino.write_text(chr(10).join(lineas + faltan) + chr(10), encoding="utf-8")
    return True


def collect(root: Path, repo: Path, *, today: str | None = None) -> dict[str, int]:
    """Deja el archivo al dia dentro del repo. Devuelve el recuento por bloque."""
    today = today or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    resumen: dict[str, int] = {}
    asegurar_gitignore(repo)

    for almacen in MIRRORED:
        resumen[almacen] = len(mirror_tree(root / almacen, repo / almacen))
    resumen["forecasts/mensual"] = len(
        bundle_months(root / "forecasts", repo / "forecasts", today=today))

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
        if not origen.is_file():
            continue
        destino = repo / nombre
        if nombre == ESTADO:
            sueltos += int(fundir_estado(origen, destino))
            continue
        # UNA MAQUINA VIEJA NO PISA A LA QUE RECOGE. El 17-09-2026 este PC,
        # que llevaba desde el 31-08 sin recoger, copio su `open_hours` encima
        # del que el runner venia manteniendo: 258 -> 254, 155 -> null, el
        # total de 1635 a 1476. Lo canonico es de solo-anadir y no puede
        # perderse asi; estos sueltos SE REESCRIBEN, y por eso son justo los
        # que hay que proteger. Se compara la fecha del fichero: en el runner
        # el del repo se acaba de clonar y el de `data/` lo reescriben los
        # workers despues, asi que sube; en un PC atrasado, no.
        if destino.exists() and destino.stat().st_mtime > origen.stat().st_mtime:
            continue
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
        # Alguien pudo empujar mientras corriamos -- otra pasada del colector, o
        # este PC. Rebase antes de empujar: sin esto, dos recogidas seguidas se
        # pisan y la segunda muere con "non-fast-forward" dejando en el runner,
        # que se destruye, lo unico que la habia guardado.
        _git(repo, "pull", "--rebase", "--autostash")
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
