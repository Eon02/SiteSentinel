"""Shared paths and constants for the storm/injury analysis."""

from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent.parent
OSHA_CSV = MODEL_DIR / "January2015toNovember2025.csv"
STORM_DETAILS_DIR = MODEL_DIR / "storm_event_details"
REFERENCE_DIR = MODEL_DIR / "reference"
CACHE_DIR = MODEL_DIR / "cache"
OUT_DIR = MODEL_DIR / "out"

YEARS = range(2015, 2026)
EARTH_RADIUS_KM = 6371.0088
RADII_KM = (10, 25, 50)
DAYS_AFTER_EVENT = 1  # an injury the day after an event ends still counts as exposed

# NOAA EVENT_TYPE -> analysis group. Types not listed (marine, drought, surf, etc.) are dropped.
EVENT_GROUPS = {
    "Heat": "heat",
    "Excessive Heat": "heat",
    "Cold/Wind Chill": "cold",
    "Extreme Cold/Wind Chill": "cold",
    "Frost/Freeze": "cold",
    "Winter Storm": "winter",
    "Winter Weather": "winter",
    "Heavy Snow": "winter",
    "Blizzard": "winter",
    "Ice Storm": "winter",
    "Lake-Effect Snow": "winter",
    "Sleet": "winter",
    "Freezing Fog": "winter",
    "High Wind": "high_wind",
    "Strong Wind": "high_wind",
    "Thunderstorm Wind": "tstorm_wind",
    "Hail": "hail",
    "Tornado": "tornado",
    "Flash Flood": "flood",
    "Flood": "flood",
    "Heavy Rain": "flood",
    "Debris Flow": "flood",
    "Lightning": "lightning",
    "Tropical Storm": "tropical",
    "Tropical Depression": "tropical",
    "Hurricane (Typhoon)": "tropical",
    "Storm Surge/Tide": "tropical",
    "Wildfire": "smoke_fire",
    "Dense Smoke": "smoke_fire",
    "Dense Fog": "fog",
    "Dust Storm": "dust",
}
GROUPS = sorted(set(EVENT_GROUPS.values()))

# 2-digit NAICS sectors where most work happens outdoors
OUTDOOR_NAICS2 = {"11", "21", "22", "23", "48", "49"}
