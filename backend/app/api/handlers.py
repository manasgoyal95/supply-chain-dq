"""Transport-independent API handlers.

The FastAPI app (server) and the in-browser build (GitHub Pages, via Pyodide) both call these functions, so the
two deployments always return identical results.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.config import get_domain_config
from app.ingestion.readers import ReaderError, registered_formats
from app.quality.decisions import build_decisions
from app.quality.rules import all_rules
from app.services.store import Store

SAMPLES = Path(__file__).resolve().parents[3] / "data" / "samples"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class CampaignIn(BaseModel):
    name: str
    source: str | None = None
    start_date: str
    end_date: str | None = None
    description: str = ""


class Api:
    def __init__(self, store: Store | None = None) -> None:
        self.store = store or Store(get_domain_config())

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "records": len(self.store.records), "formats": registered_formats()}

    def ingest(self, files: list[tuple[str, bytes]], source: str | None = None, force: bool = False) -> list[dict]:
        out = []
        for name, content in files:
            if len(content) > MAX_UPLOAD_BYTES:
                raise ApiError(413, f"{name} exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
            try:
                out.append(self.store.ingest(content, name or "upload", source or None, force).__dict__)
            except ReaderError as exc:
                raise ApiError(422, f"{name}: {exc}") from exc
        return out

    def load_samples(self) -> list[dict[str, Any]]:
        return [self.store.ingest(p.read_bytes(), p.name).__dict__ for p in sorted(SAMPLES.iterdir()) if p.is_file()]

    def reset(self) -> dict[str, str]:
        self.store.reset()
        return {"status": "reset"}

    def runs(self) -> list[dict[str, Any]]:
        return [r.__dict__ for r in reversed(self.store.runs)]

    def overview(self) -> dict[str, Any]:
        return self.store.overview()

    def sources(self) -> list[dict[str, Any]]:
        return self.store.sources()

    def trends(self) -> dict[str, Any]:
        return self.store.trends()

    def shipments(self, status: str | None = None, q: str | None = None, contradictions: bool = False) -> list[dict]:
        rows = self.store.shipment_rows()
        if contradictions:
            rows = [r for r in rows if r["contradictions"]]
        if status:
            rows = [r for r in rows if r["status"] == status]
        if q:
            rows = [r for r in rows if q.strip().upper() in r["shipment_id"]]
        return rows

    def shipment(self, shipment_id: str) -> dict[str, Any]:
        detail = self.store.shipment_detail(shipment_id.strip().upper())
        if not detail:
            raise ApiError(404, f"Shipment {shipment_id} not found")
        return detail

    def records(self, status: str | None = None, source: str | None = None, rule: str | None = None) -> list[dict]:
        out = []
        for s in self.store.records:
            if status and s.rec.status.value != status:
                continue
            if source and s.rec.source != source:
                continue
            if rule and not any(i.code == rule for i in s.rec.issues):
                continue
            out.append(self.store.record_dict(s))
        return out

    def rules(self) -> list[dict[str, Any]]:
        return [{"code": r.code, "name": r.name, "level": r.level, "severity": r.severity.value,
                 "description": r.description, "impact": r.impact} for r in all_rules()]

    def decisions(self) -> list[dict[str, Any]]:
        return build_decisions(get_domain_config())

    def campaigns(self) -> list[dict[str, Any]]:
        return [{**c, "impact": self.store.campaign_impact(c)} for c in self.store.campaigns]

    def create_campaign(self, payload: dict[str, Any] | CampaignIn) -> dict[str, Any]:
        try:
            c = payload if isinstance(payload, CampaignIn) else CampaignIn(**payload)
        except ValidationError as exc:
            raise ApiError(422, str(exc)) from exc
        next_id = max((x["id"] for x in self.store.campaigns), default=0) + 1
        data = {"id": next_id, **c.model_dump()}
        try:
            impact = self.store.campaign_impact(data)
        except ValueError as exc:
            raise ApiError(422, f"Invalid date: {exc}") from exc
        self.store.campaigns.append(data)
        return {**data, "impact": impact}

    def delete_campaign(self, cid: int) -> dict[str, str]:
        self.store.campaigns[:] = [c for c in self.store.campaigns if c["id"] != cid]
        return {"status": "deleted"}
