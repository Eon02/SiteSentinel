"""Download historical NWS county/zone (UGC) polygons from the Iowa Environmental Mesonet.

One snapshot per year, valid on July 1. Existing files are skipped.

Usage: uv run python -I analysis/fetch_zones.py [reference_dir]
"""

import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import REFERENCE_DIR, YEARS  # noqa: E402

URL = "https://mesonet.agron.iastate.edu/api/1/nws/ugcs.geojson?valid={year}-07-01T00:00Z"


def fetch(year: int, out_dir: Path) -> str:
    dest = out_dir / f"ugcs_{year}.geojson"
    if dest.exists() and dest.stat().st_size > 0:
        return f"{year}: exists"
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(URL.format(year=year), timeout=600) as resp, open(tmp, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)
    tmp.rename(dest)
    return f"{year}: {dest.stat().st_size / 1e6:.1f} MB"


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else REFERENCE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    # a few concurrent requests; IEM is a free public service, so stay polite
    with ThreadPoolExecutor(max_workers=3) as pool:
        for msg in pool.map(lambda y: fetch(y, out_dir), YEARS):
            print(msg, flush=True)


if __name__ == "__main__":
    main()
