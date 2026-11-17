"""In-browser entry point (Pyodide). Routes the dashboard's /api/v1 calls to the same handlers the server uses.

`handle()` returns (status, json_text). Used by the GitHub Pages build; never imported by the server.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.api.handlers import Api, ApiError

api = Api()


def _json(o: Any) -> Any:
    if hasattr(o, "isoformat"):
        return o.isoformat().replace("+00:00", "Z")
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    return str(o)


def _bool(v: str | None) -> bool:
    return (v or "").lower() in ("1", "true", "yes", "on")


def handle(method: str, url: str, body: str | None = None, files: Any = None, form: Any = None) -> tuple[int, str]:
    parts = urlsplit(url)
    path = re.sub(r"^.*?/api/v1", "", parts.path).rstrip("/") or "/"
    q = {k: v[-1] for k, v in parse_qs(parts.query).items()}
    method = method.upper()
    try:
        result: Any
        if method == "GET" and path == "/health":
            result = api.health()
        elif method == "POST" and path == "/ingest":
            form = form.to_py() if hasattr(form, "to_py") else (form or {})
            payload = [(f.name, f.data.to_bytes() if hasattr(f.data, "to_bytes") else bytes(f.data)) for f in (files or [])]
            result = api.ingest(payload, form.get("source"), _bool(form.get("force")))
        elif method == "POST" and path == "/admin/load-samples":
            result = api.load_samples()
        elif method == "POST" and path == "/admin/reset":
            result = api.reset()
        elif method == "GET" and path == "/runs":
            result = api.runs()
        elif method == "GET" and path == "/metrics/overview":
            result = api.overview()
        elif method == "GET" and path == "/metrics/sources":
            result = api.sources()
        elif method == "GET" and path == "/metrics/trends":
            result = api.trends()
        elif method == "GET" and path == "/shipments":
            result = api.shipments(q.get("status"), q.get("q"), _bool(q.get("contradictions")))
        elif method == "GET" and (m := re.fullmatch(r"/shipments/([^/]+)", path)):
            result = api.shipment(m.group(1))
        elif method == "GET" and path == "/records":
            result = api.records(q.get("status"), q.get("source"), q.get("rule"))
        elif method == "GET" and path == "/rules":
            result = api.rules()
        elif method == "GET" and path == "/decisions":
            result = api.decisions()
        elif method == "GET" and path == "/campaigns":
            result = api.campaigns()
        elif method == "POST" and path == "/campaigns":
            return 201, json.dumps(api.create_campaign(json.loads(body or "{}")), default=_json)
        elif method == "DELETE" and (m := re.fullmatch(r"/campaigns/(\d+)", path)):
            result = api.delete_campaign(int(m.group(1)))
        else:
            raise ApiError(404, f"No route for {method} {path}")
        return 200, json.dumps(result, default=_json)
    except ApiError as exc:
        return exc.status, json.dumps({"error": {"code": exc.status, "message": exc.message}})
