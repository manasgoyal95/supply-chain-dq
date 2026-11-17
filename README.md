# Supply Chain Data Quality Dashboard

A proof of concept that ingests shipment milestone data from fragmented source systems (spreadsheets, CSV, JSON feeds, free-text carrier messages), consolidates it per shipment, and shows how complete and trustworthy the data is, per shipment and per source, over time.

It is built for a functional manager who runs data-quality campaigns with system owners and needs to show leadership where the gaps are and whether the campaigns are working.

## Quick start

```bash
make install        # creates backend/.venv and installs dependencies
make dev            # http://localhost:8000
```

Open the portal and click **Load provided sample files**, or upload your own files on the **Ingest** tab.

Run the tests:

```bash
cd backend && .venv/bin/python -m pytest -q
```

## What it does

```
file upload ─► detect format ─► read rows ─► map columns ─► normalise ─► record rules
                                                                              │
                     consolidate per shipment (choose value per milestone, detect conflicts)
                                                                              │
                         shipment rules + scores ─► REST API (/api/v1) ─► dashboard
```

- **Any format:** `.xlsx`, `.csv`/`.tsv`, `.json`/`.ndjson`, free-text `.txt`. Header rows, column names, nested JSON and multiple messages per text line are detected automatically.
- **Any source:** files are assigned to a source system by an explicit name, a `source` field in the records, the file name pattern, or (as a fallback) the file name itself.
- **Canonical journey:** Gate In → Loaded → Departed → Arrived → Discharged → Delivered. Source-specific codes (`LOD`, `DSC`, `POD`, `Gate In`...) are mapped through an alias dictionary.
- **Quality rules:** record-level checks (missing or unknown fields, unparseable timestamps, duplicates, invalid locations) and shipment-level checks (gaps, out-of-sequence times, cross-source and same-source contradictions, conflicting carriers, stale shipments).
- **Traceability:** every value on a shipment's journey links back to the file and row it came from, with the reason it was chosen over other sources.
- **Decisions & assumptions:** every interpretation the system makes is listed in the portal with the value in effect and where to change it.

## Portal pages

| Page | Answers |
|---|---|
| Overview | How many records and shipments, how complete, how many clean / need review / unusable, which shipments have contradicting records |
| Sources | Which source systems are reliable, how each source score is calculated, field and milestone coverage, agreement between sources |
| Shipments | Each shipment's consolidated journey, the record used for each milestone and why, and every input record |
| Data Issues | Rejected, warning and duplicate records with the exact raw input |
| Trends & Campaigns | Weekly quality trends; before/after comparison for improvement campaigns |
| Ingest | Upload files and see how each was interpreted |
| Rules / Decisions & Assumptions | The full rule catalog and every design decision and assumption |

## Configuration (no code changes needed)

| File | Controls |
|---|---|
| `backend/config/milestones.yaml` | Milestone sequence and the aliases each source uses |
| `backend/config/sources.yaml` | Source registry, priority when sources disagree, column-name synonyms, text patterns, date parsing |
| `backend/config/quality.yaml` | Conflict tolerance, stale threshold, score weights |

## Project layout

```
backend/
  app/
    ingestion/   readers (csv, xlsx, json, text), format and source detection, normalisation
    quality/     rule catalog, consolidation, decisions register
    services/    store and metrics (in-memory for v1)
    web/         dashboard (single page)
    main.py      FastAPI app and REST API
  config/        YAML business configuration
  tests/         golden tests on the sample data and contradiction edge cases
data/samples/    the provided sample files
docs/            prompt log from the AI-assisted build session
```

## Status and next steps

This is v1, built in an AI-assisted session. Known limitations are listed on the portal's Decisions page. Main next steps:

- Persist data in SQLite/Postgres (currently in memory, so a restart clears it)
- Scale testing with a large generated dataset, and incremental recomputation
- Model transshipment legs (multiple arrivals per shipment)
- Per-milestone source priority
- React frontend, authentication, Docker and CI
