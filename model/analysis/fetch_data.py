"""Download NOAA Storm Events bulk CSVs (2015-2025) from NCEI and gunzip them into model/.

Picks the newest revision (cYYYYMMDD) of each year's file. Years that already have a file are
skipped unless --refresh is given, in which case the old revision is replaced (two revisions of
the same year would duplicate events; load.py asserts EVENT_ID is unique).

Usage: uv run python -I analysis/fetch_data.py [--tables details locations fatalities] [--refresh]
"""

import argparse
import gzip
import re
import shutil
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import MODEL_DIR, YEARS  # noqa: E402

BASE_URL = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"
TABLE_DIRS = {
    "details": "storm_event_details",
    "locations": "storm_event_locations",
    "fatalities": "storm_event_fatalities",
}


def latest_files(table: str) -> dict[int, str]:
    """{year: newest .csv.gz filename} from the NCEI directory listing."""
    with urllib.request.urlopen(BASE_URL, timeout=120) as resp:
        listing = resp.read().decode()
    pat = rf"StormEvents_{table}-ftp_v1\.0_d(\d{{4}})_c(\d{{8}})\.csv\.gz"
    newest: dict[int, tuple[str, str]] = {}
    for year, created in set(re.findall(pat, listing)):
        y = int(year)
        if y in YEARS and (y not in newest or created > newest[y][0]):
            newest[y] = (created, f"StormEvents_{table}-ftp_v1.0_d{year}_c{created}.csv.gz")
    return {y: name for y, (_, name) in sorted(newest.items())}


def fetch(table: str, refresh: bool) -> None:
    out_dir = MODEL_DIR / TABLE_DIRS[table]
    out_dir.mkdir(exist_ok=True)
    files = latest_files(table)
    missing = sorted(set(YEARS) - set(files))
    if missing:
        print(f"{table}: WARNING no file on NCEI for years {missing}")
    for year, gz_name in files.items():
        dest = out_dir / gz_name.removesuffix(".gz")
        existing = sorted(out_dir.glob(f"StormEvents_{table}-ftp_v1.0_d{year}_c*.csv"))
        if existing and not refresh:
            print(f"{table} {year}: exists ({existing[-1].name})")
            continue
        tmp = dest.with_suffix(".part")
        with urllib.request.urlopen(BASE_URL + gz_name, timeout=600) as resp:
            with gzip.open(resp) as gz, open(tmp, "wb") as f:
                shutil.copyfileobj(gz, f)
        for old in existing:
            old.unlink()
        tmp.rename(dest)
        print(f"{table} {year}: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", nargs="+", choices=list(TABLE_DIRS), default=["details"])
    ap.add_argument("--refresh", action="store_true", help="replace with newest NCEI revision")
    args = ap.parse_args()
    for table in args.tables:
        fetch(table, args.refresh)


if __name__ == "__main__":
    main()
