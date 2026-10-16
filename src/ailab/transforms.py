"""Kind-specific variance-stabilising transforms (pandas/numpy only).

* ``count`` / ``rate`` / ``event``: ``log1p`` (handles exact zeros, inverse ``expm1``, output >= 0).
* ``proportion``: ``logit`` on the 0..1 scale after clipping to ``[EPS, 1 - EPS]``; the inverse is
  ``expit`` times the scale, so forecasts *and* interval ends are always inside ``[0, scale]``.

Models are fitted in the transformed space; backtest errors that define the prediction intervals
are measured there too, then both point and interval ends are mapped back. Backtest *metrics*
(MAE, MASE, comparison with the seasonal-naive baseline) are computed on the original scale.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EPS = 1e-4  # logit clip: a 0 % or 100 % week maps to +-9.2 instead of +-inf


@dataclass(frozen=True)
class Transform:
    name: str
    scale: float = 1.0

    def forward(self, y: np.ndarray | float) -> np.ndarray:
        a = np.asarray(y, dtype=float)
        if self.name == "logit":
            p = np.clip(a / self.scale, EPS, 1.0 - EPS)
            return np.log(p / (1.0 - p))
        if self.name == "log1p":
            return np.log1p(np.clip(a, 0.0, None))
        return a

    def inverse(self, z: np.ndarray | float) -> np.ndarray:
        a = np.asarray(z, dtype=float)
        if self.name == "logit":
            return self.scale / (1.0 + np.exp(-np.clip(a, -50, 50)))
        if self.name == "log1p":
            return np.clip(np.expm1(np.clip(a, None, 50.0)), 0.0, None)
        return a

    @property
    def bounds(self) -> tuple[float, float | None]:
        """Valid range of back-transformed values (``None`` = unbounded above)."""
        return (0.0, self.scale if self.name == "logit" else None)


def for_kind(indicator_type: str, value_scale: float = 1.0) -> Transform:
    if indicator_type == "proportion":
        if value_scale not in (1, 100):
            raise ValueError("proportion value_scale must be 1 or 100")
        return Transform("logit", float(value_scale))
    if indicator_type in ("count", "rate", "event"):
        return Transform("log1p")
    raise ValueError(f"unknown indicator_type {indicator_type!r}")
