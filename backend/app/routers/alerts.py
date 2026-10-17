from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.config import get_settings
from app.http import HttpClientDep
from app.phones import Phone
from app.services.flood_warnings import DEFAULT_RAIN_THRESHOLD_IN, recipients, run_flood_check

router = APIRouter(prefix="/alerts", tags=["alerts"])


class FloodSmsRequest(BaseModel):
    model_config = {"json_schema_extra": {"example": {"phones": ["(205) 555-0123"]}}}

    phones: Annotated[list[Phone], Field(max_length=100)] = Field(
        default_factory=list,
        description="Extra numbers to text (any common format), on top of site workers",
    )
    notify_site_workers: bool = Field(
        default=True, description="Text the workers attached to each at-risk site"
    )
    site_ids: list[str] | None = Field(default=None, description="Sites to check; omit for all")
    rain_threshold_in: float = Field(
        default=DEFAULT_RAIN_THRESHOLD_IN,
        ge=0,
        description="Past-24h rain (inches) that counts as heavy for river/floodplain sites",
    )

    @model_validator(mode="after")
    def has_recipients(self) -> "FloodSmsRequest":
        if not self.phones and not self.notify_site_workers:
            raise ValueError("Give phones, or leave notify_site_workers on")
        self.phones = recipients(self.phones)
        return self


class Delivery(BaseModel):
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
    sms: list[Delivery]


@router.post("/flood/sms")
async def text_flood_warnings(
    body: FloodSmsRequest, client: HttpClientDep
) -> list[SiteFloodResult]:
    """Check sites for flood risk after heavy rain and text each at-risk site's crew.

    Each at-risk site texts its workers (see /sites/{id}/workers) plus any `phones` given,
    one text per number, through Twilio. Texts only go out when this endpoint is called;
    nothing automated sends them. Sites not at risk send nothing, and any active flood alert
    they had is cleared. Every site checked is listed in the response.
    """
    results = await run_flood_check(
        client,
        get_settings(),
        site_ids=body.site_ids,
        rain_threshold_in=body.rain_threshold_in,
        send_texts=True,
        extra_phones=body.phones,
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
            sms=[Delivery(to=d.to, status=d.status, error=d.error) for d in r.deliveries],
        )
        for r in results
        for a in [r.assessment]
    ]
