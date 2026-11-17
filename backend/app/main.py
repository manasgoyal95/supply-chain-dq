"""FastAPI application: REST API under /api/v1 and the dashboard UI at /. Logic lives in app.api.handlers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.handlers import Api, ApiError, CampaignIn
from app.core.logging import configure_logging
from app.core.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level, settings.log_json)
log = logging.getLogger("scdq")
api = Api()
WEB = Path(__file__).resolve().parent / "web"

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(ApiError)
async def api_error(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": {"code": exc.status, "message": exc.message}})


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.status_code, "message": exc.detail}})


@app.get("/health")
def health() -> dict[str, Any]:
    return api.health()


@app.post("/api/v1/ingest")
async def ingest(files: list[UploadFile] = File(...), source: str | None = Form(None),
                 force: bool = Form(False)) -> list[dict[str, Any]]:
    payload = [(f.filename or "upload", await f.read()) for f in files]
    result = api.ingest(payload, source, force)
    for run in result:
        log.info("ingested", extra={"ctx": {"file": run["filename"], "run": run["id"], "counts": run["counts"]}})
    return result


@app.post("/api/v1/admin/load-samples")
def load_samples() -> list[dict[str, Any]]:
    return api.load_samples()


@app.post("/api/v1/admin/reset")
def reset() -> dict[str, str]:
    return api.reset()


@app.get("/api/v1/runs")
def runs() -> list[dict[str, Any]]:
    return api.runs()


@app.get("/api/v1/metrics/overview")
def overview() -> dict[str, Any]:
    return api.overview()


@app.get("/api/v1/metrics/sources")
def sources() -> list[dict[str, Any]]:
    return api.sources()


@app.get("/api/v1/metrics/trends")
def trends() -> dict[str, Any]:
    return api.trends()


@app.get("/api/v1/shipments")
def shipments(status: str | None = None, q: str | None = None, contradictions: bool = False) -> list[dict[str, Any]]:
    return api.shipments(status, q, contradictions)


@app.get("/api/v1/shipments/{shipment_id}")
def shipment(shipment_id: str) -> dict[str, Any]:
    return api.shipment(shipment_id)


@app.get("/api/v1/records")
def records(status: str | None = None, source: str | None = None, rule: str | None = None) -> list[dict[str, Any]]:
    return api.records(status, source, rule)


@app.get("/api/v1/rules")
def rules() -> list[dict[str, Any]]:
    return api.rules()


@app.get("/api/v1/decisions")
def decisions() -> list[dict[str, Any]]:
    return api.decisions()


@app.get("/api/v1/campaigns")
def campaigns() -> list[dict[str, Any]]:
    return api.campaigns()


@app.post("/api/v1/campaigns", status_code=201)
def create_campaign(c: CampaignIn) -> dict[str, Any]:
    return api.create_campaign(c)


@app.delete("/api/v1/campaigns/{cid}")
def delete_campaign(cid: int) -> dict[str, str]:
    return api.delete_campaign(cid)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")
