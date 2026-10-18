"""ORM models mirroring database/migrations. The SQL migrations are the source of truth:
change the schema there first, then update these classes to match."""

import uuid
from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geometry, WKBElement
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    Uuid,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, REAL
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Indexes are created by the migrations, so models don't declare them.
Point = Geometry("POINT", srid=4326, spatial_index=False)
MultiPolygon = Geometry("MULTIPOLYGON", srid=4326, spatial_index=False)


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    utility: Mapped[str | None] = mapped_column(Text)
    plant_type: Mapped[str | None] = mapped_column(Text)
    county: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    near_river: Mapped[bool | None] = mapped_column(Boolean, server_default=false())
    geom: Mapped[WKBElement] = mapped_column(Point)


class Condition(Base):
    __tablename__ = "conditions"

    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    temp_f: Mapped[float | None] = mapped_column(REAL)
    heat_index_f: Mapped[float | None] = mapped_column(REAL)
    precip_24h_in: Mapped[float | None] = mapped_column(REAL)
    soil_moisture: Mapped[float | None] = mapped_column(REAL)
    wind_mph: Mapped[float | None] = mapped_column(REAL)
    wind_gust_mph: Mapped[float | None] = mapped_column(REAL)
    humidity_pct: Mapped[float | None] = mapped_column(REAL)  # relative humidity, 0-100


class HazardScore(Base):
    __tablename__ = "hazard_scores"

    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), primary_key=True)
    score_date: Mapped[date] = mapped_column(Date, primary_key=True)
    score: Mapped[float] = mapped_column(REAL)
    is_forecast: Mapped[bool] = mapped_column(Boolean)
    top_factors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id"))
    type: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_strike_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HazardReport(Base):
    __tablename__ = "hazard_reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id"))
    hazard_type: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    photo_url: Mapped[str | None] = mapped_column(Text)
    reported_by: Mapped[str | None] = mapped_column(Text)
    geom: Mapped[WKBElement] = mapped_column(Point)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    client_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, unique=True)  # offline-queue id


class FloodZone(Base):
    """FEMA NFHL flood zone, clipped to a site's search radius."""

    __tablename__ = "flood_zones"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    fld_ar_id: Mapped[str] = mapped_column(Text)
    dfirm_id: Mapped[str | None] = mapped_column(Text)
    fld_zone: Mapped[str] = mapped_column(Text)
    zone_subtype: Mapped[str | None] = mapped_column(Text)
    sfha: Mapped[bool] = mapped_column(Boolean)
    static_bfe: Mapped[float | None] = mapped_column(REAL)
    depth: Mapped[float | None] = mapped_column(REAL)
    len_unit: Mapped[str | None] = mapped_column(Text)
    geom: Mapped[WKBElement] = mapped_column(MultiPolygon)


class FloodZoneLoad(Base):
    __tablename__ = "flood_zone_loads"

    site_id: Mapped[str] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True
    )
    radius_mi: Mapped[float] = mapped_column(REAL)
    zone_count: Mapped[int] = mapped_column(Integer)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Briefing(Base):
    """Next-day safety briefing sent (or attempted) for a site."""

    __tablename__ = "briefings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    briefing_date: Mapped[date] = mapped_column(Date)
    trigger: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default="1")
    last_error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SiteWorker(Base):
    """A worker attached to a site; receives that site's flood warnings by SMS."""

    __tablename__ = "site_workers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    phone: Mapped[str] = mapped_column(Text)  # E.164, e.g. +12055550123
    name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SiteManager(Base):
    """A project manager for a site; receives emailed reports (POST /reports/email)."""

    __tablename__ = "site_managers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(Text)  # lowercase
    name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
