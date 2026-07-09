import mongomock

from app.repository import CHARGING_DATA_COLL, ChargingRepository


def test_top_up_quota_updates_string_quota_and_records_ledger(monkeypatch):
    monkeypatch.setattr("app.repository.MongoClient", mongomock.MongoClient)
    repo = ChargingRepository("mongodb://unused", "free5gc")
    repo.db[CHARGING_DATA_COLL].insert_one(
        {"ueId": "imsi-001010000000001", "ratingGroup": 1, "quota": "1000", "chargingMethod": "Online"}
    )

    ledger = repo.top_up_quota(
        ue_id="imsi-001010000000001",
        rating_group=1,
        amount_bytes=500,
        actor="tester",
        source="operator",
    )

    record = repo.db[CHARGING_DATA_COLL].find_one({"ueId": "imsi-001010000000001", "ratingGroup": 1})
    assert record["quota"] == "1500"
    assert ledger["oldQuota"] == 1000
    assert ledger["newQuota"] == 1500
    assert repo.db["chargingPortal.topups"].count_documents({}) == 1


def test_top_up_missing_record_raises_lookup_error(monkeypatch):
    monkeypatch.setattr("app.repository.MongoClient", mongomock.MongoClient)
    repo = ChargingRepository("mongodb://unused", "free5gc")

    try:
        repo.top_up_quota(
            ue_id="imsi-missing",
            rating_group=1,
            amount_bytes=500,
            actor="tester",
            source="operator",
        )
    except LookupError as exc:
        assert "charging record not found" in str(exc)
    else:
        raise AssertionError("expected LookupError")
