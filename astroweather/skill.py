"""La vara de medir, en UN sitio: Brier, AUC y el remuestreo por noches.

POR QUE EXISTE (26-10-2026). Estas funciones vivian dentro de `hindcast.py`, que
corre a mano en el escritorio. Desde que el producto se puntua solo cada manana
(`nightscore.py`) y una curva nueva tiene que GANAR para entrar
(`curvecontest.py`), las tres piezas tienen que medir con la misma vara: si el
colector usara un Brier propio y el retroanalisis otro, el dia que discreparan
no sabriamos cual de los dos miente. Se sacaron tal cual, sin cambiar una
linea de calculo; `hindcast.py` las importa de aqui y sus cifras no se mueven
(comprobado con `--resamples 50` antes y despues de mover).

Solo numpy: el colector corre en un runner que se paga por minuto y no instala
nada que no use (`requirements-colector.txt`).
"""

from __future__ import annotations

import numpy as np

RESAMPLES = 2000
SEED = 20261026


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -60, 60)))


def brier(p, o) -> float:
    p, o = np.asarray(p, float), np.asarray(o, float)
    return float(np.mean((p - o) ** 2)) if len(o) else float("nan")


def auc(p, o) -> float:
    from .scoreboard import _auc
    value = _auc(np.asarray(p, float), np.asarray(o, float))
    return float("nan") if value is None else value


def bss(p, o, ref) -> float:
    b_ref = brier(ref, o)
    return 1.0 - brier(p, o) / b_ref if b_ref > 0 else float("nan")


class NightBootstrap:
    """Remuestreo de noches enteras, el mismo para todas las metricas de un
    bloque: asi las diferencias entre dos previsiones son PAREADAS."""

    def __init__(self, nights, resamples: int = RESAMPLES, seed: int = SEED):
        nights = np.asarray(nights)
        self.unique = np.unique(nights)
        groups = [np.flatnonzero(nights == u) for u in self.unique]
        rng = np.random.default_rng(seed)
        self.everything = np.arange(len(nights))
        self.draws = []
        for _ in range(resamples):
            pick = rng.integers(0, len(groups), len(groups))
            self.draws.append(np.concatenate([groups[i] for i in pick])
                              if len(groups) else self.everything)

    @property
    def n_nights(self) -> int:
        return len(self.unique)

    def ci(self, fn) -> tuple[float, float, float]:
        return self.interval(fn, 2.5, 97.5)

    def interval(self, fn, low: float, high: float) -> tuple[float, float, float]:
        """El punto y los percentiles `low`-`high` de `fn` sobre los remuestreos."""
        point = fn(self.everything)
        values = np.array([fn(idx) for idx in self.draws], float)
        values = values[np.isfinite(values)]
        if not len(values):
            return point, float("nan"), float("nan")
        lo, hi = np.percentile(values, [low, high])
        return float(point), float(lo), float(hi)
