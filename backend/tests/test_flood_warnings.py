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
    """POST /alerts/flood with mocked sites, weather, NWS and DB, and a real local SMTP server."""
    inbox, port = smtp_server
    settings = smtp_settings(
        port, open_meteo_url="https://meteo.test/v1/forecast", nws_url="https://nws.test"
    )
    recorded = []

    async def fake_load_sites(site_ids=None):
        sites = [RIVER_SITE, DRY_UPLAND_SITE]
        return [s for s in sites if not site_ids or s.id in site_ids]

    async def fake_record_alerts(assessments):
        for i, a in enumerate(assessments):
            a.alert_id = 100 + i if a.at_risk else None
        recorded.extend(assessments)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "meteo.test":
            rainy = {**OPEN_METEO_SAMPLE, "hourly": {**OPEN_METEO_SAMPLE["hourly"]}}
            rainy["hourly"]["precipitation"] = [0.0] + [0.1] * 24  # 2.4 in
            return httpx.Response(200, json=rainy)
        return httpx.Response(200, json={"features": []})

    monkeypatch.setattr(alerts_router, "get_settings", lambda: settings)
    monkeypatch.setattr(alerts_router, "load_sites", fake_load_sites)
    monkeypatch.setattr(alerts_router, "record_alerts", fake_record_alerts)
    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    yield TestClient(app), inbox, recorded
    app.dependency_overrides.clear()


def test_endpoint_emails_each_address_for_at_risk_sites(endpoint) -> None:
    client, inbox, recorded = endpoint
    body = {"emails": ["Lead@example.com", "crew@example.com", "lead@example.com"]}

    response = client.post("/alerts/flood", json=body)

    assert response.status_code == 200
    results = {r["site_id"]: r for r in response.json()}
    assert results["55"]["at_risk"] is True
    assert results["55"]["alert_id"] == 100
    assert [e["to"] for e in results["55"]["emails"]] == ["Lead@example.com", "crew@example.com"]
    assert all(e["status"] == "sent" for e in results["55"]["emails"])
    assert results["56"]["at_risk"] is False  # not near a river or floodplain
    assert results["56"]["emails"] == []
    # duplicate address (different case) emailed once; only the at-risk site sends
    assert [rcpts for rcpts, _ in inbox.messages] == [["Lead@example.com"], ["crew@example.com"]]
    assert inbox.messages[0][1]["Subject"] == "FLOOD RISK WARNING: Allen (TN)"
    assert len(recorded) == 2


def test_endpoint_threshold_is_adjustable(endpoint) -> None:
    client, inbox, _ = endpoint
    response = client.post(
        "/alerts/flood", json={"emails": ["a@example.com"], "rain_threshold_in": 3.0}
    )
    assert not any(r["at_risk"] for r in response.json())
    assert inbox.messages == []


@pytest.mark.parametrize(
    "body",
    [
        {"emails": []},
        {"emails": ["not-an-email"]},
        {"emails": ["a@example.com"], "rain_threshold_in": -1},
    ],
)
def test_endpoint_rejects_bad_input(endpoint, body: dict) -> None:
    client, _, _ = endpoint
    assert client.post("/alerts/flood", json=body).status_code == 422


def test_endpoint_unknown_sites_404(endpoint) -> None:
    client, _, _ = endpoint
    response = client.post("/alerts/flood", json={"emails": ["a@example.com"], "site_ids": ["x"]})
    assert response.status_code == 404
