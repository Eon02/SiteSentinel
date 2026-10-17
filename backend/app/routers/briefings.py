from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.http import HttpClientDep
from app.services.briefings import send_now

router = APIRouter(prefix="/briefings", tags=["briefings"])


class RunRequest(BaseModel):
    site_ids: list[str] | None = Field(
        default=None, description="Sites to brief; omit for every site"
    )
    include_payload: bool = Field(
        default=False, description="Return each briefing's full JSON in the response"
    )


class RunResult(BaseModel):
    site_id: str
    briefing_id: int | None
    briefing_date: date
    status: str  # 'sent' | 'failed' | 'skipped'
    error: str | None
    payload: dict[str, Any] | None = None


@router.post("/run")
async def run_briefings(client: HttpClientDep, body: RunRequest | None = None) -> list[RunResult]:
    """Build and send next-day briefings right now, without waiting for 6 PM.

    Recorded as manual briefings, so the scheduled 6 PM briefing still goes out.
    """
    body = body or RunRequest()
    results = await send_now(client, body.site_ids)
    if body.site_ids and not results:
        raise HTTPException(status_code=404, detail="None of the given site ids exist")
    return [
        RunResult(
            site_id=r.site_id,
            briefing_id=r.briefing_id,
            briefing_date=r.briefing_date,
            status=r.status,
            error=r.error,
            payload=r.payload if body.include_payload else None,
        )
        for r in results
    ]
