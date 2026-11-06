"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("OWT_DATA_DIR", "./data")).resolve())
    user_agent: str = os.environ.get("OWT_USER_AGENT", "OffsetWellWebtool/0.1 (+public records research)")
    request_interval_s: float = _env_float("OWT_REQUEST_INTERVAL_S", 1.0)
    # Model for survey page classification and table extraction.
    vision_model: str = os.environ.get("OWT_VISION_MODEL", "claude-opus-5-5")
    classify_model: str = os.environ.get("OWT_CLASSIFY_MODEL", "claude-opus-5-5")
    classify_effort: str = os.environ.get("OWT_CLASSIFY_EFFORT", "low")
    extract_effort: str = os.environ.get("OWT_EXTRACT_EFFORT", "high")
    plat_effort: str = os.environ.get("OWT_PLAT_EFFORT", "high")
    max_pages_per_well: int = int(os.environ.get("OWT_MAX_PAGES_PER_WELL", "400"))
    max_offset_wells: int = int(os.environ.get("OWT_MAX_OFFSET_WELLS", "300"))
    worker_threads: int = int(os.environ.get("OWT_WORKER_THREADS", "4"))
    frontend_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("OWT_FRONTEND_DIR", Path(__file__).resolve().parents[2] / "frontend")
        )
    )

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
