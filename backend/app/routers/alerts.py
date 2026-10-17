from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field, model_validator

from app.config import get_settings
from app.http import HttpClientDep
from app.services.flood_warnings import DEFAULT_RAIN_THRESHOLD_IN, recipients, run_flood_check

router = APIRouter(prefix="/alerts", tags=["alerts"])


class FloodWarningRequest(BaseModel):
    model_config = {"json_schema_extra": {"example": {"emails": ["supervisor@example.com"]}}}

    emails: Annotated[list[EmailStr], Field(max_length=100)] = Field(
        default_factory=list,
        description="Extra addresses to warn, on top of each at-risk site's attached workers",
    )
    notify_site_workers: bool = Field(
        default=True, description="Also email the workers attached to each at-risk site"
    )
    site_ids: list[str] | None = Field(default=None, description="Sites to check; omit for all")
    rain_threshold_in: float = Field(
        default=DEFAULT_RAIN_THRESHOLD_IN,
        ge=0,
        description="Past-24h rain (inches) that counts as heavy for river/floodplain sites",
    )

    @model_validator(mode="after")
    def has_recipients(self) -> "FloodWarningRequest":
        if not self.emails and not self.notify_site_workers:
            raise ValueError("Give emails, or leave notify_site_workers on")
        self.emails = recipients(self.emails)
        return self


class EmailDelivery(BaseModel):
    to: str
    status: str  # 'sent' | 'failed' | 'skipped'
    error: str | None


class SiteFloodResult(BaseModel):
    site_id: str
    site_name: str
    at_risk: bool
    reasons: list[str]
    precip_24h_in: float | None
    precip_tomorrow_in: float | None
    soil_moisture: float | None
    nws_flood_alerts: list[str]
    alert_id: int | None
    newly_raised: bool
    source_errors: dict[str, str]
    emails: list[EmailDelivery]


@router.post("/flood")
async def send_flood_warnings(
    body: FloodWarningRequest, client: HttpClientDep
) -> list[SiteFloodResult]:
    """Check sites for flood risk after heavy rain and email a warning for each at-risk site.

    Each at-risk site emails every worker attached to it (see /sites/{id}/workers) plus any
    `emails` given, one email per address. Sites not at risk send nothing, and any active
    flood alert they had is cleared. Every site checked is listed in the response.
    """
    results = await run_flood_check(
        client,
        get_settings(),
        site_ids=body.site_ids,
        rain_threshold_in=body.rain_threshold_in,
        extra_emails=body.emails,
        notify_site_workers=body.notify_site_workers,
    )
    if body.site_ids and not results:
        raise HTTPException(status_code=404, detail="None of the given site ids exist")
    return [
        SiteFloodResult(
            site_id=a.site.id,
            site_name=a.site.name,
            at_risk=a.at_risk,
            reasons=a.reasons,
            precip_24h_in=a.precip_24h_in,
            precip_tomorrow_in=a.precip_tomorrow_in,
            soil_moisture=a.soil_moisture,
            nws_flood_alerts=[alert.event for alert in a.nws_flood_alerts],
            alert_id=a.alert_id,
            newly_raised=a.newly_raised,
            source_errors=a.source_errors,
            emails=[EmailDelivery(to=d.to, status=d.status, error=d.error) for d in r.deliveries],
        )
        for r in results
        for a in [r.assessment]
    ]
