import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.db import get_session
from app.main import app
from app.models import Site

TURKEY_POINT = {
    "id": "6045",
    "name": "Turkey Point",
    "utility": "Florida Power & Light Co",
    "plant_type": "Nuclear",
    "county": "Miami-Dade",
    "state": "FL",
    "timezone": "America/New_York",
    "near_river": False,
    "latitude": 25.4356,
    "longitude": -80.3308,
}


class FakeSession:
    """Stands in for AsyncSession; records added objects, optionally fails on commit."""

    def __init__(self, fail_commit: bool = False) -> None:
        self.added: list[object] = []
        self.committed = False
        self.rolled_back = False
        self.fail_commit = fail_commit

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        if self.fail_commit:
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.fixture
def session() -> FakeSession:
    fake = FakeSession()
    app.dependency_overrides[get_session] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


client = TestClient(app)


def test_create_site_stores_point(session: FakeSession) -> None:
    response = client.post("/sites", json=TURKEY_POINT)

    assert response.status_code == 201
    assert response.json() == TURKEY_POINT
    assert session.committed
    (site,) = session.added
    assert isinstance(site, Site)
    assert site.id == "6045"
    assert site.timezone == "America/New_York"
    assert site.geom.data == "POINT(-80.3308 25.4356)"  # PostGIS points are (lon, lat)
    assert site.geom.srid == 4326


def test_create_site_minimal_fields_use_defaults(session: FakeSession) -> None:
    body = {
        "id": " 99 ",
        "name": "Plant",
        "timezone": "America/Chicago",
        "latitude": 35.1,
        "longitude": -90.0,
    }
    response = client.post("/sites", json=body)

    assert response.status_code == 201
    data = response.json()
    assert data["id"] == "99"  # whitespace stripped
    assert data["near_river"] is False
    assert data["utility"] is None


def test_duplicate_site_returns_409(session: FakeSession) -> None:
    session.fail_commit = True
    response = client.post("/sites", json=TURKEY_POINT)

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]
    assert session.rolled_back


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timezone", "Florida/Everglades"),
        ("latitude", 91),
        ("longitude", -181),
        ("state", "Florida"),
        ("name", "   "),
        ("id", ""),
    ],
)
def test_invalid_site_returns_422(session: FakeSession, field: str, value: object) -> None:
    response = client.post("/sites", json={**TURKEY_POINT, field: value})

    assert response.status_code == 422
    assert not session.added


def test_missing_required_field_returns_422(session: FakeSession) -> None:
    body = {k: v for k, v in TURKEY_POINT.items() if k != "timezone"}
    assert client.post("/sites", json=body).status_code == 422
