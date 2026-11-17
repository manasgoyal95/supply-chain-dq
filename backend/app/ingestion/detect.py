"""Format sniffing and source resolution for uploaded files."""

from __future__ import annotations

import fnmatch
import re
from collections import Counter
from pathlib import PurePath

from app.core.config import SourcesConfig
from app.domain.milestones import alias_key
from app.ingestion.readers import registered_formats


def detect_format(filename: str, content: bytes) -> str:
    ext = PurePath(filename).suffix.lower()
    for fmt, extensions in registered_formats().items():
        if ext in extensions:
            return fmt
    head = content[:4096].lstrip()
    if head.startswith(b"PK\x03\x04"):
        return "xlsx"
    if head[:1] in (b"[", b"{"):
        return "json"
    try:
        sample = head.decode("utf-8", errors="ignore").splitlines()[:10]
    except Exception:  # pragma: no cover - defensive
        sample = []
    if len(sample) >= 2:
        for delim in (",", "\t", ";", "|"):
            counts = {line.count(delim) for line in sample if line.strip()}
            if len(counts) == 1 and next(iter(counts)) >= 2:
                return "csv"
    return "text"


class SourceResolver:
    def __init__(self, config: SourcesConfig) -> None:
        self.config = config
        self._alias: dict[str, str] = {}
        for code, definition in config.sources.items():
            for alias in [code, *definition.aliases]:
                self._alias[alias_key(alias)] = code

    def canonical(self, value: str) -> str:
        """Known alias → registered source code; unknown value → sanitized upper-case code."""
        key = alias_key(value)
        return self._alias.get(key) or re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_") or "UNKNOWN"

    def for_file(self, filename: str, record_sources: list[str], override: str | None) -> tuple[str, str]:
        """Returns (source_code, how_it_was_resolved)."""
        if override:
            return self.canonical(override), "explicit"
        values = [v for v in record_sources if v]
        if values:
            top, _ = Counter(self.canonical(v) for v in values).most_common(1)[0]
            return top, "record field"
        name = PurePath(filename).name.lower()
        for code, definition in self.config.sources.items():
            if any(fnmatch.fnmatch(name, pattern.lower()) for pattern in definition.filename_patterns):
                return code, "filename pattern"
        return self.canonical(PurePath(filename).stem), "filename stem"

    def priority(self, source: str) -> int:
        definition = self.config.sources.get(source)
        return definition.priority if definition else self.config.default_priority

    def display_name(self, source: str) -> str:
        definition = self.config.sources.get(source)
        return definition.display_name if definition else source.replace("_", " ").title()
