from fastapi import APIRouter, HTTPException, status
from geoalchemy2 import WKTElement
from sqlalchemy.exc import IntegrityError

from app.db import SessionDep
from app.models import Site
from app.schemas import SiteCreate, SiteOut

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
