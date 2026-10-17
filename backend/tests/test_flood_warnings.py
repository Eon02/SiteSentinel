import asyncio
from dataclasses import dataclass, replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.http import get_http_client
from app.main import app
from app.routers import alerts as alerts_router
from app.services import flood_warnings
from app.services.briefings import SiteInfo
from app.services.flood_warnings import SMS_MAX_CHARS, assess, warning_sms
from app.services.nws import ActiveAlert
from app.services.open_meteo import _parse
from app.services.sms import OutgoingSms, send_sms
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


# --- SMS text ------------------------------------------------------------------------


def test_sms_warning_is_short_and_plain() -> None:
    alerts = [nws_alert("Flash Flood Warning"), nws_alert("Flood Watch")]
    result = assess(RIVER_SITE, weather(2.4), alerts, 1.0)
    text = warning_sms(result, "+12055550123")

    assert len(text.body) <= SMS_MAX_CHARS
    assert text.body.isascii()  # plain GSM characters keep 160-character segments
    assert "NWS Flash Flood Warning in effect" in text.body
    assert "2.4 in rain in 24h, site near river" in text.body
    assert text.body.endswith("Map: https://maps.google.com/?q=35.07,-90.15")


def test_sms_warning_trims_to_two_segments() -> None:
    alerts = [nws_alert(f"Flood Warning number {i} with a long name") for i in range(10)]
    long_name = replace(RIVER_SITE, name="A Very Long Power Plant Name " * 3)
    text = warning_sms(assess(long_name, weather(2.4), alerts, 1.0), "+12055550123")
    assert len(text.body) <= SMS_MAX_CHARS
    assert "maps.google.com" in text.body


# --- Twilio sender (mocked; never reaches Twilio) ------------------------------------


def twilio_settings(**overrides) -> Settings:
    return Settings(
        database_url="postgresql://t:t@localhost/t",
        twilio_account_sid="AC123",
        twilio_auth_token="token",
        twilio_from_number="+12055550100",
        twilio_api_url="https://twilio.test",
        **overrides,
    )


def run_sms(settings: Settings, responses: list[httpx.Response]) -> tuple[list, list]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses[min(len(calls), len(responses)) - 1]

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await send_sms(client, settings, [OutgoingSms("+12055550123", "Hello")])

    return asyncio.run(go()), calls


def test_send_sms_posts_to_twilio() -> None:
    results, calls = run_sms(twilio_settings(), [httpx.Response(201, json={"sid": "SM1"})])

    assert results[0].status == "sent"
    request = calls[0]
    assert str(request.url) == "https://twilio.test/2010-04-01/Accounts/AC123/Messages.json"
    assert request.headers["Authorization"].startswith("Basic ")
    form = dict(httpx.QueryParams(request.content.decode()))
    assert form == {"To": "+12055550123", "Body": "Hello", "From": "+12055550100"}


def test_send_sms_uses_messaging_service_when_set() -> None:
    settings = twilio_settings(twilio_messaging_service_sid="MG999")
    _, calls = run_sms(settings, [httpx.Response(201, json={})])
    form = dict(httpx.QueryParams(calls[0].content.decode()))
    assert form["MessagingServiceSid"] == "MG999"
    assert "From" not in form


def test_send_sms_uses_trial_template_as_body() -> None:
    settings = twilio_settings(twilio_body_template="sms_internal_alerts")
    results, calls = run_sms(settings, [httpx.Response(201, json={})])

    form = dict(httpx.QueryParams(calls[0].content.decode()))
    assert form["Body"] == "sms_internal_alerts"
    assert results[0].status == "sent"


def test_send_sms_reports_twilio_rejection_without_retrying() -> None:
    rejection = httpx.Response(
        400, json={"code": 21608, "message": "The number is unverified for trial accounts"}
    )
    results, calls = run_sms(twilio_settings(), [rejection])

    assert results[0].status == "failed"
    assert results[0].error == "Twilio error 21608: The number is unverified for trial accounts"
    assert len(calls) == 1


def test_send_sms_retries_server_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        pass

    monkeypatch.setattr("app.services.sms.asyncio.sleep", no_sleep)
    results, calls = run_sms(twilio_settings(), [httpx.Response(503), httpx.Response(201)])
    assert results[0].status == "sent"
    assert len(calls) == 2


def test_send_sms_skipped_when_not_configured() -> None:
    settings = Settings(database_url="postgresql://t:t@localhost/t")
    results, calls = run_sms(settings, [httpx.Response(201)])
    assert results[0].status == "skipped"
    assert calls == []


# --- endpoint and automated check (mocked sites, weather, NWS, Twilio and DB) --------


@dataclass
class Env:
    client: TestClient
    texts: list[dict]  # form bodies POSTed to the mock Twilio API
    settings: Settings
    http: httpx.AsyncClient


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch):
    settings = twilio_settings(
        open_meteo_url="https://meteo.test/v1/forecast", nws_url="https://nws.test"
    )
    active_alerts: dict[str, int] = {}  # site_id -> alert id, standing in for the alerts table
    workers = {"55": ["+12055550123", "+12055550199"]}
    texts: list[dict] = []

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
        if request.url.host == "twilio.test":
            texts.append(dict(httpx.QueryParams(request.content.decode())))
            return httpx.Response(201, json={"sid": "SM1", "status": "queued"})
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
    yield Env(TestClient(app), texts, settings, http)
    app.dependency_overrides.clear()


def test_sms_endpoint_texts_crew_of_at_risk_sites(endpoint) -> None:
    response = endpoint.client.post(
        "/alerts/flood/sms", json={"phones": ["205-555-0123", "(205) 555-0177"]}
    )

    assert response.status_code == 200
    results = {r["site_id"]: r for r in response.json()}
    site = results["55"]
    assert site["at_risk"] is True
    assert site["alert_id"] == 100
    # extra numbers first, normalized to E.164; the worker's copy of +12055550123 sent once
    expected = ["+12055550123", "+12055550177", "+12055550199"]
    assert [d["to"] for d in site["sms"]] == expected
    assert all(d["status"] == "sent" for d in site["sms"])
    assert results["56"]["at_risk"] is False  # not near a river or floodplain
    assert results["56"]["sms"] == []
    assert [t["To"] for t in endpoint.texts] == expected
    assert endpoint.texts[0]["From"] == "+12055550100"
    assert endpoint.texts[0]["Body"].startswith("FLOOD RISK: Allen (TN).")


def test_sms_endpoint_texts_site_workers_by_default(endpoint) -> None:
    endpoint.client.post("/alerts/flood/sms", json={})
    assert [t["To"] for t in endpoint.texts] == ["+12055550123", "+12055550199"]


def test_sms_endpoint_can_skip_site_workers(endpoint) -> None:
    body = {"phones": ["205-555-0177"], "notify_site_workers": False}
    endpoint.client.post("/alerts/flood/sms", json=body)
    assert [t["To"] for t in endpoint.texts] == ["+12055550177"]


def test_sms_endpoint_texts_on_every_call(endpoint) -> None:
    endpoint.client.post("/alerts/flood/sms", json={})
    second = endpoint.client.post("/alerts/flood/sms", json={}).json()

    assert {r["site_id"]: r["newly_raised"] for r in second}["55"] is False
    assert len(endpoint.texts) == 4  # 2 workers x 2 calls: each request sends


def test_sms_endpoint_threshold_is_adjustable(endpoint) -> None:
    response = endpoint.client.post("/alerts/flood/sms", json={"rain_threshold_in": 3.0})
    assert not any(r["at_risk"] for r in response.json())
    assert endpoint.texts == []


@pytest.mark.parametrize(
    "body",
    [
        {"phones": ["555-0123"]},
        {"phones": [], "notify_site_workers": False},
        {"rain_threshold_in": -1},
    ],
)
def test_sms_endpoint_rejects_bad_input(endpoint, body: dict) -> None:
    assert endpoint.client.post("/alerts/flood/sms", json=body).status_code == 422


def test_sms_endpoint_unknown_sites_404(endpoint) -> None:
    response = endpoint.client.post("/alerts/flood/sms", json={"site_ids": ["x"]})
    assert response.status_code == 404


def test_email_endpoint_is_gone(endpoint) -> None:
    assert endpoint.client.post("/alerts/flood", json={}).status_code in (404, 405)


def test_automated_check_records_alerts_but_sends_nothing(endpoint) -> None:
    async def run():
        return await flood_warnings.run_flood_check(endpoint.http, endpoint.settings)

    results = asyncio.run(run())

    assert [r.assessment.at_risk for r in results] == [True, False]
    assert results[0].assessment.alert_id == 100
    assert all(r.deliveries == [] for r in results)
    assert endpoint.texts == []


def test_recipients_merges_without_duplicates() -> None:
    merged = flood_warnings.recipients(["+12055550123", "+12055550124"], [" +12055550123 "])
    assert merged == ["+12055550123", "+12055550124"]


def test_tests_never_read_real_settings() -> None:
    """Guard: the developer's .env (real Twilio credentials) must be ignored under pytest."""
    settings = Settings()
    assert settings.twilio_account_sid is None
    assert settings.twilio_auth_token is None
    assert settings.briefing_webhook_url is None
