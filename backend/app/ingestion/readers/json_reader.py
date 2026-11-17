from __future__ import annotations

import json
from typing import Any

from app.domain.types import RawRow
from app.ingestion.readers.base import ReadContext, Reader, ReaderError, ReadResult, decode_text, register

_CONTAINER_KEYS = ("events", "data", "records", "items", "results", "shipments", "milestones")


def _flatten(obj: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in obj.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{path}."))
        else:
            flat[path] = value
    return flat


def _find_records(doc: Any) -> tuple[list[Any], str]:
    if isinstance(doc, list):
        return doc, "$"
    if isinstance(doc, dict):
        for key in _CONTAINER_KEYS:
            if isinstance(doc.get(key), list):
                return doc[key], f"$.{key}"
        for key, value in doc.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value, f"$.{key}"
        return [doc], "$"
    raise ReaderError("JSON document does not contain records")


@register
class JsonReader(Reader):
    format = "json"
    extensions = (".json", ".ndjson", ".jsonl")

    def read(self, content: bytes, ctx: ReadContext) -> ReadResult:
        text = decode_text(content).strip()
        if not text:
            raise ReaderError("File is empty")
        rows: list[RawRow] = []
        notes: list[str] = []
        try:
            records, base = _find_records(json.loads(text))
            items = [(f"{base}[{i}]", rec) for i, rec in enumerate(records)]
        except json.JSONDecodeError:
            items = []
            for n, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    items.append((f"line {n}", json.loads(line)))
                except json.JSONDecodeError as exc:
                    rows.append(RawRow(locator=f"line {n}", payload=line, parse_error=f"Invalid JSON: {exc.msg}"))
            if not items and not rows:
                raise ReaderError("File is neither valid JSON nor NDJSON") from None
            notes.append("Parsed as newline-delimited JSON")

        mapping_seen: dict[str, str] = {}
        for locator, rec in items:
            if not isinstance(rec, dict):
                rows.append(RawRow(locator=locator, payload=json.dumps(rec), parse_error="Record is not an object"))
                continue
            flat = _flatten(rec)
            fields: dict[str, Any] = {}
            for key, value in flat.items():
                canonical = ctx.matcher.match(key)
                if canonical and canonical not in fields:
                    fields[canonical] = value
                    mapping_seen.setdefault(key, canonical)
            rows.append(RawRow(locator=locator, payload=rec, fields=fields))
        return ReadResult(format=self.format, rows=rows, column_mapping=mapping_seen, notes=notes)
