import asyncio
import json
from datetime import UTC, date, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db import get_session
from app.http import get_http_client
from app.main import app
from app.models import Site, SiteManager
from app.routers import reports as reports_router
from app.services import reports
from app.services.email import DeliveryResult

SETTINGS = Settings(
    database_url="postgresql://t:t@localhost/t",
    gemini_api_key="test-key",
    gemini_api_url="https://gemini.test/v1beta",
)

SITE_A = {
    "id": "46",
    "name": "Browns Ferry",
    "county": "Limestone",
    "state": "AL",
    "site_in_sfha": True,
    "zone_at_site": "AE",
    "latest_conditions": {
        "observed_at": datetime(2026, 10, 17, 14, 0, tzinfo=UTC),
        "temp_f": 91.2,
        "heat_index_f": 104.6,
        "precip_24h_in": 1.25,
        "wind_mph": 12.0,
        "wind_gust_mph": 25.0,
    },
    "hazard_scores": [
        {"score_date": date(2026, 10, 17), "score": 7.4, "is_forecast": False, "top_factors": []}
    ],
    "active_alerts": [{"type": "heat", "severity": "stop_work", "message": "Heat index 105F"}],
    "crew_reports": [{"hazard_type": "wildlife"}, {"hazard_type": "downed_line"}],
}
SITE_B = {
    "id": "2050",
    "name": "Baxter Wilson",
    "county": None,
    "state": "MS",
    "latest_conditions": None,
    "hazard_scores": [],
    "active_alerts": [],
    "crew_reports": [],
}


def gemini_reply(text: str, thought: str | None = None) -> httpx.Response:
    parts = ([{"text": thought, "thought": True}] if thought else []) + [{"text": text}]
    return httpx.Response(
        200, json={"candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}]}
    )


def run(coro):
    return asyncio.run(coro)


# --- Gemini -------------------------------------------------------------------------------


def test_gemini_request_and_answer() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return gemini_reply("  Two sites need attention.  ", thought="thinking...")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    body = run(reports.summarize_with_gemini(client, SETTINGS, [SITE_A], 7))

    assert body == "Two sites need attention."  # thought parts dropped, trimmed
    request = seen[0]
    assert str(request.url) == "https://gemini.test/v1beta/models/gemini-3.8-flash:generateContent"
    assert request.headers["x-goog-api-key"] == "test-key"
    payload = json.loads(request.content)
    assert "project managers" in payload["systemInstruction"]["parts"][0]["text"]
    data = json.loads(payload["contents"][0]["parts"][0]["text"])
    assert data["sites"][0]["name"] == "Browns Ferry"
    assert data["crew_reports_cover_last_days"] == 7


def test_gemini_retries_server_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(reports.asyncio, "sleep", no_sleep)
    replies = iter([httpx.Response(503), httpx.Response(429), gemini_reply("ok")])
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: next(replies)))
    assert run(reports.summarize_with_gemini(client, SETTINGS, [SITE_A], 7)) == "ok"


def test_gemini_rejection_is_not_retried() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(400, json={"error": {"message": "API key not valid"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(reports.GeminiError, match="API key not valid"):
        run(reports.summarize_with_gemini(client, SETTINGS, [SITE_A], 7))
    assert len(calls) == 1


def test_gemini_needs_a_key() -> None:
    settings = SETTINGS.model_copy(update={"gemini_api_key": None})
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: pytest.fail("called")))
    with pytest.raises(reports.GeminiError, match="GEMINI_API_KEY"):
        run(reports.summarize_with_gemini(client, settings, [SITE_A], 7))


def test_fallback_summary() -> None:
    text = reports.fallback_summary([SITE_A, SITE_B])
    assert "Browns Ferry (Limestone, AL)" in text
    assert "STOP WORK (heat): Heat index 105F" in text
    assert "Hazard score 7.4/10 on 2026-10-17 (today)" in text
    assert "91F, heat index 105F, wind 12 mph (gusts 25), rain past 24h 1.25 in" in text
    assert "2 recent crew report(s): downed_line, wildlife" in text
    assert "FEMA flood hazard area (zone AE)" in text
    assert "Baxter Wilson (MS)\n  - No recorded conditions" in text


# --- Recipients and sending ---------------------------------------------------------------


class FakeSession:
    """Answers the recipient queries: site_managers rows and the list of all sites."""

    def __init__(self, managers: list[tuple[str, str]], all_sites: list[str]) -> None:
        self.managers, self.all_sites = managers, all_sites
        self.added: list = []
        self.fail_commit = False

    async def execute(self, _statement, params):
        ids = params["ids"]
        return iter([m for m in self.managers if ids is None or m[1] in ids])

    async def scalars(self, _statement):
        return iter(self.all_sites)

    async def get(self, _model, key):
        return Site(id=key, name="x", timezone="UTC") if key in self.all_sites else None

    async def scalar(self, _statement):
        return None

    def add(self, obj):
        obj.id = len(self.added) + 1
        self.added.append(obj)

    async def commit(self):
        if self.fail_commit:
            raise IntegrityError("insert", {}, Exception("duplicate"))

    async def rollback(self):
        pass


MANAGERS = [("pat@example.com", "46"), ("pat@example.com", "2050"), ("kim@example.com", "46")]


def test_recipients_grouped_by_their_sites() -> None:
    session = FakeSession(MANAGERS, ["46", "2050"])
    groups = run(reports.report_recipients(session, None, ["boss@example.com"], True))
    assert groups == {
        frozenset({"46", "2050"}): ["pat@example.com", "boss@example.com"],
        frozenset({"46"}): ["kim@example.com"],
    }
    # Limited to requested sites; managers of none of them get nothing.
    groups = run(reports.report_recipients(session, ["2050"], [], True))
    assert groups == {frozenset({"2050"}): ["pat@example.com"]}


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch):
    """Fake site data, Gemini and SMTP. Returns the emails that would have been sent."""
    sent: list = []
    data = {"46": SITE_A, "2050": SITE_B}

    async def gather(_session, site_ids, _days):
        return sorted((data[i] for i in site_ids), key=lambda s: s["name"])

    async def send(_settings, emails):
        sent.extend(emails)
        return [DeliveryResult(e.to, e.subject, "sent") for e in emails]

    monkeypatch.setattr(reports, "gather_site_data", gather)
    monkeypatch.setattr(reports, "send_emails", send)
    return sent


def client_for(reply):
    return httpx.AsyncClient(transport=httpx.MockTransport(reply))


def test_sends_one_report_per_site_group(fakes) -> None:
    calls = []

    def reply(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(json.loads(request.content)["contents"][0]["parts"][0]["text"]))
        return gemini_reply(f"Summary of {len(calls[-1]['sites'])} site(s).")

    session = FakeSession(MANAGERS, ["46", "2050"])
    out = run(
        reports.send_reports(
            session,
            client_for(reply),
            SETTINGS,
            site_ids=None,
            emails=[],
            notify_managers=True,
            days=7,
            dry_run=False,
        )
    )
    assert len(calls) == 2  # one Gemini call per distinct site set
    by_to = {e.to: e for e in fakes}
    assert set(by_to) == {"pat@example.com", "kim@example.com"}
    assert by_to["pat@example.com"].subject.startswith("SiteSentinel report: 2 sites - ")
    assert by_to["kim@example.com"].subject.startswith("SiteSentinel report: Browns Ferry - ")
    assert by_to["pat@example.com"].body.startswith("Summary of 2 site(s).")
    assert "Sites: Baxter Wilson, Browns Ferry" in by_to["pat@example.com"].body
    assert all(r.summary_source == "gemini" for r in out)
    assert [d.status for r in out for d in r.deliveries] == ["sent", "sent"]


def test_falls_back_without_gemini(fakes) -> None:
    settings = SETTINGS.model_copy(update={"gemini_api_key": None})
    session = FakeSession([("kim@example.com", "46")], ["46"])
    (report,) = run(
        reports.send_reports(
            session,
            client_for(lambda _r: pytest.fail("called")),
            settings,
            site_ids=None,
            emails=[],
            notify_managers=True,
            days=7,
            dry_run=False,
        )
    )
    assert report.summary_source == "fallback"
    assert "GEMINI_API_KEY" in report.summary_error
    assert "STOP WORK (heat)" in fakes[0].body
    assert "written by Gemini" not in fakes[0].body


def test_dry_run_sends_nothing_and_previews_all_sites(fakes) -> None:
    session = FakeSession([], ["46", "2050"])
    (report,) = run(
        reports.send_reports(
            session,
            client_for(lambda _r: gemini_reply("Preview.")),
            SETTINGS,
            site_ids=None,
            emails=[],
            notify_managers=True,
            days=7,
            dry_run=True,
        )
    )
    assert fakes == []
    assert report.recipients == [] and report.deliveries == []
    assert report.site_ids == ["2050", "46"]


# --- API ----------------------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, fakes):
    monkeypatch.setattr(reports_router, "get_settings", lambda: SETTINGS)
    session = FakeSession(MANAGERS, ["46", "2050"])
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_http_client] = lambda: client_for(lambda _r: gemini_reply("Hi."))
    yield TestClient(app), session, fakes
    app.dependency_overrides.clear()


def test_endpoint_emails_reports(api) -> None:
    client, _, sent = api
    response = client.post(
        "/reports/email",
        json={"emails": ["Boss@Example.com"], "notify_site_managers": False, "site_ids": ["46"]},
    )
    assert response.status_code == 200
    (report,) = response.json()
    assert report["recipients"] == ["boss@example.com"]
    assert report["deliveries"] == [{"to": "boss@example.com", "status": "sent", "error": None}]
    assert [e.to for e in sent] == ["boss@example.com"]


def test_endpoint_needs_someone_to_email(api) -> None:
    client, _, sent = api
    response = client.post("/reports/email", json={"notify_site_managers": False})
    assert response.status_code == 422
    response = client.post("/reports/email", json={"emails": ["not-an-email"]})
    assert response.status_code == 422
    assert sent == []


def test_manager_endpoints(api) -> None:
    client, session, _ = api
    response = client.post(
        "/sites/46/managers", json={"email": "Pat.Lee@Example.com", "name": "Pat"}
    )
    assert response.status_code == 201
    assert response.json() == {
        "id": 1,
        "site_id": "46",
        "email": "pat.lee@example.com",
        "name": "Pat",
    }
    assert isinstance(session.added[0], SiteManager)

    session.fail_commit = True
    assert (
        client.post("/sites/46/managers", json={"email": "pat.lee@example.com"}).status_code == 409
    )
    assert client.post("/sites/999/managers", json={"email": "a@example.com"}).status_code == 404
    assert client.delete("/sites/46/managers/nobody@example.com").status_code == 404


def test_settings_ignore_real_credentials() -> None:
    from app.config import get_settings

    settings = get_settings()
    assert settings.smtp_host is None and settings.gemini_api_key is None
