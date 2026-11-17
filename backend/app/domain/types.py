"""Plain data structures passed between pipeline stages (no ORM, no I/O)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.domain.milestones import Milestone


class Severity(StrEnum):
    ERROR = "ERROR"      # record is unsuitable for operational use
    WARNING = "WARNING"  # usable, but deserves attention
    INFO = "INFO"        # context only; does not affect status


class RecordStatus(StrEnum):
    VALID = "VALID"
    WARNING = "WARNING"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"


class ShipmentStatus(StrEnum):
    CLEAN = "CLEAN"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNUSABLE = "UNUSABLE"


class JourneyState(StrEnum):
    COMPLETE = "COMPLETE"                    # all milestones present
    DELIVERED_WITH_GAPS = "DELIVERED_WITH_GAPS"
    IN_PROGRESS = "IN_PROGRESS"
    NO_DATA = "NO_DATA"


CANONICAL_FIELDS = ("shipment_id", "milestone", "event_time", "location", "carrier", "source", "notes")


@dataclass(slots=True)
class RawRow:
    """One unit of input as produced by a reader, before any interpretation."""

    locator: str                          # human readable position: "row 7", "$[12]", "line 3 · segment 2"
    payload: dict[str, Any] | str         # original cells/object/text, kept verbatim for lineage
    fields: dict[str, Any] = field(default_factory=dict)  # canonical field -> raw value (filled by mapping)
    parse_error: str | None = None        # set when the row could not be interpreted at all


@dataclass(slots=True)
class Issue:
    code: str
    severity: Severity
    message: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedRecord:
    """A raw row interpreted into the canonical event model, plus the issues found doing so."""

    source: str
    locator: str
    shipment_id: str | None
    milestone: Milestone | None
    milestone_raw: str | None
    event_ts: datetime | None
    timestamp_raw: str | None
    location: str | None
    carrier: str | None
    issues: list[Issue] = field(default_factory=list)
    status: RecordStatus = RecordStatus.VALID
    origin: str = ""  # "file · position", set when the record is stored; used in conflict messages

    def dedupe_key(self) -> tuple | None:
        if self.status == RecordStatus.REJECTED or not self.shipment_id or not self.milestone or not self.event_ts:
            return None
        # every field must match; a repeat that differs in any value is a contradiction, not a duplicate
        return (self.source, self.shipment_id, self.milestone.value, self.event_ts.isoformat(), self.location or "",
                self.carrier or "")
