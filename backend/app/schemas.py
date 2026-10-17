"""Request and response bodies for the API."""

from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SiteCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
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
        }
    )

    id: NonEmpty = Field(description="EIA plant code")
    name: NonEmpty
    utility: str | None = None
    plant_type: str | None = Field(default=None, description="e.g. 'Solar', 'Natural Gas'")
    county: str | None = None
    state: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")] | None = Field(
        default=None, description="Two-letter state code"
    )
    timezone: NonEmpty = Field(description="IANA timezone, e.g. 'America/New_York'")
    near_river: bool = False
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown IANA timezone: {value!r}") from exc
        return value


class SiteOut(SiteCreate):
    pass
