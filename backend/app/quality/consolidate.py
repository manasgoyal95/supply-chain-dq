"""Per-shipment consolidation: golden event per milestone, cross-source conflicts, sequence, gaps, scores."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.core.config import QualityConfig
from app.domain.milestones import MILESTONE_INDEX, MILESTONE_ORDER, TOTAL_MILESTONES, Milestone
from app.domain.types import Issue, JourneyState, NormalizedRecord, RecordStatus, Severity, ShipmentStatus
from app.quality.rules import RULES, issue

# rules that mean "input records contradict each other" (highlighted as contradictions in the portal)
CONTRADICTION_RULES = {"S002", "S003", "S004", "S005", "S009"}


def _at(r: NormalizedRecord) -> str:
    return f"{r.source} [{r.origin or r.locator}]"


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
    usable_sources: list[str] = field(default_factory=list)
    choice_reason: dict[Milestone, str] = field(default_factory=dict)   # why the chosen record won
    alt_info: dict[int, dict[str, Any]] = field(default_factory=dict)   # id(record) -> why it was not chosen
    status_reason: str = ""
    score_formula: str = ""


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
    view.usable_sources = sorted({r.source for r in usable})
    tolerance = timedelta(minutes=cfg.conflict_time_tolerance_minutes)

    if not usable:
        view.issues.append(issue("S008"))
        view.status, view.journey = ShipmentStatus.UNUSABLE, JourneyState.NO_DATA
        view.score_breakdown = {"completeness": 0, "consistency": 0, "validity": 0, "note": "no usable events"}
        n = len([r for r in recs if r.status != RecordStatus.DUPLICATE])
        view.status_reason = (f"Unusable: all {n} record(s) received for this shipment were rejected, so no milestone "
                              "can be placed on its journey.")
        view.score_formula = "Score fixed at 0 because the shipment has no usable records."
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
        srcs = sorted({r.source for r in items}, key=lambda x: priority.get(x, 50))
        if len(srcs) == 1 and len(items) == 1:
            view.choice_reason[ms] = f"Only {gold.source} reported this milestone."
        elif len(srcs) == 1:
            view.choice_reason[ms] = (f"Only {gold.source} reported this milestone ({len(items)} times); "
                                      "the earliest time is used.")
        else:
            view.choice_reason[ms] = (f"{len(srcs)} sources reported this milestone; {gold.source} is used because "
                                      f"it has the highest source priority ({' > '.join(srcs)}).")
        for other in items[1:]:
            delta = abs(other.event_ts - gold.event_ts)  # type: ignore[operator]
            mins = round(delta.total_seconds() / 60)
            loc_conflict = bool(gold.location and other.location and gold.location != other.location)
            time_conflict = delta > tolerance
            if other.source == gold.source:
                if time_conflict or loc_conflict:
                    what = []
                    if time_conflict:
                        what.append(f"{gold.event_ts:%Y-%m-%d %H:%M} vs {other.event_ts:%Y-%m-%d %H:%M} (Δ {mins} min)")
                    if loc_conflict:
                        what.append(f"{gold.location} vs {other.location}")
                    view.issues.append(issue(
                        "S009", f"{ms.value}: {gold.source} contradicts itself: {'; '.join(what)}. "
                                f"Records: {gold.origin or gold.locator} and {other.origin or other.locator}.",
                        milestone=ms.value, sources=[gold.source], records=[gold.origin, other.origin]))
                    view.conflict_sources.add(gold.source)
                    conflicts += 1
                    view.alt_info[id(other)] = {"relation": "conflict", "delta_minutes": mins,
                                                "reason": f"CONFLICT within {gold.source}: {'; '.join(what)}. "
                                                          "The earliest report is used."}
                else:
                    view.alt_info[id(other)] = {"relation": "same_source", "delta_minutes": mins,
                                                "reason": f"Same source reported it again ({mins} min apart, same "
                                                          "location); the earliest report is used."}
                continue
            parts = []
            if time_conflict:
                parts.append(f"time differs by {mins} min (tolerance {cfg.conflict_time_tolerance_minutes} min)")
            if loc_conflict:
                parts.append(f"location {other.location} vs {gold.location}")
            if parts:
                reason = f"CONFLICT with {gold.source}: " + "; ".join(parts) + f". Not used: {gold.source} has higher priority."
            else:
                reason = (f"AGREES with {gold.source} (Δ {mins} min, within {cfg.conflict_time_tolerance_minutes} min"
                          f"{', same location' if gold.location == other.location else ''}). Not used: lower priority.")
            view.alt_info[id(other)] = {"relation": "conflict" if parts else "agrees", "delta_minutes": mins,
                                        "reason": reason}
            agreed = True
            if time_conflict:
                agreed = False
                view.issues.append(issue(
                    "S003",
                    f"{ms.value}: {_at(gold)} says {gold.event_ts:%Y-%m-%d %H:%M}, "
                    f"{_at(other)} says {other.event_ts:%Y-%m-%d %H:%M} (Δ {mins} min, tolerance "
                    f"{cfg.conflict_time_tolerance_minutes} min)",
                    milestone=ms.value, sources=[gold.source, other.source], records=[gold.origin, other.origin],
                    delta_minutes=mins))
            if loc_conflict:
                agreed = False
                view.issues.append(issue(
                    "S004", f"{ms.value}: {_at(gold)} says {gold.location}, {_at(other)} says {other.location}",
                    milestone=ms.value, sources=[gold.source, other.source], records=[gold.origin, other.origin]))
            if not agreed:
                conflicts += 1
                view.conflict_sources.update({gold.source, other.source})
            view.agreements += [(gold.source, agreed), (other.source, agreed)]

    if len(view.carriers) > 1:
        by_carrier: dict[str, list[str]] = defaultdict(list)
        for r in usable:
            if r.carrier:
                by_carrier[r.carrier].append(_at(r))
        detail = "; ".join(f"{c}: {', '.join(sorted(set(v))[:3])}{' …' if len(set(v)) > 3 else ''}"
                           for c, v in sorted(by_carrier.items()))
        view.issues.append(issue("S005", f"Different carriers reported: {detail}", carriers=view.carriers))

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
            ga, gb = view.golden[a], view.golden[b]
            view.issues.append(issue(
                "S002", f"{b.value} at {tb:%Y-%m-%d %H:%M} ({_at(gb)}) is before {a.value} at {ta:%Y-%m-%d %H:%M} "
                        f"({_at(ga)})", pair=[a.value, b.value], records=[ga.origin, gb.origin]))

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
    view.score_formula = (f"100 × ({w['completeness']} × completeness {view.completeness:.3f} + {w['consistency']} × "
                          f"consistency {consistency:.3f} + {w['validity']} × validity {validity:.3f}) = {view.score}")
    view.score_breakdown = {"completeness": round(view.completeness, 3), "consistency": round(consistency, 3),
                            "validity": round(validity, 3), "weights": w,
                            "sequence_violations": seq_violations, "conflicting_milestones": conflicts}
    has_warning = any(i.severity == Severity.WARNING for i in view.issues) or any(
        r.status == RecordStatus.WARNING for r in usable)
    view.status = ShipmentStatus.NEEDS_REVIEW if has_warning else ShipmentStatus.CLEAN
    warn_codes = sorted({i.code for i in view.issues if i.severity == Severity.WARNING})
    journey_txt = {JourneyState.COMPLETE: "all 6 milestones are present",
                   JourneyState.DELIVERED_WITH_GAPS: "it is delivered but earlier milestones are missing",
                   JourneyState.IN_PROGRESS: f"{len(present)} of 6 milestones so far, latest is "
                                             f"{view.latest_milestone.value} (not yet delivered)"}[view.journey]
    if view.status == ShipmentStatus.CLEAN:
        view.status_reason = (f"Clean: no quality warnings were found. Clean describes data quality only; "
                              f"for completeness, {journey_txt}.")
    else:
        rec_warn = sum(1 for r in usable if r.status == RecordStatus.WARNING)
        why = ", ".join(f"{c} ({RULES[c].name.replace('_', ' ').lower()})" for c in warn_codes) + (f"{' and ' if warn_codes else ''}{rec_warn} record warning(s)" if rec_warn else "")
        view.status_reason = f"Needs review because of {why}. Journey: {journey_txt}."
    return view
