"""FastAPI application: REST API under /api/v1 and the dashboard UI at /."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.core.config import get_domain_config, get_settings
from app.core.logging import configure_logging
from app.ingestion.readers import ReaderError, registered_formats
from app.quality.decisions import build_decisions
from app.quality.rules import all_rules
from app.services.store import Store

settings = get_settings()
configure_logging(settings.log_level, settings.log_json)
log = logging.getLogger("scdq")
store = Store(get_domain_config())
STATIC = Path(__file__).resolve().parent / "web"
SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples"

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.status_code, "message": exc.detail}})


class CampaignIn(BaseModel):
    name: str
    source: str | None = None
    start_date: str
    end_date: str | None = None
    description: str = ""


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "records": len(store.records), "formats": registered_formats()}


@app.post("/api/v1/ingest")
async def ingest(files: list[UploadFile] = File(...), source: str | None = Form(None),
                 force: bool = Form(False)) -> list[dict[str, Any]]:
    out = []
    for f in files:
        content = await f.read()
        if len(content) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"{f.filename} exceeds {settings.max_upload_mb} MB")
        try:
            run = store.ingest(content, f.filename or "upload", source or None, force)
        except ReaderError as exc:
            raise HTTPException(422, f"{f.filename}: {exc}") from exc
        log.info("ingested", extra={"ctx": {"file": f.filename, "run": run.id, "counts": run.counts}})
        out.append(run.__dict__)
    return out


@app.post("/api/v1/admin/load-samples")
def load_samples() -> list[dict[str, Any]]:
    return [store.ingest(p.read_bytes(), p.name).__dict__ for p in sorted(SAMPLES.iterdir()) if p.is_file()]


@app.post("/api/v1/admin/reset")
def reset() -> dict[str, str]:
    store.reset()
    return {"status": "reset"}


@app.get("/api/v1/runs")
def runs() -> list[dict[str, Any]]:
    return [r.__dict__ for r in reversed(store.runs)]


@app.get("/api/v1/metrics/overview")
def overview() -> dict[str, Any]:
    return store.overview()


@app.get("/api/v1/metrics/sources")
def sources() -> list[dict[str, Any]]:
    return store.sources()


@app.get("/api/v1/metrics/trends")
def trends() -> dict[str, Any]:
    return store.trends()


@app.get("/api/v1/shipments")
def shipments(status: str | None = None, q: str | None = None, contradictions: bool = False) -> list[dict[str, Any]]:
    rows = store.shipment_rows()
    if contradictions:
        rows = [r for r in rows if r["contradictions"]]
    if status:
        rows = [r for r in rows if r["status"] == status]
    if q:
        rows = [r for r in rows if q.upper() in r["shipment_id"]]
    return rows


@app.get("/api/v1/shipments/{shipment_id}")
def shipment(shipment_id: str) -> dict[str, Any]:
    detail = store.shipment_detail(shipment_id.upper())
    if not detail:
        raise HTTPException(404, f"Shipment {shipment_id} not found")
    return detail


@app.get("/api/v1/records")
def records(status: str | None = None, source: str | None = None, rule: str | None = None) -> list[dict[str, Any]]:
    out = []
    for s in store.records:
        if status and s.rec.status.value != status:
            continue
        if source and s.rec.source != source:
            continue
        if rule and not any(i.code == rule for i in s.rec.issues):
            continue
        out.append(store.record_dict(s))
    return out


@app.get("/api/v1/rules")
def rules() -> list[dict[str, Any]]:
    return [{"code": r.code, "name": r.name, "level": r.level, "severity": r.severity.value,
             "description": r.description, "impact": r.impact} for r in all_rules()]


@app.get("/api/v1/decisions")
def decisions() -> list[dict[str, Any]]:
    return build_decisions(get_domain_config())


@app.get("/api/v1/campaigns")
def campaigns() -> list[dict[str, Any]]:
    return [{**c, "impact": store.campaign_impact(c)} for c in store.campaigns]


@app.post("/api/v1/campaigns", status_code=201)
def create_campaign(c: CampaignIn) -> dict[str, Any]:
    data = {"id": len(store.campaigns) + 1, **c.model_dump()}
    store.campaigns.append(data)
    return {**data, "impact": store.campaign_impact(data)}


@app.delete("/api/v1/campaigns/{cid}")
def delete_campaign(cid: int) -> dict[str, str]:
    store.campaigns[:] = [c for c in store.campaigns if c["id"] != cid]
    return {"status": "deleted"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
