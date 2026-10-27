"""Los terciles del seeing, de la distribucion PROPIA del modelo (M9).

`seeing.py` publicaba el seeing como tercil con dos cortes fijos, 1,35" y 1,90",
que el comentario decia sacados "de la distribucion del modelo sobre el
archivo". Ya no lo eran: medido el 27-10-2026 sobre 63 noches (26-08 a 27-10),
el seeing previsto va de 0,91" a 1,49" porque lo domina la constante de capa
limite (0,90" con jet nulo), y esos cortes piden un jet de ~43 y ~63 m/s que no
hubo ninguna noche (p90 = 33 m/s). Resultado: "favorable" 58 de 58 noches.

Decision de Nino (27-10-2026, opcion A): terciles propios sobre el ULTIMO ANIO,
recalculados solos. Cada noche se resume como el seeing de la mediana del jet de
sus horas de noche (lo mismo que hace `queries.night_inputs` para la cupula) y
los cortes son los percentiles 33,3 y 66,7 de esas noches. Una vez por semana
(`scheduler`, trabajo `seeing_terciles`), y se guarda en `seeing_terciles.json`,
que viaja al archivo como el resto del estado (`archivepush.LOOSE`).

Lo que esto NO es: una medida contra el cielo. Sin FWHM local el tercil solo
ordena las noches por el chorro; entre los dos cortes de hoy hay 0,06", muy por
debajo de la banda de +-25 % que se publica al lado. Con menos de
`MIN_NIGHTS` noches no se escribe nada y `seeing.py` se queda con los cortes
fijos de respaldo.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np

UTC = dt.timezone.utc
TERCILES_FILE = "seeing_terciles.json"
WINDOW_DAYS = 365
MIN_NIGHTS = 30
# Horas de noche, en UTC, para resumir cada noche: de 19 a 05 UTC, la misma
# franja con la que se midio M9. No hace falta mas finura: la mediana de diez
# horas de jet apenas se mueve por una hora de crepusculo de mas o de menos.
NIGHT_HOURS = (19, 20, 21, 22, 23, 0, 1, 2, 3, 4, 5)
VERSION = "seeingterciles/1"


def nightly_jets(root: Path, now: dt.datetime) -> dict[dt.date, float]:
    """La mediana del jet de cada noche del ultimo anio (la pasada mas reciente
    de cada hora, como `queries.night_series`)."""
    # Directo sobre los Parquet de `forecasts/` y no por `queries.connect`, que
    # monta todas las vistas del archivo: aqui solo hacen falta los niveles altos.
    import duckdb
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    files = (Path(root) / "forecasts" / "**" / "*.parquet").as_posix()
    rows = con.execute(f"""
        SELECT valid_utc, greatest(coalesce(v200, 0), coalesce(v250, 0),
                                   coalesce(v300, 0)) AS jet
        FROM read_parquet('{files}', union_by_name=true)
        WHERE site_id = 'nerpio' AND valid_utc BETWEEN ? AND ?
          AND (v200 IS NOT NULL OR v250 IS NOT NULL OR v300 IS NOT NULL)
        QUALIFY row_number() OVER (PARTITION BY valid_utc
                                   ORDER BY run_utc DESC, retrieved_at DESC) = 1
    """, [now - dt.timedelta(days=WINDOW_DAYS), now]).fetchall()
    by_night: dict[dt.date, list[float]] = {}
    for valid, jet in rows:
        valid = valid.astimezone(UTC)
        if not jet or valid.hour not in NIGHT_HOURS:
            continue
        by_night.setdefault((valid - dt.timedelta(hours=12)).date(), []).append(float(jet))
    return {n: float(np.median(v)) for n, v in by_night.items() if len(v) >= 6}


def fit(root: Path, now: dt.datetime | None = None) -> dict | None:
    """Los dos cortes, o None si no hay noches bastantes."""
    from .seeing import integrate
    now = now or dt.datetime.now(UTC)
    jets = nightly_jets(root, now)
    if len(jets) < MIN_NIGHTS:
        return None
    nights = sorted(jets)
    seeing = np.array([integrate(jets[n]).seeing_arcsec for n in nights])
    good, poor = np.percentile(seeing, [100 / 3, 200 / 3])
    if not good < poor:
        return None
    return {"good": round(float(good), 3), "poor": round(float(poor), 3),
            "nights": len(nights), "from": nights[0].isoformat(),
            "to": nights[-1].isoformat(),
            "seeing_min": round(float(seeing.min()), 3),
            "seeing_max": round(float(seeing.max()), 3),
            "fitted_at": now.replace(microsecond=0).isoformat(),
            "method": f"p33/p67 del seeing de la mediana nocturna del jet, ultimos {WINDOW_DAYS} dias",
            "version": VERSION}


def save(record: dict, root: Path | None = None) -> Path:
    from .archive import DATA_ROOT
    path = Path(root or DATA_ROOT) / TERCILES_FILE
    path.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def refit(root: Path | None = None, now: dt.datetime | None = None) -> str:
    """El trabajo semanal: ajusta, guarda y lo dice en una linea."""
    from .archive import DATA_ROOT
    from . import seeing
    root = Path(root or DATA_ROOT)
    record = fit(root, now)
    if record is None:
        return f"menos de {MIN_NIGHTS} noches con jet: se quedan los cortes de respaldo"
    save(record, root)
    seeing._current_terciles_from_file.cache_clear()
    return (f"terciles {record['good']:.2f}\" / {record['poor']:.2f}\" sobre "
            f"{record['nights']} noches ({record['from']} a {record['to']})")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=None)
    args = ap.parse_args(argv)
    print(refit(args.root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
