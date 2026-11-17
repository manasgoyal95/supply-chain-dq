"""Canonical milestone vocabulary and alias resolution."""

from __future__ import annotations

import re
from enum import StrEnum

from app.core.config import MilestonesConfig


class Milestone(StrEnum):
    GATE_IN = "GATE_IN"
    LOADED = "LOADED"
    DEPARTED = "DEPARTED"
    ARRIVED = "ARRIVED"
    DISCHARGED = "DISCHARGED"
    DELIVERED = "DELIVERED"


MILESTONE_ORDER: list[Milestone] = list(Milestone)
MILESTONE_INDEX: dict[Milestone, int] = {m: i for i, m in enumerate(MILESTONE_ORDER)}
TOTAL_MILESTONES = len(MILESTONE_ORDER)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def alias_key(value: str) -> str:
    """Normalise a label so 'Gate In', 'GATE_IN' and 'gate-in' compare equal."""
    return _NON_ALNUM.sub("", value.strip().lower())


class MilestoneResolver:
    """Maps raw source labels/codes to canonical milestones using the YAML alias dictionary."""

    def __init__(self, config: MilestonesConfig) -> None:
        self._lookup: dict[str, Milestone] = {}
        configured = [m.code for m in config.sequence]
        if configured != [m.value for m in MILESTONE_ORDER]:
            raise ValueError(f"milestones.yaml sequence {configured} must match {[m.value for m in MILESTONE_ORDER]}")
        for definition in config.sequence:
            milestone = Milestone(definition.code)
            for alias in [definition.code, definition.label, *definition.aliases]:
                key = alias_key(alias)
                existing = self._lookup.get(key)
                if existing and existing != milestone:
                    raise ValueError(f"alias '{alias}' maps to both {existing} and {milestone}")
                self._lookup[key] = milestone
        self.labels = {Milestone(d.code): d.label for d in config.sequence}
        self.descriptions = {Milestone(d.code): d.description for d in config.sequence}

    def resolve(self, raw: str | None) -> Milestone | None:
        if raw is None:
            return None
        return self._lookup.get(alias_key(str(raw)))
