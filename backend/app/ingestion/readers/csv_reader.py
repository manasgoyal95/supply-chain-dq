from __future__ import annotations

import csv
import io

from app.ingestion.readers.base import ReadContext, Reader, ReaderError, ReadResult, decode_text, register, tabular_rows


@register
class CsvReader(Reader):
    format = "csv"
    extensions = (".csv", ".tsv", ".psv")

    def read(self, content: bytes, ctx: ReadContext) -> ReadResult:
        text = decode_text(content)
        if not text.strip():
            raise ReaderError("File is empty")
        sample = text[:20_000]
        try:
            dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel_tab if ctx.filename.lower().endswith(".tsv") else csv.excel
        reader = csv.reader(io.StringIO(text), dialect)
        grid = [(f"row {i}", list(cells)) for i, cells in enumerate(reader, start=1)]
        return tabular_rows(grid, ctx, self.format)
