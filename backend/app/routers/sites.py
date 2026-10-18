from typing import Annotated

from fastapi import APIRouter, HTTPException, Response, status
from geoalchemy2 import WKTElement
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import SessionDep
from app.http import HttpClientDep
from app.models import Site, SiteWorker
from app.phones import normalize_phone
from app.schemas import SiteCreate, SiteOut, WorkerCreate, WorkerOut
from app.services.sms import OutgoingSms, send_sms

router = APIRouter(prefix="/sites", tags=["sites"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "A site with this id already exists"}},
)
async def create_site(body: SiteCreate, session: SessionDep) -> SiteOut:
    """Add a job site. Coordinates are stored as a PostGIS point (SRID 4326)."""
    site = Site(
        **body.model_dump(exclude={"latitude", "longitude"}),
        geom=WKTElement(f"POINT({body.longitude} {body.latitude})", srid=4326),
    )
    session.add(site)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Site {body.id!r} already exists",
        ) from exc
    return SiteOut(**body.model_dump())


async def _require_site(session: SessionDep, site_id: str) -> None:
    if await session.get(Site, site_id) is None:
        raise HTTPException(status_code=404, detail=f"Site {site_id!r} not found")


def _worker_out(worker: SiteWorker) -> WorkerOut:
    return WorkerOut.model_construct(
        id=worker.id,
        site_id=worker.site_id,
        phone=worker.phone,
        name=worker.name,
        role=worker.role,
    )


@router.get("/{site_id}/workers")
async def list_workers(site_id: str, session: SessionDep) -> list[WorkerOut]:
    """Workers attached to a site. POST /alerts/flood/sms texts them flood warnings."""
    await _require_site(session, site_id)
    workers = await session.scalars(
        select(SiteWorker).where(SiteWorker.site_id == site_id).order_by(SiteWorker.id)
    )
    return [_worker_out(w) for w in workers]


@router.post(
    "/{site_id}/workers",
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Site not found"},
        409: {"description": "This phone number is already attached to the site"},
    },
)
async def add_worker(site_id: str, body: WorkerCreate, session: SessionDep) -> WorkerOut:
    """Attach a worker's phone number to a site."""
    await _require_site(session, site_id)
    worker = SiteWorker(site_id=site_id, **body.model_dump())
    session.add(worker)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{body.phone} is already attached to site {site_id!r}",
        ) from exc
    return _worker_out(worker)


@router.delete(
    "/{site_id}/workers/{phone}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "Worker not attached to this site"}},
)
async def remove_worker(site_id: str, phone: str, session: SessionDep) -> Response:
    """Detach a worker from a site by phone number (any format)."""
    try:
        number = normalize_phone(phone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    worker = await session.scalar(
        select(SiteWorker).where(SiteWorker.site_id == site_id, SiteWorker.phone == number)
    )
    if worker is None:
        raise HTTPException(status_code=404, detail=f"{phone} is not attached to site {site_id!r}")
    await session.delete(worker)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


SmsText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=280)]


class CrewSmsRequest(BaseModel):
    # Optional: the site page's "Force SMS Update" button sends no message (see text_site_crew).
    message: SmsText | None = None


class CrewSmsDelivery(BaseModel):
    to: str
    name: str | None
    status: str  # 'sent' | 'failed' | 'skipped'
    error: str | None


@router.post(
    "/{site_id}/sms",
    responses={404: {"description": "Site not found"}},
)
async def text_site_crew(
    site_id: str, body: CrewSmsRequest, session: SessionDep, client: HttpClientDep
) -> list[CrewSmsDelivery]:
    """Text every worker assigned to a site, one text per phone, via Twilio.

    Each text reads "SiteSentinel - <site name>: <message>" (a generic status line when no
    message is given). Returns the delivery status per worker; an empty list when the site has
    no crew.

    Note: while the team is on Twilio's free trial, TWILIO_BODY_TEMPLATE is set, so Twilio
    replaces this text with its predefined template ("sms_internal_alerts") anyway.
    """
    site = await session.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail=f"Site {site_id!r} not found")
    workers = list(
        await session.scalars(
            select(SiteWorker).where(SiteWorker.site_id == site_id).order_by(SiteWorker.id)
        )
    )
    # TODO: send a proper status message: active alerts, flood risk, current conditions and
    # tomorrow's hazard score for the site. Not built yet because on the Twilio trial every text
    # goes out as the predefined template regardless of this body (see TWILIO_BODY_TEMPLATE).
    message = body.message or "Site status update. Check the SiteSentinel app for details."
    text = f"SiteSentinel - {site.name}: {message}"
    results = await send_sms(client, get_settings(), [OutgoingSms(w.phone, text) for w in workers])
    return [
        CrewSmsDelivery(to=r.to, name=w.name, status=r.status, error=r.error)
        for w, r in zip(workers, results, strict=True)
    ]
