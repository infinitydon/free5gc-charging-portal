from __future__ import annotations

import httpx


async def notify_recharge(
    *,
    chf_base_url: str,
    ue_id: str,
    rating_group: int,
    bearer_token: str = "",
) -> tuple[bool, str]:
    url = f"{chf_base_url.rstrip('/')}/nchf-convergedcharging/v3/recharging/{ue_id}"
    headers = {"content-type": "application/json"}
    if bearer_token:
        headers["authorization"] = f"Bearer {bearer_token}"

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.put(url, params={"ratingGroup": rating_group}, headers=headers)
    except httpx.HTTPError as exc:
        detail = str(exc) or exc.__class__.__name__
        return False, f"CHF notification failed: {detail}"

    if 200 <= response.status_code < 300:
        return True, f"CHF notification accepted with HTTP {response.status_code}"
    return False, f"CHF notification returned HTTP {response.status_code}: {response.text[:300]}"
