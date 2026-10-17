import asyncio
from typing import Any

import httpx
import pytest

from app.services import conditions
from app.services.conditions import (
    SiteLocation,
    condition_row,
    record_conditions,
    soil_saturation,
)
from app.services.open_meteo import _parse
from tests.test_weather import SAMPLE


@pytest.mark.parametrize(
    ("volumetric", "expected"),
    [(None, None), (0.0, 0.0), (0.2, 0.4), (0.45, 0.9), (0.5, 1.0), (0.62, 1.0), (-0.01, 0.0)],
)
def test_soil_saturation(volumetric: float | None, expected: float | None) -> None:
    assert soil_saturation(volumetric) == expected


def test_condition_row_maps_current_weather() -> None:
    row = condition_row("6045", _parse(SAMPLE))

    assert row["site_id"] == "6045"
    assert row["observed_at"].isoformat() == "2026-08-14T06:45:00-04:00"
    assert row["temp_f"] == 90.0
    assert row["heat_index_f"] == pytest.approx(100, abs=1)
    assert row["precip_24h_in"] == 2.4
    assert row["soil_moisture"] == 0.9  # 0.45 m³/m³ volumetric -> 90% saturated
    assert row["wind_mph"] == 8.0


def test_record_conditions_stores_good_sites_and_reports_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sites = [SiteLocation("ok", 25.9, -80.9), SiteLocation("bad", 0.0, 0.0)]
    stored: list[dict[str, Any]] = []

    async def fake_load_sites(site_ids: list[str] | None = None) -> list[SiteLocation]:
        return sites

    async def fake_store_rows(rows: list[dict[str, Any]]) -> None:
        stored.extend(rows)

    monkeypatch.setattr(conditions, "load_sites", fake_load_sites)
    monkeypatch.setattr(conditions, "store_rows", fake_store_rows)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["latitude"] == "0.0":
            return httpx.Response(400, json={"error": True, "reason": "No data"})
        return httpx.Response(200, json=SAMPLE)

    async def run() -> conditions.RecordResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await record_conditions(client)

    result = asyncio.run(run())

    assert result.stored == 1
    assert [r["site_id"] for r in stored] == ["ok"]
    assert list(result.failed) == ["bad"]
    assert "No data" in result.failed["bad"]
