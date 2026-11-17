"""Edge cases: entries for the same shipment in different files that contradict each other."""

from app.domain.types import ShipmentStatus

SECOND_OPS_FILE = b"""Shipment No,Carrier,Milestone,Event Time,Location
SHP0001,HLCU,Gate In,2026-05-02 18:00 UTC,INMAA
SHP0002,MSCU,Gate In,2026-05-03 20:00 UTC,INNSA
SHP0003,MSCU,Gate In,2026-05-05 02:00 UTC,INMAA
SHP0004,ONEY,Delivered,2026-05-06 23:00 UTC,DEHAM
SHP0005,MAEU,Delivered,2026-05-08 09:00 UTC,NLRTM
SHP0006,HLCU,Gate In,2026-05-08 20:00 UTC,INMAA
SHP0007,MSCU,Gate In,2026-05-10 02:30 UTC,INMAA
"""


def codes(store, sid):
    return {i.code for i in store.shipments[sid].issues}


def test_contradicting_second_file(store):
    run = store.ingest(SECOND_OPS_FILE, "ops_upload_week2.csv")
    assert run.source == "OPS_UPLOAD"
    assert "S009" in codes(store, "SHP0001")   # same source, Gate In 14:00 vs 18:00
    assert "S009" in codes(store, "SHP0002")   # same source, same time, INMAA vs INNSA
    assert "S005" in codes(store, "SHP0003")   # carrier CMDU elsewhere vs MSCU here
    assert "S004" in codes(store, "SHP0004")   # delivered NLRTM (carrier) vs DEHAM (ops)
    assert "S003" in codes(store, "SHP0005")   # delivered 05:00 (carrier) vs 09:00 (ops)
    # ops has higher priority than carrier, so the ops value is shown, and the carrier value kept as alternate
    assert store.shipments["SHP0005"].golden[next(m for m in store.shipments["SHP0005"].golden
                                                   if m.value == "DELIVERED")].source == "OPS_UPLOAD"
    # exact repeat is a duplicate, not a contradiction
    assert "S009" not in codes(store, "SHP0006")
    assert any(s.rec.status == "DUPLICATE" and s.filename == "ops_upload_week2.csv" for s in store.records)
    # 30 minutes apart is within the 60 minute tolerance
    assert "S009" not in codes(store, "SHP0007")
    for sid in ("SHP0001", "SHP0002", "SHP0003", "SHP0004", "SHP0005"):
        assert store.shipments[sid].status == ShipmentStatus.NEEDS_REVIEW
    # conflict messages point at the exact file and row of both sides
    msg = next(i.message for i in store.shipments["SHP0001"].issues if i.code == "S009")
    assert "ops_upload.xlsx" in msg and "ops_upload_week2.csv" in msg


def test_same_file_twice_is_skipped(store):
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "data" / "samples" / "db_feed.json"
    assert store.ingest(p.read_bytes(), "db_feed.json").status == "SKIPPED_ALREADY_INGESTED"
    assert store.overview()["events_received"] == 135
