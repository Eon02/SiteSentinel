from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.config import get_settings
from app.http import HttpClientDep
from app.services.briefings import load_sites
from app.services.email import send_emails
from app.services.flood_warnings import (
    DEFAULT_RAIN_THRESHOLD_IN,
    assess_sites,
    record_alerts,
    warning_email,
)

router = APIRouter(prefix="/alerts", tags=["alerts"])


class FloodWarningRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {"emails": ["supervisor@example.com", "crew-lead@example.com"]}
        }
    }

    emails: Annotated[list[EmailStr], Field(min_length=1, max_length=100)]
    site_ids: list[str] | None = Field(default=None, description="Sites to check; omit for all")
    rain_threshold_in: float = Field(
        default=DEFAULT_RAIN_THRESHOLD_IN,
        ge=0,
        description="Past-24h rain (inches) that counts as heavy for river/floodplain sites",
    )

    @field_validator("emails")
    @classmethod
    def dedupe(cls, emails: list[str]) -> list[str]:
        seen: dict[str, str] = {}
        for email in emails:
            seen.setdefault(email.lower(), email)
        return list(seen.values())


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
    source_errors: dict[str, str]
    emails: list[EmailDelivery]


@router.post("/flood")
async def send_flood_warnings(
    body: FloodWarningRequest, client: HttpClientDep
) -> list[SiteFloodResult]:
    """Check sites for flood risk after heavy rain and email a warning to every address.

    Each at-risk site sends one email per address. Sites not at risk send nothing, and any
    active flood alert they had is cleared. Every site checked is listed in the response.
    """
    settings = get_settings()
    sites = await load_sites(body.site_ids)
    if body.site_ids and not sites:
        raise HTTPException(status_code=404, detail="None of the given site ids exist")

    assessments = await assess_sites(client, settings, sites, body.rain_threshold_in)
    await record_alerts(assessments)

    pairs = [(a.site.id, to) for a in assessments if a.at_risk for to in body.emails]
    outgoing = [warning_email(a, to) for a in assessments if a.at_risk for to in body.emails]
    deliveries = await send_emails(settings, outgoing)

    by_site: dict[str, list[EmailDelivery]] = {a.site.id: [] for a in assessments}
    for (site_id, _), d in zip(pairs, deliveries, strict=True):
        by_site[site_id].append(EmailDelivery(to=d.to, status=d.status, error=d.error))

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
            source_errors=a.source_errors,
            emails=by_site[a.site.id],
        )
        for a in assessments
    ]
