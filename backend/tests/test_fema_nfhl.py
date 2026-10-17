import asyncio

import httpx
import pytest

from app.services import fema_nfhl
from app.services.fema_nfhl import FemaNfhlError, fetch_flood_zones

BASE = "https://example.test/NFHL/MapServer"
SQUARE = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]]}


def feature(ar_id: str, zone: str = "AE", sfha: str = "T", bfe: float = -9999) -> dict:
    return {
        "type": "Feature",
        "geometry": SQUARE,
        "properties": {
            "FLD_AR_ID": ar_id,
            "DFIRM_ID": "47157C",
            "FLD_ZONE": zone,
            "ZONE_SUBTY": "",
            "SFHA_TF": sfha,
            "STATIC_BFE": bfe,
            "DEPTH": -9999,
            "LEN_UNIT": "Feet",
        },
    }


def run(handler) -> list:
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_flood_zones(client, BASE, 35.15, -90.05, 2.0)

    return asyncio.run(go())


def test_parses_features_and_placeholders() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/28/query")
        assert request.url.params["distance"] == "2.0"
        assert request.url.params["geometry"] == "-90.05,35.15"
        return httpx.Response(
            200, json={"features": [feature("A1", bfe=212.5), feature("X1", "X", "F")]}
        )

    zones = run(handler)

    summary = [(z.fld_ar_id, z.fld_zone, z.sfha) for z in zones]
    assert summary == [("A1", "AE", True), ("X1", "X", False)]
    assert zones[0].static_bfe == 212.5
    assert zones[1].static_bfe is None  # -9999 means "no value"
    assert zones[0].depth is None
    assert zones[0].zone_subtype is None  # empty strings become None


def test_follows_pagination() -> None:
    offsets = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["resultOffset"])
        offsets.append(offset)
        if offset == 0:
            return httpx.Response(
                200,
                json={"features": [feature("A1"), feature("A2")], "exceededTransferLimit": True},
            )
        return httpx.Response(200, json={"features": [feature("A3")]})

    assert [z.fld_ar_id for z in run(handler)] == ["A1", "A2", "A3"]
    assert offsets == [0, 2]


def test_retries_server_errors_then_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        pass

    monkeypatch.setattr(fema_nfhl.asyncio, "sleep", no_sleep)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(FemaNfhlError, match="after 3 attempts"):
        run(handler)
    assert len(calls) == 3


def test_service_error_is_not_retried() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"error": {"code": 400, "message": "Invalid query"}})

    with pytest.raises(FemaNfhlError, match="rejected"):
        run(handler)
    assert len(calls) == 1
