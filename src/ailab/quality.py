"""Transparent data-quality checklist (not a black-box score). Drives the forecast policy."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import SeriesMeta

MAX_INTERP_GAP = 2  # model input may interpolate at most this many consecutive missing weeks


def regularise(df: pd.DataFrame) -> pd.DataFrame:
    """Reindex to a complete weekly grid (week start = Sunday). Gaps become NaN, never 0."""
    if df.empty:
        return df.copy()
    idx = pd.date_range(df["ds"].min(), df["ds"].max(), freq="7D")
    s = df.set_index("ds")["y"].reindex(idx)
    return pd.DataFrame({"ds": idx, "y": s.to_numpy()})


def model_input(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Fit-ready series: short gaps interpolated (count returned), long gaps left as NaN."""
    g = regularise(df)
    y = g["y"].copy()
    before = int(y.isna().sum())
    y = y.interpolate(limit=MAX_INTERP_GAP, limit_area="inside")
    g["y"] = y
    return g, before - int(y.isna().sum())


def apply_mask(g: pd.DataFrame, periods: list[list[str]], season_length: int):
    """Replace masked weeks by the median of the same week in other seasons (+-1..4 seasons,
    unmasked values only); weeks without such a value are left NaN (then interpolated by the
    caller). Returns ``(frame, boolean array of masked weeks)``."""
    y = g["y"].to_numpy(dtype=float).copy()
    masked = np.zeros(len(y), dtype=bool)
    ds = g["ds"]
    for a, b in periods:
        masked |= ((ds >= pd.Timestamp(a)) & (ds <= pd.Timestamp(b))).to_numpy()
    if masked.any():
        src = y.copy()
        src[masked] = np.nan
        for i in np.flatnonzero(masked):
            cand = [
                src[j]
                for k in range(1, 5)
                for j in (i - k * season_length, i + k * season_length)
                if 0 <= j < len(y) and not np.isnan(src[j])
            ]
            y[i] = float(np.median(cand)) if cand else np.nan
    out = g.copy()
    out["y"] = y
    return out, masked


def model_frame(
    df: pd.DataFrame, meta: SeriesMeta, trim_tail: int = 0, max_gap: int = MAX_INTERP_GAP
) -> tuple[pd.DataFrame, np.ndarray, dict]:
    """Fit-ready frame for forecasting: mask filled, short gaps interpolated, leading/trailing
    missing periods and (optionally) a provisional tail trimmed. Returns
    ``(frame, masked_flags_aligned_to_frame, info)``."""
    g = regularise(df)
    g, masked = apply_mask(g, meta.mask_periods, meta.season_length)
    y = g["y"].interpolate(limit=max_gap, limit_area="inside")
    g = g.assign(y=y)
    info = {"trimmed_leading": 0, "trimmed_trailing": 0, "trimmed_provisional": 0}
    ok = ~g["y"].isna().to_numpy()
    if ok.any():
        first, last = int(np.argmax(ok)), len(ok) - 1 - int(np.argmax(ok[::-1]))
        info["trimmed_leading"], info["trimmed_trailing"] = first, len(ok) - 1 - last
        g, masked = g.iloc[first : last + 1], masked[first : last + 1]
    if trim_tail > 0 and len(g) > trim_tail + 1:
        g, masked = g.iloc[:-trim_tail], masked[:-trim_tail]
        info["trimmed_provisional"] = trim_tail
    return g.reset_index(drop=True), masked, info


def check_series(
    meta: SeriesMeta, df: pd.DataFrame, today: pd.Timestamp | None = None, cfg=None
) -> dict:
    """Checklist + grade. ``cfg`` (a ``gate.Thresholds``) overrides the blocking thresholds;
    default thresholds are used when omitted."""
    from .gate import Thresholds

    cfg = cfg or Thresholds()
    max_gap = cfg.max_gap
    g = regularise(df)
    n = len(g)
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, severity: str = "warn") -> None:
        checks.append({"check": name, "ok": bool(ok), "severity": severity, "detail": detail})

    missing = int(g["y"].isna().sum()) if n else 0
    seasons = n / meta.season_length if n else 0.0
    add(
        "length",
        seasons >= cfg.min_seasons_block,
        f"{n} weeks = {seasons:.1f} seasons (need >= {cfg.min_seasons_block:g})",
        "fail",
    )
    add("completeness", missing == 0, f"{missing} missing of {n} weeks ({_pct(missing, n)}%)")
    # longest run of consecutive missing weeks
    run = longest = 0
    for v in g["y"].isna():
        run = run + 1 if v else 0
        longest = max(longest, run)
    add("gaps", longest <= max_gap, f"longest gap {longest} weeks", "fail")
    vals = g["y"].dropna()
    nz = float((vals > 0).mean()) if len(vals) else 0.0
    add(
        "non_zero_share", nz >= cfg.min_nonzero_block, f"{nz:.0%} of observed weeks are > 0", "fail"
    )
    # implicit zero-fill: long runs of exact zeros inside a series that is otherwise positive
    zrun = zl = 0
    for v in g["y"]:
        zrun = zrun + 1 if v == 0 else 0
        zl = max(zl, zrun)
    add(
        "zero_runs",
        zl < cfg.zero_run_warn or nz < cfg.min_nonzero_block,
        f"longest run of exact zeros {zl} weeks",
    )
    add("negative_values", bool((vals >= 0).all()), "no negative values", "fail")
    if today is not None and n:
        lag = int((today.normalize() - g["ds"].iloc[-1]).days // 7)
        add("timeliness", lag <= 4, f"latest period is {lag} weeks old")
    fails = [c for c in checks if not c["ok"] and c["severity"] == "fail"]
    warns = [c for c in checks if not c["ok"] and c["severity"] == "warn"]
    grade = "C" if fails else ("B" if warns else "A")
    return {
        "grade": grade,
        "n_weeks": n,
        "n_missing": missing,
        "data_from": None if not n else g["ds"].iloc[0].date().isoformat(),
        "data_through": None if not n else g["ds"].iloc[-1].date().isoformat(),
        "checks": checks,
        "reasons": [f"{c['check']}: {c['detail']}" for c in fails + warns],
    }


def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}" if b else "0"
