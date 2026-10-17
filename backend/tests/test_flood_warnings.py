import asyncio
import socket
from dataclasses import replace
from email import message_from_bytes

import httpx
import pytest
from aiosmtpd.controller import Controller
from fastapi.testclient import TestClient

from app.config import Settings
from app.http import get_http_client
from app.main import app
from app.routers import alerts as alerts_router
from app.services import flood_warnings
from app.services.briefings import SiteInfo
from app.services.email import OutgoingEmail, send_emails
from app.services.flood_warnings import assess, warning_email
from app.services.nws import ActiveAlert
from app.services.open_meteo import _parse
from tests.test_weather import SAMPLE as OPEN_METEO_SAMPLE

RIVER_SITE = SiteInfo(
    id="55",
    name="Allen",
    utility="TVA",
    plant_type="Natural Gas",
    county="Shelby",
    state="TN",
    timezone="America/Chicago",
    near_river=True,
    latitude=35.07,
    longitude=-90.15,
    flood={"fema_mapped": True, "site_in_sfha": False, "nearest_sfha_mi": 2.0},
)
DRY_UPLAND_SITE = replace(
    RIVER_SITE, id="56", near_river=False, flood={"fema_mapped": True, "site_in_sfha": False}
)


def weather(rain_24h: float):
    data = {**OPEN_METEO_SAMPLE, "hourly": {**OPEN_METEO_SAMPLE["hourly"]}}
    data["hourly"]["precipitation"] = [0.0] + [rain_24h / 24] * 24
    return _parse(data)


def nws_alert(event: str) -> ActiveAlert:
    return ActiveAlert(
        event=event,
        severity="Severe",
        urgency="Immediate",
        headline=f"{event} until 6 PM",
        instruction="Turn around, don't drown.",
        onset=None,
        ends=None,
    )


# --- rules ---------------------------------------------------------------------------


def test_heavy_rain_at_river_site_is_at_risk() -> None:
    result = assess(RIVER_SITE, weather(2.4), [], 1.0, "2026-08-15")

    assert result.at_risk
    assert "2.40 in of rain in the past 24 hours and the site is near a river" in result.reasons[0]
    assert result.precip_tomorrow_in == 0.4


def test_heavy_rain_without_exposure_is_not_at_risk() -> None:
    assert not assess(DRY_UPLAND_SITE, weather(2.4), [], 1.0).at_risk


def test_light_rain_at_river_site_is_not_at_risk() -> None:
    assert not assess(RIVER_SITE, weather(0.5), [], 1.0).at_risk


@pytest.mark.parametrize(
    "flood",
    [
        {"site_in_sfha": True, "zone_at_site": "AE"},
        {"site_in_sfha": False, "nearest_sfha_mi": 0.21},
    ],
)
def test_floodplain_counts_as_exposure(flood: dict) -> None:
    site = replace(DRY_UPLAND_SITE, flood=flood)
    assert assess(site, weather(1.5), [], 1.0).at_risk


def test_nws_flood_alert_triggers_even_without_rain() -> None:
    alerts = [nws_alert("Flash Flood Warning"), nws_alert("Heat Advisory")]
    result = assess(DRY_UPLAND_SITE, weather(0.0), alerts, 1.0)

    assert result.at_risk
    assert result.reasons == ["NWS Flash Flood Warning in effect"]
    assert [a.event for a in result.nws_flood_alerts] == ["Flash Flood Warning"]


def test_missing_weather_is_not_at_risk_but_flags_unknowns() -> None:
    result = assess(RIVER_SITE, None, None, 1.0)
    assert not result.at_risk
    assert result.precip_24h_in is None


def test_warning_email_contents() -> None:
    result = assess(RIVER_SITE, weather(2.4), [nws_alert("Flood Warning")], 1.0, "2026-08-15")
    email = warning_email(result, "crew@example.com")

    assert email.to == "crew@example.com"
    assert email.subject == "FLOOD RISK WARNING: Allen (TN)"
    assert "NWS Flood Warning in effect" in email.body
    assert "Rain, past 24 hours: 2.40 in" in email.body
    assert "Turn around, don't drown." in email.body
    assert "never drive through standing or moving water" in email.body


# --- SMTP ----------------------------------------------------------------------------


class Inbox:
    def __init__(self) -> None:
        self.messages: list = []

    async def handle_DATA(self, server, session, envelope):  # noqa: N802 (aiosmtpd API)
        self.messages.append((envelope.rcpt_tos, message_from_bytes(envelope.content)))
        return "250 OK"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def smtp_server():
    inbox = Inbox()
    controller = Controller(inbox, hostname="127.0.0.1", port=free_port())
    controller.start()
    yield inbox, controller.port
    controller.stop()


def smtp_settings(port: int, **overrides) -> Settings:
    return Settings(
        database_url="postgresql://t:t@localhost/t",
        smtp_host="127.0.0.1",
        smtp_port=port,
        smtp_starttls=False,
        smtp_from="SiteSentinel <alerts@sitesentinel.test>",
        **overrides,
    )


def test_send_emails_one_message_per_recipient(smtp_server) -> None:
    inbox, port = smtp_server
    emails = [
        OutgoingEmail("a@example.com", "Subj", "Body"),
        OutgoingEmail("b@example.com", "Subj", "Body"),
    ]

    results = asyncio.run(send_emails(smtp_settings(port), emails))

    assert [r.status for r in results] == ["sent", "sent"]
    assert [rcpts for rcpts, _ in inbox.messages] == [["a@example.com"], ["b@example.com"]]
    assert inbox.messages[0][1]["From"] == "SiteSentinel <alerts@sitesentinel.test>"


def test_send_emails_skipped_when_not_configured() -> None:
    settings = Settings(database_url="postgresql://t:t@localhost/t")
    results = asyncio.run(send_emails(settings, [OutgoingEmail("a@example.com", "S", "B")]))
    assert results[0].status == "skipped"


def test_send_emails_reports_unreachable_server() -> None:
    results = asyncio.run(
        send_emails(smtp_settings(free_port()), [OutgoingEmail("a@example.com", "S", "B")])
    )
    assert results[0].status == "failed"
    assert "SMTP connection failed" in results[0].error


# --- endpoint ------------------------------------------------------------------------


@pytest.fixture
def endpoint(smtp_server, monkeypatch: pytest.MonkeyPatch):
    """Flood checks with mocked sites, weather, NWS and DB, and a real local SMTP server."""
    inbox, port = smtp_server
    settings = smtp_settings(
        port, open_meteo_url="https://meteo.test/v1/forecast", nws_url="https://nws.test"
    )
    active_alerts: dict[str, int] = {}  # site_id -> alert id, standing in for the alerts table
    workers = {"55": ["worker1@example.com", "lead@EXAMPLE.com"]}

    async def fake_load_sites(site_ids=None):
        sites = [RIVER_SITE, DRY_UPLAND_SITE]
        return [s for s in sites if not site_ids or s.id in site_ids]

    async def fake_record_alerts(assessments):
        for a in assessments:
            if a.at_risk:
                a.newly_raised = a.site.id not in active_alerts
                a.alert_id = active_alerts.setdefault(a.site.id, 100 + len(active_alerts))
            elif a.complete:
                active_alerts.pop(a.site.id, None)

    async def fake_load_site_workers(site_ids):
        return {k: v for k, v in workers.items() if k in site_ids}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "meteo.test":
            rainy = {**OPEN_METEO_SAMPLE, "hourly": {**OPEN_METEO_SAMPLE["hourly"]}}
            rainy["hourly"]["precipitation"] = [0.0] + [0.1] * 24  # 2.4 in
            return httpx.Response(200, json=rainy)
        return httpx.Response(200, json={"features": []})

    monkeypatch.setattr(alerts_router, "get_settings", lambda: settings)
    monkeypatch.setattr(flood_warnings, "load_sites", fake_load_sites)
    monkeypatch.setattr(flood_warnings, "record_alerts", fake_record_alerts)
    monkeypatch.setattr(flood_warnings, "load_site_workers", fake_load_site_workers)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.dependency_overrides[get_http_client] = lambda: http
    yield TestClient(app), inbox, settings, http
    app.dependency_overrides.clear()


def sent_to(inbox) -> list[str]:
    return [rcpts[0] for rcpts, _ in inbox.messages]


def test_endpoint_emails_site_workers_and_extra_addresses(endpoint) -> None:
    client, inbox, _, _ = endpoint
    body = {"emails": ["Lead@example.com", "crew@example.com", "lead@example.com"]}

    response = client.post("/alerts/flood", json=body)

    assert response.status_code == 200
    results = {r["site_id"]: r for r in response.json()}
    assert results["55"]["at_risk"] is True
    assert results["55"]["alert_id"] == 100
    assert results["55"]["newly_raised"] is True
    # extra emails first, then the site's workers; case-insensitive duplicates sent once
    expected = ["Lead@example.com", "crew@example.com", "worker1@example.com"]
    assert [e["to"] for e in results["55"]["emails"]] == expected
    assert all(e["status"] == "sent" for e in results["55"]["emails"])
    assert results["56"]["at_risk"] is False  # not near a river or floodplain
    assert results["56"]["emails"] == []
    assert sent_to(inbox) == expected
    assert inbox.messages[0][1]["Subject"] == "FLOOD RISK WARNING: Allen (TN)"


def test_endpoint_without_emails_warns_site_workers(endpoint) -> None:
    client, inbox, _, _ = endpoint
    response = client.post("/alerts/flood", json={})

    assert response.status_code == 200
    assert sent_to(inbox) == ["worker1@example.com", "lead@EXAMPLE.com"]


def test_endpoint_can_skip_site_workers(endpoint) -> None:
    client, inbox, _, _ = endpoint
    body = {"emails": ["crew@example.com"], "notify_site_workers": False}
    client.post("/alerts/flood", json=body)
    assert sent_to(inbox) == ["crew@example.com"]


def test_endpoint_emails_on_every_manual_call(endpoint) -> None:
    client, inbox, _, _ = endpoint
    client.post("/alerts/flood", json={"notify_site_workers": True})
    second = client.post("/alerts/flood", json={}).json()

    assert {r["site_id"]: r["newly_raised"] for r in second}["55"] is False
    assert len(inbox.messages) == 4  # 2 workers x 2 calls: manual checks always send


def test_automated_check_emails_only_when_warning_first_raised(endpoint) -> None:
    _, inbox, settings, http = endpoint

    async def run():
        return await flood_warnings.run_flood_check(http, settings, only_newly_raised=True)

    first = asyncio.run(run())
    second = asyncio.run(run())

    assert [len(r.deliveries) for r in first] == [2, 0]
    assert [len(r.deliveries) for r in second] == [0, 0]  # still active: no repeat emails
    assert sent_to(inbox) == ["worker1@example.com", "lead@EXAMPLE.com"]


def test_endpoint_threshold_is_adjustable(endpoint) -> None:
    client, inbox, _, _ = endpoint
    response = client.post(
        "/alerts/flood", json={"emails": ["a@example.com"], "rain_threshold_in": 3.0}
    )
    assert not any(r["at_risk"] for r in response.json())
    assert inbox.messages == []


@pytest.mark.parametrize(
    "body",
    [
        {"emails": [], "notify_site_workers": False},  # nobody to email
        {"emails": ["not-an-email"]},
        {"emails": ["a@example.com"], "rain_threshold_in": -1},
    ],
)
def test_endpoint_rejects_bad_input(endpoint, body: dict) -> None:
    client, _, _, _ = endpoint
    assert client.post("/alerts/flood", json=body).status_code == 422


def test_endpoint_unknown_sites_404(endpoint) -> None:
    client, _, _, _ = endpoint
    response = client.post("/alerts/flood", json={"emails": ["a@example.com"], "site_ids": ["x"]})
    assert response.status_code == 404


def test_recipients_merges_without_duplicates() -> None:
    merged = flood_warnings.recipients(["A@x.com", "b@x.com"], ["a@X.com", " c@x.com "])
    assert merged == ["A@x.com", "b@x.com", "c@x.com"]


def test_tests_never_read_real_email_settings() -> None:
    """Guard: the developer's .env (real SMTP credentials) must be ignored under pytest."""
    settings = Settings()
    assert settings.smtp_host is None
    assert settings.smtp_password is None
    assert settings.briefing_webhook_url is None
