from fastapi import APIRouter, HTTPException, Response, status
from geoalchemy2 import WKTElement
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionDep
from app.models import Site, SiteWorker
from app.phones import normalize_phone
from app.schemas import SiteCreate, SiteOut, WorkerCreate, WorkerOut

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
