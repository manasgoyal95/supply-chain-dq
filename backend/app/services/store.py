"""In-memory store + orchestration (v1 demo). Swap for the SQL repository in v2 without changing the API layer."""

from __future__ import annotations

import hashlib
import threading
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.config import DomainConfig
from app.domain.milestones import MILESTONE_ORDER
from app.domain.types import NormalizedRecord, RawRow, RecordStatus, ShipmentStatus
from app.ingestion.normalize import mark_duplicates
from app.ingestion.pipeline import Pipeline
from app.quality.consolidate import ShipmentView, consolidate


@dataclass
class Run:
    id: str
    filename: str
    sha256: str
    format: str
    source: str
    source_resolution: str
    started_at: datetime
    counts: dict[str, int]
    column_mapping: dict[str, str]
    notes: list[str]
    status: str = "COMPLETED"


@dataclass
class StoredRecord:
    id: int
    run_id: str
    filename: str
    row: RawRow
    rec: NormalizedRecord
    period_ts: datetime | None = None


@dataclass
class Store:
    config: DomainConfig
    runs: list[Run] = field(default_factory=list)
    records: list[StoredRecord] = field(default_factory=list)
    shipments: dict[str, ShipmentView] = field(default_factory=dict)
    campaigns: list[dict[str, Any]] = field(default_factory=list)
    as_of: datetime | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.pipeline = Pipeline(self.config)

    def ingest(self, content: bytes, filename: str, source: str | None = None, force: bool = False) -> Run:
        sha = hashlib.sha256(content).hexdigest()
        with self._lock:
            existing = next((r for r in self.runs if r.sha256 == sha), None)
            if existing and not force:
                return Run(**{**existing.__dict__, "status": "SKIPPED_ALREADY_INGESTED"})
            result = self.pipeline.process(content, filename, source)
            keys = {s.rec.dedupe_key() for s in self.records if s.rec.status != RecordStatus.DUPLICATE} - {None}
            mark_duplicates(result.records, keys)
            run_id = uuid.uuid4().hex[:12]
            file_max = max((r.event_ts for r in result.records if r.event_ts), default=None)
            for row, rec in zip(result.rows, result.records, strict=True):
                self.records.append(StoredRecord(len(self.records) + 1, run_id, filename, row, rec,
                                                 rec.event_ts or file_max))
            counts = Counter(r.status.value for r in result.records)
            run = Run(run_id, filename, sha, result.format, result.source, result.source_resolution,
                      datetime.now(UTC), {"received": len(result.records), **counts}, result.column_mapping,
                      result.notes)
            self.runs.append(run)
            self._rebuild()
            return run

    def reset(self) -> None:
        with self._lock:
            self.runs.clear(); self.records.clear(); self.shipments.clear(); self.as_of = None

    def _rebuild(self) -> None:
        recs = [s.rec for s in self.records]
        usable_ts = [r.event_ts for r in recs if r.event_ts and r.status in (RecordStatus.VALID, RecordStatus.WARNING)]
        self.as_of = max(usable_ts) if usable_ts else None
        prio = {src: self.pipeline.sources.priority(src) for src in {r.source for r in recs}}
        self.shipments = consolidate(recs, self.config.quality, prio, self.as_of)

    # ------------------------------------------------------------------ metrics
    def overview(self) -> dict[str, Any]:
        recs = [s.rec for s in self.records]
        status = Counter(r.status.value for r in recs)
        ships = list(self.shipments.values())
        sstatus = Counter(s.status.value for s in ships)
        issues = Counter()
        for s in self.records:
            issues.update(i.code for i in s.rec.issues)
        for v in ships:
            issues.update(i.code for i in v.issues)
        usable_ships = [s for s in ships if s.status != ShipmentStatus.UNUSABLE]
        funnel = {m.value: sum(1 for s in ships if m in s.golden) for m in MILESTONE_ORDER}
        return {
            "as_of": self.as_of,
            "events_received": len(recs),
            "record_status": {k: status.get(k, 0) for k in RecordStatus},
            "shipments": len(ships),
            "shipment_status": {k: sstatus.get(k, 0) for k in ShipmentStatus},
            "journey_state": Counter(s.journey.value for s in ships),
            "avg_completeness": round(100 * sum(s.completeness for s in ships) / len(ships), 1) if ships else 0,
            "avg_score": round(sum(s.score for s in ships) / len(ships), 1) if ships else 0,
            "avg_score_usable": round(sum(s.score for s in usable_ships) / len(usable_ships), 1) if usable_ships else 0,
            "milestone_coverage": funnel,
            "issues_by_rule": dict(issues.most_common()),
            "sources": len({r.source for r in recs}),
            "runs": len(self.runs),
        }

    def sources(self) -> list[dict[str, Any]]:
        cfg = self.config.quality
        by_src: dict[str, list[NormalizedRecord]] = defaultdict(list)
        for s in self.records:
            by_src[s.rec.source].append(s.rec)
        agree: dict[str, list[bool]] = defaultdict(list)
        conflicts: Counter = Counter()
        golden: Counter = Counter()
        for v in self.shipments.values():
            for src, ok in v.agreements:
                agree[src].append(ok)
            for src in v.conflict_sources:
                conflicts[src] += 1
            for g in v.golden.values():
                golden[g.source] += 1
        out = []
        for src, recs in sorted(by_src.items()):
            st = Counter(r.status.value for r in recs)
            nondup = [r for r in recs if r.status != RecordStatus.DUPLICATE] or recs
            n = len(nondup)
            fc = {
                "shipment_id": sum(1 for r in nondup if r.shipment_id) / n,
                "milestone": sum(1 for r in nondup if r.milestone) / n,
                "timestamp": sum(1 for r in nondup if r.event_ts) / n,
                "location": sum(1 for r in nondup if r.location) / n,
                "carrier": sum(1 for r in nondup if r.carrier) / n,
            }
            validity = sum(cfg.record_validity_credit.get(r.status.value, 0) for r in nondup) / n
            uniqueness = 1 - st.get("DUPLICATE", 0) / len(recs)
            agreement = (sum(agree[src]) / len(agree[src])) if agree[src] else 1.0
            field_completeness = sum(fc.values()) / len(fc)
            w = cfg.source_score_weights
            score = 100 * (w["validity"] * validity + w["field_completeness"] * field_completeness
                           + w["uniqueness"] * uniqueness + w["agreement"] * agreement)
            out.append({
                "source": src, "display_name": self.pipeline.sources.display_name(src),
                "priority": self.pipeline.sources.priority(src),
                "records": len(recs), "status": {k: st.get(k, 0) for k in RecordStatus},
                "shipments": len({r.shipment_id for r in recs if r.shipment_id}),
                "field_completeness": {k: round(v * 100, 1) for k, v in fc.items()},
                "milestones": {m.value: sum(1 for r in nondup if r.milestone == m and r.status != RecordStatus.REJECTED)
                               for m in MILESTONE_ORDER},
                "overlaps": len(agree[src]), "agreement_rate": round(agreement * 100, 1),
                "shipments_with_conflicts": conflicts[src], "golden_events": golden[src],
                "score": round(score, 1),
                "score_breakdown": {"validity": round(validity, 3), "field_completeness": round(field_completeness, 3),
                                    "uniqueness": round(uniqueness, 3), "agreement": round(agreement, 3),
                                    "weights": w},
            })
        return out

    def trends(self) -> dict[str, Any]:
        cfg = self.config.quality
        buckets: dict[tuple[str, str], list[NormalizedRecord]] = defaultdict(list)
        for s in self.records:
            if s.period_ts:
                week = s.period_ts.date().isocalendar()
                buckets[(f"{week.year}-W{week.week:02d}", s.rec.source)].append(s.rec)
        weeks = sorted({k[0] for k in buckets})
        series: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for (wk, src), recs in sorted(buckets.items()):
            nondup = [r for r in recs if r.status != RecordStatus.DUPLICATE]
            if not nondup:
                continue
            series[src].append({"week": wk, "records": len(recs),
                                "validity": round(100 * sum(cfg.record_validity_credit.get(r.status.value, 0)
                                                            for r in nondup) / len(nondup), 1),
                                "rejected": sum(1 for r in nondup if r.status == RecordStatus.REJECTED)})
        cohort: dict[str, list[ShipmentView]] = defaultdict(list)
        for v in self.shipments.values():
            if v.first_ts:
                w = v.first_ts.date().isocalendar()
                cohort[f"{w.year}-W{w.week:02d}"].append(v)
        ship_series = [{"week": wk, "shipments": len(vs),
                        "avg_completeness": round(100 * sum(v.completeness for v in vs) / len(vs), 1),
                        "avg_score": round(sum(v.score for v in vs) / len(vs), 1),
                        "clean_pct": round(100 * sum(v.status == ShipmentStatus.CLEAN for v in vs) / len(vs), 1)}
                       for wk, vs in sorted(cohort.items())]
        return {"weeks": weeks, "sources": series, "shipments": ship_series}

    def campaign_impact(self, c: dict[str, Any]) -> dict[str, Any]:
        cfg = self.config.quality
        start = datetime.fromisoformat(c["start_date"]).replace(tzinfo=UTC)
        end = datetime.fromisoformat(c["end_date"]).replace(tzinfo=UTC) if c.get("end_date") else None

        def stats(recs: list[NormalizedRecord]) -> dict[str, Any]:
            nondup = [r for r in recs if r.status != RecordStatus.DUPLICATE]
            if not nondup:
                return {"records": 0}
            n = len(nondup)
            return {"records": n,
                    "validity": round(100 * sum(cfg.record_validity_credit.get(r.status.value, 0) for r in nondup) / n, 1),
                    "rejected_pct": round(100 * sum(r.status == RecordStatus.REJECTED for r in nondup) / n, 1),
                    "location_completeness": round(100 * sum(1 for r in nondup if r.location) / n, 1),
                    "duplicate_pct": round(100 * (len(recs) - n) / len(recs), 1)}

        before, after = [], []
        for s in self.records:
            if c.get("source") and s.rec.source != c["source"]:
                continue
            if not s.period_ts:
                continue
            if s.period_ts < start:
                before.append(s.rec)
            elif end is None or s.period_ts <= end:
                after.append(s.rec)
        b, a = stats(before), stats(after)
        delta = {k: round(a[k] - b[k], 1) for k in a if k in b and k != "records"} if a.get("records") and b.get("records") else {}
        return {"before": b, "after": a, "delta": delta}

    # ------------------------------------------------------------------ shipments / records
    def shipment_rows(self) -> list[dict[str, Any]]:
        return [{"shipment_id": v.shipment_id, "status": v.status.value, "journey": v.journey.value,
                 "completeness": round(v.completeness * 100, 1), "score": v.score,
                 "milestones": [m.value for m in MILESTONE_ORDER if m in v.golden],
                 "latest_milestone": v.latest_milestone.value if v.latest_milestone else None,
                 "sources": v.sources, "carriers": v.carriers, "last_event": v.last_ts,
                 "issues": [i.code for i in v.issues if i.severity != "INFO"],
                 "info": [i.code for i in v.issues if i.severity == "INFO"]}
                for v in self.shipments.values()]

    def record_dict(self, s: StoredRecord) -> dict[str, Any]:
        r = s.rec
        return {"id": s.id, "run_id": s.run_id, "file": s.filename, "locator": s.row.locator, "raw": s.row.payload,
                "source": r.source, "shipment_id": r.shipment_id,
                "milestone": r.milestone.value if r.milestone else None, "milestone_raw": r.milestone_raw,
                "event_ts": r.event_ts, "timestamp_raw": r.timestamp_raw, "location": r.location,
                "carrier": r.carrier, "status": r.status.value,
                "issues": [{"code": i.code, "severity": i.severity.value, "message": i.message} for i in r.issues]}

    def shipment_detail(self, sid: str) -> dict[str, Any] | None:
        v = self.shipments.get(sid)
        if not v:
            return None
        by_rec = {id(s.rec): s for s in self.records}
        timeline = []
        for m in MILESTONE_ORDER:
            g = v.golden.get(m)
            timeline.append({"milestone": m.value, "label": self.pipeline.resolver.labels[m],
                             "golden": self.record_dict(by_rec[id(g)]) if g else None,
                             "alternates": [self.record_dict(by_rec[id(a)]) for a in v.alternates.get(m, [])]})
        return {**next(r for r in self.shipment_rows() if r["shipment_id"] == sid),
                "score_breakdown": v.score_breakdown, "timeline": timeline,
                "issue_details": [{"code": i.code, "severity": i.severity.value, "message": i.message} for i in v.issues],
                "records": [self.record_dict(by_rec[id(r)]) for r in v.records]}
