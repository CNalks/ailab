"""Service configuration from environment variables (nothing is read from files)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MIN_API_KEY_LEN = 16


class ConfigError(ValueError):
    pass


def _int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None
    if not lo <= v <= hi:
        raise ConfigError(f"{name} must be in {lo}..{hi}")
    return v


@dataclass(frozen=True)
class Settings:
    db_path: str = ":memory:"
    out_dir: Path = Path("out")
    api_key: str | None = None  # None => read-only service
    preload_demo: bool = False
    max_body_bytes: int = 2 * 1024 * 1024
    max_observations: int = 5200  # per series (100 years of weeks)
    max_series: int = 500
    cors_origins: tuple[str, ...] = field(default_factory=tuple)
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if self.api_key is not None and len(self.api_key) < MIN_API_KEY_LEN:
            raise ConfigError(
                f"AILAB_API_KEY must be at least {MIN_API_KEY_LEN} characters "
                "(e.g. `openssl rand -hex 24`)"
            )

    @classmethod
    def from_env(cls, **overrides) -> Settings:
        env = os.environ
        origins = tuple(
            o.strip() for o in env.get("AILAB_CORS_ORIGINS", "").split(",") if o.strip()
        )
        base = {
            "db_path": env.get("AILAB_DB") or ":memory:",
            "out_dir": Path(env.get("AILAB_OUT", "out")),
            "api_key": env.get("AILAB_API_KEY") or None,
            "preload_demo": env.get("AILAB_PRELOAD_DEMO", "") in ("1", "true", "yes"),
            "max_body_bytes": _int("AILAB_MAX_BODY_BYTES", 2 * 1024 * 1024, 1024, 64 * 1024 * 1024),
            "max_observations": _int("AILAB_MAX_OBSERVATIONS", 5200, 10, 100_000),
            "max_series": _int("AILAB_MAX_SERIES", 500, 1, 100_000),
            "cors_origins": origins,
            "log_level": env.get("AILAB_LOG_LEVEL", "INFO").upper(),
        }
        base.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**base)
