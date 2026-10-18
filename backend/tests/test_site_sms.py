import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import get_session
from app.http import get_http_client
from app.main import app
from app.models import Site, SiteWorker
from app.routers import sites as sites_router

SITE = Site(id="46", name="Browns Ferry", timezone="America/Chicago")
WORKERS = [
    SiteWorker(id=1, site_id="46", phone="+12055550123", name="Sam Rivera"),
    SiteWorker(id=2, site_id="46", phone="+12055550199", name=None),
]


class FakeSession:
    def __init__(self, site: Site | None, workers: list[SiteWorker]) -> None:
        self.site, self.workers = site, workers

    async def get(self, model, key):
        return self.site if self.site and self.site.id == key else None

    async def scalars(self, _statement):
        return iter(self.workers)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    texts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        texts.append(dict(httpx.QueryParams(request.content.decode())))
        if texts[-1]["To"] == "+12055550199":
            return httpx.Response(400, json={"code": 21608, "message": "Unverified number"})
        return httpx.Response(201, json={"sid": "SM1"})

    settings = Settings(
        database_url="postgresql://t:t@localhost/t",
        twilio_account_sid="AC123",
        twilio_auth_token="token",
        twilio_from_number="+12055550100",
        twilio_api_url="https://twilio.test",
    )
    monkeypatch.setattr(sites_router, "get_settings", lambda: settings)
    state = {"session": FakeSession(SITE, WORKERS)}
    app.dependency_overrides[get_session] = lambda: state["session"]
    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    yield TestClient(app), texts, state
    app.dependency_overrides.clear()


def test_texts_every_worker_on_the_site(env) -> None:
    client, texts, _ = env
    body = {"message": "  Lightning cleared, resume at 2 PM.  "}
    response = client.post("/sites/46/sms", json=body)

    assert response.status_code == 200
    assert response.json() == [
        {"to": "+12055550123", "name": "Sam Rivera", "status": "sent", "error": None},
        {
            "to": "+12055550199",
            "name": None,
            "status": "failed",
            "error": "Twilio error 21608: Unverified number",
        },
    ]
    assert [t["To"] for t in texts] == ["+12055550123", "+12055550199"]
    assert texts[0]["Body"] == "SiteSentinel - Browns Ferry: Lightning cleared, resume at 2 PM."


def test_no_crew_sends_nothing(env) -> None:
    client, texts, state = env
    state["session"] = FakeSession(SITE, [])
    assert client.post("/sites/46/sms", json={"message": "hi"}).json() == []
    assert texts == []


def test_unknown_site_404(env) -> None:
    client, texts, state = env
    state["session"] = FakeSession(None, [])
    assert client.post("/sites/nope/sms", json={"message": "hi"}).status_code == 404
    assert texts == []


@pytest.mark.parametrize("message", ["", "   ", "x" * 281])
def test_rejects_blank_or_long_message(env, message: str) -> None:
    client, texts, _ = env
    assert client.post("/sites/46/sms", json={"message": message}).status_code == 422
    assert texts == []


def test_no_message_sends_default_status_text(env) -> None:
    client, texts, _ = env
    assert client.post("/sites/46/sms", json={}).status_code == 200
    assert texts[0]["Body"] == (
        "SiteSentinel - Browns Ferry: Site status update. Check the SiteSentinel app for details."
    )
