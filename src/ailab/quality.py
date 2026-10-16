"""Transparent data-quality checklist (not a black-box score). Drives the forecast policy."""

from __future__ import annotations

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


def check_series(meta: SeriesMeta, df: pd.DataFrame, today: pd.Timestamp | None = None) -> dict:
    g = regularise(df)
    n = len(g)
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, severity: str = "warn") -> None:
        checks.append({"check": name, "ok": bool(ok), "severity": severity, "detail": detail})

    missing = int(g["y"].isna().sum()) if n else 0
    seasons = n / meta.season_length if n else 0.0
    add("length", seasons >= 3, f"{n} weeks = {seasons:.1f} seasons (need >= 3)", "fail")
    add("completeness", missing == 0, f"{missing} missing of {n} weeks ({_pct(missing, n)}%)")
    # longest run of consecutive missing weeks
    run = longest = 0
    for v in g["y"].isna():
        run = run + 1 if v else 0
        longest = max(longest, run)
    add("gaps", longest <= MAX_INTERP_GAP, f"longest gap {longest} weeks", "fail")
    vals = g["y"].dropna()
    nz = float((vals > 0).mean()) if len(vals) else 0.0
    add("non_zero_share", nz >= 0.5, f"{nz:.0%} of observed weeks are > 0", "fail")
    # implicit zero-fill: long runs of exact zeros inside a series that is otherwise positive
    zrun = zl = 0
    for v in g["y"]:
        zrun = zrun + 1 if v == 0 else 0
        zl = max(zl, zrun)
    add("zero_runs", zl < 8 or nz < 0.5, f"longest run of exact zeros {zl} weeks")
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
