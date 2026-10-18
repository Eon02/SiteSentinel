from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from app.config import get_settings
from app.db import SessionDep
from app.http import HttpClientDep
from app.schemas import Email
from app.services.reports import send_reports

router = APIRouter(prefix="/reports", tags=["reports"])


class ReportEmailRequest(BaseModel):
    model_config = {"json_schema_extra": {"example": {"emails": ["pat.lee@example.com"]}}}

    emails: Annotated[list[Email], Field(max_length=50)] = Field(
        default_factory=list,
        description="Extra addresses, on top of site managers; they get a report on site_ids "
        "(or every site)",
    )
    notify_site_managers: bool = Field(
        default=True, description="Email each site manager a report on the sites they manage"
    )
    site_ids: list[str] | None = Field(default=None, description="Sites to cover; omit for all")
    days: int = Field(default=7, ge=1, le=90, description="How far back crew reports go")
    dry_run: bool = Field(
        default=False, description="Write the reports and return them without emailing anyone"
    )

    @model_validator(mode="after")
    def has_recipients(self) -> "ReportEmailRequest":
        if not self.emails and not self.notify_site_managers and not self.dry_run:
            raise ValueError("Give emails, leave notify_site_managers on, or use dry_run")
        self.emails = list(dict.fromkeys(self.emails))
        return self


class Delivery(BaseModel):
    to: str
    status: str  # 'sent' | 'failed' | 'skipped'
    error: str | None


class ReportOut(BaseModel):
    site_ids: list[str]
    recipients: list[str]
    subject: str
    body: str
    summary_source: str = Field(description="'gemini', or 'fallback' when Gemini was unavailable")
    summary_error: str | None
    deliveries: list[Delivery] = Field(description="Empty on a dry run")


@router.post("/email")
async def email_reports(
    body: ReportEmailRequest, session: SessionDep, client: HttpClientDep
) -> list[ReportOut]:
    """Email project managers a Gemini-written report summarizing their sites.

    Covers each site's latest conditions, today's and upcoming hazard scores, active alerts,
    recent crew hazard reports and FEMA flood risk. Each manager (see /sites/{id}/managers) gets
    one email for all the sites they manage; `emails` get one for `site_ids` (or every site).
    Recipients covering the same sites share one report. Falls back to a plain built-in summary
    when Gemini isn't configured or fails (see summary_source / summary_error).
    """
    reports = await send_reports(
        session,
        client,
        get_settings(),
        site_ids=body.site_ids,
        emails=body.emails,
        notify_managers=body.notify_site_managers,
        days=body.days,
        dry_run=body.dry_run,
    )
    return [
        ReportOut(
            site_ids=r.site_ids,
            recipients=r.recipients,
            subject=r.subject,
            body=r.body,
            summary_source=r.summary_source,
            summary_error=r.summary_error,
            deliveries=[Delivery(to=d.to, status=d.status, error=d.error) for d in r.deliveries],
        )
        for r in reports
    ]
