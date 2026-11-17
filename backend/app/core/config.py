"""Domain configuration (YAML).

YAML files under ``config/`` control *business behaviour* (milestone aliases, source registry, quality thresholds) so that product
owners can tune rules without a code change. Infrastructure settings live in ``settings.py``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

BACKEND_DIR = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- domain config models


class MilestoneDef(BaseModel):
    code: str
    label: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)


class MilestonesConfig(BaseModel):
    sequence: list[MilestoneDef]


class SourceDef(BaseModel):
    display_name: str
    description: str = ""
    priority: int = 50
    aliases: list[str] = Field(default_factory=list)
    filename_patterns: list[str] = Field(default_factory=list)


class TextPattern(BaseModel):
    name: str
    regex: str


class TextConfig(BaseModel):
    segment_separator: str = ";"
    patterns: list[TextPattern] = Field(default_factory=list)


class ParsingConfig(BaseModel):
    day_first: bool = True
    assume_timezone: str = "UTC"
    null_tokens: list[str] = Field(default_factory=lambda: ["", "-", "n/a", "null", "none"])
    header_scan_rows: int = 25


class SourcesConfig(BaseModel):
    sources: dict[str, SourceDef]
    default_priority: int = 50
    field_synonyms: dict[str, list[str]]
    text: TextConfig = Field(default_factory=TextConfig)
    parsing: ParsingConfig = Field(default_factory=ParsingConfig)


class QualityConfig(BaseModel):
    conflict_time_tolerance_minutes: int = 60
    stale_after_days: int = 14
    future_tolerance_hours: int = 24
    shipment_score_weights: dict[str, float]
    consistency_penalties: dict[str, float]
    source_score_weights: dict[str, float]
    record_validity_credit: dict[str, float]

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> QualityConfig:
        for name in ("shipment_score_weights", "source_score_weights"):
            total = sum(getattr(self, name).values())
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"{name} must sum to 1.0 (got {total})")
        return self


class DomainConfig(BaseModel):
    milestones: MilestonesConfig
    sources: SourcesConfig
    quality: QualityConfig


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_domain_config(config_dir: Path) -> DomainConfig:
    return DomainConfig(
        milestones=MilestonesConfig(**_load_yaml(config_dir / "milestones.yaml")),
        sources=SourcesConfig(**_load_yaml(config_dir / "sources.yaml")),
        quality=QualityConfig(**_load_yaml(config_dir / "quality.yaml")),
    )


@lru_cache
def get_domain_config() -> DomainConfig:
    return load_domain_config(Path(os.environ.get("SCDQ_CONFIG_DIR", BACKEND_DIR / "config")))
