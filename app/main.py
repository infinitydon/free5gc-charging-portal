from __future__ import annotations

import json
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .chf import notify_recharge
from .repository import ChargingRepository
from .settings import Settings, get_settings

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


class TopUpRequest(BaseModel):
    ue_id: str = Field(alias="ueId", min_length=5)
    rating_group: int = Field(alias="ratingGroup", ge=0)
    amount_bytes: int = Field(alias="amountBytes", gt=0)
    actor: str = Field(default="operator", min_length=1)
    pin: str = Field(default="")


class SelfTopUpRequest(BaseModel):
    rating_group: int = Field(alias="ratingGroup", ge=0)
    amount_bytes: int = Field(alias="amountBytes", gt=0)
    actor: str = Field(default="self-service", min_length=1)


class TopUpResponse(BaseModel):
    ok: bool
    ledger: dict
    chf_notified: bool
    message: str


class UsageReportRequest(BaseModel):
    ue_id: str = Field(alias="ueId", min_length=5)
    rx_bytes: int = Field(alias="rxBytes", ge=0)
    tx_bytes: int = Field(alias="txBytes", ge=0)
    source: str = Field(default="ue-tunnel", min_length=1)


def get_repository(settings: Annotated[Settings, Depends(get_settings)]) -> ChargingRepository:
    return ChargingRepository(settings.mongo_uri, settings.mongo_db)


app = FastAPI(title="free5GC Charging Portal", version="0.3.14")


def format_bytes(value: int | str | None) -> str:
    amount = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.2f} {unit}"
        amount /= 1024
    return f"{amount:.2f} TB"


templates.env.filters["bytes"] = format_bytes


def flash_from_request(request: Request) -> dict[str, str] | None:
    status = request.query_params.get("status", "")
    message = request.query_params.get("message", "")
    if not status or not message:
        return None
    return {"status": status, "message": message}


def resolve_subscriber_from_request(request: Request, settings: Settings) -> str:
    if settings.trusted_subscriber_header_enabled:
        header_value = request.headers.get(settings.trusted_subscriber_header)
        if header_value and header_value.strip():
            return header_value.strip()

    source_ip = request.client.host if request.client else ""
    if source_ip:
        try:
            bindings = json.loads(settings.subscriber_bindings_json or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail="invalid subscriber binding configuration") from exc
        for match, supi in bindings.items():
            if not supi:
                continue
            try:
                if "/" in match and ip_address(source_ip) in ip_network(match, strict=False):
                    return str(supi)
                if source_ip == match:
                    return str(supi)
            except ValueError:
                continue

    if settings.default_subscriber_supi:
        return settings.default_subscriber_supi

    raise HTTPException(status_code=403, detail="subscriber identity could not be resolved")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    repo: Annotated[ChargingRepository, Depends(get_repository)],
) -> HTMLResponse:
    if settings.portal_mode.lower() == "user":
        ue_id = resolve_subscriber_from_request(request, settings)
        account = repo.user_account(ue_id)
        return templates.TemplateResponse(
            "user.html",
            {
                "request": request,
                "title": settings.portal_title,
                "ue_id": ue_id,
                "account": account,
                "topups": account["recentTopups"],
                "usage": account["recentUsage"],
                "flash": flash_from_request(request),
            },
        )

    summaries = repo.subscriber_summaries()
    records = repo.list_active_plan_records()
    raw_records = repo.list_charging_records(actionable_only=True)
    return templates.TemplateResponse(
        "operator.html",
        {
            "request": request,
            "title": settings.portal_title,
            "records": records,
            "raw_records": raw_records,
            "topups": repo.list_topups(15),
            "usage": repo.list_usage(15),
            "summaries": summaries,
            "subscriber_count": len(summaries),
            "record_count": len(records),
            "total_remaining_bytes": sum(int(item.get("remainingBytes") or 0) for item in summaries),
            "total_topup_bytes": sum(int(item.get("topUpBytes") or 0) for item in summaries),
            "total_usage_bytes": sum(int(item.get("usageBytes") or 0) for item in summaries),
            "self_topup": settings.end_user_self_topup,
            "flash": flash_from_request(request),
        },
    )


@app.get("/api/charging-records")
def charging_records(repo: Annotated[ChargingRepository, Depends(get_repository)]) -> list[dict]:
    return repo.list_charging_records()


@app.get("/api/me")
def me(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    repo: Annotated[ChargingRepository, Depends(get_repository)],
) -> dict:
    ue_id = resolve_subscriber_from_request(request, settings)
    return {
        "ueId": ue_id,
        "account": repo.user_account(ue_id),
        "chargingRecords": repo.list_charging_records(ue_id),
    }


@app.get("/api/topups")
def topups(repo: Annotated[ChargingRepository, Depends(get_repository)], limit: int = 50) -> list[dict]:
    return repo.list_topups(limit)


@app.get("/api/usage")
def usage(repo: Annotated[ChargingRepository, Depends(get_repository)], limit: int = 50) -> list[dict]:
    return repo.list_usage(limit)


@app.get("/api/operator-summary")
def operator_summary(repo: Annotated[ChargingRepository, Depends(get_repository)]) -> dict:
    summaries = repo.subscriber_summaries()
    records = repo.list_active_plan_records()
    return {
        "subscriberCount": len(summaries),
        "recordCount": len(records),
        "totalRemainingBytes": sum(int(item.get("remainingBytes") or 0) for item in summaries),
        "totalTopUpBytes": sum(int(item.get("topUpBytes") or 0) for item in summaries),
        "totalUsageBytes": sum(int(item.get("usageBytes") or 0) for item in summaries),
    }


@app.post("/api/usage")
def usage_report(payload: UsageReportRequest, repo: Annotated[ChargingRepository, Depends(get_repository)]) -> dict:
    return {
        "ok": True,
        "ledger": repo.record_usage(
            ue_id=payload.ue_id,
            rx_bytes=payload.rx_bytes,
            tx_bytes=payload.tx_bytes,
            source=payload.source,
        ),
    }


async def apply_topup(
    payload: TopUpRequest,
    source: str,
    settings: Settings,
    repo: ChargingRepository,
) -> TopUpResponse:
    if source == "operator" and payload.pin != settings.operator_pin:
        raise HTTPException(status_code=403, detail="invalid operator PIN")
    if source == "self-service" and not settings.end_user_self_topup:
        raise HTTPException(status_code=403, detail="self top-up is disabled")

    try:
        ledger = repo.top_up_quota(
            ue_id=payload.ue_id,
            rating_group=payload.rating_group,
            amount_bytes=payload.amount_bytes,
            actor=payload.actor,
            source=source,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if settings.chf_notify_enabled:
        chf_notified, message = await notify_recharge(
            chf_base_url=settings.chf_base_url,
            ue_id=payload.ue_id,
            rating_group=payload.rating_group,
            bearer_token=settings.chf_bearer_token,
        )
    else:
        chf_notified, message = False, "CHF notification disabled"

    return TopUpResponse(ok=True, ledger=ledger, chf_notified=chf_notified, message=message)


@app.post("/api/topups", response_model=TopUpResponse)
async def operator_topup(
    payload: TopUpRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    repo: Annotated[ChargingRepository, Depends(get_repository)],
) -> TopUpResponse:
    return await apply_topup(payload, "operator", settings, repo)


@app.post("/api/topups/self", response_model=TopUpResponse)
async def self_topup(
    request: Request,
    payload: SelfTopUpRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    repo: Annotated[ChargingRepository, Depends(get_repository)],
) -> TopUpResponse:
    ue_id = resolve_subscriber_from_request(request, settings)
    account = repo.user_account(ue_id)
    return await apply_topup(
        TopUpRequest(
            ueId=ue_id,
            ratingGroup=account["activeRatingGroup"],
            amountBytes=payload.amount_bytes,
            actor=payload.actor,
            pin="",
        ),
        "self-service",
        settings,
        repo,
    )


@app.post("/topup/form")
async def topup_form(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    repo: Annotated[ChargingRepository, Depends(get_repository)],
    rating_group: Annotated[int, Form(alias="ratingGroup")],
    amount_mb: Annotated[int, Form(alias="amountMb")],
    actor: Annotated[str, Form()],
    ue_id: Annotated[str, Form(alias="ueId")] = "",
    pin: Annotated[str, Form()] = "",
    source: Annotated[str, Form()] = "operator",
) -> RedirectResponse:
    if source == "self-service":
        ue_id = resolve_subscriber_from_request(request, settings)
        rating_group = repo.user_account(ue_id)["activeRatingGroup"]
        pin = ""

    payload = TopUpRequest(
        ueId=ue_id,
        ratingGroup=rating_group,
        amountBytes=amount_mb * 1024 * 1024,
        actor=actor,
        pin=pin,
    )
    try:
        result = await apply_topup(payload, source, settings, repo)
    except HTTPException as exc:
        detail = str(exc.detail)
        return RedirectResponse(
            "/?" + urlencode({"status": "error", "message": detail}),
            status_code=303,
        )

    if source == "self-service":
        message = (
            f"Top-up successful: {format_bytes(payload.amount_bytes)} added to your data plan. "
            f"New balance: {format_bytes(result.ledger.get('newQuota'))}."
        )
    else:
        message = (
            f"Top-up applied: {format_bytes(payload.amount_bytes)} added to {payload.ue_id} "
            f"RG {payload.rating_group}. New quota: {format_bytes(result.ledger.get('newQuota'))}."
        )
    status = "success" if result.chf_notified else "warning"
    if not result.chf_notified:
        message = f"{message} {result.message}"
    return RedirectResponse(
        "/?" + urlencode({"status": status, "message": message}),
        status_code=303,
    )
