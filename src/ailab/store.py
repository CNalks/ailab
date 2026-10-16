"""Embedded series store: DuckDB (single file or in-memory) with Parquet import/export.

No server, no Postgres/Redis. Missing observations are stored as NULL and are never zero-filled.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import duckdb
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
  rule_json VARCHAR NOT NULL
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
        """Replace a series (metadata + observations). ``data`` has columns ``ds`` and ``y``."""
        df = _clean_frame(data)
        with self._lock:
            self._con.execute("BEGIN")
            try:
                self._con.execute("DELETE FROM observations WHERE series_id = ?", [meta.series_id])
                self._con.execute("DELETE FROM series WHERE series_id = ?", [meta.series_id])
                self._con.execute(
                    "INSERT INTO series VALUES (?, ?)", [meta.series_id, meta.model_dump_json()]
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

    def list_series(self) -> list[SeriesMeta]:
        with self._lock:
            rows = self._con.execute("SELECT meta_json FROM series ORDER BY series_id").fetchall()
        return [SeriesMeta.model_validate_json(r[0]) for r in rows]

    def get_observations(
        self, series_id: str, start: str | None = None, end: str | None = None
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
        with self._lock:
            df = self._con.execute(q, args).df()
        df["ds"] = pd.to_datetime(df["ds"])
        return df

    def delete_series(self, series_id: str) -> None:
        self.get_meta(series_id)
        with self._lock:
            self._con.execute("DELETE FROM observations WHERE series_id = ?", [series_id])
            self._con.execute("DELETE FROM series WHERE series_id = ?", [series_id])
            self._con.execute(
                "DELETE FROM alert_rules WHERE rule_json LIKE ?", [f'%"series_id":"{series_id}"%']
            )

    def count_series(self) -> int:
        with self._lock:
            return self._con.execute("SELECT count(*) FROM series").fetchone()[0]

    # ---- alert rules --------------------------------------------------------------------
    def upsert_rule(self, rule: AlertRule) -> None:
        self.get_meta(rule.series_id)
        with self._lock:
            self._con.execute("DELETE FROM alert_rules WHERE rule_id = ?", [rule.rule_id])
            self._con.execute(
                "INSERT INTO alert_rules VALUES (?, ?)",
                [rule.rule_id, rule.model_dump_json()],
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


def _clean_frame(data: pd.DataFrame) -> pd.DataFrame:
    if not {"ds", "y"} <= set(data.columns):
        raise ValueError("data must have columns 'ds' and 'y'")
    df = data[["ds", "y"]].copy()
    df["ds"] = pd.to_datetime(df["ds"]).dt.normalize()
    df["y"] = pd.to_numeric(df["y"], errors="coerce")
    if df["ds"].duplicated().any():
        raise ValueError("duplicate period_start values")
    return df.sort_values("ds").reset_index(drop=True)
