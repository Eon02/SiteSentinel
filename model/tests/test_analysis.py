import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
from exposure import expand_days, point_exposure, reference_days  # noqa: E402
from load import parse_damage  # noqa: E402
from model import design  # noqa: E402


def test_design_combines_zone_and_point_exposure_for_same_group():
    exp = pd.DataFrame({
        "upa": [1, 1, 1],
        "case": [True, False, False],
        "dist_hail": [5.0, np.nan, 40.0],
        "zone_hail": [False, True, False],
        "zone_heat": [True, False, False],
    })  # fmt: skip
    X = design(exp, radius=25)
    assert list(X.columns) == ["hail", "heat"]
    assert X.hail.tolist() == [1.0, 1.0, 0.0]  # point match OR zone match; 40 km is outside
    assert X.heat.tolist() == [1.0, 0.0, 0.0]


def test_parse_damage():
    s = pd.Series(["70000", "0.00K", "1.5M", "2B", "", "12K"])
    out = parse_damage(s)
    assert out.iloc[:4].tolist() == [70000, 0, 1_500_000, 2_000_000_000]
    assert np.isnan(out.iloc[4])
    assert out.iloc[5] == 12_000


def test_reference_days_same_weekday_same_month():
    osha = pd.DataFrame({"upa": [1, 2], "date": pd.to_datetime(["2023-07-12", "2024-02-29"])})
    refs = reference_days(osha)
    for upa, d in zip(osha.upa, osha.date, strict=True):
        r = refs[refs.upa == upa]
        assert r.case.sum() == 1 and r.loc[r.case, "ref_date"].iloc[0] == d
        assert (r.ref_date.dt.weekday == d.weekday()).all()
        assert (r.ref_date.dt.month == d.month).all()
    # July 2023 has four Wednesdays: 5, 12, 19, 26
    assert refs[refs.upa == 1].ref_date.dt.day.tolist() == [5, 12, 19, 26]


def test_expand_days_includes_day_after():
    ev = pd.DataFrame({
        "begin_date": pd.to_datetime(["2020-01-01"]),
        "end_date": pd.to_datetime(["2020-01-02"]),
        "group": ["heat"],
    })  # fmt: skip
    days = expand_days(ev, ["group"])
    assert days.date.dt.day.tolist() == [1, 2, 3]  # begin..end plus DAYS_AFTER_EVENT=1


def test_point_exposure_distance_and_date():
    osha = pd.DataFrame({"upa": [1], "date": pd.to_datetime(["2020-06-10"]),
                         "lat": [40.0], "lon": [-90.0]})  # fmt: skip
    refs = reference_days(osha)
    storms = pd.DataFrame({
        "begin_date": pd.to_datetime(["2020-06-10"]),
        "end_date": pd.to_datetime(["2020-06-10"]),
        "group": ["hail"],
        "begin_lat": [40.1], "begin_lon": [-90.0],  # ~11.1 km north
        "end_lat": [np.nan], "end_lon": [np.nan],
        "has_point": [True],
    })  # fmt: skip
    out = point_exposure(osha, refs, storms).merge(refs, on=["upa", "ref_date"])
    case = out[out.case].iloc[0]
    assert abs(case.dist_hail - 11.1) < 0.2
    assert out.loc[~out.case, "dist_hail"].isna().all()  # other Wednesdays: no event
