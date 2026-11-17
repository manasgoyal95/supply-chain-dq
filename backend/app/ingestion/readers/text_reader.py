from __future__ import annotations

import re

from app.domain.types import RawRow
from app.ingestion.readers.base import ReadContext, Reader, ReaderError, ReadResult, decode_text, register


@register
class TextReader(Reader):
    """Free-text messages (e.g. carrier e-mails). Each line may contain several messages split by a separator;
    each message is matched against the configured regex patterns (named groups = canonical fields)."""

    format = "text"
    extensions = (".txt", ".log", ".text", ".msg")

    def read(self, content: bytes, ctx: ReadContext) -> ReadResult:
        text = decode_text(content)
        if not text.strip():
            raise ReaderError("File is empty")
        cfg = ctx.config.text
        patterns = [(p.name, re.compile(p.regex, re.IGNORECASE | re.VERBOSE)) for p in cfg.patterns]
        sep = cfg.segment_separator
        rows: list[RawRow] = []
        matched_by: dict[str, int] = {}
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            segments = [s.strip() for s in (line.split(sep) if sep else [line]) if s.strip()]
            for seg_no, segment in enumerate(segments, start=1):
                locator = f"line {line_no}" + (f" · segment {seg_no}" if len(segments) > 1 else "")
                for name, regex in patterns:
                    m = regex.match(segment)
                    if m:
                        fields = {k: v for k, v in m.groupdict().items() if v not in (None, "")}
                        rows.append(RawRow(locator=locator, payload=segment, fields=fields))
                        matched_by[name] = matched_by.get(name, 0) + 1
                        break
                else:
                    rows.append(
                        RawRow(locator=locator, payload=segment, parse_error="Message did not match any known pattern")
                    )
        notes = [f"pattern '{k}' matched {v} message(s)" for k, v in matched_by.items()]
        return ReadResult(format=self.format, rows=rows, notes=notes)
