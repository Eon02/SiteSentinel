"""Load and clean OSHA severe injury reports and NOAA storm events into cache/*.parquet.

Usage: uv run python -I analysis/load.py [osha_csv] [storm_details_dir] [cache_dir]
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    CACHE_DIR,
    EVENT_GROUPS,
    OSHA_CSV,
    OUTDOOR_NAICS2,
    STORM_DETAILS_DIR,
)

# NOAA STATE_FIPS -> postal code. Territories use NOAA's own codes (96-99);
# 81-95 are marine areas and are intentionally absent.
STATE_POSTAL = {
    1: "AL", 2: "AK", 4: "AZ", 5: "AR", 6: "CA", 8: "CO", 9: "CT", 10: "DE", 11: "DC",
    12: "FL", 13: "GA", 15: "HI", 16: "ID", 17: "IL", 18: "IN", 19: "IA", 20: "KS",
    21: "KY", 22: "LA", 23: "ME", 24: "MD", 25: "MA", 26: "MI", 27: "MN", 28: "MS",
    29: "MO", 30: "MT", 31: "NE", 32: "NV", 33: "NH", 34: "NJ", 35: "NM", 36: "NY",
    37: "NC", 38: "ND", 39: "OH", 40: "OK", 41: "OR", 42: "PA", 44: "RI", 45: "SC",
    46: "SD", 47: "TN", 48: "TX", 49: "UT", 50: "VT", 51: "VA", 53: "WA", 54: "WV",
    55: "WI", 56: "WY", 96: "VI", 97: "AS", 98: "GU", 99: "PR",
}  # fmt: skip

STORM_COLS = [
    "EVENT_ID", "EPISODE_ID", "BEGIN_YEARMONTH", "BEGIN_DAY", "END_YEARMONTH", "END_DAY",
    "STATE_FIPS", "EVENT_TYPE", "CZ_TYPE", "CZ_FIPS", "CZ_NAME", "INJURIES_DIRECT",
    "DEATHS_DIRECT", "DAMAGE_PROPERTY", "MAGNITUDE", "BEGIN_LAT", "BEGIN_LON", "END_LAT",
    "END_LON",
]  # fmt: skip

DAMAGE_MULT = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}


def parse_damage(s: pd.Series) -> pd.Series:
    """'1.5M' -> 1_500_000; '70000' -> 70000; blank -> NaN."""
    parts = s.str.strip().str.extract(r"^([0-9.]*)([KMB]?)$")
    num = pd.to_numeric(parts[0], errors="coerce")
    return num * parts[1].map(DAMAGE_MULT)


def ymd(yearmonth: pd.Series, day: pd.Series) -> pd.Series:
    return pd.to_datetime(yearmonth.astype(int) * 100 + day.astype(int), format="%Y%m%d")


def load_storms(details_dir: Path) -> pd.DataFrame:
    files = sorted(details_dir.glob("StormEvents_details-*.csv"))
    raw = pd.concat(
        (pd.read_csv(f, usecols=STORM_COLS, dtype=str, keep_default_na=False) for f in files),
        ignore_index=True,
    )
    print(f"storm events: {len(raw):,} rows from {len(files)} files")

    ev = pd.DataFrame({
        "event_id": raw.EVENT_ID.astype("int64"),
        "episode_id": raw.EPISODE_ID.astype("int64"),
        "event_type": raw.EVENT_TYPE,
        "group": raw.EVENT_TYPE.map(EVENT_GROUPS),
        "begin_date": ymd(raw.BEGIN_YEARMONTH, raw.BEGIN_DAY),
        "end_date": ymd(raw.END_YEARMONTH, raw.END_DAY),
        "state": raw.STATE_FIPS.astype(int).map(STATE_POSTAL),
        "ugc": None,
        "cz_name": raw.CZ_NAME,
        "injuries_direct": pd.to_numeric(raw.INJURIES_DIRECT, errors="coerce"),
        "deaths_direct": pd.to_numeric(raw.DEATHS_DIRECT, errors="coerce"),
        "damage_property": parse_damage(raw.DAMAGE_PROPERTY),
        "magnitude": pd.to_numeric(raw.MAGNITUDE, errors="coerce"),
        "begin_lat": pd.to_numeric(raw.BEGIN_LAT, errors="coerce"),
        "begin_lon": pd.to_numeric(raw.BEGIN_LON, errors="coerce"),
        "end_lat": pd.to_numeric(raw.END_LAT, errors="coerce"),
        "end_lon": pd.to_numeric(raw.END_LON, errors="coerce"),
    })  # fmt: skip
    # UGC code, e.g. state IL + type Z + zone 13 -> "ILZ013"
    ev["ugc"] = ev.state + raw.CZ_TYPE + raw.CZ_FIPS.str.zfill(3)
    assert ev.event_id.is_unique

    n = len(ev)
    ev = ev.dropna(subset=["group", "state"])
    print(f"  dropped {n - len(ev):,} marine / drought / other ungrouped events")
    bad = ev.end_date < ev.begin_date
    if bad.any():
        print(f"  dropped {bad.sum():,} events ending before they begin")
        ev = ev[~bad]

    ev["has_point"] = ev.begin_lat.notna() & ev.begin_lon.notna()
    print(
        f"  kept {len(ev):,}: {ev.has_point.sum():,} point events, "
        f"{(~ev.has_point).sum():,} zone/county events"
    )
    print(
        ev.groupby("group")
        .agg(events=("event_id", "size"), point_share=("has_point", "mean"))
        .round(2)
        .to_string()
    )
    return ev.reset_index(drop=True)


def load_osha(csv: Path) -> pd.DataFrame:
    raw = pd.read_csv(csv, dtype=str, keep_default_na=False)
    print(f"\nOSHA reports: {len(raw):,}")
    df = pd.DataFrame({
        "upa": raw.UPA.astype("int64"),
        "date": pd.to_datetime(raw.EventDate, format="%m/%d/%Y"),
        "state": raw.State,
        "lat": pd.to_numeric(raw.Latitude, errors="coerce"),
        "lon": pd.to_numeric(raw.Longitude, errors="coerce"),
        "naics": raw["Primary NAICS"].str.strip(),
        "hospitalized": pd.to_numeric(raw.Hospitalized, errors="coerce"),
        "amputation": pd.to_numeric(raw.Amputation, errors="coerce"),
        "narrative": raw["Final Narrative"],
        "event_title": raw.EventTitle,
        "nature_title": raw.NatureTitle,
        "source_title": raw.SourceTitle,
    })  # fmt: skip
    assert df.upa.is_unique

    ok = df.lat.between(-15, 72) & df.lon.between(-180, 180) & ~((df.lat == 0) & (df.lon == 0))
    print(f"  dropped {(~ok).sum():,} with missing or invalid coordinates")
    df = df[ok].copy()

    df["naics2"] = df.naics.str[:2]
    df["outdoor"] = df.naics2.isin(OUTDOOR_NAICS2)
    print(
        f"  kept {len(df):,}; {df.date.min():%Y-%m-%d} to {df.date.max():%Y-%m-%d}; "
        f"outdoor-industry share {df.outdoor.mean():.1%}"
    )
    return df.reset_index(drop=True)


def main() -> None:
    osha_csv = Path(sys.argv[1]) if len(sys.argv) > 1 else OSHA_CSV
    details_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else STORM_DETAILS_DIR
    cache = Path(sys.argv[3]) if len(sys.argv) > 3 else CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)

    storms = load_storms(details_dir)
    storms.to_parquet(cache / "storms.parquet", index=False)
    osha = load_osha(osha_csv)
    osha.to_parquet(cache / "osha.parquet", index=False)
    print(f"\nwrote {cache / 'storms.parquet'} and {cache / 'osha.parquet'}")


if __name__ == "__main__":
    main()
