"""Pydantic models: series metadata, policy, alert rules. These define the public JSON contract."""

from __future__ import annotations

import datetime as dt
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Series kind (``indicator_type``). count: non-negative integer-like tallies (log1p transform);
# proportion: part/whole, bounded (logit transform, clipped to its scale); rate: generic
# non-negative real measure (log1p transform); event: sparse notifiable events (monitor only).
IndicatorType = Literal["count", "proportion", "rate", "event"]
KINDS: tuple[str, ...] = ("count", "proportion", "rate", "event")
DisplayMode = Literal["forecast_band", "forecast_point_only", "monitor_only", "history_only"]
ModelFamily = Literal["mstl_ets", "theta", "naive", "seasonal_naive", "none"]
AlertMethod = Literal["ears_c1", "ears_c2", "ears_c3", "cusum", "farrington"]

SERIES_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")


class SeriesMeta(BaseModel):
    series_id: str
    name: str
    disease: str = ""
    geography: str = ""
    frequency: Literal["W"] = "W"  # weekly (week starts Sunday) in v0.1
    unit: str = ""
    indicator_type: IndicatorType = "count"
    # Upper bound of a ``proportion`` series: 1 (fractions) or 100 (percent). Ignored otherwise.
    value_scale: Literal[1, 100] = 1
    season_length: int = Field(default=52, ge=2, le=60)
    source_id: str = ""
    licence: str = ""
    attribution: str = ""
    notice: str = ""
    retrieved_at: str = ""
    sha256: str = ""
    created_at: str = ""
    # --- lineage of derived (composite) series; empty for base series -------------------
    derived_expr: str | None = None
    derived_from: list[str] = Field(default_factory=list)  # source series ids (sorted)
    bindings: dict[str, str] = Field(default_factory=dict)  # alias used in expr -> series id
    lineage: dict | None = None  # kind-inference trail, alignment report, source fingerprints
    # --- forecast-quality options (v0.3) -----------------------------------------------------
    # per-series overrides of quality-gate thresholds, e.g. {"min_seasons_block": 4,
    # "reporting_lag_weeks": 2}; names/units in docs/MODEL_POLICY.md
    gate: dict = Field(default_factory=dict)
    # [["2020-03-01", "2021-08-29"], ...]: disruption periods (e.g. COVID-19) that are replaced by
    # same-week values of other seasons for fitting and excluded from backtest scoring / alerts
    mask_periods: list[list[str]] = Field(default_factory=list, max_length=20)

    @field_validator("name")
    @classmethod
    def _name_len(cls, v: str) -> str:
        if not v.strip() or len(v) > 200:
            raise ValueError("name must be 1..200 characters")
        return v

    @field_validator("gate")
    @classmethod
    def _valid_gate(cls, v: dict) -> dict:
        from .gate import GateConfigError, validate_series_overrides

        try:
            return validate_series_overrides(v)
        except GateConfigError as exc:
            raise ValueError(str(exc)) from None

    @field_validator("mask_periods")
    @classmethod
    def _valid_mask(cls, v: list[list[str]]) -> list[list[str]]:
        out = []
        for p in v:
            if len(p) != 2:
                raise ValueError("each mask period is [start, end] (YYYY-MM-DD)")
            try:
                a, b = dt.date.fromisoformat(p[0]), dt.date.fromisoformat(p[1])
            except ValueError:
                raise ValueError("mask period dates must be YYYY-MM-DD") from None
            if a > b:
                raise ValueError("mask period start must be <= end")
            out.append([a.isoformat(), b.isoformat()])
        return out

    @property
    def is_derived(self) -> bool:
        return self.derived_expr is not None

    @field_validator("series_id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not SERIES_ID_RE.match(v):
            raise ValueError("series_id must match ^[a-z0-9][a-z0-9_]{0,63}$")
        return v


class ForecastPolicy(BaseModel):
    """Contract carried by every forecast (names kept from the original project)."""

    forecast_allowed: bool
    reason: str
    display_mode: DisplayMode
    model_family: ModelFamily
    horizon_weeks: int = Field(ge=0)
    indicator_type: IndicatorType
    warnings: list[str] = Field(default_factory=list)
    # --- added in 0.3 (quality gate); older clients simply ignore them -------------------------
    gate_decision: Literal["allowed", "limited", "refused"] = "allowed"
    reason_codes: list[str] = Field(default_factory=list)  # machine-readable gate reasons
    limited: bool = False  # True: forecast_allowed but constrained (see gate.limits)


class AlertRule(BaseModel):
    rule_id: str
    series_id: str
    method: AlertMethod = "ears_c2"
    baseline: int = Field(default=7, ge=3, le=104)
    threshold: float | None = Field(default=None, gt=0)
    enabled: bool = True

    @field_validator("rule_id")
    @classmethod
    def _valid_rule_id(cls, v: str) -> str:
        if not SERIES_ID_RE.match(v):
            raise ValueError("rule_id must match ^[a-z0-9][a-z0-9_]{0,63}$")
        return v
