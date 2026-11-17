"""Per-shipment consolidation: golden event per milestone, cross-source conflicts, sequence, gaps, scores."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.core.config import QualityConfig
from app.domain.milestones import MILESTONE_INDEX, MILESTONE_ORDER, TOTAL_MILESTONES, Milestone
from app.domain.types import Issue, JourneyState, NormalizedRecord, RecordStatus, Severity, ShipmentStatus
from app.quality.rules import issue


@dataclass
class ShipmentView:
    shipment_id: str
    golden: dict[Milestone, NormalizedRecord] = field(default_factory=dict)
    alternates: dict[Milestone, list[NormalizedRecord]] = field(default_factory=dict)
    records: list[NormalizedRecord] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    carriers: list[str] = field(default_factory=list)
    completeness: float = 0.0
    score: float = 0.0
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    status: ShipmentStatus = ShipmentStatus.CLEAN
    journey: JourneyState = JourneyState.NO_DATA
    latest_milestone: Milestone | None = None
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    conflict_sources: set[str] = field(default_factory=set)
    agreements: list[tuple[str, bool]] = field(default_factory=list)  # (source, agreed) per overlap


def consolidate(
    records: list[NormalizedRecord], cfg: QualityConfig, priority: dict[str, int], as_of: datetime | None
) -> dict[str, ShipmentView]:
    by_shipment: dict[str, list[NormalizedRecord]] = defaultdict(list)
    for rec in records:
        if rec.shipment_id:
            by_shipment[rec.shipment_id].append(rec)
    return {sid: _build(sid, recs, cfg, priority, as_of) for sid, recs in sorted(by_shipment.items())}


def _build(sid, recs, cfg: QualityConfig, priority, as_of) -> ShipmentView:
    view = ShipmentView(shipment_id=sid, records=recs)
    usable = [r for r in recs if r.status in (RecordStatus.VALID, RecordStatus.WARNING)]
    view.sources = sorted({r.source for r in recs if r.status != RecordStatus.DUPLICATE})
    view.carriers = sorted({r.carrier for r in usable if r.carrier})
    tolerance = timedelta(minutes=cfg.conflict_time_tolerance_minutes)

    if not usable:
        view.issues.append(issue("S008"))
        view.status, view.journey = ShipmentStatus.UNUSABLE, JourneyState.NO_DATA
        view.score_breakdown = {"completeness": 0, "consistency": 0, "validity": 0, "note": "no usable events"}
        return view

    grouped: dict[Milestone, list[NormalizedRecord]] = defaultdict(list)
    for r in usable:
        grouped[r.milestone].append(r)  # type: ignore[index]
    conflicts = 0
    for ms, items in grouped.items():
        items.sort(key=lambda r: (priority.get(r.source, 50), r.event_ts))
        gold = items[0]
        view.golden[ms] = gold
        view.alternates[ms] = items[1:]
        for other in items[1:]:
            if other.source == gold.source:
                continue
            delta = abs(other.event_ts - gold.event_ts)  # type: ignore[operator]
            agreed = True
            if delta > tolerance:
                agreed = False
                view.issues.append(issue(
                    "S003",
                    f"{ms.value}: {gold.source} says {gold.event_ts:%Y-%m-%d %H:%M}, "
                    f"{other.source} says {other.event_ts:%Y-%m-%d %H:%M} (Δ {delta})",
                    milestone=ms.value, sources=[gold.source, other.source], delta_minutes=delta.total_seconds() / 60))
            if gold.location and other.location and gold.location != other.location:
                agreed = False
                view.issues.append(issue(
                    "S004", f"{ms.value}: {gold.source} says {gold.location}, {other.source} says {other.location}",
                    milestone=ms.value, sources=[gold.source, other.source]))
            if not agreed:
                conflicts += 1
                view.conflict_sources.update({gold.source, other.source})
            view.agreements += [(gold.source, agreed), (other.source, agreed)]

    if len(view.carriers) > 1:
        view.issues.append(issue("S005", f"Carriers reported: {', '.join(view.carriers)}", carriers=view.carriers))

    present = [m for m in MILESTONE_ORDER if m in view.golden]
    view.latest_milestone = present[-1]
    latest_idx = MILESTONE_INDEX[view.latest_milestone]
    gaps = [m.value for m in MILESTONE_ORDER[:latest_idx] if m not in view.golden]
    if gaps:
        view.issues.append(issue("S001", f"Missing before {view.latest_milestone.value}: {', '.join(gaps)}", missing=gaps))

    seq_violations = 0
    for a, b in zip(present, present[1:], strict=False):
        ta, tb = view.golden[a].event_ts, view.golden[b].event_ts
        if ta and tb and tb < ta:
            seq_violations += 1
            view.issues.append(issue("S002", f"{b.value} ({tb:%Y-%m-%d %H:%M}) is before {a.value} ({ta:%Y-%m-%d %H:%M})",
                                     pair=[a.value, b.value]))

    times = [r.event_ts for r in usable if r.event_ts]
    view.first_ts, view.last_ts = min(times), max(times)
    delivered = Milestone.DELIVERED in view.golden
    if not delivered and as_of and view.last_ts < as_of - timedelta(days=cfg.stale_after_days):
        view.issues.append(issue("S006", f"No update since {view.last_ts:%Y-%m-%d} (data as-of {as_of:%Y-%m-%d})"))
    if len(view.sources) == 1:
        view.issues.append(issue("S007", f"Only reported by {view.sources[0]}"))

    view.completeness = len(present) / TOTAL_MILESTONES
    view.journey = (JourneyState.COMPLETE if len(present) == TOTAL_MILESTONES
                    else JourneyState.DELIVERED_WITH_GAPS if delivered else JourneyState.IN_PROGRESS)

    pen = cfg.consistency_penalties
    consistency = max(0.0, 1 - seq_violations * pen["out_of_sequence"] - conflicts * pen["conflict"])
    counted = [r for r in recs if r.status != RecordStatus.DUPLICATE]
    validity = sum(cfg.record_validity_credit.get(r.status.value, 0) for r in counted) / len(counted)
    w = cfg.shipment_score_weights
    view.score = round(100 * (w["completeness"] * view.completeness + w["consistency"] * consistency
                              + w["validity"] * validity), 1)
    view.score_breakdown = {"completeness": round(view.completeness, 3), "consistency": round(consistency, 3),
                            "validity": round(validity, 3), "weights": w,
                            "sequence_violations": seq_violations, "conflicting_milestones": conflicts}
    has_warning = any(i.severity == Severity.WARNING for i in view.issues) or any(
        r.status == RecordStatus.WARNING for r in usable)
    view.status = ShipmentStatus.NEEDS_REVIEW if has_warning else ShipmentStatus.CLEAN
    return view
