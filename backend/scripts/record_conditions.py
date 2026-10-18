"""Record current weather conditions for each site into the conditions table.

Run from backend/:
    uv run python -m scripts.record_conditions                    # every site
    uv run python -m scripts.record_conditions --site 6145 --site 3456

Safe to run from cron: re-running within the same 15-minute Open-Meteo interval updates the
existing row. To record continuously inside the API instead, set CONDITIONS_ENABLED=true.
"""

import argparse
import asyncio

import httpx

from app.db import engine
from app.services.conditions import record_conditions


async def run(site_ids: list[str] | None) -> int:
    """Record conditions and hazard scores; returns the number of failures."""
    async with httpx.AsyncClient() as client:
        result = await record_conditions(client, site_ids)
    await engine.dispose()

    for site_id, error in sorted(result.failed.items()):
        print(f"{site_id}: FAILED - {error}")
    for site_id, error in sorted(result.score_failed.items()):
        print(f"{site_id}: SCORE FAILED - {error}")
    for site_id in sorted(result.score_skipped):
        print(f"{site_id}: no usable weather to score; its hazard score was left unchanged")
    print(f"Done: {result.stored} recorded, {len(result.failed)} failed.")
    print(f"Hazard scores: {result.scored} saved, {len(result.score_failed)} failed.")
    if site_ids and result.stored + len(result.failed) < len(set(site_ids)):
        print("Some site ids were not found.")
    return len(result.failed) + len(result.score_failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--site", action="append", dest="sites", help="site id (repeatable)")
    args = parser.parse_args()
    failures = asyncio.run(run(args.sites))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
