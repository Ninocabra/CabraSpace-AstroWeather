"""Raw + canonical archive (SPEC v8 sections 4.1 and 5).

Rule that governs this module: the raw response is never rewritten and never
replaced by a corrected value. Everything downstream is derived and can be
recomputed; the raw cannot.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

from .schema import SCHEMAS, CLAVES_OBLIGATORIAS, RANGOS

PIPELINE_VERSION = "0.2.0"   # 0.2.0: FWHM por ajuste gaussiano (0.1.0 usaba momentos, ~30% alto)

DATA_ROOT = Path(__file__).resolve().parent.parent / "data"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


# --- raw --------------------------------------------------------------------
def store_raw(
    payload: bytes | str | dict[str, Any] | list[Any],
    *,
    source: str,
    site_id: str,
    retrieved_at: datetime | None = None,
    suffix: str = "json",
    root: Path | None = None,
) -> Path:
    """Persist a provider response verbatim, gzipped, and return its path.

    Files are content-addressed by hash, so re-fetching the same payload does
    not duplicate it, and an existing file is never overwritten.
    """
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")

    retrieved_at = retrieved_at or utcnow()
    digest = hashlib.sha256(payload).hexdigest()[:16]
    base = root or DATA_ROOT
    directory = (
        base
        / "raw"
        / f"site={site_id}"
        / f"source={source}"
        / f"year={retrieved_at:%Y}"
        / f"month={retrieved_at:%m}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{retrieved_at:%Y%m%dT%H%M%SZ}_{digest}.{suffix}.gz"
    if not path.exists():
        with gzip.open(path, "wb") as handle:
            handle.write(payload)
    return path


# --- canonical --------------------------------------------------------------
def write_canonical(
    rows: Iterable[dict[str, Any]],
    *,
    dataset: str,
    site_id: str,
    root: Path | None = None,
) -> Path | None:
    """Append typed rows to the partitioned Parquet dataset.

    `dataset` is 'forecasts' or 'measurements'. Rows are validated against the
    schema on the way in: a typo in a column name fails here, not silently
    three months later when the model is trained.
    """
    rows = list(rows)
    if not rows:
        return None

    schema = SCHEMAS[dataset]
    known = set(schema.names)
    obligatorias = CLAVES_OBLIGATORIAS.get(dataset, ())
    valores = [n for n in schema.names
               if n not in obligatorias and n != "pipeline_version"]
    for indice, row in enumerate(rows):
        unknown = set(row) - known
        if unknown:
            raise ValueError(f"unknown columns for {dataset}: {sorted(unknown)}")

        # LA FRONTERA DE ESCRITURA EXIGE, NO SOLO DESCRIBE. Hasta el
        # 01-09-2026 aqui solo se rechazaba un nombre de columna inventado, y
        # una fila entera a nulos entraba sin una queja. Es el mismo fallo que
        # llevamos dos semanas persiguiendo aguas abajo -- la ausencia escrita
        # como si fuera dato -- pero en el sitio donde ya no se puede
        # distinguir: una vez en el Parquet, "no lo sabiamos" y "no lo habia"
        # son la misma fila.
        faltan = [c for c in obligatorias if row.get(c) is None]
        if faltan:
            raise ValueError(
                f"{dataset}[{indice}]: sin {faltan}, la fila no se puede "
                f"volver a encontrar; algo la ha construido a medias")

        if valores and all(row.get(c) is None for c in valores):
            raise ValueError(
                f"{dataset}[{indice}]: fila sin un solo valor. Escribirla "
                f"seria publicar 'no hay dato' con aspecto de medida")

        for columna, (minimo, maximo) in RANGOS.items():
            valor = row.get(columna)
            if valor is not None and not minimo <= float(valor) <= maximo:
                # Fuera de rango no es "raro", es imposible: casi siempre una
                # conversion de unidades que no se hizo (m/s contra km/h) o una
                # fraccion que alguien creia porcentaje.
                raise ValueError(
                    f"{dataset}[{indice}]: {columna}={valor} fuera de "
                    f"[{minimo}, {maximo}]; revisa las unidades de origen")

        row.setdefault("pipeline_version", PIPELINE_VERSION)

    table = pa.Table.from_pylist(
        [{name: row.get(name) for name in schema.names} for row in rows],
        schema=schema,
    )

    stamp = utcnow()
    base = root or DATA_ROOT
    time_col = {"forecasts": "valid_utc", "measurements": "t_utc"}.get(dataset, "date")
    first = rows[0].get(time_col) or stamp
    directory = (
        base
        / dataset
        / f"site={site_id}"
        / f"year={first:%Y}"
        / f"month={first:%m}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    # The name must be unique even for two writes within the same second
    # (surface and profile workers run back to back): without the content
    # digest the second write silently overwrote the first.
    models = sorted({str(row.get("model")) for row in rows})
    fingerprint = f"{dataset}|{models}|{len(rows)}|{first}"
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:8]
    path = directory / f"{stamp:%Y%m%dT%H%M%SZ}_{digest}.parquet"
    counter = 0
    while path.exists():
        counter += 1
        path = directory / f"{stamp:%Y%m%dT%H%M%SZ}_{digest}_{counter}.parquet"
    pq.write_table(table, path, compression="zstd")
    return path
