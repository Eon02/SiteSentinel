"""Import job sites from an EIA-860M style spreadsheet (.xlsx) into the sites table.

Run from backend/:
    uv run python -m scripts.import_sites path/to/sheet.xlsx              # add new sites
    uv run python -m scripts.import_sites path/to/sheet.xlsx --dry-run    # preview only
    uv run python -m scripts.import_sites path/to/sheet.xlsx --update     # also overwrite existing

The sheet needs EIA-860M "Operating" columns: Plant ID, Plant Name, Entity Name, County,
Plant State, Technology, Nameplate Capacity (MW), Latitude, Longitude. EIA lists one row per
generator, so rows are grouped by Plant ID; the plant type is the technology of its largest
generator. Two fields EIA doesn't have are derived:

- timezone: looked up offline from the coordinates (timezonefinder).
- near_river: true for hydroelectric plants, or when a river of Strahler stream order 5 or
  more (a sizable river, not a creek) runs within 1 mile, per the USGS National Hydrography
  Dataset. If the USGS lookup fails, near_river falls back to the hydro rule and is reported.
"""

import argparse
import asyncio
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook
from pydantic import ValidationError
from sqlalchemy import text
from timezonefinder import TimezoneFinder

from app.db import SessionLocal, engine
from app.schemas import SiteCreate

NHD_FLOWLINES = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/4/query"
RIVER_RADIUS_MI = 1
MIN_STREAM_ORDER = 5
CONCURRENT_LOOKUPS = 6  # USGS is slow per request but handles a few in parallel

REQUIRED = ["Plant ID", "Plant Name", "Plant State", "Latitude", "Longitude"]


@dataclass
class NearestRiver:
    name: str | None
    stream_order: int


def read_plants(path: Path) -> list[dict]:
    """One dict per plant (grouped by Plant ID), keeping its largest generator's row."""
    sheet = load_workbook(path, read_only=True, data_only=True).active
    rows = list(sheet.iter_rows(values_only=True))
    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise SystemExit(f"Spreadsheet is missing columns: {', '.join(missing)}")
    plants: dict[str, dict] = {}
    for values in rows[1:]:
        row = dict(zip(header, values, strict=False))
        if row.get("Plant ID") in (None, ""):
            continue
        plant_id = str(int(float(row["Plant ID"])))
        capacity = float(row.get("Nameplate Capacity (MW)") or 0)
        kept = plants.get(plant_id)
        if kept is None or capacity > float(kept.get("Nameplate Capacity (MW)") or 0):
            plants[plant_id] = {**row, "Plant ID": plant_id}
    return list(plants.values())


def is_hydro(technology: str | None) -> bool:
    return "hydro" in (technology or "").lower()


def nearest_major_river(lat: float, lon: float) -> NearestRiver | None:
    """Largest river within RIVER_RADIUS_MI per USGS NHD, or None if there isn't one.

    Raises OSError/ValueError if USGS can't be reached after retries.
    """
    query = urllib.parse.urlencode(
        {
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "distance": RIVER_RADIUS_MI,
            "units": "esriSRUnit_StatuteMile",
            "returnGeometry": "false",
            "outFields": "GNIS_NAME,StreamOrde",
            "f": "json",
        }
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(f"{NHD_FLOWLINES}?{query}", timeout=30) as response:
                data = json.load(response)
            if "error" in data:
                raise ValueError(data["error"])
            break
        except (OSError, ValueError) as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
    else:
        raise OSError(f"USGS NHD unavailable: {last_error!r}")

    best = max(
        (f["attributes"] for f in data.get("features", [])),
        key=lambda a: a.get("StreamOrde") or 0,
        default=None,
    )
    if not best or (best.get("StreamOrde") or 0) < MIN_STREAM_ORDER:
        return None
    return NearestRiver((best.get("GNIS_NAME") or "").strip() or None, best["StreamOrde"])


RiverLookup = NearestRiver | None | OSError  # OSError = USGS lookup failed


def lookup_river(plant: dict) -> RiverLookup:
    try:
        return nearest_major_river(float(plant["Latitude"]), float(plant["Longitude"]))
    except OSError as exc:
        return exc
    except (TypeError, ValueError):
        return None  # bad coordinates; to_site reports them


def to_site(plant: dict, tf: TimezoneFinder, river: RiverLookup | str) -> tuple[SiteCreate, str]:
    """Build a validated site and a short note on how near_river was decided.

    `river` is the USGS lookup result, or "skip" when the river check is off.
    """
    lat, lon = float(plant["Latitude"]), float(plant["Longitude"])
    technology = plant.get("Technology")
    near_river = is_hydro(technology)
    if river == "skip":
        note = "hydro rule only (--no-river-check)"
    elif isinstance(river, OSError):
        note = f"USGS lookup failed, used hydro rule ({river})"
    elif river is not None:
        near_river = True
        note = f"{river.name or 'unnamed river'} (order {river.stream_order})"
    else:
        note = "hydro" if near_river else "no major river within 1 mi"

    site = SiteCreate(
        id=plant["Plant ID"],
        name=str(plant["Plant Name"]).strip(),
        utility=(str(plant.get("Entity Name") or "").strip() or None),
        plant_type=(str(technology).strip() if technology else None),
        county=(str(plant.get("County") or "").strip().title() or None),
        state=(str(plant.get("Plant State") or "").strip().upper() or None),
        timezone=tf.timezone_at(lat=lat, lng=lon) or "",
        near_river=near_river,
        latitude=lat,
        longitude=lon,
    )
    return site, note


async def save(sites: list[SiteCreate], update: bool) -> tuple[int, int]:
    """Insert sites; existing ids are skipped, or overwritten with --update."""
    conflict = (
        """DO UPDATE SET name = EXCLUDED.name, utility = EXCLUDED.utility,
             plant_type = EXCLUDED.plant_type, county = EXCLUDED.county, state = EXCLUDED.state,
             timezone = EXCLUDED.timezone, near_river = EXCLUDED.near_river, geom = EXCLUDED.geom"""
        if update
        else "DO NOTHING"
    )
    statement = text(
        f"""
        INSERT INTO sites (id, name, utility, plant_type, county, state, timezone, near_river, geom)
        VALUES (:id, :name, :utility, :plant_type, :county, :state, :timezone, :near_river,
                ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326))
        ON CONFLICT (id) {conflict}
        RETURNING (xmax = 0) AS inserted
        """
    )
    inserted = updated = 0
    async with SessionLocal() as session, session.begin():
        for site in sites:
            row = (await session.execute(statement, site.model_dump())).first()
            if row is None:
                continue
            inserted += row.inserted
            updated += not row.inserted
    await engine.dispose()
    return inserted, updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("path", type=Path, help="EIA-860M style .xlsx file")
    parser.add_argument("--dry-run", action="store_true", help="show what would be imported")
    parser.add_argument("--update", action="store_true", help="overwrite sites that already exist")
    parser.add_argument(
        "--no-river-check", action="store_true", help="skip the USGS lookup (hydro rule only)"
    )
    args = parser.parse_args()

    tf = TimezoneFinder()
    plants = read_plants(args.path)
    print(f"{len(plants)} plants in {args.path.name}", flush=True)
    if args.no_river_check:
        rivers = ["skip"] * len(plants)
    else:
        print("Checking USGS river data...", flush=True)
        with ThreadPoolExecutor(CONCURRENT_LOOKUPS) as pool:
            rivers = list(pool.map(lookup_river, plants))

    sites, problems = [], []
    for plant, river in zip(plants, rivers, strict=True):
        try:
            site, note = to_site(plant, tf, river)
        except (ValidationError, ValueError, TypeError) as exc:
            problems.append(f"Plant {plant.get('Plant ID')}: {exc}")
            continue
        sites.append(site)
        river = "river" if site.near_river else "     "
        state = site.state or ""
        print(f"{site.id:>6}  {site.name[:28]:<28} {state:<2} {site.timezone:<20} {river}  {note}")

    for problem in problems:
        print("SKIPPED", problem)
    if args.dry_run:
        print(f"Dry run: {len(sites)} valid sites, {len(problems)} skipped. Nothing written.")
        return
    inserted, updated = asyncio.run(save(sites, args.update))
    unchanged = len(sites) - inserted - updated
    print(
        f"Done: {inserted} added, {updated} updated, {unchanged} already existed, "
        f"{len(problems)} skipped."
    )


if __name__ == "__main__":
    main()
