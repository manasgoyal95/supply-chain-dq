from __future__ import annotations

import io
import zipfile

from openpyxl import load_workbook

from app.ingestion.readers.base import ReadContext, Reader, ReaderError, ReadResult, register, tabular_rows


@register
class XlsxReader(Reader):
    format = "xlsx"
    extensions = (".xlsx", ".xlsm")

    def read(self, content: bytes, ctx: ReadContext) -> ReadResult:
        try:
            wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except (zipfile.BadZipFile, KeyError, OSError) as exc:
            raise ReaderError(f"Not a valid Excel workbook: {exc}") from exc
        try:
            results: list[ReadResult] = []
            errors: list[str] = []
            for ws in wb.worksheets:
                grid = [
                    (f"sheet '{ws.title}' row {i}", list(cells))
                    for i, cells in enumerate(ws.iter_rows(values_only=True), start=1)
                ]
                if not grid:
                    continue
                try:
                    res = tabular_rows(grid, ctx, self.format)
                except ReaderError as exc:
                    errors.append(f"{ws.title}: {exc}")
                    continue
                if res.column_mapping:
                    results.append(res)
        finally:
            wb.close()
        if not results:
            raise ReaderError("No worksheet with recognisable shipment data" + (f" ({'; '.join(errors)})" if errors else ""))
        merged = results[0]
        for extra in results[1:]:
            merged.rows.extend(extra.rows)
            merged.notes.extend(extra.notes)
        if len(results) > 1:
            merged.notes.append(f"Read {len(results)} worksheets")
        return merged
