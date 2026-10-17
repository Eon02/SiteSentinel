"""Build case-crossover exposure table: one row per injury x reference day.

Reference days (time-stratified): every day in the injury's month and year that falls on the
same weekday as the injury. The injury day is the case (case=1); the rest are controls.

Exposure for a reference day d, per weather group:
  * zone/county events (no coordinates): the injury point lies inside the event's UGC polygon
    and begin_date <= d <= end_date + DAYS_AFTER_EVENT  -> boolean column  zone_<group>
  * point events: min haversine distance (km) from the injury to any event point active on d,
    up to max(RADII_KM)                                   -> float column  dist_<group>

Usage: uv run python -I analysis/exposure.py [--placebo-shift-days N]
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    CACHE_DIR,
    DAYS_AFTER_EVENT,
    EARTH_RADIUS_KM,
    RADII_KM,
    REFERENCE_DIR,
    YEARS,
)


def reference_days(osha: pd.DataFrame) -> pd.DataFrame:
    """Injury day plus same-weekday days in the same month (3-4 controls per injury)."""
    parts = []
    for weeks in range(-4, 5):
        ref = osha.date + pd.Timedelta(weeks=weeks)
        same_month = (ref.dt.month == osha.date.dt.month) & (ref.dt.year == osha.date.dt.year)
        parts.append(
            pd.DataFrame(
                {"upa": osha.upa[same_month], "ref_date": ref[same_month], "case": weeks == 0}
            )
        )
    refs = pd.concat(parts, ignore_index=True).sort_values(["upa", "ref_date"])
    assert refs.groupby("upa").case.sum().eq(1).all()
    return refs.reset_index(drop=True)


def expand_days(events: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """One row per event per active day, from begin_date to end_date + DAYS_AFTER_EVENT."""
    n_days = (events.end_date - events.begin_date).dt.days + 1 + DAYS_AFTER_EVENT
    rep = events.loc[events.index.repeat(n_days), cols + ["begin_date"]]
    offset = rep.groupby(level=0).cumcount()
    rep["date"] = rep.begin_date + pd.to_timedelta(offset, unit="D")
    return rep.drop(columns="begin_date").reset_index(drop=True)


def load_snapshots(ref_dir: Path) -> dict[int, gpd.GeoDataFrame]:
    snaps = {}
    for year in YEARS:
        g = gpd.read_file(ref_dir / f"ugcs_{year}.geojson", columns=["ugc"])
        g["geometry"] = g.geometry.make_valid()  # IEM's simplified shapes are ~0.2% invalid
        # a UGC can appear as several features (e.g. split counties); merge to one geometry
        snaps[year] = g.dissolve("ugc").reset_index()[["ugc", "geometry"]].to_crs("EPSG:4326")
    return snaps


def zone_exposure(osha, refs, storms, snaps) -> pd.DataFrame:
    zone_ev = storms[~storms.has_point]
    days = expand_days(zone_ev, ["ugc", "group"]).drop_duplicates()
    days["year"] = days.date.dt.year

    # report how many zone events have a polygon in their own year's snapshot
    ev_year = zone_ev.begin_date.dt.year
    codes_by_year = pd.DataFrame(
        [(y, u) for y, s in snaps.items() for u in s.ugc.unique()], columns=["year", "ugc"]
    ).assign(found=True)
    in_own = (
        pd.DataFrame({"year": ev_year.values, "ugc": zone_ev.ugc.values})
        .merge(codes_by_year, on=["year", "ugc"], how="left")
        .found.fillna(False)
        .values
    )
    all_codes = set().union(*(set(s.ugc) for s in snaps.values()))
    in_any = zone_ev.ugc.isin(all_codes).values
    print(
        f"zone events: {len(zone_ev):,}; polygon in own-year snapshot {in_own.mean():.1%}, "
        f"in any snapshot {in_any.mean():.1%}"
    )
    missing = zone_ev.loc[~in_any, "ugc"].value_counts().head(5)
    if len(missing):
        print("  most common codes with no polygon:", dict(missing))

    pts = gpd.GeoDataFrame(
        osha[["upa"]], geometry=gpd.points_from_xy(osha.lon, osha.lat), crs="EPSG:4326"
    )
    year_of = osha.date.dt.year.values
    membership = []
    for year in YEARS:
        snap = snaps[year]
        needed = set(days.loc[days.year == year, "ugc"])
        # fallback: codes this year's events use but this year's snapshot lacks
        extra_codes = needed - set(snap.ugc)
        extra = []
        for code in extra_codes:
            for other in sorted(YEARS, key=lambda y, year=year: abs(y - year)):
                hit = snaps[other][snaps[other].ugc == code]
                if len(hit):
                    extra.append(hit)
                    break
        polys = pd.concat([snap[snap.ugc.isin(needed)], *extra], ignore_index=True)
        j = gpd.sjoin(pts[year_of == year], polys, predicate="within", how="inner")
        membership.append(j[["upa", "ugc"]])
    membership = pd.concat(membership, ignore_index=True).drop_duplicates()
    inside_any = osha.upa.isin(membership.upa).mean()
    print(f"injuries inside at least one event-bearing UGC polygon: {inside_any:.1%}")

    hits = (
        refs[["upa", "ref_date"]]
        .merge(membership, on="upa")
        .merge(days.rename(columns={"date": "ref_date"}), on=["ugc", "ref_date"])
    )
    flags = (
        hits.drop_duplicates(["upa", "ref_date", "group"])
        .assign(v=True)
        .pivot_table(
            index=["upa", "ref_date"], columns="group", values="v", aggfunc="any", fill_value=False
        )
    )
    flags.columns = [f"zone_{c}" for c in flags.columns]
    return flags.reset_index()


def point_exposure(osha, refs, storms) -> pd.DataFrame:
    pev = storms[storms.has_point]
    begin = pev[["begin_date", "end_date", "group", "begin_lat", "begin_lon"]].rename(
        columns={"begin_lat": "lat", "begin_lon": "lon"}
    )
    has_end = pev.end_lat.notna() & (
        (pev.end_lat != pev.begin_lat) | (pev.end_lon != pev.begin_lon)
    )
    end = pev.loc[has_end, ["begin_date", "end_date", "group", "end_lat", "end_lon"]].rename(
        columns={"end_lat": "lat", "end_lon": "lon"}
    )
    pts = expand_days(pd.concat([begin, end], ignore_index=True), ["group", "lat", "lon"])
    print(f"point events: {len(pev):,} -> {len(pts):,} point-days")

    loc = osha.set_index("upa")[["lat", "lon"]]
    r = refs[["upa", "ref_date"]].join(loc, on="upa")
    groups = sorted(pts.group.unique())
    pts["gi"] = pts.group.map({g: i for i, g in enumerate(groups)})
    out = np.full((len(r), len(groups)), np.inf)
    max_r = max(RADII_KM) / EARTH_RADIUS_KM
    latlon = np.radians(r[["lat", "lon"]].values)

    pts_by_day = {d: g for d, g in pts.groupby("date")}
    for day, rows in r.groupby("ref_date").indices.items():
        ev = pts_by_day.get(day)
        if ev is None:
            continue
        tree = BallTree(np.radians(ev[["lat", "lon"]].values), metric="haversine")
        idx, dist = tree.query_radius(latlon[rows], r=max_r, return_distance=True)
        counts = np.fromiter((len(i) for i in idx), dtype=int, count=len(idx))
        if not counts.sum():
            continue
        row_ids = np.repeat(rows, counts)
        gi = ev.gi.values[np.concatenate(idx)]
        np.minimum.at(out, (row_ids, gi), np.concatenate(dist) * EARTH_RADIUS_KM)
    out[np.isinf(out)] = np.nan

    res = r[["upa", "ref_date"]].copy()
    for gi, g in enumerate(groups):
        res[f"dist_{g}"] = out[:, gi]
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--placebo-shift-days",
        type=int,
        default=0,
        help="shift all storm dates by N days (placebo test; 364 keeps weekday)",
    )
    ap.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    ap.add_argument("--reference-dir", type=Path, default=REFERENCE_DIR)
    args = ap.parse_args()

    osha = pd.read_parquet(args.cache_dir / "osha.parquet")
    storms = pd.read_parquet(args.cache_dir / "storms.parquet")
    if args.placebo_shift_days:
        shift = pd.Timedelta(days=args.placebo_shift_days)
        storms["begin_date"] += shift
        storms["end_date"] += shift
        print(f"PLACEBO: storm dates shifted by {args.placebo_shift_days} days")

    refs = reference_days(osha)
    print(
        f"reference table: {len(refs):,} rows for {refs.upa.nunique():,} injuries "
        f"({len(refs) / refs.upa.nunique():.2f} days per injury)"
    )

    snaps = load_snapshots(args.reference_dir)
    zone = zone_exposure(osha, refs, storms, snaps)
    point = point_exposure(osha, refs, storms)

    exp = refs.merge(point, on=["upa", "ref_date"], how="left", validate="1:1")
    exp = exp.merge(zone, on=["upa", "ref_date"], how="left", validate="1:1")
    zcols = [c for c in exp.columns if c.startswith("zone_")]
    exp[zcols] = exp[zcols].fillna(False).astype(bool)
    assert len(exp) == len(refs)

    name = "exposure_placebo.parquet" if args.placebo_shift_days else "exposure.parquet"
    exp.to_parquet(args.cache_dir / name, index=False)
    print(f"\nwrote {args.cache_dir / name}: {len(exp):,} rows")
    summary = pd.DataFrame(
        {
            "case_days": [exp.loc[exp.case, c].mean() for c in zcols],
            "control_days": [exp.loc[~exp.case, c].mean() for c in zcols],
        },
        index=zcols,
    )
    for c in [c for c in exp.columns if c.startswith("dist_")]:
        within = exp[c] <= 25
        summary.loc[f"{c}<=25km"] = [within[exp.case].mean(), within[~exp.case].mean()]
    print("Share of days exposed (raw, unadjusted):")
    print((summary * 100).round(2).to_string())


if __name__ == "__main__":
    main()
