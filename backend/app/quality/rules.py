"""Rule catalog: the single source of truth for every data-quality check, its severity and its meaning.

Codes are stable identifiers (safe to store and filter on); `R` = record-level, `S` = shipment-level.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.types import Issue, Severity


@dataclass(frozen=True, slots=True)
class Rule:
    code: str
    name: str
    level: str  # "record" | "shipment"
    severity: Severity
    description: str
    impact: str


_RULES = [
    Rule("R000", "UNPARSEABLE_RECORD", "record", Severity.ERROR,
         "The input row/message could not be interpreted (unknown text pattern or malformed JSON).",
         "Record rejected; nothing can be used from it."),
    Rule("R001", "MISSING_SHIPMENT_ID", "record", Severity.ERROR,
         "No shipment identifier present.", "Record rejected; cannot be linked to a shipment."),
    Rule("R002", "MISSING_MILESTONE", "record", Severity.ERROR,
         "No milestone / event code present.", "Record rejected."),
    Rule("R003", "UNKNOWN_MILESTONE", "record", Severity.ERROR,
         "Milestone code is not in the canonical alias dictionary (config/milestones.yaml).",
         "Record rejected. If the code is legitimate, add it as an alias and reprocess."),
    Rule("R004", "MISSING_TIMESTAMP", "record", Severity.ERROR,
         "No event timestamp present.", "Record rejected; cannot be placed on the shipment timeline."),
    Rule("R005", "INVALID_TIMESTAMP", "record", Severity.ERROR,
         "Event timestamp could not be parsed.", "Record rejected; cannot be placed on the shipment timeline."),
    Rule("R006", "DUPLICATE_RECORD", "record", Severity.INFO,
         "Same source already reported this exact shipment/milestone/time/location/carrier.",
         "Record ignored for metrics; counts toward the source's duplicate rate."),
    Rule("R007", "MISSING_LOCATION", "record", Severity.WARNING,
         "No location supplied for the event.", "Usable, but location-based tracking is incomplete."),
    Rule("R008", "INVALID_LOCATION_CODE", "record", Severity.WARNING,
         "Location is not a valid 5-character UN/LOCODE.", "Usable, but location may be unreliable."),
    Rule("R009", "FUTURE_TIMESTAMP", "record", Severity.WARNING,
         "Event timestamp lies in the future.", "Usable, but likely a planned/estimated time or a data entry error."),
    Rule("S001", "MILESTONE_GAP", "shipment", Severity.WARNING,
         "A milestone is missing before the latest milestone reached (e.g. DELIVERED without DEPARTED).",
         "Shipment needs review; journey shows a hole that a customer may ask about."),
    Rule("S002", "OUT_OF_SEQUENCE", "shipment", Severity.WARNING,
         "Milestone timestamps contradict the canonical order (e.g. ARRIVED before DEPARTED).",
         "Shipment needs review; at least one timestamp is wrong."),
    Rule("S003", "TIMESTAMP_CONFLICT", "shipment", Severity.WARNING,
         "Two sources report the same milestone at times further apart than the tolerance.",
         "Shipment needs review; sources disagree. The higher-priority source is shown by default."),
    Rule("S004", "LOCATION_CONFLICT", "shipment", Severity.WARNING,
         "Two sources report the same milestone at different locations.",
         "Shipment needs review; sources disagree on where the event happened."),
    Rule("S005", "CARRIER_CONFLICT", "shipment", Severity.WARNING,
         "Records disagree on which carrier handles the shipment.", "Shipment needs review."),
    Rule("S009", "SAME_SOURCE_CONFLICT", "shipment", Severity.WARNING,
         "One source reported the same milestone more than once with different times (beyond tolerance) or locations, "
         "e.g. two ops uploads that contradict each other.",
         "Shipment needs review; the source contradicts itself. The earliest report is shown."),
    Rule("S006", "STALE_SHIPMENT", "shipment", Severity.WARNING,
         "Not delivered and no event received for longer than the stale threshold (relative to data as-of).",
         "Shipment needs review; updates may have been lost."),
    Rule("S007", "SINGLE_SOURCE", "shipment", Severity.INFO,
         "Shipment is only known to one source system.", "No cross-validation possible."),
    Rule("S008", "NO_VALID_EVENTS", "shipment", Severity.ERROR,
         "Every record received for this shipment was rejected.", "Shipment unusable for operational tracking."),
]

RULES: dict[str, Rule] = {r.code: r for r in _RULES}


def issue(code: str, message: str | None = None, **context: Any) -> Issue:
    rule = RULES[code]
    return Issue(code=code, severity=rule.severity, message=message or rule.description, context=context)


def all_rules() -> list[Rule]:
    return list(_RULES)
