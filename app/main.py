from __future__ import annotations

from pathlib import Path
from typing import Annotated

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


class TopUpResponse(BaseModel):
    ok: bool
    ledger: dict
    chf_notified: bool
    message: str


def get_repository(settings: Annotated[Settings, Depends(get_settings)]) -> ChargingRepository:
    return ChargingRepository(settings.mongo_uri, settings.mongo_db)


app = FastAPI(title="free5GC Charging Portal", version="0.1.1")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    repo: Annotated[ChargingRepository, Depends(get_repository)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "title": settings.portal_title,
            "records": repo.list_charging_records(),
            "topups": repo.list_topups(15),
            "self_topup": settings.end_user_self_topup,
        },
    )


@app.get("/api/charging-records")
def charging_records(repo: Annotated[ChargingRepository, Depends(get_repository)]) -> list[dict]:
    return repo.list_charging_records()


@app.get("/api/topups")
def topups(repo: Annotated[ChargingRepository, Depends(get_repository)], limit: int = 50) -> list[dict]:
    return repo.list_topups(limit)


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
    payload: TopUpRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    repo: Annotated[ChargingRepository, Depends(get_repository)],
) -> TopUpResponse:
    return await apply_topup(payload, "self-service", settings, repo)


@app.post("/topup/form")
async def topup_form(
    settings: Annotated[Settings, Depends(get_settings)],
    repo: Annotated[ChargingRepository, Depends(get_repository)],
    ue_id: Annotated[str, Form(alias="ueId")],
    rating_group: Annotated[int, Form(alias="ratingGroup")],
    amount_mb: Annotated[int, Form(alias="amountMb")],
    actor: Annotated[str, Form()],
    pin: Annotated[str, Form()] = "",
    source: Annotated[str, Form()] = "operator",
) -> RedirectResponse:
    payload = TopUpRequest(
        ueId=ue_id,
        ratingGroup=rating_group,
        amountBytes=amount_mb * 1024 * 1024,
        actor=actor,
        pin=pin,
    )
    await apply_topup(payload, source, settings, repo)
    return RedirectResponse("/", status_code=303)
