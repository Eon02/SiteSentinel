"""Heat and cold/ice slowdown estimates as LossEvents, lost worker-hours and labor cost.

Inputs are plain objects or dicts with the backend field names (the `CurrentConditions` and
`DailyForecast` shapes in backend/app/services/open_meteo.py), so nothing here imports the backend.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from .heat_metrics import (
    f_to_c,
    foster_pwc_percent,
    wbgt_sun_c,
    wind_chill_f,
)

HERE = Path(__file__).resolve().parent
SCENARIOS = ("low", "central", "high")
CREW_FIELDS = ("num_crews", "crew_size", "hours_worked", "work_start_hour", "labor_rate")


@dataclass
class LossEvent:
    """One slowdown or stoppage window. `assumptions` is an extension of the shared contract."""

    site_id: Any
    start: datetime
    end: datetime
    kind: str  # "slowdown" | "stoppage"
    fraction_lost: float  # 0-1; 1.0 for stoppage
    scenario: str  # "low" | "central" | "high"
    factor: str  # "heat", "cold", "ice"
    rule_or_curve_id: str
    source: str
    activity: str = "outdoor_construction"
    assumptions: dict = field(default_factory=dict)


@dataclass
class Crew:
    num_crews: int
    crew_size: int
    hours_worked: float
    work_start_hour: int
    labor_rate: float
    labor_rate_basis: str = "loaded"
    defaulted: tuple[str, ...] = ()

    @property
    def workers(self) -> int:
        return self.num_crews * self.crew_size


def load_config(
    curves_path: Path | None = None, defaults_path: Path | None = None
) -> dict[str, dict]:
    curves = yaml.safe_load((curves_path or HERE / "curves.yaml").read_text())
    defaults = yaml.safe_load((defaults_path or HERE / "defaults.yaml").read_text())
    return {"curves": curves, "defaults": defaults}


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def resolve_crew(crew: Any, defaults: dict) -> Crew:
    """Fill null crew inputs from defaults.yaml and record which ones fell back."""
    d = defaults["crew"]
    values: dict[str, Any] = {}
    defaulted = []
    for name in CREW_FIELDS:
        v = _get(crew, name)
        if v is None:
            v = d[name]
            defaulted.append(name)
        values[name] = v
    basis = _get(crew, "labor_rate_basis") or d["labor_rate_basis"]
    return Crew(**values, labor_rate_basis=basis, defaulted=tuple(defaulted))


def piecewise(x: float, points: list[list[float]]) -> float:
    """Linear interpolation over points ascending in x, clamped at both ends."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def foster_loss(
    metric_c: float, curve: dict, sun_scale: float = 0.0, metric_offset_c: float = 0.0
) -> float:
    """Loss fraction from a Foster curve entry: zero at or below onset, continuous above it."""
    m = metric_c + metric_offset_c
    onset = curve["onset_c"]
    if m <= onset:
        return 0.0
    lo, hi = curve["valid_range_c"]
    args = (curve["a"], curve["b"], curve["solar_correction"], sun_scale)

    def raw(x: float) -> float:
        return 1.0 - foster_pwc_percent(min(max(x, lo), hi), *args) / 100.0

    return min(1.0, max(0.0, raw(m) - raw(onset)))


def cold_loss(wind_chill: float, cfg: dict, scenario: str) -> float:
    if wind_chill >= cfg["zero_at_or_above_f"]:
        return 0.0
    return piecewise(wind_chill, cfg[scenario])


def ice_loss(
    weather_code: int | None,
    temp_f: float | None,
    precip_24h_in: float | None,
    cfg: dict,
    scenario: str,
) -> tuple[float, str | None]:
    """Slowdown from freezing precipitation or snow, and the id of the rule that matched."""
    if weather_code is None:
        return 0.0, None
    for rule in cfg["rules"]:
        if weather_code not in rule["codes"]:
            continue
        limit = rule["max_temp_f"]
        if limit is not None and temp_f is not None and temp_f > limit:
            continue
        loss = rule["loss"][scenario]
        acc = cfg["accumulation"]
        if precip_24h_in is not None and precip_24h_in >= acc["precip_24h_in_min"]:
            loss *= acc["multiplier"]
        return min(loss, cfg["cap"]), rule["id"]
    return 0.0, None


def _window(day: Any, crew: Crew, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, int(crew.work_start_hour), tzinfo=tz)
    return start, start + timedelta(hours=min(float(crew.hours_worked), 24.0))


def _emit(
    out: list[LossEvent],
    site_id: Any,
    window: tuple[datetime, datetime],
    scenario: str,
    fraction: float,
    factor: str,
    curve_id: str,
    source: str,
    assumptions: dict,
) -> None:
    if fraction <= 0:
        return
    out.append(
        LossEvent(
            site_id=site_id,
            start=window[0],
            end=window[1],
            kind="slowdown",
            fraction_lost=round(min(fraction, 1.0), 4),
            scenario=scenario,
            factor=factor,
            rule_or_curve_id=curve_id,
            source=source,
            assumptions=assumptions,
        )
    )


def _cold_ice_events(
    out: list[LossEvent],
    site_id: Any,
    window: tuple[datetime, datetime],
    temp_f: float | None,
    wind_mph: float | None,
    weather_code: int | None,
    precip_24h_in: float | None,
    cfg: dict,
    base: dict,
) -> None:
    """One cold-or-ice event per scenario: the larger of the two, so they never double count."""
    if temp_f is None:
        return
    wc = wind_chill_f(temp_f, wind_mph)
    for scenario in SCENARIOS:
        cold = cold_loss(wc, cfg["cold"]["wind_chill"], scenario)
        ice, rule_id = ice_loss(weather_code, temp_f, precip_24h_in, cfg["ice_snow"], scenario)
        if ice > 0 and ice >= cold:
            factor, curve_id, source = "ice", f"ice_snow/{rule_id}", cfg["ice_snow"]["source"]
            metric = {"weather_code": weather_code, "temp_f": temp_f}
        else:
            factor = "cold"
            curve_id = cfg["cold"]["wind_chill"]["id"]
            source = cfg["cold"]["wind_chill"]["source"]
            metric = {"wind_chill_f": round(wc, 1)}
        status = (
            cfg["ice_snow"]["status"] if factor == "ice" else cfg["cold"]["wind_chill"]["status"]
        )
        _emit(
            out,
            site_id,
            window,
            scenario,
            max(cold, ice),
            factor,
            curve_id,
            source,
            {**base, "metric": metric, "status": status},
        )


def estimate(
    site: Any, conditions: Any, daily: list[Any] | None, crew: Any, config: dict | None = None
) -> list[LossEvent]:
    """Heat and cold/ice LossEvents (low/central/high) for today and tomorrow.

    Today: the current observation stands for the whole work window, but only if it was taken
    inside the window (site-local); otherwise today loses nothing. Tomorrow (daily[1]): daily
    values only, so heat uses `feels_like_max_f` as a worst-hour upper bound on a lower-confidence
    curve, and cold/ice uses `weather_code` and `temp_max_f` (no wind, so no wind chill).
    """
    cfg = config or load_config()
    curves, defaults = cfg["curves"], cfg["defaults"]
    c = resolve_crew(crew, defaults)
    site_defaulted = list(c.defaulted)
    tz_name = _get(site, "timezone")
    if tz_name is None:
        tz_name = defaults["site"]["timezone"]
        site_defaulted.append("timezone")
    tz = ZoneInfo(tz_name)
    site_id = _get(site, "id")
    heat_metric = defaults["heat"]["metric"]
    out: list[LossEvent] = []

    def base(period: str, confidence: str) -> dict:
        return {
            "period": period,
            "confidence": confidence,
            "workers": c.workers,
            "hours_worked": c.hours_worked,
            "work_start_hour": c.work_start_hour,
            "labor_rate": c.labor_rate,
            "labor_rate_basis": c.labor_rate_basis,
            "defaulted": site_defaulted,
        }

    observed = _get(conditions, "observed_at")
    if observed is not None:
        local = observed.astimezone(tz) if observed.tzinfo else observed.replace(tzinfo=tz)
        window = _window(local.date(), c, tz)
        if window[0] <= local < window[1]:
            _today(out, site_id, conditions, window, curves, heat_metric, base)
    if daily and len(daily) > 1:
        tomorrow = daily[1]
        window = _window(_get(tomorrow, "date"), c, tz)
        _tomorrow(out, site_id, tomorrow, window, curves, base)
    return out


def _today(out, site_id, cond, window, curves, heat_metric, base) -> None:
    temp_f = _get(cond, "temp_f")
    hum = _get(cond, "humidity_pct")
    heat = curves["heat"]
    if heat_metric == "wbgt":
        sc = heat["scenarios"]["today_wbgt"]
        curve = heat["curves"][sc["curve"]]
        if temp_f is not None and hum is not None:
            for s in SCENARIOS:
                adj = sc[s]["sun_adjustment_c"]
                wbgt = wbgt_sun_c(temp_f, hum, adj)
                _emit(
                    out, site_id, window, s, foster_loss(wbgt, curve), "heat",
                    f"heat/{sc['curve']}/sun_adj_{adj:g}c", curve["source"],
                    {**base("today", sc["confidence"]), "metric": {"wbgt_c": round(wbgt, 1)},
                     "status": curve["status"]},
                )  # fmt: skip
    else:
        sc = heat["scenarios"]["today_heat_index"]
        curve = heat["curves"][sc["curve"]]
        hi_f = _get(cond, "heat_index_f")
        if hi_f is not None:
            hi_c = f_to_c(hi_f)
            for s in SCENARIOS:
                p = sc[s]
                frac = foster_loss(hi_c, curve, p["sun_scale"], p["metric_offset_c"])
                _emit(
                    out, site_id, window, s, frac, "heat",
                    f"heat/{sc['curve']}/sun_{p['sun_scale']:g}", curve["source"],
                    {**base("today", sc["confidence"]), "metric": {"heat_index_f": hi_f},
                     "status": curve["status"]},
                )  # fmt: skip
    _cold_ice_events(
        out, site_id, window, temp_f, _get(cond, "wind_mph"), _get(cond, "weather_code"),
        _get(cond, "precip_24h_in"), curves, base("today", "medium"),
    )  # fmt: skip


def _tomorrow(out, site_id, day, window, curves, base) -> None:
    heat = curves["heat"]
    sc = heat["scenarios"]["tomorrow_feels_like"]
    curve = heat["curves"][sc["curve"]]
    fl_f = _get(day, "feels_like_max_f")
    if fl_f is not None:
        fl_c = f_to_c(fl_f)
        for s in SCENARIOS:
            p = sc[s]
            frac = foster_loss(fl_c, curve, p["sun_scale"], p["metric_offset_c"])
            _emit(
                out, site_id, window, s, frac, "heat",
                f"heat/{sc['curve']}/apparent_temperature_approx", curve["source"],
                {**base("tomorrow", sc["confidence"]), "metric": {"feels_like_max_f": fl_f},
                 "status": "illustrative approximation: apparent temperature used as a heat index"},
            )  # fmt: skip
    _cold_ice_events(
        out, site_id, window, _get(day, "temp_max_f"), None, _get(day, "weather_code"), None,
        curves, base("tomorrow", "low"),
    )  # fmt: skip


def _crew_rate(crew: Crew, defaults: dict) -> float:
    if crew.labor_rate_basis == "base":
        return crew.labor_rate * defaults["crew"]["base_wage_load_factor"]
    return crew.labor_rate


def summarize(events: list[LossEvent], crew: Any, config: dict | None = None) -> dict[str, dict]:
    """Lost worker-hours and labor-cost equivalent per scenario.

    Events for the same site and day are combined as 1 - prod(1 - f), and hours lost per day never
    exceed `hours_worked`. This is a labor-cost equivalent, not lost profit.
    """
    cfg = config or load_config()
    c = resolve_crew(crew, cfg["defaults"])
    rate = _crew_rate(c, cfg["defaults"])
    result = {s: {"lost_worker_hours": 0.0, "labor_cost_usd": 0.0} for s in SCENARIOS}
    groups: dict[tuple, list[LossEvent]] = {}
    for e in events:
        groups.setdefault((e.site_id, e.scenario, e.start.date()), []).append(e)
    for (_, scenario, _), evs in groups.items():
        keep = 1.0
        for e in evs:
            keep *= 1.0 - e.fraction_lost
        hours = min(
            max((e.end - e.start).total_seconds() / 3600.0 for e in evs), float(c.hours_worked)
        )
        lost = c.workers * hours * (1.0 - keep)
        result[scenario]["lost_worker_hours"] += lost
        result[scenario]["labor_cost_usd"] += lost * rate
    for r in result.values():
        r["lost_worker_hours"] = round(r["lost_worker_hours"], 2)
        r["labor_cost_usd"] = round(r["labor_cost_usd"], 2)
    return result


def hazard_factors(
    events: list[LossEvent], crew: Any, config: dict | None = None, period: str = "today"
) -> list[dict]:
    """`top_factors` entries from the central events of one period ("today" or "tomorrow").

    weight is the crew-hours-weighted central fraction_lost (0-1) per factor, named
    "<factor>_productivity". Combining it into the 0-10 hazard score is up to the score's owner.
    """
    cfg = config or load_config()
    c = resolve_crew(crew, cfg["defaults"])
    num: dict[str, float] = {}
    den: dict[str, float] = {}
    for e in events:
        if e.scenario != "central" or e.assumptions.get("period") != period:
            continue
        crew_hours = c.workers * min((e.end - e.start).total_seconds() / 3600.0, c.hours_worked)
        num[e.factor] = num.get(e.factor, 0.0) + crew_hours * e.fraction_lost
        den[e.factor] = den.get(e.factor, 0.0) + crew_hours
    factors = [
        {"factor": f"{f}_productivity", "weight": round(num[f] / den[f], 4)}
        for f in num
        if den[f] > 0
    ]
    return sorted(factors, key=lambda d: -d["weight"])
