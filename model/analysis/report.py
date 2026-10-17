"""Forest plots of odds ratios from out/odds_ratios*.csv -> out/*.png.

Usage: uv run python -I analysis/report.py [--radius 25]
"""

import argparse
import sys
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import FixedFormatter, FixedLocator, NullLocator  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import OUT_DIR  # noqa: E402

# reference palette (dataviz skill, light mode)
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
SERIES = ["#2a78d6", "#eb6834"]  # categorical slots 1-2: blue, orange

LABELS = {
    "cold": "Cold / wind chill", "dust": "Dust storm", "flood": "Flood / heavy rain",
    "fog": "Dense fog", "hail": "Hail", "heat": "Heat / excessive heat",
    "high_wind": "High / strong wind", "lightning": "Lightning", "smoke_fire": "Wildfire / smoke",
    "tornado": "Tornado", "tropical": "Tropical storm / hurricane",
    "tstorm_wind": "Thunderstorm wind", "winter": "Winter storm / ice / snow",
    "heat_illness": "Heat illness → heat",
    "cold_injury": "Cold injury → cold",
    "slip_on_ice_snow": "Slip on ice/snow → winter",
}  # fmt: skip

HEADER_IN, FOOTER_IN, ROW_IN = 1.45, 0.85, 0.5  # figure layout, inches


def style(ax, title: str, subtitle: str) -> None:
    fig = ax.figure
    h = fig.get_figheight()
    ax.set_facecolor(SURFACE)
    fig.set_facecolor(SURFACE)
    ax.axvline(1, color=INK_2, lw=1, ls=(0, (3, 3)), zorder=1)
    ax.grid(axis="x", color=GRID, lw=0.8, zorder=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_2, length=0, labelsize=10)
    ax.tick_params(axis="y", labelcolor=INK, labelsize=11)
    ax.set_xlabel(
        "Odds ratio (log scale)  ·  >1 = more severe injuries on exposed days",
        color=INK_2,
        fontsize=10,
    )
    fig.text(0.02, 1 - 0.22 / h, title, color=INK, fontsize=14, weight="bold", va="top")
    fig.text(0.02, 1 - 0.55 / h, textwrap.fill(subtitle, 120), color=INK_2, fontsize=9.5,
             va="top", linespacing=1.4)  # fmt: skip


def set_ticks(ax, lo: float, hi: float) -> None:
    """Log x-axis with plain-number tick labels (call after any set_xscale)."""
    ax.set_xscale("log")
    if hi / lo > 20:  # wide range: sparser ticks so labels don't collide
        candidates = (0.5, 1, 2, 5, 10, 20, 50, 100)
    else:
        candidates = (0.25, 0.5, 0.67, 0.8, 1, 1.25, 1.5, 2, 3, 5, 10)
    ticks = [t for t in candidates if lo <= t <= hi]
    ax.set_xlim(lo, hi)
    ax.xaxis.set_major_locator(FixedLocator(ticks))
    ax.xaxis.set_major_formatter(FixedFormatter([f"{t:g}" for t in ticks]))
    ax.xaxis.set_minor_locator(NullLocator())


def forest(df: pd.DataFrame, series_col: str | None, title: str, subtitle: str, path: Path) -> None:
    """Dot = odds ratio, line = 95% CI. Optional second series offset vertically."""
    order = (
        (df[df[series_col] == df[series_col].iloc[0]] if series_col else df)
        .sort_values("odds_ratio")["group"]
        .tolist()
    )
    order += [g for g in df.group.unique() if g not in order]
    series = df[series_col].unique().tolist() if series_col else [None]
    h = ROW_IN * len(order) + HEADER_IN + FOOTER_IN
    fig, ax = plt.subplots(figsize=(9, h))
    fig.subplots_adjust(left=0.30, right=0.78, top=1 - HEADER_IN / h, bottom=FOOTER_IN / h)
    offsets = np.linspace(-0.15, 0.15, len(series)) if len(series) > 1 else [0]
    y_of = {g: i for i, g in enumerate(order)}
    for s, off, color in zip(series, offsets, SERIES, strict=False):
        d = df if s is None else df[df[series_col] == s]
        y = d.group.map(y_of).values + off
        ax.hlines(y, d.ci_low, d.ci_high, color=color, lw=2, zorder=2)
        ax.scatter(
            d.odds_ratio,
            y,
            s=64,
            color=color,
            edgecolor=SURFACE,
            linewidth=2,
            zorder=3,
            label=None if s is None else s.replace("_", " "),
        )
    lo = min(0.5, df.ci_low.min() * 0.9)
    hi = max(2.0, df.ci_high.max() * 1.1)
    set_ticks(ax, lo, hi)
    ax.set_yticks(range(len(order)), [LABELS.get(g, g) for g in order])
    ax.set_ylim(-0.7, len(order) - 0.3)

    # right-hand text column: each estimate beside its own dot, in ink (not series color)
    for s, off, color in zip(series, offsets, SERIES, strict=False):
        d = df if s is None else df[df[series_col] == s]
        for _, r in d.iterrows():
            if series_col:  # colored mark carries series identity; the text stays ink
                ax.scatter([1.025], [y_of[r.group] + off], s=14, color=color, clip_on=False,
                           transform=ax.get_yaxis_transform())  # fmt: skip
            ax.annotate(
                f"{r.odds_ratio:.2f}  ({r.ci_low:.2f}–{r.ci_high:.2f})",
                xy=(1.045 if series_col else 1.02, y_of[r.group] + off),
                xycoords=("axes fraction", "data"),
                va="center",
                fontsize=8.5 if series_col else 9,
                color=INK_2,
            )
    ax.annotate("OR (95% CI)", xy=(1.02, 1.0), xycoords="axes fraction", va="bottom",
                fontsize=9, color=INK, weight="bold")  # fmt: skip
    if series_col:
        # legend in the header band, clear of the data
        ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=len(series), frameon=False,
                  fontsize=10, labelcolor=INK, handletextpad=0.3, columnspacing=1.5)  # fmt: skip
    style(ax, title, subtitle)
    fig.savefig(path, dpi=160)
    plt.close(fig)
    print(f"wrote {path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius", type=int, default=25)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args()
    r = args.radius

    ors = pd.read_csv(args.out_dir / "odds_ratios.csv")
    ors = ors[ors.radius_km == r]
    note = f"Case-crossover, conditional logit. Point events within {r} km; zone events by polygon."

    forest(
        ors[ors.subset == "all"],
        None,
        "Severe workplace injuries on storm days vs. comparable non-storm days",
        f"All OSHA severe injury reports, 2015–2025. {note}",
        args.out_dir / f"forest_all_{r}km.png",
    )

    split = ors[ors.subset.isin(["outdoor_industries", "indoor_industries"])]
    forest(
        split,
        "subset",
        "Outdoor vs. indoor industries",
        f"Outdoor = NAICS 11, 21, 22, 23, 48–49. {note}",
        args.out_dir / f"forest_outdoor_indoor_{r}km.png",
    )

    val = ors[ors.subset.isin(["heat_illness", "cold_injury", "slip_on_ice_snow"])].copy()
    val["group"] = val.subset  # one row per validation subset
    forest(
        val,
        None,
        "Validation: weather-coded injuries vs. matching NOAA events",
        "Injuries OSHA itself attributes to heat, cold, or ice/snow should show strong "
        "associations.",
        args.out_dir / f"forest_validation_{r}km.png",
    )

    placebo_path = args.out_dir / "odds_ratios_placebo.csv"
    if placebo_path.exists():
        pl = pd.read_csv(placebo_path)
        pl = pl[(pl.radius_km == r) & (pl.subset == "all")].assign(run="placebo (dates +364 d)")
        real = ors[ors.subset == "all"].assign(run="actual dates")
        forest(
            pd.concat([real, pl]),
            "run",
            "Placebo check: storm dates shifted one year",
            "Placebo odds ratios should sit near 1; real effects should not.",
            args.out_dir / f"forest_placebo_{r}km.png",
        )


if __name__ == "__main__":
    main()
