"""Client for FEMA's National Flood Hazard Layer (NFHL) ArcGIS service.

Queries the "Flood Hazard Zones" layer (28) for polygons within a radius of a coordinate.
https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer
"""

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel

FLOOD_HAZARD_ZONES_LAYER = 28
OUT_FIELDS = [
    "FLD_AR_ID",
    "DFIRM_ID",
    "FLD_ZONE",
    "ZONE_SUBTY",
    "SFHA_TF",
    "STATIC_BFE",
    "DEPTH",
    "LEN_UNIT",
]
PAGE_SIZE = 1000  # the service caps pages at 2000; smaller pages keep large polygons manageable
NO_VALUE = -9999  # FEMA's placeholder for "not applicable"

# FEMA's server is slow and occasionally stalls, so allow long reads and retry.
TIMEOUT = httpx.Timeout(120.0, connect=20.0)
RETRIES = 3


class FemaNfhlError(Exception):
    """FEMA NFHL could not be reached or returned an error."""


class FloodZoneFeature(BaseModel):
    fld_ar_id: str
    dfirm_id: str | None
    fld_zone: str
    zone_subtype: str | None
    sfha: bool
    static_bfe: float | None
    depth: float | None
    len_unit: str | None
    geometry: dict[str, Any]  # GeoJSON Polygon/MultiPolygon in EPSG:4326


async def fetch_flood_zones(
    client: httpx.AsyncClient,
    base_url: str,
    latitude: float,
    longitude: float,
    radius_mi: float,
) -> list[FloodZoneFeature]:
    """Return every flood zone polygon intersecting the circle around the coordinate.

    Polygons come back whole (not clipped), so they can extend well past the radius.
    """
    url = f"{base_url.rstrip('/')}/{FLOOD_HAZARD_ZONES_LAYER}/query"
    params: dict[str, Any] = {
        "geometry": f"{longitude},{latitude}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "distance": radius_mi,
        "units": "esriSRUnit_StatuteMile",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(OUT_FIELDS),
        "returnGeometry": "true",
        "outSR": 4326,
        "geometryPrecision": 6,  # ~0.1 m
        "orderByFields": "OBJECTID",
        "resultRecordCount": PAGE_SIZE,
        "f": "geojson",
    }

    features: list[FloodZoneFeature] = []
    offset = 0
    while True:
        page = await _get_json(client, url, {**params, "resultOffset": offset})
        raw = page.get("features", [])
        features.extend(_parse_feature(f) for f in raw if f.get("geometry"))
        exceeded = page.get("exceededTransferLimit") or page.get("properties", {}).get(
            "exceededTransferLimit"
        )
        if not exceeded or not raw:
            return features
        offset += len(raw)


async def _get_json(client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> dict:
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        try:
            response = await client.get(url, params=params, timeout=TIMEOUT)
            if response.status_code < 500:
                data = response.json()
                if response.is_error or "error" in data:
                    raise FemaNfhlError(f"FEMA NFHL rejected the request: {data.get('error', '')}")
                return data
            last_error = FemaNfhlError(f"FEMA NFHL returned HTTP {response.status_code}")
        except (httpx.TimeoutException, httpx.TransportError, ValueError) as exc:
            last_error = exc
        await asyncio.sleep(2**attempt)
    raise FemaNfhlError(f"FEMA NFHL unavailable after {RETRIES} attempts: {last_error}")


def _parse_feature(feature: dict[str, Any]) -> FloodZoneFeature:
    props = feature["properties"]
    return FloodZoneFeature(
        fld_ar_id=props["FLD_AR_ID"],
        dfirm_id=props.get("DFIRM_ID") or None,
        fld_zone=props["FLD_ZONE"],
        zone_subtype=props.get("ZONE_SUBTY") or None,
        sfha=props.get("SFHA_TF") == "T",
        static_bfe=_value(props.get("STATIC_BFE")),
        depth=_value(props.get("DEPTH")),
        len_unit=props.get("LEN_UNIT") or None,
        geometry=feature["geometry"],
    )


def _value(raw: float | None) -> float | None:
    return None if raw is None or raw == NO_VALUE else raw
