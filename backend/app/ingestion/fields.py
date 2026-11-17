"""Header/key → canonical field matching driven by `field_synonyms` in sources.yaml."""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def header_key(value: object) -> str:
    return _NON_ALNUM.sub("", str(value).strip().lower()) if value is not None else ""


class FieldMatcher:
    def __init__(self, synonyms: dict[str, list[str]]) -> None:
        self._lookup: dict[str, str] = {}
        for canonical, names in synonyms.items():
            for name in [canonical, *names]:
                self._lookup.setdefault(header_key(name), canonical)

    def match(self, header: object) -> str | None:
        key = header_key(header)
        if not key:
            return None
        if key in self._lookup:
            return self._lookup[key]
        # nested JSON keys such as "event.code" → try the last path component
        if "." in str(header):
            return self._lookup.get(header_key(str(header).rsplit(".", 1)[-1]))
        return None

    def map_columns(self, headers: list[object]) -> dict[int, str]:
        """Column index → canonical field. The first column wins if two columns map to the same field."""
        mapping: dict[int, str] = {}
        taken: set[str] = set()
        for idx, header in enumerate(headers):
            canonical = self.match(header)
            if canonical and canonical not in taken:
                mapping[idx] = canonical
                taken.add(canonical)
        return mapping

    def looks_like_header(self, cells: list[object]) -> bool:
        matched = {self.match(c) for c in cells if c is not None} - {None}
        return len(matched) >= 2 and bool(matched & {"shipment_id", "milestone", "event_time"})
