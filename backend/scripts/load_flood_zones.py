"""Load FEMA NFHL flood zones around each site into the flood_zones table.

Run from backend/:
    uv run python -m scripts.load_flood_zones                    # every site
    uv run python -m scripts.load_flood_zones --site 6145 --site 3456
    uv run python -m scripts.load_flood_zones --radius-mi 5

Each site's zones are replaced on every run. Polygons are clipped to the radius in PostGIS
so storage stays small. Re-run after adding sites or when FEMA updates its maps.
"""

import argparse
import asyncio
import json

import httpx
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal, engine
from app.services.fema_nfhl import FemaNfhlError, FloodZoneFeature, fetch_flood_zones

METERS_PER_MILE = 1609.344
CONCURRENT_REQUESTS = 3  # stay gentle on FEMA's server

INSERT_ZONE = text(
    """
    INSERT INTO flood_zones (
      site_id, fld_ar_id, dfirm_id, fld_zone, zone_subtype, sfha, static_bfe, depth, len_unit, geom
    )
    SELECT
      s.id, CAST(:fld_ar_id AS text), CAST(:dfirm_id AS text), CAST(:fld_zone AS text),
      CAST(:zone_subtype AS text), CAST(:sfha AS boolean), CAST(:static_bfe AS real),
      CAST(:depth AS real), CAST(:len_unit AS text), clipped.geom
    FROM sites s
    CROSS JOIN LATERAL (
      SELECT ST_Multi(ST_CollectionExtract(ST_Intersection(
        ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(CAST(:geojson AS text)), 4326)),
        ST_Buffer(s.geom::geography, CAST(:radius_m AS float8))::geometry
      ), 3)) AS geom
    ) clipped
    WHERE s.id = CAST(:site_id AS text) AND NOT ST_IsEmpty(clipped.geom)
    ON CONFLICT (site_id, fld_ar_id) DO NOTHING
    """
)

RECORD_LOAD = text(
    """
    INSERT INTO flood_zone_loads (site_id, radius_mi, zone_count, loaded_at)
    SELECT CAST(:site_id AS text), CAST(:radius_mi AS real), count(*), now()
    FROM flood_zones WHERE site_id = CAST(:site_id AS text)
    ON CONFLICT (site_id) DO UPDATE
      SET radius_mi = EXCLUDED.radius_mi, zone_count = EXCLUDED.zone_count, loaded_at = now()
    RETURNING zone_count
    """
)


async def store_site_zones(site_id: str, zones: list[FloodZoneFeature], radius_mi: float) -> int:
    """Replace a site's flood zones in one transaction. Returns the number stored."""
    rows = [
        {
            "site_id": site_id,
            "radius_m": radius_mi * METERS_PER_MILE,
            "geojson": json.dumps(zone.geometry),
            **zone.model_dump(exclude={"geometry"}),
        }
        for zone in zones
    ]
    async with SessionLocal() as session, session.begin():
        await session.execute(
            text("DELETE FROM flood_zones WHERE site_id = :site_id"), {"site_id": site_id}
        )
        if rows:
            await session.execute(INSERT_ZONE, rows)
        result = await session.execute(RECORD_LOAD, {"site_id": site_id, "radius_mi": radius_mi})
        return result.scalar_one()


async def load(site_ids: list[str] | None, radius_mi: float) -> int:
    """Load zones for the given sites (all sites when None). Returns the number of failures."""
    async with SessionLocal() as session:
        query = "SELECT id, ST_Y(geom) AS lat, ST_X(geom) AS lon FROM sites"
        if site_ids:
            query += " WHERE id = ANY(:ids)"
        sites = (await session.execute(text(query + " ORDER BY id"), {"ids": site_ids})).all()

    if site_ids and (missing := set(site_ids) - {s.id for s in sites}):
        print(f"Unknown site ids: {', '.join(sorted(missing))}")
    if not sites:
        print("No sites to load.")
        return 1 if site_ids else 0

    base_url = get_settings().fema_nfhl_url
    limit = asyncio.Semaphore(CONCURRENT_REQUESTS)
    failures = 0

    async def load_site(client: httpx.AsyncClient, site_id: str, lat: float, lon: float) -> None:
        nonlocal failures
        try:
            async with limit:
                zones = await fetch_flood_zones(client, base_url, lat, lon, radius_mi)
            stored = await store_site_zones(site_id, zones, radius_mi)
            print(f"{site_id}: {stored} flood zones stored ({len(zones)} fetched)")
        except FemaNfhlError as exc:
            failures += 1
            print(f"{site_id}: FAILED - {exc}")

    async with httpx.AsyncClient() as client:
        await asyncio.gather(*(load_site(client, s.id, s.lat, s.lon) for s in sites))
    await engine.dispose()

    print(f"Done: {len(sites) - failures} loaded, {failures} failed.")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--site", action="append", dest="sites", help="site id (repeatable)")
    parser.add_argument("--radius-mi", type=float, default=2.0, help="search radius in miles")
    args = parser.parse_args()
    failures = asyncio.run(load(args.sites, args.radius_mi))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
