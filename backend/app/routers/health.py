from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionDep

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/db")
async def health_db(session: SessionDep) -> dict[str, str]:
    try:
        postgis_version = await session.scalar(text("SELECT postgis_full_version()"))
    except (SQLAlchemyError, OSError) as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
    return {"status": "ok", "postgis": postgis_version}
