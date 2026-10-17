"""Fit conditional logistic regressions (case-crossover) and write odds-ratio tables.

Each injury is its own stratum: the injury day is compared with same-weekday days in the same
month at the same location, so location, industry, season, and weekday are controlled by design.
An odds ratio of 1.10 for a weather group means ~10% more severe injuries on exposed days.

Usage: uv run python -I analysis/model.py [--placebo]
"""

import argparse
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.discrete.conditional_models import ConditionalLogit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import CACHE_DIR, OUT_DIR, RADII_KM  # noqa: E402

ICE_SNOW = r"\b(?:ice|icy|iced|snow|snowy|sleet|frozen|frost)\b"

# OSHA-coded injuries whose cause is weather; each should respond to its matching group
VALIDATION = {
    "heat_illness": ("heat", lambda o: o.event_title.eq("Exposure to environmental heat")),
    "cold_injury": (
        "cold",
        lambda o: (
            o.event_title.eq("Exposure to environmental cold") | o.nature_title.eq("Frostbite")
        ),
    ),
    "slip_on_ice_snow": (
        "winter",
        lambda o: (
            o.event_title.str.contains("slipping", case=False)
            & o.narrative.str.contains(ICE_SNOW, case=False)
        ),
    ),
}


def design(exp: pd.DataFrame, radius: int) -> pd.DataFrame:
    """Exposure indicators per group: inside an event polygon OR within `radius` km of an event
    point. A group can have both (e.g. a few hail events lack coordinates and use their zone)."""
    exposed: dict[str, pd.Series] = {}
    for c in exp.columns:
        if c.startswith("zone_"):
            group, flag = c.removeprefix("zone_"), exp[c].astype(bool)
        elif c.startswith("dist_"):
            group, flag = c.removeprefix("dist_"), exp[c] <= radius
        else:
            continue
        exposed[group] = exposed[group] | flag if group in exposed else flag
    return pd.DataFrame(exposed, index=exp.index).astype(float)[sorted(exposed)]


def fit(exp: pd.DataFrame, X: pd.DataFrame, label: str) -> pd.DataFrame:
    """Conditional logit of case ~ X, stratified by injury. Returns one row per covariate."""
    # strata where no covariate varies carry no information; dropping them is exact and fast
    by = X.groupby(exp.upa.values)
    varies = (by.transform("max") != by.transform("min")).any(axis=1)
    X, y, g = X[varies], exp.case[varies].astype(float), exp.upa[varies]
    # drop covariates with no exposed cases (inestimable)
    keep = [c for c in X.columns if X.loc[y == 1, c].sum() >= 5]
    X = X[keep]
    if X.empty:
        return pd.DataFrame()
    t0 = time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = ConditionalLogit(y.values, X.values, groups=g.values).fit(disp=False)
    ci = np.exp(res.conf_int())
    print(
        f"  {label}: {g.nunique():,} informative injuries, {len(keep)} groups, "
        f"{time.perf_counter() - t0:.1f}s"
    )
    return pd.DataFrame(
        {
            "subset": label.split(" @ ")[0],
            "group": keep,
            "odds_ratio": np.exp(res.params),
            "ci_low": ci[:, 0],
            "ci_high": ci[:, 1],
            "p_value": res.pvalues,
            "exposed_cases": X[y == 1].sum().values.astype(int),
            "exposed_controls": X[y == 0].sum().values.astype(int),
        }
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--placebo", action="store_true", help="use exposure_placebo.parquet")
    ap.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    tag = "_placebo" if args.placebo else ""
    exp = pd.read_parquet(args.cache_dir / f"exposure{tag}.parquet")
    osha = pd.read_parquet(args.cache_dir / "osha.parquet").set_index("upa")
    exp = exp.join(osha[["outdoor"]], on="upa")

    tables = []
    for radius in RADII_KM:
        print(f"radius {radius} km")
        X = design(exp, radius)
        for label, mask in [
            ("all", np.ones(len(exp), bool)),
            ("outdoor_industries", exp.outdoor.values),
            ("indoor_industries", ~exp.outdoor.values),
        ]:
            t = fit(exp[mask], X[mask], f"{label} @ {radius}km")
            tables.append(t.assign(radius_km=radius))

        for name, (group, select) in VALIDATION.items():
            ids = osha.index[select(osha)]
            m = exp.upa.isin(ids).values
            print(f"  validation {name}: {len(ids):,} injuries")
            t = fit(exp[m], X.loc[m, [group]], f"{name} @ {radius}km")
            tables.append(t.assign(radius_km=radius))

    out = pd.concat(tables, ignore_index=True)
    out["n_cases_in_subset"] = out.subset.map(
        {
            **{k: len(osha.index[f(osha)]) for k, (_, f) in VALIDATION.items()},
            "all": len(osha),
            "outdoor_industries": int(osha.outdoor.sum()),
            "indoor_industries": int((~osha.outdoor).sum()),
        }
    )
    path = args.out_dir / f"odds_ratios{tag}.csv"
    out.round(4).to_csv(path, index=False)
    print(f"\nwrote {path}")

    view = out[(out.radius_km == 25)].copy()
    view["OR (95% CI)"] = view.apply(
        lambda r: f"{r.odds_ratio:.2f} ({r.ci_low:.2f}-{r.ci_high:.2f})", axis=1
    )
    print("\nPoint-event radius 25 km:")
    print(view.pivot(index="group", columns="subset", values="OR (95% CI)").fillna("").to_string())


if __name__ == "__main__":
    main()
