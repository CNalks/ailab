"""Pydantic models: series metadata, policy, alert rules. These define the public JSON contract."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

IndicatorType = Literal["count", "proportion", "rate", "event"]
DisplayMode = Literal["forecast_band", "forecast_point_only", "monitor_only", "history_only"]
ModelFamily = Literal["mstl_ets", "theta", "naive", "seasonal_naive", "none"]
AlertMethod = Literal["ears_c1", "ears_c2", "ears_c3", "cusum"]

SERIES_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")


class SeriesMeta(BaseModel):
    series_id: str
    name: str
    disease: str = ""
    geography: str = ""
    frequency: Literal["W"] = "W"  # weekly (week starts Sunday) in v0.1
    unit: str = ""
    indicator_type: IndicatorType = "count"
    season_length: int = Field(default=52, ge=2, le=60)
    source_id: str = ""
    licence: str = ""
    attribution: str = ""
    notice: str = ""
    retrieved_at: str = ""
    sha256: str = ""
    derived_expr: str | None = None
    derived_from: list[str] = Field(default_factory=list)

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
