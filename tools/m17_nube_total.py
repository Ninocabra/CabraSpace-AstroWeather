"""M17: is the ECMWF 0.25 deg total cloud cover the native TCC or a formula?

Read-only diagnostic over the private archive. It turns into MEASURED what
docs/m17_nube_total_ecmwf025.md can only call PLAUSIBLE without the data:

1. How often `cloud_total < max(low, mid, high)` per model (the 50 % / 2 %).
2. How many `ecmwf_ifs025` rows match Open-Meteo's weighted total
   `min(0.9*low + 0.6*mid + 0.3*high, 100)` -- the formula it used before it
   started storing ECMWF's own `tcc` (open-meteo PR #1587, 2025-11-21).
3. Whether the backfill (`ecmwf_ifs025_prev*`, total only) changes regime at
   that date: under the weighted formula a sky full of cirrus is 30 %, so
   totals near 100 % are rare; under the native TCC they are not.

Usage: python tools/m17_nube_total.py [--root data]   (from the repo root)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Open-Meteo stores ECMWF open-data `tcc` for runs from this date on; before it,
# `cloud_cover` of ecmwf_ifs025 was computed from its own low/mid/high layers.
NATIVE_TCC_SINCE = "2025-11-21"
# Archived cloud covers are rounded to whole percent: a total one point below
# its layer is rounding, not a different number.
ROUNDING = 1.0


def weighted_total(low: float, mid: float, high: float) -> float:
    """Open-Meteo's IFS 0.25 deg total before 2025-11-21 (Meteorology.swift)."""
    return min(low * 0.9 + mid * 0.6 + high * 0.3, 100.0)


def below_its_layers(total: float, low: float, mid: float, high: float,
                     tolerance: float = ROUNDING) -> bool:
    """Total cover under its cloudiest layer: impossible for one coherent field."""
    return total < max(low, mid, high) - tolerance


def incoherent_share(rows, tolerance: float = ROUNDING) -> float:
    """Share of (total, low, mid, high) rows whose total is below a layer."""
    rows = list(rows)
    if not rows:
        return float("nan")
    return sum(below_its_layers(*row, tolerance=tolerance) for row in rows) / len(rows)


def main(argv=None) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from astroweather.calibration import BACKFILL_PREFIX
    from astroweather.queries import connect

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    connection = connect(args.root)

    print("1) total < max(capas), por modelo")
    for model in ("ecmwf_ifs", "ecmwf_ifs025"):
        rows = connection.execute("""
            SELECT cloud_total, cloud_low, cloud_mid, cloud_high, run_utc
            FROM forecasts
            WHERE model = ? AND member IS NULL AND cloud_total IS NOT NULL
              AND cloud_low IS NOT NULL AND cloud_mid IS NOT NULL
              AND cloud_high IS NOT NULL
        """, [model]).fetchall()
        layers = [row[:4] for row in rows]
        print(f"   {model:14s} n={len(rows):6d}  incoherentes={incoherent_share(layers):.1%}")
        if model == "ecmwf_ifs025" and rows:
            formula = sum(abs(t - weighted_total(lo, mi, hi)) <= ROUNDING
                          for t, lo, mi, hi, _ in rows) / len(rows)
            print(f"2) {model}: total == min(0.9L+0.6M+0.3H,100) en {formula:.1%} "
                  "(cerca de 100 % = formula; bajo = TCC nativa)")

    print(f"3) retroanalisis {BACKFILL_PREFIX}*: antes/despues de {NATIVE_TCC_SINCE}")
    for label, condition in (("antes", "<"), ("despues", ">=")):
        n, overcast, mean = connection.execute(f"""
            SELECT count(*), avg(CASE WHEN cloud_total >= 99 THEN 1 ELSE 0 END),
                   avg(cloud_total)
            FROM forecasts
            WHERE starts_with(model, ?) AND member IS NULL
              AND cloud_total IS NOT NULL
              AND run_utc {condition} TIMESTAMP '{NATIVE_TCC_SINCE}'
        """, [BACKFILL_PREFIX]).fetchone()
        if n:
            print(f"   {label:8s} n={n:6d}  total>=99%={overcast:.1%}  media={mean:.1f}")


if __name__ == "__main__":
    main()
