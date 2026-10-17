"""Site-closure engine: turns current conditions, NWS alerts and lightning into a site decision.

Thresholds live in rules.yaml with their sources (see SOURCES.md); they come from regulations
and standards, not from our injury data. Inputs are duck-typed objects with the field names of
`CurrentConditions`, `DailyForecast` and `ActiveAlert` in backend/app/services, so this package
does not import the backend. Needs PyYAML.

Only whole-site rules may produce `stop_work`; rules for specific equipment give a `warning`
that names the restricted work. Flooding is never escalated.
"""

from __future__ import annotations

import operator
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

RULES_PATH = Path(__file__).with_name("rules.yaml")
SEVERITY_ORDER = {"none": 0, "warning": 1, "stop_work": 2}
OPS = {"gt": operator.gt, "ge": operator.ge, "lt": operator.lt, "le": operator.le}
OP_WORDS = {"gt": "exceeds", "ge": "is at or above", "lt": "is below", "le": "is within"}
REQUIRED_FIELDS = ("id", "hazard", "kind", "scope", "severity", "source_type", "citation")
SOURCE_TYPES = {
    "regulation",
    "consensus standard",
    "manufacturer",
    "agency guidance",
    "company policy",
}
FACTORS = {"lightning": "lightning", "wind": "wind", "flooding": "flood", "rain": "rain"}
SCENARIOS = ("low", "central", "high")


@dataclass(frozen=True)
class Rule:
    id: str
    hazard: str
    kind: str
    scope: str
    severity: str
    source_type: str
    citation: str
    resume_condition: str = ""
    jurisdiction: str = ""
    metric: str = ""
    threshold: float | None = None
    op: str = "gt"
    codes: tuple[int, ...] = ()
    events: tuple[str, ...] = ()
    hold_minutes: float = 0
    verified: str = "no"

    @property
    def site_wide(self) -> bool:
        return self.scope == "site"

    @property
    def alert_type(self) -> str:
        """Frontend `AlertType` for this rule's hazard; wind, hail and icing map to storm."""
        return {"lightning": "lightning", "flooding": "flood"}.get(self.hazard, "storm")


@dataclass(frozen=True)
class Config:
    settings: dict[str, Any]
    rules: tuple[Rule, ...]

    @property
    def lightning_radius_mi(self) -> float:
        return float(self.settings["lightning_radius_mi"])


def load_config(path: Path = RULES_PATH) -> Config:
    """Load and validate rules.yaml."""
    raw = yaml.safe_load(path.read_text())
    rules = []
    for entry in raw["rules"]:
        missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
        if missing:
            raise ValueError(f"rule {entry.get('id')} is missing {missing}")
        if entry["source_type"] not in SOURCE_TYPES:
            raise ValueError(f"rule {entry['id']}: unknown source_type {entry['source_type']}")
        if entry["severity"] not in ("warning", "stop_work"):
            raise ValueError(f"rule {entry['id']}: bad severity {entry['severity']}")
        if entry["severity"] == "stop_work" and entry["scope"] != "site":
            raise ValueError(f"rule {entry['id']}: only whole-site rules may be stop_work")
        if entry["kind"] == "flood" and entry["severity"] != "warning":
            raise ValueError(f"rule {entry['id']}: flooding is never escalated")
        rules.append(
            Rule(
                id=entry["id"],
                hazard=entry["hazard"],
                kind=entry["kind"],
                scope=entry["scope"],
                severity=entry["severity"],
                source_type=entry["source_type"],
                citation=entry["citation"],
                resume_condition=entry.get("resume_condition", ""),
                jurisdiction=entry.get("jurisdiction", ""),
                metric=entry.get("metric", ""),
                threshold=entry.get("threshold"),
                op=entry.get("op", "gt"),
                codes=tuple(entry.get("codes", ())),
                events=tuple(entry.get("events", ())),
                hold_minutes=float(entry.get("hold_minutes", 0)),
                verified=entry.get("verified", "no"),
            )
        )
    return Config(settings=raw["settings"], rules=tuple(rules))


@dataclass(frozen=True)
class Strike:
    distance_mi: float
    at: datetime


@dataclass(frozen=True)
class Workday:
    """Site-local workday. Null fields fall back to the config defaults and are reported."""

    hours_worked: float | None = None
    work_start_hour: int | None = None


@dataclass(frozen=True)
class Reason:
    rule_id: str
    hazard: str
    value: Any
    threshold: Any
    restricted_work: str  # "site" for whole-site rules
    severity: str
    alert_type: str
    message: str
    last_triggered: datetime
    until: datetime | None  # when the rule is expected to clear (None: unknown)
    held: bool = False  # still active only because of the hysteresis hold


@dataclass(frozen=True)
class SiteDecision:
    site_id: str
    severity: str  # none | warning | stop_work
    reasons: tuple[Reason, ...]
    resume_condition: str
    resume_at: datetime | None
    last_strike_at: datetime | None
    last_triggered: dict[str, datetime]  # feed back as `previous` on the next call
    evaluated_at: datetime


@dataclass(frozen=True)
class AlertRecord:
    """Maps onto the `alerts` columns."""

    type: str
    severity: str
    message: str
    last_strike_at: datetime | None = None
    cleared_at: datetime | None = None


@dataclass(frozen=True)
class LossEvent:
    site_id: str
    start: datetime
    end: datetime
    activity: str
    kind: str  # slowdown | stoppage
    fraction_lost: float
    scenario: str  # low | central | high
    factor: str
    rule_or_curve_id: str
    source: str
    defaults_used: tuple[str, ...] = ()

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


def _get(obj: Any, name: str) -> Any:
    return getattr(obj, name, None) if obj is not None else None


def _triggered(
    rule: Rule,
    config: Config,
    now: datetime,
    conditions: Any,
    alerts: Sequence[Any],
    strikes: Sequence[Strike],
    flood: Any,
) -> tuple[datetime, Any, Any, datetime | None] | None:
    """Return (triggered_at, value, threshold, until) when the rule fires, else None."""
    if rule.kind == "lightning":
        radius = config.lightning_radius_mi
        near = [s for s in strikes if s.distance_mi <= radius and s.at <= now]
        if not near:
            return None
        last = max(s.at for s in near)
        nearest = min(s.distance_mi for s in near)
        return last, nearest, radius, None
    if rule.kind == "numeric":
        value = _get(conditions, rule.metric)
        if value is None or rule.threshold is None or not OPS[rule.op](value, rule.threshold):
            return None
        return now, value, rule.threshold, None
    if rule.kind == "weather_code":
        code = _get(conditions, "weather_code")
        return (now, code, list(rule.codes), None) if code in rule.codes else None
    if rule.kind == "nws_event":
        hits = [a for a in alerts if a.event in rule.events]
        if not hits:
            return None
        ends = [a.ends for a in hits if a.ends is not None]
        until = max(ends) if len(ends) == len(hits) else None
        return now, hits[0].event, list(rule.events), until
    if rule.kind == "flood":
        if flood is None or not flood.at_risk:
            return None
        return now, "; ".join(flood.reasons), "flood_warnings.assess", None
    raise ValueError(f"rule {rule.id}: unknown kind {rule.kind}")


def _describe(rule: Rule, value: Any, threshold: Any) -> str:
    if rule.kind == "lightning":
        return f"lightning {value:g} mi away, within the {threshold:g} mi limit"
    if rule.kind == "numeric":
        name = rule.metric.removesuffix("_mph").replace("wind_gust", "gusts").replace("_", " ")
        unit = (
            " mph" if rule.metric.endswith("_mph") else " F" if rule.metric.endswith("_f") else ""
        )
        return f"{name} {value:g}{unit} {OP_WORDS[rule.op]} the {threshold:g}{unit} limit"
    if rule.kind == "weather_code":
        return f"{rule.hazard} conditions reported (weather code {value})"
    if rule.kind == "flood":
        return value
    return f"NWS {value} in effect"


def evaluate(
    site: Any,
    conditions: Any,
    active_alerts: Iterable[Any],
    *,
    now: datetime,
    lightning: Iterable[Strike] = (),
    flood: Any = None,
    previous: Mapping[str, datetime] | None = None,
    config: Config | None = None,
) -> SiteDecision:
    """Apply every rule to one site.

    `flood` is the result of the backend's `flood_warnings.assess()` (anything with `at_risk` and
    `reasons`); it is consumed, not recomputed. `previous` is the prior decision's
    `last_triggered`; with it, a rule stays active for its `hold_minutes` after it last fired
    (hysteresis, so a metric hovering at the threshold does not flap).
    """
    config = config or load_config()
    alerts, strikes = list(active_alerts), list(lightning)
    prior = dict(previous or {})
    last_triggered: dict[str, datetime] = {}
    reasons: list[Reason] = []

    for rule in config.rules:
        hit = _triggered(rule, config, now, conditions, alerts, strikes, flood)
        fired_at = hit[0] if hit else None
        last = max((t for t in (fired_at, prior.get(rule.id)) if t is not None), default=None)
        if last is None:
            continue
        hold = timedelta(minutes=rule.hold_minutes)
        active = last == now or now - last < hold
        if not active:
            continue
        last_triggered[rule.id] = last
        _, value, threshold, until = hit or (last, None, rule.threshold, None)
        held = hit is None
        if rule.hold_minutes:
            until = max(until, last + hold) if until else last + hold
        if held:
            what = "recently cleared, holding until the hold period ends"
        else:
            what = _describe(rule, value, threshold)
        lead = "Stop work" if rule.severity == "stop_work" else f"Suspend {rule.scope}"
        if rule.kind == "flood":
            lead = "Flood risk"
        reasons.append(
            Reason(
                rule_id=rule.id,
                hazard=rule.hazard,
                value=value,
                threshold=threshold,
                restricted_work=rule.scope,
                severity=rule.severity,
                alert_type=rule.alert_type,
                message=f"{lead}: {what}",
                last_triggered=last,
                until=until,
                held=held,
            )
        )

    severity = max((r.severity for r in reasons), key=SEVERITY_ORDER.__getitem__, default="none")
    top = [r for r in reasons if r.severity == severity]
    by_id = {r.id: r for r in config.rules}
    resumes = [by_id[r.rule_id].resume_condition for r in top]
    untils = [r.until for r in top]
    strike_times = [r.last_triggered for r in reasons if r.alert_type == "lightning"]
    return SiteDecision(
        site_id=str(site.id),
        severity=severity,
        reasons=tuple(reasons),
        resume_condition="; ".join(dict.fromkeys(c for c in resumes if c)),
        resume_at=max(untils) if untils and all(u is not None for u in untils) else None,
        last_strike_at=max(strike_times, default=None),
        last_triggered=last_triggered,
        evaluated_at=now,
    )


def to_alerts(
    decision: SiteDecision, previously_active: Mapping[str, str] | None = None
) -> list[AlertRecord]:
    """One record per alert type that fires, plus a clearing record for types that stopped.

    Mirrors `record_alerts()` in flood_warnings.py: the caller inserts or updates the one active
    alert per (site, type). `previously_active` maps alert type to its stored severity.
    """
    out: list[AlertRecord] = []
    types = dict.fromkeys(r.alert_type for r in decision.reasons)
    for alert_type in types:
        group = [r for r in decision.reasons if r.alert_type == alert_type]
        severity = max((r.severity for r in group), key=SEVERITY_ORDER.__getitem__)
        out.append(
            AlertRecord(
                type=alert_type,
                severity=severity,
                message="; ".join(r.message for r in group),
                last_strike_at=decision.last_strike_at if alert_type == "lightning" else None,
            )
        )
    for alert_type, severity in (previously_active or {}).items():
        if alert_type not in types:
            out.append(
                AlertRecord(alert_type, severity, "Cleared", cleared_at=decision.evaluated_at)
            )
    return out


def _resolve_workday(workday: Workday | None, config: Config) -> tuple[int, float, tuple[str, ...]]:
    workday = workday or Workday()
    defaults = []
    start = workday.work_start_hour
    if start is None:
        start = int(config.settings["default_work_start_hour"])
        defaults.append("work_start_hour")
    hours = workday.hours_worked
    if hours is None:
        hours = float(config.settings["default_hours_worked"])
        defaults.append("hours_worked")
    return start, min(float(hours), 24.0), tuple(defaults)


def _window(day: date, start_hour: int, hours: float, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, start_hour, tzinfo=tz)
    return start, start + timedelta(hours=hours)


def _clip(
    start: datetime, end: datetime, start_hour: int, hours: float, tz: ZoneInfo
) -> list[tuple[datetime, datetime]]:
    """Intersect [start, end) with each day's workday window (site-local)."""
    out = []
    day = start.astimezone(tz).date() - timedelta(days=1)  # a window may run past midnight
    last = end.astimezone(tz).date()
    while day <= last:
        w_start, w_end = _window(day, start_hour, hours, tz)
        lo, hi = max(start, w_start), min(end, w_end)
        if lo < hi:
            out.append((lo, hi))
        day += timedelta(days=1)
    return out


def to_loss_events(
    decision: SiteDecision,
    forecast: Sequence[Any],
    *,
    timezone: str,
    workday: Workday | None = None,
    config: Config | None = None,
) -> list[LossEvent]:
    """Stoppage events for a `stop_work` decision, clipped to the workday window.

    Now: from the decision time until the longest-lasting stop reason clears (hold end, NWS
    `ends`; unknown means the end of today's workday). Tomorrow: if the daily `weather_code`
    matches a whole-site weather-code rule, one event per scenario starting at `work_start_hour`,
    lasting the illustrative `forecast_day_fraction` of `hours_worked` (daily data cannot say
    when). Warnings give no event. Hours per day never exceed `hours_worked`.
    """
    config = config or load_config()
    tz = ZoneInfo(timezone)
    start_hour, hours, defaults = _resolve_workday(workday, config)
    events: list[LossEvent] = []
    now = decision.evaluated_at
    rules = {r.id: r for r in config.rules}
    covered: set[date] = set()

    stops = [r for r in decision.reasons if r.severity == "stop_work"]
    if stops:
        known = [r for r in stops if r.until is not None]
        lead = max(known, key=lambda r: r.until) if known else stops[0]
        end = lead.until
        if end is None:
            today = now.astimezone(tz).date()
            end = _window(today, start_hour, hours, tz)[1]
        for lo, hi in _clip(now, end, start_hour, hours, tz):
            covered.add(lo.astimezone(tz).date())
            events.append(
                LossEvent(
                    decision.site_id,
                    lo,
                    hi,
                    "site",
                    "stoppage",
                    1.0,
                    "central",
                    FACTORS.get(lead.hazard, "storm"),
                    lead.rule_id,
                    rules[lead.rule_id].citation,
                    defaults,
                )
            )

    tomorrow = now.astimezone(tz).date() + timedelta(days=1)
    day = next((d for d in forecast if d.date == tomorrow), None)
    code = _get(day, "weather_code")
    rule = next(
        (r for r in config.rules if r.kind == "weather_code" and r.site_wide and code in r.codes),
        None,
    )
    if rule and tomorrow not in covered:
        fractions = config.settings["forecast_day_fraction"]
        w_start, _ = _window(tomorrow, start_hour, hours, tz)
        for scenario in SCENARIOS:
            events.append(
                LossEvent(
                    decision.site_id,
                    w_start,
                    w_start + timedelta(hours=hours * fractions[scenario]),
                    "site",
                    "stoppage",
                    1.0,
                    scenario,
                    FACTORS.get(rule.hazard, "storm"),
                    rule.id,
                    rule.citation,
                    defaults,
                )
            )
    return events


__all__ = [
    "AlertRecord",
    "Config",
    "LossEvent",
    "Reason",
    "Rule",
    "SiteDecision",
    "Strike",
    "Workday",
    "evaluate",
    "load_config",
    "to_alerts",
    "to_loss_events",
]
