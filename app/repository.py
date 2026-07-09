from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import MongoClient, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

CHARGING_DATA_COLL = "policyData.ues.chargingData"
TOPUP_LEDGER_COLL = "chargingPortal.topups"


class ChargingRepository:
    def __init__(self, mongo_uri: str, db_name: str) -> None:
        self.client: MongoClient[Any] = MongoClient(mongo_uri)
        self.db: Database[Any] = self.client[db_name]

    @property
    def charging_data(self) -> Collection[Any]:
        return self.db[CHARGING_DATA_COLL]

    @property
    def topups(self) -> Collection[Any]:
        return self.db[TOPUP_LEDGER_COLL]

    def list_charging_records(self, ue_id: str | None = None) -> list[dict[str, Any]]:
        query = {"ueId": ue_id} if ue_id else {}
        records = list(
            self.charging_data.find(
                query,
                {
                    "_id": 0,
                    "ueId": 1,
                    "servingPlmnId": 1,
                    "snssai": 1,
                    "dnn": 1,
                    "ratingGroup": 1,
                    "chargingMethod": 1,
                    "quota": 1,
                    "unitCost": 1,
                    "filter": 1,
                    "qosRef": 1,
                },
            ).sort([("ueId", 1), ("ratingGroup", 1)])
        )
        return records

    def top_up_quota(
        self,
        *,
        ue_id: str,
        rating_group: int,
        amount_bytes: int,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        record = self.charging_data.find_one({"ueId": ue_id, "ratingGroup": rating_group})
        if record is None:
            raise LookupError(f"charging record not found for {ue_id} ratingGroup {rating_group}")

        old_quota = int(record.get("quota") or 0)
        new_quota = old_quota + amount_bytes
        updated = self.charging_data.find_one_and_update(
            {"ueId": ue_id, "ratingGroup": rating_group},
            {"$set": {"quota": str(new_quota)}},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise LookupError(f"charging record disappeared for {ue_id} ratingGroup {rating_group}")

        ledger = {
            "ueId": ue_id,
            "ratingGroup": rating_group,
            "amountBytes": amount_bytes,
            "oldQuota": old_quota,
            "newQuota": new_quota,
            "actor": actor,
            "source": source,
            "createdAt": datetime.now(UTC),
        }
        self.topups.insert_one(ledger)
        ledger.pop("_id", None)
        return ledger

    def list_topups(self, limit: int = 50) -> list[dict[str, Any]]:
        docs = list(self.topups.find({}, {"_id": 0}).sort("createdAt", -1).limit(limit))
        for doc in docs:
            if isinstance(doc.get("createdAt"), datetime):
                doc["createdAt"] = doc["createdAt"].isoformat()
        return docs

    def subscriber_summaries(self) -> list[dict[str, Any]]:
        records = self.list_charging_records()
        topup_docs = list(self.topups.find({}, {"_id": 0}).sort("createdAt", -1))
        by_ue: dict[str, dict[str, Any]] = {}

        for record in records:
            ue_id = str(record.get("ueId") or "")
            if not ue_id:
                continue
            summary = by_ue.setdefault(
                ue_id,
                {
                    "ueId": ue_id,
                    "recordCount": 0,
                    "remainingBytes": 0,
                    "topUpBytes": 0,
                    "ratingGroups": set(),
                    "dnns": set(),
                    "snssais": set(),
                    "methods": set(),
                    "lastTopUpAt": "",
                },
            )
            summary["recordCount"] += 1
            summary["remainingBytes"] += int(record.get("quota") or 0)
            if record.get("ratingGroup") is not None:
                summary["ratingGroups"].add(str(record.get("ratingGroup")))
            if record.get("dnn"):
                summary["dnns"].add(str(record.get("dnn")))
            if record.get("snssai"):
                summary["snssais"].add(str(record.get("snssai")))
            if record.get("chargingMethod"):
                summary["methods"].add(str(record.get("chargingMethod")))

        for topup in topup_docs:
            ue_id = str(topup.get("ueId") or "")
            if not ue_id:
                continue
            summary = by_ue.setdefault(
                ue_id,
                {
                    "ueId": ue_id,
                    "recordCount": 0,
                    "remainingBytes": 0,
                    "topUpBytes": 0,
                    "ratingGroups": set(),
                    "dnns": set(),
                    "snssais": set(),
                    "methods": set(),
                    "lastTopUpAt": "",
                },
            )
            summary["topUpBytes"] += int(topup.get("amountBytes") or 0)
            if not summary["lastTopUpAt"]:
                created_at = topup.get("createdAt")
                summary["lastTopUpAt"] = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at or "")

        summaries = []
        for summary in by_ue.values():
            summaries.append(
                {
                    **summary,
                    "ratingGroups": ", ".join(sorted(summary["ratingGroups"])) or "-",
                    "dnns": ", ".join(sorted(summary["dnns"])) or "-",
                    "snssais": ", ".join(sorted(summary["snssais"])) or "-",
                    "methods": ", ".join(sorted(summary["methods"])) or "-",
                }
            )
        return sorted(summaries, key=lambda item: item["ueId"])
