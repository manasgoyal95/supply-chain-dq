"""File → normalized records. Pure (no database), so it can be unit-tested and reused from CLI, API or a worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.core.config import DomainConfig
from app.domain.milestones import MilestoneResolver
from app.domain.types import NormalizedRecord, RawRow
from app.ingestion.detect import SourceResolver, detect_format
from app.ingestion.fields import FieldMatcher
from app.ingestion.normalize import Normalizer
from app.ingestion.readers import ReadContext, get_reader


@dataclass(slots=True)
class FileResult:
    filename: str
    format: str
    source: str
    source_resolution: str
    rows: list[RawRow]
    records: list[NormalizedRecord]
    column_mapping: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, config: DomainConfig, now: datetime | None = None) -> None:
        self.config = config
        self.resolver = MilestoneResolver(config.milestones)
        self.matcher = FieldMatcher(config.sources.field_synonyms)
        self.sources = SourceResolver(config.sources)
        self.normalizer = Normalizer(config, self.resolver, now=now)

    def process(
        self, content: bytes, filename: str, source_override: str | None = None, fmt: str | None = None
    ) -> FileResult:
        fmt = fmt or detect_format(filename, content)
        result = get_reader(fmt).read(content, ReadContext(filename, self.config.sources, self.matcher))
        record_sources = [str(r.fields.get("source")) for r in result.rows if r.fields.get("source")]
        file_source, how = self.sources.for_file(filename, record_sources, source_override)
        records = []
        for row in result.rows:
            source = file_source
            if not source_override and row.fields.get("source"):
                source = self.sources.canonical(str(row.fields["source"]))
            records.append(self.normalizer.normalize(row, source))
        return FileResult(
            filename=filename,
            format=fmt,
            source=file_source,
            source_resolution=how,
            rows=result.rows,
            records=records,
            column_mapping=result.column_mapping,
            notes=result.notes,
        )

    def renormalize(self, row: RawRow, source: str) -> NormalizedRecord:
        return self.normalizer.normalize(row, source)
