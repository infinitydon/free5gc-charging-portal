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


def test_top_up_transient_rating_group_updates_durable_data_plan_records(monkeypatch):
    monkeypatch.setattr("app.repository.MongoClient", mongomock.MongoClient)
    repo = ChargingRepository("mongodb://unused", "free5gc")
    repo.db[CHARGING_DATA_COLL].insert_many(
        [
            {
                "ueId": "imsi-001",
                "snssai": "01010203",
                "dnn": "internet",
                "filter": "any",
                "ratingGroup": 0,
                "quota": "1000",
                "chargingMethod": "Online",
            },
            {
                "ueId": "imsi-001",
                "snssai": "01112233",
                "dnn": "internet",
                "filter": "any",
                "ratingGroup": 0,
                "quota": "1000",
                "chargingMethod": "Online",
            },
            {
                "ueId": "imsi-001",
                "snssai": "01112233",
                "dnn": "internet",
                "filter": "any",
                "ratingGroup": 1748,
                "quota": "-100",
                "chargingMethod": "Online",
            },
        ]
    )

    ledger = repo.top_up_quota(
        ue_id="imsi-001",
        rating_group=1748,
        amount_bytes=500,
        actor="tester",
        source="self-service",
    )

    durable = list(repo.db[CHARGING_DATA_COLL].find({"ueId": "imsi-001", "ratingGroup": 0}).sort("snssai", 1))
    transient = repo.db[CHARGING_DATA_COLL].find_one({"ueId": "imsi-001", "ratingGroup": 1748})
    assert [record["quota"] for record in durable] == ["1500", "1500"]
    assert transient["quota"] == "400"
    assert ledger["oldQuota"] == -100
    assert ledger["newQuota"] == 400


def test_charging_records_list_newest_online_buckets_first(monkeypatch):
    monkeypatch.setattr("app.repository.MongoClient", mongomock.MongoClient)
    repo = ChargingRepository("mongodb://unused", "free5gc")
    repo.db[CHARGING_DATA_COLL].insert_many(
        [
            {"ueId": "imsi-001", "ratingGroup": 3, "quota": "0", "chargingMethod": "Offline"},
            {"ueId": "imsi-001", "ratingGroup": 9, "quota": "1000", "chargingMethod": "Online"},
            {"ueId": "imsi-001", "ratingGroup": 4, "quota": "1000", "chargingMethod": "Online"},
        ]
    )

    records = repo.list_charging_records("imsi-001", actionable_only=True)

    assert [record["ratingGroup"] for record in records] == [9, 4, 3]


def test_active_plan_records_hide_base_policy_buckets(monkeypatch):
    monkeypatch.setattr("app.repository.MongoClient", mongomock.MongoClient)
    repo = ChargingRepository("mongodb://unused", "free5gc")
    repo.db[CHARGING_DATA_COLL].insert_many(
        [
            {"ueId": "imsi-001", "snssai": "01010203", "dnn": "", "filter": "", "quota": "1000", "chargingMethod": "Online"},
            {"ueId": "imsi-001", "snssai": "01010203", "dnn": "internet", "filter": "any", "quota": "1000", "chargingMethod": "Online"},
            {"ueId": "imsi-001", "snssai": "01010203", "dnn": "internet", "filter": "any", "ratingGroup": 33, "quota": "750", "chargingMethod": "Online"},
            {"ueId": "imsi-001", "snssai": "01112233", "dnn": "internet", "filter": "any", "ratingGroup": 44, "quota": "500", "chargingMethod": "Online"},
        ]
    )

    records = repo.list_active_plan_records("imsi-001")

    assert [(record.get("snssai"), record.get("ratingGroup"), record.get("quota")) for record in records] == [
        ("01112233", 44, "500"),
        ("01010203", 33, "750"),
    ]


def test_current_plan_records_select_one_billable_plan_per_subscriber(monkeypatch):
    monkeypatch.setattr("app.repository.MongoClient", mongomock.MongoClient)
    repo = ChargingRepository("mongodb://unused", "free5gc")
    repo.db[CHARGING_DATA_COLL].insert_many(
        [
            {"ueId": "imsi-001", "snssai": "01010203", "dnn": "internet", "filter": "any", "quota": "1000", "chargingMethod": "Online"},
            {"ueId": "imsi-001", "snssai": "01112233", "dnn": "internet", "filter": "any", "ratingGroup": 44, "quota": "500", "chargingMethod": "Online"},
        ]
    )

    records = repo.list_current_plan_records("imsi-001")

    assert len(records) == 1
    assert records[0]["ratingGroup"] == 44
    assert records[0]["quota"] == "500"


def test_record_usage_debits_latest_online_bucket_by_delta(monkeypatch):
    monkeypatch.setattr("app.repository.MongoClient", mongomock.MongoClient)
    repo = ChargingRepository("mongodb://unused", "free5gc")
    repo.db[CHARGING_DATA_COLL].insert_many(
        [
            {"ueId": "imsi-001", "ratingGroup": 4, "quota": "1000", "chargingMethod": "Online"},
            {"ueId": "imsi-001", "ratingGroup": 9, "quota": "2000", "chargingMethod": "Online"},
        ]
    )

    first = repo.record_usage(ue_id="imsi-001", rx_bytes=300, tx_bytes=200, source="ue-tunnel")
    second = repo.record_usage(ue_id="imsi-001", rx_bytes=500, tx_bytes=250, source="ue-tunnel")
    repeated = repo.record_usage(ue_id="imsi-001", rx_bytes=500, tx_bytes=250, source="ue-tunnel")

    record = repo.db[CHARGING_DATA_COLL].find_one({"ueId": "imsi-001", "ratingGroup": 9})
    assert first["deltaBytes"] == 500
    assert second["deltaBytes"] == 250
    assert repeated["deltaBytes"] == 0
    assert record["quota"] == "1250"


def test_missing_rating_group_is_actionable_as_zero(monkeypatch):
    monkeypatch.setattr("app.repository.MongoClient", mongomock.MongoClient)
    repo = ChargingRepository("mongodb://unused", "free5gc")
    repo.db[CHARGING_DATA_COLL].insert_one(
        {"ueId": "imsi-001", "quota": "1000", "chargingMethod": "Online", "dnn": "internet"}
    )

    records = repo.list_charging_records("imsi-001", actionable_only=True)
    ledger = repo.top_up_quota(
        ue_id="imsi-001",
        rating_group=0,
        amount_bytes=500,
        actor="tester",
        source="self-service",
    )

    record = repo.db[CHARGING_DATA_COLL].find_one({"ueId": "imsi-001"})
    assert records[0]["ratingGroup"] == 0
    assert ledger["newQuota"] == 1500
    assert record["quota"] == "1500"


def test_record_usage_debits_online_bucket_without_rating_group(monkeypatch):
    monkeypatch.setattr("app.repository.MongoClient", mongomock.MongoClient)
    repo = ChargingRepository("mongodb://unused", "free5gc")
    repo.db[CHARGING_DATA_COLL].insert_one(
        {"ueId": "imsi-001", "quota": "1000", "chargingMethod": "Online", "dnn": "internet"}
    )

    ledger = repo.record_usage(ue_id="imsi-001", rx_bytes=300, tx_bytes=200, source="ue-tunnel")

    record = repo.db[CHARGING_DATA_COLL].find_one({"ueId": "imsi-001"})
    assert ledger["ratingGroup"] == 0
    assert ledger["newQuota"] == 500
    assert record["quota"] == "500"


def test_user_account_hides_internal_bucket_shape(monkeypatch):
    monkeypatch.setattr("app.repository.MongoClient", mongomock.MongoClient)
    repo = ChargingRepository("mongodb://unused", "free5gc")
    repo.db[CHARGING_DATA_COLL].insert_one(
        {"ueId": "imsi-001", "quota": "2000", "chargingMethod": "Online", "dnn": "internet"}
    )
    repo.top_up_quota(ue_id="imsi-001", rating_group=0, amount_bytes=500, actor="tester", source="self-service")
    repo.record_usage(ue_id="imsi-001", rx_bytes=300, tx_bytes=200, source="ue-tunnel")

    account = repo.user_account("imsi-001")

    assert account["subscriptionName"] == "5G internet Data Plan"
    assert account["remainingBytes"] == 2000
    assert account["usedBytes"] == 500
    assert account["topUpBytes"] == 500
    assert account["activeRatingGroup"] == 0
    assert account["hasActivePlan"] is True


def test_user_account_used_bytes_sums_full_usage_ledger(monkeypatch):
    monkeypatch.setattr("app.repository.MongoClient", mongomock.MongoClient)
    repo = ChargingRepository("mongodb://unused", "free5gc")
    repo.db[CHARGING_DATA_COLL].insert_one(
        {"ueId": "imsi-001", "ratingGroup": 10, "quota": "2000", "chargingMethod": "Online", "dnn": "internet", "filter": "any"}
    )
    for index in range(60):
        repo.usage.insert_one({"ueId": "imsi-001", "deltaBytes": index + 1, "createdAt": index})

    account = repo.user_account("imsi-001")

    assert account["usedBytes"] == 1830
    assert len(account["recentUsage"]) == 8


def test_user_account_clamps_negative_remaining_balance(monkeypatch):
    monkeypatch.setattr("app.repository.MongoClient", mongomock.MongoClient)
    repo = ChargingRepository("mongodb://unused", "free5gc")
    repo.db[CHARGING_DATA_COLL].insert_one(
        {"ueId": "imsi-001", "ratingGroup": 10, "quota": "-839850", "chargingMethod": "Online", "dnn": "internet", "filter": "any"}
    )

    account = repo.user_account("imsi-001")

    assert account["remainingBytes"] == 0
    assert account["rawRemainingBytes"] == -839850
    assert account["overageBytes"] == 839850


def test_subscriber_summary_clamps_negative_remaining_balance(monkeypatch):
    monkeypatch.setattr("app.repository.MongoClient", mongomock.MongoClient)
    repo = ChargingRepository("mongodb://unused", "free5gc")
    repo.db[CHARGING_DATA_COLL].insert_one(
        {"ueId": "imsi-001", "ratingGroup": 10, "quota": "-839850", "chargingMethod": "Online", "dnn": "internet", "filter": "any"}
    )

    summary = repo.subscriber_summaries()[0]

    assert summary["remainingBytes"] == 0
    assert summary["rawRemainingBytes"] == -839850
    assert summary["overageBytes"] == 839850
