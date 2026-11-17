"""Turns mapped raw fields into canonical values (UTC timestamps, canonical milestones, UN/LOCODEs) and applies
record-level quality rules."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import DomainConfig
from app.domain.milestones import MilestoneResolver
from app.domain.types import NormalizedRecord, RawRow, RecordStatus, Severity
from app.quality.rules import issue

LOCODE_RE = re.compile(r"^[A-Z]{2}[A-Z2-9]{3}$")
_TZ_SUFFIX = re.compile(r"\s*(?:UTC|GMT|Z)$", re.IGNORECASE)
_OFFSET = re.compile(r"\s*([+-])(\d{2}):?(\d{2})$")
_EXCEL_EPOCH = datetime(1899, 12, 30, tzinfo=UTC)

_DATE_PARTS_DMY = ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"]
_DATE_PARTS_MDY = ["%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y", "%m/%d/%y"]
_DATE_PARTS_OTHER = ["%Y/%m/%d", "%Y.%m.%d", "%d-%b-%Y", "%d %b %Y", "%b %d %Y", "%d %B %Y", "%B %d %Y", "%Y%m%d"]
_TIME_PARTS = ["", " %H:%M", " %H:%M:%S", "T%H:%M", "T%H:%M:%S", " %I:%M %p"]


class TimestampError(ValueError):
    pass


class Normalizer:
    def __init__(self, config: DomainConfig, resolver: MilestoneResolver, now: datetime | None = None) -> None:
        self.cfg = config
        self.resolver = resolver
        self.now = now or datetime.now(UTC)
        parsing = config.sources.parsing
        self.null_tokens = {t.strip().lower() for t in parsing.null_tokens}
        self.tz = ZoneInfo(parsing.assume_timezone)
        dates = (_DATE_PARTS_DMY + _DATE_PARTS_MDY) if parsing.day_first else (_DATE_PARTS_MDY + _DATE_PARTS_DMY)
        self._formats = [d + t for d in dates + _DATE_PARTS_OTHER for t in _TIME_PARTS]
        self.future_tolerance = timedelta(hours=config.quality.future_tolerance_hours)

    # ------------------------------------------------------------------ field normalisers

    def clean(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return None if stripped.lower() in self.null_tokens else stripped
        return value

    def shipment_id(self, value: Any) -> str | None:
        value = self.clean(value)
        if value is None:
            return None
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return re.sub(r"\s+", "", str(value)).upper() or None

    def location(self, value: Any) -> str | None:
        value = self.clean(value)
        return re.sub(r"\s+", "", str(value)).upper() if value is not None else None

    def carrier(self, value: Any) -> str | None:
        value = self.clean(value)
        return str(value).strip().upper() if value is not None else None

    def timestamp(self, value: Any) -> datetime | None:
        """Returns a tz-aware UTC datetime, None if missing, or raises TimestampError if unparseable."""
        value = self.clean(value)
        if value is None:
            return None
        if isinstance(value, datetime):
            return self._to_utc(value)
        if isinstance(value, date):
            return self._to_utc(datetime(value.year, value.month, value.day))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return self._from_number(float(value))
        text = str(value).strip()
        if re.fullmatch(r"\d+(\.\d+)?", text):
            return self._from_number(float(text))
        tz_override = None
        if _TZ_SUFFIX.search(text):
            text = _TZ_SUFFIX.sub("", text)
            tz_override = UTC
        else:
            m = _OFFSET.search(text)
            if m and re.search(r"\d{1,2}:\d{2}", text[: m.start()]):
                text = text[: m.start()] + f"{m.group(1)}{m.group(2)}:{m.group(3)}"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = self._strptime(text)
        if tz_override and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz_override)
        return self._to_utc(parsed)

    def _strptime(self, text: str) -> datetime:
        offset = None
        m = _OFFSET.search(text)
        if m:
            sign = 1 if m.group(1) == "+" else -1
            offset = timedelta(hours=int(m.group(2)), minutes=int(m.group(3))) * sign
            text = text[: m.start()]
        for fmt in self._formats:
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue
            if offset is not None:
                return (parsed - offset).replace(tzinfo=UTC)
            return parsed
        raise TimestampError(f"unrecognised timestamp '{text}'")

    def _from_number(self, number: float) -> datetime:
        if 20_000 < number < 80_000:  # Excel serial date
            return _EXCEL_EPOCH + timedelta(days=number)
        if 1e9 <= number < 1e11:
            return datetime.fromtimestamp(number, tz=UTC)
        if 1e12 <= number < 1e14:
            return datetime.fromtimestamp(number / 1000, tz=UTC)
        raise TimestampError(f"numeric value {number} is not a recognisable date")

    def _to_utc(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.tz)
        return dt.astimezone(UTC)

    # ------------------------------------------------------------------ record

    def normalize(self, row: RawRow, source: str) -> NormalizedRecord:
        f = row.fields
        ts_raw = f.get("event_time")
        # milestone: only blank counts as missing; tokens like "UNKNOWN" are reported as unrecognised codes
        ms_val = f.get("milestone")
        ms_raw = str(ms_val).strip() if ms_val is not None and str(ms_val).strip() else None
        rec = NormalizedRecord(
            source=source,
            locator=row.locator,
            shipment_id=self.shipment_id(f.get("shipment_id")),
            milestone=self.resolver.resolve(ms_raw) if ms_raw is not None else None,
            milestone_raw=str(ms_raw) if ms_raw is not None else None,
            event_ts=None,
            timestamp_raw=str(ts_raw).strip() if ts_raw is not None else None,
            location=self.location(f.get("location")),
            carrier=self.carrier(f.get("carrier")),
        )
        if row.parse_error:
            rec.issues.append(issue("R000", row.parse_error))
            rec.status = RecordStatus.REJECTED
            return rec

        if rec.shipment_id is None:
            rec.issues.append(issue("R001"))
        if ms_raw is None:
            rec.issues.append(issue("R002"))
        elif rec.milestone is None:
            rec.issues.append(issue("R003", f"Milestone '{ms_raw}' is not a recognised code", value=str(ms_raw)))
        try:
            rec.event_ts = self.timestamp(ts_raw)
            if rec.event_ts is None:
                rec.issues.append(issue("R004"))
            elif rec.event_ts > self.now + self.future_tolerance:
                rec.issues.append(issue("R009", f"Timestamp {rec.event_ts.isoformat()} is in the future"))
        except (TimestampError, ValueError, OverflowError):
            rec.issues.append(issue("R005", f"Timestamp '{ts_raw}' could not be parsed", value=str(ts_raw)))
        if rec.location is None:
            rec.issues.append(issue("R007"))
        elif not LOCODE_RE.match(rec.location):
            rec.issues.append(issue("R008", f"'{rec.location}' is not a valid UN/LOCODE", value=rec.location))

        rec.status = status_from_issues(rec)
        return rec


def status_from_issues(rec: NormalizedRecord) -> RecordStatus:
    severities = {i.severity for i in rec.issues}
    if Severity.ERROR in severities:
        return RecordStatus.REJECTED
    if any(i.code == "R006" for i in rec.issues):
        return RecordStatus.DUPLICATE
    if Severity.WARNING in severities:
        return RecordStatus.WARNING
    return RecordStatus.VALID


def mark_duplicates(records: list[NormalizedRecord], seen: dict[tuple, str], ref: str) -> int:
    """Flags records already seen from the same source (in earlier files or earlier in this file).

    `seen` maps dedupe key -> human reference of the first occurrence ("file · position"); it is mutated.
    `ref` is the file name of the batch being processed.
    """
    count = 0
    for rec in records:
        key = rec.dedupe_key()
        if key is None:
            continue
        if key in seen:
            rec.issues.append(issue("R006", f"Duplicate of {seen[key]} (same source, shipment, milestone, time, "
                                            "location and carrier)", duplicate_of=seen[key]))
            rec.status = RecordStatus.DUPLICATE
            count += 1
        else:
            seen[key] = f"{ref} · {rec.locator}"
    return count
