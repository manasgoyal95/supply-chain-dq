"""Decision & assumption register shown on the portal.

Every interpretation the system makes that is not dictated by the raw data is listed here, with the *live* value
taken from config, why it was chosen, and where to change it. UI elements link to these ids (D01, D02, ...).
"""

from __future__ import annotations

from typing import Any

from app.core.config import DomainConfig


def build_decisions(cfg: DomainConfig) -> list[dict[str, Any]]:
    q, s = cfg.quality, cfg.sources
    prio = sorted(s.sources.items(), key=lambda kv: kv[1].priority)
    aliases = {m.code: ", ".join(m.aliases) for m in cfg.milestones.sequence}
    w, sw = q.shipment_score_weights, q.source_score_weights
    return [
        dict(id="D01", area="Ingestion", type="Design choice", title="Milestone codes are mapped by an alias dictionary",
             decision="Each source's label/code is mapped to one of the 6 canonical milestones (case and punctuation "
                      "ignored). Codes not in the dictionary are rejected (R003), never guessed.",
             rationale="Sources name the same milestone differently (LOD, Loaded, Vessel loaded...). An explicit "
                       "dictionary is auditable; fuzzy guessing would silently create wrong journeys.",
             current_value=aliases, change="backend/config/milestones.yaml"),
        dict(id="D02", area="Ingestion", type="Assumption", title="Carrier 'POD' means Delivered",
             decision="'POD' (proof of delivery) in carrier messages is treated as DELIVERED (shipment SHP0029).",
             rationale="Proof of delivery is issued when cargo reaches the receiver, matching the DELIVERED definition.",
             current_value="POD → DELIVERED", change="backend/config/milestones.yaml", confirm=True),
        dict(id="D03", area="Ingestion", type="Design choice", title="How each file is assigned to a source",
             decision="Order: (1) source typed by the uploader, (2) a 'source' field inside the records, "
                      "(3) file-name pattern, (4) the file name itself becomes a new source.",
             rationale="db_feed.json declares source='JSON' in every record, which is mapped to SYSTEM_FEED; "
                       "ops_upload.xlsx and carrier_updates.txt are identified by file name. The Ingest page shows "
                       "which rule was used for every file.",
             current_value={k: {"aliases": v.aliases, "file_patterns": v.filename_patterns} for k, v in s.sources.items()},
             change="backend/config/sources.yaml"),
        dict(id="D04", area="Ingestion", type="Assumption", title="Timestamps without a time zone are UTC",
             decision=f"All times are converted to and displayed in UTC. A timestamp with no zone is assumed to be "
                      f"{s.parsing.assume_timezone}. Ambiguous dates like 03/04/2026 are read "
                      f"{'day-first (3 April)' if s.parsing.day_first else 'month-first (4 March)'}.",
             rationale="All three sample files are explicitly UTC, so no sample value depends on this assumption.",
             current_value={"assume_timezone": s.parsing.assume_timezone, "day_first": s.parsing.day_first},
             change="backend/config/sources.yaml → parsing", confirm=True),
        dict(id="D05", area="Record quality", type="Design choice", title="When a record is rejected",
             decision="A record is REJECTED if it has no shipment id, no/unknown milestone, or a missing/unparseable "
                      "timestamp. Rejected records are excluded from the journey but still counted in quality metrics.",
             rationale="Without these three fields a record cannot be placed on any shipment timeline.",
             current_value="R000–R005 = reject; R007–R009 = warning", change="backend/app/quality/rules.py"),
        dict(id="D06", area="Record quality", type="Design choice", title="What counts as a duplicate",
             decision="A record is a DUPLICATE when the SAME source already sent the same shipment, milestone, time, "
                      "location and carrier. If any value differs it is treated as a contradiction instead (D08). Duplicates are ignored for the journey and reported in the source's duplicate "
                      "rate. The same event reported by two DIFFERENT sources is not a duplicate; it is "
                      "corroboration (or a conflict, see D08).",
             rationale="Repeated rows inflate counts; cross-source agreement is useful evidence and is kept.",
             current_value="key = source + shipment + milestone + time + location + carrier", change="code"),
        dict(id="D07", area="Consolidation", type="Assumption", title="Source priority decides the value shown when sources disagree",
             decision="For each milestone, the value from the highest-priority source is used (then the earliest "
                      "time within that source). Other values are kept as alternates and shown on the shipment page.",
             rationale="The system feed is machine-generated and assumed most reliable, then ops uploads (manual), "
                       "then free-text carrier messages. This order is an assumption and should be confirmed per "
                       "milestone with the business (e.g. carrier may be the authority for Departed).",
             current_value=" > ".join(f"{k} ({v.priority})" for k, v in prio),
             change="backend/config/sources.yaml → priority", confirm=True),
        dict(id="D08", area="Consolidation", type="Design choice", title="When two sources are 'in conflict'",
             decision=f"Records contradict each other when, for the same shipment and milestone, their times differ by "
                      f"more than {q.conflict_time_tolerance_minutes} minutes or their locations differ. Between two "
                      f"sources this is S003 (time) / S004 (location); within ONE source (e.g. two ops uploads that "
                      f"disagree) it is S009. Different carrier codes for one shipment are S005, and times that break "
                      f"the milestone order are S002. All of these mark the shipment NEEDS REVIEW and are counted as "
                      f"'contradictions'. A missing value on one side is not a contradiction.",
             rationale="Small differences are normal between systems; larger ones mean one of them is wrong. "
                       "The system never silently picks a side: the value used (D07) and every contradicting record, "
                       "with its file and row, are shown on the shipment page.",
             current_value=f"{q.conflict_time_tolerance_minutes} minutes", change="backend/config/quality.yaml",
             confirm=True),
        dict(id="D09", area="Consolidation", type="Known limitation", title="One event per milestone (no transshipment legs)",
             decision="Each shipment has at most one Arrived, one Departed, etc. If two sources report Arrived at "
                      "different ports (e.g. SGSIN vs NLRTM for SHP0022/24/26) it is flagged as a location conflict.",
             rationale="The primer defines a single 6-step journey. A real route may have transshipment stops, so "
                       "some of these 'conflicts' could both be true. Modelling route legs is a planned improvement.",
             current_value="single journey of 6 milestones", change="code (planned)"),
        dict(id="D10", area="Shipment quality", type="Design choice", title="Completeness = milestones present ÷ 6",
             decision="Every milestone has equal weight. A milestone counts as present if at least one usable record "
                      "(valid or warning) reports it, from any source.",
             rationale="Simple and explainable; matches 'how much of the expected journey is present' in the primer.",
             current_value="6 milestones, equal weight", change="code"),
        dict(id="D11", area="Shipment quality", type="Design choice", title="Gaps vs not-yet-happened",
             decision="Only milestones missing BEFORE the latest milestone reached are gaps (S001). Milestones after "
                      "the latest one are treated as not yet happened, unless the shipment is stale (D12).",
             rationale="A shipment that has departed but not arrived is not a data error; one that is delivered "
                       "without ever departing is.",
             current_value="—", change="code"),
        dict(id="D12", area="Shipment quality", type="Assumption", title="Stale = no update for N days, measured against the data's as-of date",
             decision=f"An undelivered shipment is STALE (S006, needs review) if its last event is more than "
                      f"{q.stale_after_days} days before the data as-of date (the latest valid event in all data, shown in "
                      f"the header), not before today.",
             rationale="The sample data is historical (May–June 2026). Measuring against today would flag every "
                       "undelivered shipment. Note: uploading newer data moves the as-of date and can make more "
                       "shipments stale.",
             current_value=f"{q.stale_after_days} days", change="backend/config/quality.yaml", confirm=True),
        dict(id="D13", area="Shipment quality", type="Design choice", title="Shipment status definitions",
             decision="UNUSABLE = every record was rejected. NEEDS REVIEW = at least one warning (gap, out of sequence, "
                      "conflict, stale, or a record warning). CLEAN = no warnings. CLEAN describes data QUALITY only: "
                      "a clean shipment can still be incomplete (in transit). Completeness is shown separately.",
             rationale="Keeps 'is the data trustworthy?' separate from 'how far has the shipment got?'.",
             current_value="CLEAN / NEEDS_REVIEW / UNUSABLE", change="code"),
        dict(id="D14", area="Scoring", type="Design choice", title="Shipment quality score (0–100)",
             decision=f"score = 100 × ({w['completeness']} × completeness + {w['consistency']} × consistency + "
                      f"{w['validity']} × validity). Consistency starts at 1, minus "
                      f"{q.consistency_penalties['out_of_sequence']} per out-of-sequence pair and "
                      f"{q.consistency_penalties['conflict']} per conflicting milestone. Validity = average record "
                      f"credit (valid {q.record_validity_credit['VALID']}, warning {q.record_validity_credit['WARNING']}, "
                      f"rejected {q.record_validity_credit['REJECTED']}; duplicates excluded). Unusable shipments score 0.",
             rationale="Completeness matters most to CX agents; consistency and validity capture trust.",
             current_value={"weights": w, "penalties": q.consistency_penalties, "credit": q.record_validity_credit},
             change="backend/config/quality.yaml"),
        dict(id="D15", area="Scoring", type="Design choice", title="Source quality score (0–100)",
             decision=f"score = 100 × ({sw['validity']} × validity + {sw['field_completeness']} × field completeness + "
                      f"{sw['uniqueness']} × uniqueness + {sw['agreement']} × agreement). Field completeness averages "
                      "5 fields (shipment id, milestone, timestamp, location, carrier). A field the source never "
                      "supplies (e.g. carrier code in carrier messages) counts as 0%. Agreement = share of overlapping "
                      "milestones that agree with other sources (100% if no overlaps).",
             rationale="Measures how usable each system's data is on its own and against the others.",
             current_value=sw, change="backend/config/quality.yaml"),
        dict(id="D16", area="Trends & campaigns", type="Design choice", title="Trends are grouped by event week, not upload date",
             decision="Records are placed in the ISO week of their event time. Records with no usable time are placed "
                      "in the week of the latest event in the same file. Shipment cohorts use the week of the "
                      "shipment's first event.",
             rationale="Uploads in the demo all happen today, so upload date would show no trend.",
             current_value="ISO week (Mon–Sun), UTC", change="code"),
        dict(id="D17", area="Trends & campaigns", type="Known limitation", title="Recent weeks look less complete",
             decision="Shipments that started recently have not finished their journey yet, so recent cohorts show "
                      "lower completeness even if data capture is fine.",
             rationale="Read the last 1–2 weeks of the shipment cohort chart with care.",
             current_value="—", change="planned: compare against expected progress"),
        dict(id="D18", area="Trends & campaigns", type="Design choice", title="Campaign before/after comparison",
             decision="'Before' = all records of the target source(s) dated before the campaign start; 'after' = "
                      "records from the start date (until the end date, if set). Windows are not equal length and "
                      "small samples swing strongly; record counts are shown next to every comparison.",
             rationale="Simple and transparent for a proof of concept.",
             current_value="—", change="planned: equal windows, significance"),
        dict(id="D19", area="Platform", type="Known limitation", title="Data is held in memory",
             decision="Ingested data and campaigns are lost when the server restarts. Re-upload or use "
                      "'Load provided sample files'.",
             rationale="Demo build; database persistence is the next step.",
             current_value="in-memory", change="planned: SQLite/Postgres"),
        dict(id="D20", area="Ingestion", type="Design choice", title="Same file uploaded twice is skipped",
             decision="A file with identical content (SHA-256) to an earlier upload is skipped, so metrics are not "
                      "double counted. A different file containing the same events is ingested and its repeated "
                      "events are marked DUPLICATE (D06).",
             rationale="Makes uploads safe to repeat.",
             current_value="SHA-256 content hash", change="code"),
    ]
