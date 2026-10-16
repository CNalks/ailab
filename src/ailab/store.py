"""Embedded series store: DuckDB (single file or in-memory) with Parquet import/export.

No server, no Postgres/Redis. Missing observations are stored as NULL and are never zero-filled.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from .schema import AlertRule, SeriesMeta

_SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
  series_id VARCHAR PRIMARY KEY,
  meta_json VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
  series_id VARCHAR NOT NULL,
  period_start DATE NOT NULL,
  value DOUBLE,
  PRIMARY KEY (series_id, period_start)
);
CREATE TABLE IF NOT EXISTS alert_rules (
  rule_id VARCHAR PRIMARY KEY,
  series_id VARCHAR NOT NULL,
  rule_json VARCHAR NOT NULL
);
-- lineage edges of derived series: derived_id was computed from source_id
CREATE TABLE IF NOT EXISTS series_lineage (
  derived_id VARCHAR NOT NULL,
  source_id VARCHAR NOT NULL,
  PRIMARY KEY (derived_id, source_id)
);
"""


class SeriesStore:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(self.path)
        self._lock = threading.RLock()
        with self._lock:
            self._con.execute(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._con.close()

    # ---- series -------------------------------------------------------------------------
    def upsert_series(self, meta: SeriesMeta, data: pd.DataFrame) -> int:
        """Replace a series (metadata + observations). ``data`` has columns ``ds`` and ``y``.

        Validates the values against the series kind (count >= 0, proportion within
        ``[0, value_scale]``) and that all dates are on one weekly grid.
        """
        df = _clean_frame(data)
        validate_values(meta, df)
        if not meta.created_at:
            meta = meta.model_copy(
                update={"created_at": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")}
            )
        with self._lock:
            self._con.execute("BEGIN")
            try:
                self._con.execute("DELETE FROM observations WHERE series_id = ?", [meta.series_id])
                self._con.execute("DELETE FROM series WHERE series_id = ?", [meta.series_id])
                self._con.execute(
                    "DELETE FROM series_lineage WHERE derived_id = ?", [meta.series_id]
                )
                self._con.execute(
                    "INSERT INTO series VALUES (?, ?)", [meta.series_id, meta.model_dump_json()]
                )
                for src in meta.derived_from:
                    self._con.execute(
                        "INSERT INTO series_lineage VALUES (?, ?)", [meta.series_id, src]
                    )
                if len(df):
                    rows = [
                        (meta.series_id, d.date(), None if pd.isna(v) else float(v))
                        for d, v in zip(df["ds"], df["y"], strict=True)
                    ]
                    self._con.executemany("INSERT INTO observations VALUES (?, ?, ?)", rows)
                self._con.execute("COMMIT")
            except Exception:
                self._con.execute("ROLLBACK")
                raise
        return len(df)

    def get_meta(self, series_id: str) -> SeriesMeta:
        with self._lock:
            row = self._con.execute(
                "SELECT meta_json FROM series WHERE series_id = ?", [series_id]
            ).fetchone()
        if row is None:
            raise KeyError(series_id)
        return SeriesMeta.model_validate_json(row[0])

    def list_series(
        self,
        limit: int | None = None,
        offset: int = 0,
        indicator_type: str | None = None,
        derived: bool | None = None,
    ) -> list[SeriesMeta]:
        return self.page_series(limit, offset, indicator_type, derived)[0]

    def page_series(
        self,
        limit: int | None = None,
        offset: int = 0,
        indicator_type: str | None = None,
        derived: bool | None = None,
    ) -> tuple[list[SeriesMeta], int]:
        """(page, total matching). Filters are applied before paging."""
        with self._lock:
            rows = self._con.execute("SELECT meta_json FROM series ORDER BY series_id").fetchall()
        metas = [SeriesMeta.model_validate_json(r[0]) for r in rows]
        if indicator_type is not None:
            metas = [m for m in metas if m.indicator_type == indicator_type]
        if derived is not None:
            metas = [m for m in metas if m.is_derived == derived]
        total = len(metas)
        return metas[offset : None if limit is None else offset + limit], total

    def dependents(self, series_id: str, transitive: bool = True) -> list[str]:
        """Ids of derived series computed (directly or transitively) from ``series_id``."""
        found: list[str] = []
        frontier = [series_id]
        while frontier:
            with self._lock:
                rows = self._con.execute(
                    "SELECT DISTINCT derived_id FROM series_lineage WHERE source_id IN ("
                    + ",".join("?" * len(frontier))
                    + ")",
                    frontier,
                ).fetchall()
            frontier = [r[0] for r in rows if r[0] not in found and r[0] != series_id]
            found.extend(frontier)
            if not transitive:
                break
        return sorted(found)

    def db_check(self) -> bool:
        """True if the database answers a trivial read (used by /health and /ready)."""
        with self._lock:
            return self._con.execute("SELECT 1").fetchone()[0] == 1

    def get_observations(
        self,
        series_id: str,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> pd.DataFrame:
        self.get_meta(series_id)  # raises KeyError if unknown
        q = "SELECT period_start AS ds, value AS y FROM observations WHERE series_id = ?"
        args: list[Any] = [series_id]
        if start:
            q += " AND period_start >= ?"
            args.append(start)
        if end:
            q += " AND period_start <= ?"
            args.append(end)
        q += " ORDER BY period_start"
        if limit is not None:
            q += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        with self._lock:
            df = self._con.execute(q, args).df()
        df["ds"] = pd.to_datetime(df["ds"])
        return df

    def count_observations(
        self, series_id: str, start: str | None = None, end: str | None = None
    ) -> int:
        q = "SELECT count(*) FROM observations WHERE series_id = ?"
        args: list[Any] = [series_id]
        if start:
            q += " AND period_start >= ?"
            args.append(start)
        if end:
            q += " AND period_start <= ?"
            args.append(end)
        with self._lock:
            return int(self._con.execute(q, args).fetchone()[0])

    def delete_series(self, series_id: str, cascade: bool = False) -> list[str]:
        """Delete a series. Refuses (``DependencyError``) if derived series depend on it, unless
        ``cascade`` is set, in which case they are deleted too. Returns all deleted ids."""
        self.get_meta(series_id)
        deps = self.dependents(series_id)
        if deps and not cascade:
            raise DependencyError(series_id, deps)
        ids = [*deps, series_id]
        with self._lock:
            self._con.execute("BEGIN")
            try:
                for sid in ids:
                    self._con.execute("DELETE FROM observations WHERE series_id = ?", [sid])
                    self._con.execute("DELETE FROM series WHERE series_id = ?", [sid])
                    self._con.execute("DELETE FROM alert_rules WHERE series_id = ?", [sid])
                    self._con.execute(
                        "DELETE FROM series_lineage WHERE derived_id = ? OR source_id = ?",
                        [sid, sid],
                    )
                self._con.execute("COMMIT")
            except Exception:
                self._con.execute("ROLLBACK")
                raise
        return ids

    def count_series(self) -> int:
        with self._lock:
            return self._con.execute("SELECT count(*) FROM series").fetchone()[0]

    # ---- alert rules --------------------------------------------------------------------
    def upsert_rule(self, rule: AlertRule) -> None:
        self.get_meta(rule.series_id)
        with self._lock:
            self._con.execute("DELETE FROM alert_rules WHERE rule_id = ?", [rule.rule_id])
            self._con.execute(
                "INSERT INTO alert_rules VALUES (?, ?, ?)",
                [rule.rule_id, rule.series_id, rule.model_dump_json()],
            )

    def list_rules(self, series_id: str | None = None) -> list[AlertRule]:
        with self._lock:
            rows = self._con.execute(
                "SELECT rule_json FROM alert_rules ORDER BY rule_id"
            ).fetchall()
        rules = [AlertRule.model_validate(json.loads(r[0])) for r in rows]
        return [r for r in rules if series_id is None or r.series_id == series_id]

    def delete_rule(self, rule_id: str) -> bool:
        with self._lock:
            n = self._con.execute(
                "SELECT count(*) FROM alert_rules WHERE rule_id = ?", [rule_id]
            ).fetchone()[0]
            self._con.execute("DELETE FROM alert_rules WHERE rule_id = ?", [rule_id])
        return bool(n)

    # ---- parquet ------------------------------------------------------------------------
    def export_parquet(self, series_id: str, path: str | Path) -> Path:
        df = self.get_observations(series_id)
        df = df.rename(columns={"ds": "period_start", "y": "value"})
        df.insert(0, "series_id", series_id)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        return Path(path)

    def import_parquet(self, meta: SeriesMeta, path: str | Path) -> int:
        df = pd.read_parquet(path).rename(columns={"period_start": "ds", "value": "y"})
        return self.upsert_series(meta, df[["ds", "y"]])


class DependencyError(RuntimeError):
    """Raised when deleting a series that derived series were computed from."""

    def __init__(self, series_id: str, dependents: list[str]):
        super().__init__(f"{series_id!r} is a source of derived series: {dependents}")
        self.series_id = series_id
        self.dependents = dependents


def _clean_frame(data: pd.DataFrame) -> pd.DataFrame:
    if not {"ds", "y"} <= set(data.columns):
        raise ValueError("data must have columns 'ds' and 'y'")
    df = data[["ds", "y"]].copy()
    try:
        df["ds"] = pd.to_datetime(df["ds"]).dt.normalize()
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid date in 'ds': {exc}") from None
    if df["ds"].isna().any():
        raise ValueError("'ds' must not contain missing dates")
    raw_y = df["y"]
    df["y"] = pd.to_numeric(raw_y, errors="coerce")
    # strings/garbage must not silently become "missing": only real nulls may be NaN
    if (df["y"].isna() & raw_y.notna()).any():
        raise ValueError("'y' must be numeric or null")
    if np.isinf(df["y"].to_numpy(dtype=float, na_value=np.nan)).any():
        raise ValueError("'y' must be finite or null")
    if df["ds"].duplicated().any():
        raise ValueError("duplicate period_start values")
    df = df.sort_values("ds").reset_index(drop=True)
    if len(df) > 1 and ((df["ds"] - df["ds"].iloc[0]).dt.days % 7 != 0).any():
        raise ValueError("all dates must lie on one weekly grid (same weekday, 7-day multiples)")
    return df


def validate_values(meta: SeriesMeta, df: pd.DataFrame) -> None:
    """Kind-specific range checks; raises ValueError."""
    v = df["y"].dropna()
    if meta.indicator_type in ("count", "event") and (v < 0).any():
        raise ValueError(f"{meta.indicator_type} series must be non-negative")
    if meta.indicator_type == "proportion" and ((v < 0) | (v > meta.value_scale)).any():
        raise ValueError(f"proportion values must lie in [0, {meta.value_scale}]")
