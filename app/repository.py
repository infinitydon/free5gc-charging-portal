from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import MongoClient, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

CHARGING_DATA_COLL = "policyData.ues.chargingData"
TOPUP_LEDGER_COLL = "chargingPortal.topups"
USAGE_LEDGER_COLL = "chargingPortal.usage"


def _rating_group_query(rating_group: int) -> dict[str, Any]:
    if rating_group == 0:
        return {"$or": [{"ratingGroup": 0}, {"ratingGroup": None}, {"ratingGroup": {"$exists": False}}]}
    return {"ratingGroup": rating_group}


def _normalize_rating_group(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("ratingGroup") is None:
        record["ratingGroup"] = 0
    return record


def _record_sort_key(record: dict[str, Any]) -> tuple[int, int, str]:
    method_rank = 0 if str(record.get("chargingMethod") or "").lower() == "online" else 1
    rating_group = int(record.get("ratingGroup") or 0)
    return (method_rank, -rating_group, str(record.get("ueId") or ""))


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

    @property
    def usage(self) -> Collection[Any]:
        return self.db[USAGE_LEDGER_COLL]

    def list_charging_records(self, ue_id: str | None = None, actionable_only: bool = False) -> list[dict[str, Any]]:
        query = {"ueId": ue_id} if ue_id else {}
        if actionable_only:
            query["chargingMethod"] = {"$in": ["Online", "Offline"]}
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
            )
        )
        records = [_normalize_rating_group(record) for record in records]
        records.sort(key=_record_sort_key)
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
        query = {"ueId": ue_id, **_rating_group_query(rating_group)}
        record = self.charging_data.find_one(query)
        if record is None:
            raise LookupError(f"charging record not found for {ue_id} ratingGroup {rating_group}")

        old_quota = int(record.get("quota") or 0)
        new_quota = old_quota + amount_bytes
        updated = self.charging_data.find_one_and_update(
            query,
            {"$set": {"quota": str(new_quota)}},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise LookupError(f"charging record disappeared for {ue_id} ratingGroup {rating_group}")

        if "ratingGroup" in record:
            base_query = {
                "ueId": ue_id,
                "ratingGroup": {"$exists": False},
                "snssai": record.get("snssai", ""),
                "dnn": record.get("dnn", ""),
                "filter": record.get("filter", ""),
            }
            base_record = self.charging_data.find_one(base_query)
            if base_record is not None:
                base_quota = int(base_record.get("quota") or 0) + amount_bytes
                self.charging_data.update_one(base_query, {"$set": {"quota": str(base_quota)}})

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

    def record_usage(
        self,
        *,
        ue_id: str,
        rx_bytes: int,
        tx_bytes: int,
        source: str,
    ) -> dict[str, Any]:
        previous_docs = list(
            self.usage.find({"ueId": ue_id, "source": source}, {"totalBytes": 1}).sort([("_id", -1)]).limit(1)
        )
        previous = previous_docs[0] if previous_docs else None
        previous_total = int(previous.get("totalBytes") or 0) if previous else 0
        total_bytes = max(0, rx_bytes + tx_bytes)
        delta_bytes = max(0, total_bytes - previous_total)

        record = self.charging_data.find_one(
            {"ueId": ue_id, "chargingMethod": "Online"},
            sort=[("ratingGroup", -1), ("_id", -1)],
        )
        rating_group = int(record.get("ratingGroup") or 0) if record else 0
        old_quota = int(record.get("quota") or 0) if record else 0
        new_quota = max(0, old_quota - delta_bytes)
        if record and delta_bytes:
            self.charging_data.update_one({"_id": record["_id"]}, {"$set": {"quota": str(new_quota)}})

        ledger = {
            "ueId": ue_id,
            "source": source,
            "ratingGroup": rating_group,
            "rxBytes": rx_bytes,
            "txBytes": tx_bytes,
            "totalBytes": total_bytes,
            "deltaBytes": delta_bytes,
            "oldQuota": old_quota,
            "newQuota": new_quota,
            "createdAt": datetime.now(UTC),
        }
        self.usage.insert_one(ledger)
        ledger.pop("_id", None)
        return ledger

    def list_topups(self, limit: int = 50) -> list[dict[str, Any]]:
        docs = list(self.topups.find({}, {"_id": 0}).sort("createdAt", -1).limit(limit))
        for doc in docs:
            if isinstance(doc.get("createdAt"), datetime):
                doc["createdAt"] = doc["createdAt"].isoformat()
        return docs

    def list_usage(self, limit: int = 50, ue_id: str | None = None) -> list[dict[str, Any]]:
        query = {"ueId": ue_id} if ue_id else {}
        docs = list(self.usage.find(query, {"_id": 0}).sort("createdAt", -1).limit(limit))
        for doc in docs:
            if isinstance(doc.get("createdAt"), datetime):
                doc["createdAt"] = doc["createdAt"].isoformat()
        return docs

    def user_account(self, ue_id: str) -> dict[str, Any]:
        records = self.list_charging_records(ue_id, actionable_only=True)
        active = next((record for record in records if str(record.get("chargingMethod") or "").lower() == "online"), None)
        topups = [doc for doc in self.list_topups(50) if doc.get("ueId") == ue_id]
        usage = self.list_usage(50, ue_id)
        remaining_bytes = int(active.get("quota") or 0) if active else sum(int(record.get("quota") or 0) for record in records)
        used_bytes = sum(int(doc.get("deltaBytes") or 0) for doc in usage)
        added_bytes = sum(int(doc.get("amountBytes") or 0) for doc in topups)
        subscription_name = "5G Data Plan"
        if active and active.get("dnn"):
            subscription_name = f"5G {active.get('dnn')} Data Plan"
        return {
            "ueId": ue_id,
            "subscriptionName": subscription_name,
            "remainingBytes": remaining_bytes,
            "usedBytes": used_bytes,
            "topUpBytes": added_bytes,
            "lastTopUpAt": str(topups[0].get("createdAt") or "") if topups else "",
            "activeRatingGroup": int(active.get("ratingGroup") or 0) if active else 0,
            "hasActivePlan": active is not None,
            "recentTopups": topups[:5],
            "recentUsage": usage[:8],
        }

    def subscriber_summaries(self) -> list[dict[str, Any]]:
        records = self.list_charging_records(actionable_only=True)
        topup_docs = list(self.topups.find({}, {"_id": 0}).sort("createdAt", -1))
        usage_docs = list(self.usage.find({}, {"_id": 0}).sort("createdAt", -1))
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
                    "usageBytes": 0,
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
                    "usageBytes": 0,
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

        for usage in usage_docs:
            ue_id = str(usage.get("ueId") or "")
            if not ue_id:
                continue
            summary = by_ue.setdefault(
                ue_id,
                {
                    "ueId": ue_id,
                    "recordCount": 0,
                    "remainingBytes": 0,
                    "topUpBytes": 0,
                    "usageBytes": 0,
                    "ratingGroups": set(),
                    "dnns": set(),
                    "snssais": set(),
                    "methods": set(),
                    "lastTopUpAt": "",
                },
            )
            summary["usageBytes"] += int(usage.get("deltaBytes") or 0)

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
