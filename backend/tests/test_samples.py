"""Golden expectations for the provided sample files, derived by hand from the raw data."""

from app.domain.types import ShipmentStatus


def codes(store, sid):
    return {i.code for i in store.shipments[sid].issues}


def test_record_counts(store):
    ov = store.overview()
    assert ov["events_received"] == 135  # 39 carrier messages + 47 JSON objects + 49 spreadsheet rows
    assert ov["record_status"] == {"VALID": 130, "WARNING": 0, "REJECTED": 3, "DUPLICATE": 2}
    assert ov["shipments"] == 32
    assert ov["shipment_status"] == {"CLEAN": 13, "NEEDS_REVIEW": 16, "UNUSABLE": 3}


def test_sources_detected(store):
    assert {r.filename: r.source for r in store.runs} == {
        "carrier_updates.txt": "CARRIER", "db_feed.json": "SYSTEM_FEED", "ops_upload.xlsx": "OPS_UPLOAD"}


def test_complete_only_when_sources_combined(store):
    v = store.shipments["SHP0001"]
    assert v.completeness == 1 and v.status == ShipmentStatus.CLEAN and v.score == 100
    assert {m.value: g.source for m, g in v.golden.items()} == {
        "GATE_IN": "OPS_UPLOAD", "LOADED": "SYSTEM_FEED", "DEPARTED": "CARRIER",
        "ARRIVED": "OPS_UPLOAD", "DISCHARGED": "SYSTEM_FEED", "DELIVERED": "CARRIER"}


def test_rejections(store):
    rejected = {s.rec.shipment_id: {i.code for i in s.rec.issues} for s in store.records if s.rec.status == "REJECTED"}
    assert "R005" in rejected["SHP0030"]   # 'invalid-date'
    assert "R004" in rejected["SHP0031"]   # 'DELIVERED. Location unknown.' has no time
    assert "R003" in rejected["SHP0032"]   # event code UNKNOWN
    for sid in ("SHP0030", "SHP0031", "SHP0032"):
        assert store.shipments[sid].status == ShipmentStatus.UNUSABLE


def test_duplicates(store):
    dups = sorted((s.filename, s.rec.shipment_id) for s in store.records if s.rec.status == "DUPLICATE")
    assert dups == [("carrier_updates.txt", "SHP0002"), ("ops_upload.xlsx", "SHP0001")]


def test_pod_alias(store):
    assert [m.value for m in store.shipments["SHP0029"].golden] == ["DELIVERED"]


def test_cross_source_conflicts(store):
    for sid in ("SHP0021", "SHP0022", "SHP0023", "SHP0024", "SHP0025", "SHP0026"):
        assert "S003" in codes(store, sid)  # departure 2h apart between SYSTEM_FEED and CARRIER
    for sid in ("SHP0022", "SHP0024", "SHP0026"):
        assert "S004" in codes(store, sid)  # arrival SGSIN (ops) vs NLRTM (carrier)
    assert "S003" not in codes(store, "SHP0015")  # both sources say 08:00: agreement, not conflict
    assert store.overview()["shipments_with_contradictions"] == 7  # SHP0021-26 + SHP0028


def test_sequence_and_gaps(store):
    assert {"S001", "S002"} <= codes(store, "SHP0028")  # ARR 11:00 before DEP 14:00
    assert "S001" in codes(store, "SHP0027")            # delivered with nothing before


def test_issue_units_count_each_shipment_once(store):
    by = {(i["code"], i["unit"]): i["count"] for i in store.overview()["issues_by_rule"]}
    assert by[("S003", "shipments")] == 6 and by[("R006", "records")] == 2
