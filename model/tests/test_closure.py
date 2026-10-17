import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from closure.evaluate import (  # noqa: E402
    SOURCE_TYPES,
    Strike,
    Workday,
    evaluate,
    load_config,
    to_alerts,
    to_loss_events,
)

CONFIG = load_config()
TZ = "America/Chicago"
NOW = datetime(2026, 6, 10, 14, 0, tzinfo=ZoneInfo(TZ))


# Fixtures mirror CurrentConditions / DailyForecast / ActiveAlert / SiteInfo field names.
@dataclass
class Site:
    id: str = "S1"
    timezone: str = TZ
    near_river: bool = False


@dataclass
class Cond:
    temp_f: float | None = 75.0
    wind_mph: float | None = 5.0
    wind_gust_mph: float | None = 8.0
    precip_24h_in: float | None = 0.0
    weather_code: int | None = 1
    cape_j_kg: float | None = 0.0


@dataclass
class Daily:
    date: date
    weather_code: int | None = None
    wind_gust_max_mph: float | None = None


@dataclass
class Alert:
    event: str
    ends: datetime | None = None


@dataclass
class Flood:
    at_risk: bool
    reasons: list[str] = field(default_factory=list)


def run(cond=None, alerts=(), **kw):
    return evaluate(Site(), cond or Cond(), alerts, now=NOW, config=CONFIG, **kw)


def ids(decision):
    return {r.rule_id for r in decision.reasons}


def test_every_rule_has_source_type_and_citation():
    assert CONFIG.rules
    for rule in CONFIG.rules:
        assert rule.source_type in SOURCE_TYPES
        assert rule.citation.strip()


def test_only_site_scope_rules_can_stop_work():
    for rule in CONFIG.rules:
        if rule.severity == "stop_work":
            assert rule.scope == "site"


def test_calm_conditions_give_no_decision():
    d = run()
    assert d.severity == "none" and not d.reasons
    assert to_alerts(d) == []


@pytest.mark.parametrize(
    ("gust", "fires"), [(19.9, False), (20.0, False), (20.1, True)]
)  # "exceeds 20 mph": at the limit does not fire
def test_crane_gust_boundary(gust, fires):
    d = run(Cond(wind_gust_mph=gust))
    assert ("crane_wind_20mph" in ids(d)) is fires
    assert d.severity == ("warning" if fires else "none")


def test_aerial_lift_boundary_is_a_warning_naming_the_work():
    below, above = run(Cond(wind_gust_mph=28.0)), run(Cond(wind_gust_mph=32.0))
    assert "aerial_lift_wind_28mph" not in ids(below)
    assert "aerial_lift_wind_28mph" in ids(above)
    assert above.severity == "warning"
    msg = next(r.message for r in above.reasons if r.rule_id == "aerial_lift_wind_28mph")
    assert "aerial work platform" in msg and "32" in msg


def test_gust_rules_ignore_sustained_wind():
    d = run(Cond(wind_mph=40.0, wind_gust_mph=10.0))
    assert not {"crane_wind_20mph", "aerial_lift_wind_28mph"} & ids(d)


def test_lightning_radius_boundary():
    strike = lambda mi: [Strike(mi, NOW - timedelta(minutes=1))]  # noqa: E731
    assert run(lightning=strike(10.0)).severity == "stop_work"
    assert run(lightning=strike(10.1)).severity == "none"


def test_lightning_resume_after_30_minutes():
    strike = [Strike(4.0, NOW - timedelta(minutes=29))]
    assert run(lightning=strike).severity == "stop_work"
    strike = [Strike(4.0, NOW - timedelta(minutes=30))]
    assert run(lightning=strike).severity == "none"


def test_thunderstorm_code_stops_work():
    assert run(Cond(weather_code=95)).severity == "stop_work"


def test_nws_warnings_and_severity_mapping():
    assert run(alerts=[Alert("Tornado Warning")]).severity == "stop_work"
    assert run(alerts=[Alert("Severe Thunderstorm Warning")]).severity == "stop_work"
    assert run(alerts=[Alert("High Wind Warning")]).severity == "warning"
    assert run(alerts=[Alert("Heat Advisory")]).severity == "none"


def test_highest_severity_wins_and_message_lists_every_reason():
    d = run(
        Cond(wind_gust_mph=35.0),
        alerts=[Alert("Tornado Warning")],
        lightning=[Strike(3.0, NOW)],
    )
    assert d.severity == "stop_work"
    assert {"crane_wind_20mph", "nws_tornado_warning", "lightning_within_range"} <= ids(d)
    alerts = {a.type: a for a in to_alerts(d)}
    assert alerts["storm"].severity == "stop_work"
    assert "crane" in alerts["storm"].message and "Tornado" in alerts["storm"].message
    assert alerts["lightning"].last_strike_at == NOW


def test_flood_never_produces_stop_work():
    flood = Flood(True, ["NWS Flash Flood Warning in effect", "2.50 in of rain in 24 h"])
    d = run(Cond(precip_24h_in=6.0), flood=flood)
    assert d.severity == "warning"
    assert all(r.severity == "warning" for r in d.reasons if r.alert_type == "flood")
    assert [a.severity for a in to_alerts(d) if a.type == "flood"] == ["warning"]
    assert to_loss_events(d, [], timezone=TZ, config=CONFIG) == []


def test_flood_not_at_risk_is_quiet():
    assert run(flood=Flood(False)).severity == "none"


def test_hysteresis_holds_gust_rule_then_releases():
    first = run(Cond(wind_gust_mph=30.0))
    calm = Cond(wind_gust_mph=5.0)
    held = evaluate(
        Site(), calm, [], now=NOW + timedelta(minutes=10),
        previous=first.last_triggered, config=CONFIG,
    )  # fmt: skip
    assert "crane_wind_20mph" in ids(held)
    assert next(r for r in held.reasons if r.rule_id == "crane_wind_20mph").held
    released = evaluate(
        Site(), calm, [], now=NOW + timedelta(minutes=30),
        previous=first.last_triggered, config=CONFIG,
    )  # fmt: skip
    assert released.severity == "none"


def test_storm_day_replay():
    site = Site()
    # 1) gusts rise past the lift limit: warning
    t1 = NOW
    d1 = evaluate(site, Cond(wind_gust_mph=32.0), [], now=t1, config=CONFIG)
    assert d1.severity == "warning"
    a1 = to_alerts(d1)
    assert [(a.type, a.severity) for a in a1] == [("storm", "warning")]
    assert "aerial work platform" in a1[0].message and "28" in a1[0].message
    # 2) lightning strike 6 mi away: whole site stops
    t2 = t1 + timedelta(minutes=20)
    strike = Strike(6.0, t2)
    d2 = evaluate(
        site, Cond(wind_gust_mph=32.0), [], now=t2, lightning=[strike],
        previous=d1.last_triggered, config=CONFIG,
    )  # fmt: skip
    assert d2.severity == "stop_work"
    assert d2.last_strike_at == t2
    assert d2.resume_at == t2 + timedelta(minutes=30)
    a2 = {a.type: a for a in to_alerts(d2, {a.type: a.severity for a in a1})}
    assert a2["lightning"].severity == "stop_work" and a2["lightning"].last_strike_at == t2
    # stoppage runs from now (2:20 PM) to the resume time (2:50 PM): 0.5 h in the workday
    events = to_loss_events(d2, [], timezone=TZ, workday=Workday(8, 7), config=CONFIG)
    assert len(events) == 1
    assert events[0].kind == "stoppage" and events[0].fraction_lost == 1.0
    assert events[0].activity == "site" and events[0].factor == "lightning"
    assert events[0].hours == pytest.approx(0.5)
    # 3) 30 minutes with no further strikes: lightning clears, gusts drop
    t3 = t2 + timedelta(minutes=30)
    calm = Cond(wind_gust_mph=8.0)
    d3 = evaluate(
        site, calm, [], now=t3, lightning=[strike],
        previous=d2.last_triggered, config=CONFIG,
    )  # fmt: skip
    assert d3.severity == "none"
    a3 = to_alerts(d3, {a.type: a.severity for a in to_alerts(d2)})
    assert {a.type for a in a3} == {"lightning", "storm"}
    assert all(a.cleared_at == t3 for a in a3)


def test_null_work_start_hour_falls_back_to_7():
    now = datetime(2026, 6, 10, 5, 30, tzinfo=ZoneInfo(TZ))  # before the workday
    d = evaluate(Site(), Cond(), [], now=now, lightning=[Strike(2.0, now)], config=CONFIG)
    events = to_loss_events(d, [], timezone=TZ, workday=Workday(hours_worked=8), config=CONFIG)
    assert events == []  # 5:30-6:00 is outside a 7:00 start
    assert CONFIG.settings["default_work_start_hour"] == 7

    now = datetime(2026, 6, 10, 6, 45, tzinfo=ZoneInfo(TZ))
    d = evaluate(Site(), Cond(), [], now=now, lightning=[Strike(2.0, now)], config=CONFIG)
    (event,) = to_loss_events(d, [], timezone=TZ, workday=Workday(hours_worked=8), config=CONFIG)
    assert event.start.hour == 7 and event.start.minute == 0  # clipped to 7:00
    assert event.end == now + timedelta(minutes=30)
    assert "work_start_hour" in event.defaults_used and "hours_worked" not in event.defaults_used


def test_stoppage_outside_window_dropped_and_daily_hours_capped():
    now = datetime(2026, 6, 10, 13, 30, tzinfo=ZoneInfo(TZ))
    alert = Alert("Tornado Warning", ends=now + timedelta(hours=20))  # until 9:30 AM next day
    d = evaluate(Site(), Cond(), [alert], now=now, config=CONFIG)
    events = to_loss_events(d, [], timezone=TZ, workday=Workday(8, 7), config=CONFIG)
    by_day: dict[date, float] = {}
    for e in events:
        by_day[e.start.date()] = by_day.get(e.start.date(), 0) + e.hours
    assert by_day[date(2026, 6, 10)] == pytest.approx(1.5)  # 1:30-3:00 PM; night is dropped
    assert by_day[date(2026, 6, 11)] == pytest.approx(2.5)  # 7:00-9:30 AM
    assert all(h <= 8 for h in by_day.values())


def test_tomorrow_forecast_thunderstorm_gives_scenarios():
    tomorrow = NOW.date() + timedelta(days=1)
    d = run()
    events = to_loss_events(
        d, [Daily(tomorrow, weather_code=95)], timezone=TZ, workday=Workday(10, 8), config=CONFIG
    )
    assert [e.scenario for e in events] == ["low", "central", "high"]
    assert [e.hours for e in events] == pytest.approx([2.5, 5.0, 10.0])
    assert all(e.start.hour == 8 and e.fraction_lost == 1.0 for e in events)
    assert to_loss_events(d, [Daily(tomorrow, weather_code=3)], timezone=TZ, config=CONFIG) == []


def test_warning_only_decision_has_no_loss_events():
    d = run(Cond(wind_gust_mph=40.0))
    assert d.severity == "warning"
    assert to_loss_events(d, [], timezone=TZ, config=CONFIG) == []


def test_icing_is_warning_for_freezing_rain_codes():
    for code in (56, 57, 66, 67):
        d = run(Cond(weather_code=code, temp_f=30.0))
        assert "scaffold_icing" in ids(d) and d.severity == "warning"


def test_daily_gust_alone_never_stops_site():
    # no whole-site wind rule exists (no regulation sets one); see SOURCES.md
    assert run(Cond(wind_mph=45.0, wind_gust_mph=60.0)).severity == "warning"


def test_utc_inputs_are_handled():
    now = NOW.astimezone(UTC)
    d = evaluate(Site(), Cond(), [], now=now, lightning=[Strike(1.0, now)], config=CONFIG)
    assert d.severity == "stop_work"
    (event,) = to_loss_events(d, [], timezone=TZ, workday=Workday(8, 7), config=CONFIG)
    assert event.hours == pytest.approx(0.5)
