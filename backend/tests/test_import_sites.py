from pathlib import Path

import pytest
from openpyxl import Workbook
from timezonefinder import TimezoneFinder

from scripts.import_sites import NearestRiver, read_plants, to_site

HEADER = [
    "Entity Name", "Plant ID", "Plant Name", "Plant State", "County", "Generator ID",
    "Nameplate Capacity (MW)", "Technology", "Latitude", "Longitude",
]  # fmt: skip


def write_sheet(path: Path, rows: list[list]) -> Path:
    wb = Workbook()
    wb.active.append(HEADER)
    for row in rows:
        wb.active.append(row)
    wb.save(path)
    return path


BROWNS = (34.7042, -87.1189)


@pytest.fixture(scope="module")
def tf() -> TimezoneFinder:
    return TimezoneFinder()


def test_read_plants_groups_generators_by_plant(tmp_path: Path) -> None:
    sheet = write_sheet(
        tmp_path / "eia.xlsx",
        [
            ["TVA", 46.0, "Browns Ferry", "AL", "Limestone", 1, 1281.0, "Nuclear", *BROWNS],
            ["TVA", 46.0, "Browns Ferry", "AL", "Limestone", 9, 20.0, "Petroleum Liquids", *BROWNS],
            ["APC", 2.0, "Bankhead Dam", "AL", "Tuscaloosa", 1, 53.9, "Hydroelectric", 33.46, -87],
            [None, None, None, None, None, None, None, None, None, None],  # blank row
        ],
    )  # fmt: skip

    plants = {p["Plant ID"]: p for p in read_plants(sheet)}

    assert set(plants) == {"46", "2"}  # float ids from Excel become clean text ids
    assert plants["46"]["Technology"] == "Nuclear"  # largest generator wins


def test_read_plants_requires_columns(tmp_path: Path) -> None:
    wb = Workbook()
    wb.active.append(["Plant ID", "Plant Name"])
    wb.save(tmp_path / "bad.xlsx")
    with pytest.raises(SystemExit, match="Latitude"):
        read_plants(tmp_path / "bad.xlsx")


PLANT = {
    "Plant ID": "9",
    "Plant Name": " Copper ",
    "Entity Name": "El Paso Electric Co",
    "County": "EL PASO",
    "Plant State": "tx",
    "Technology": "Natural Gas Fired Combustion Turbine",
    "Latitude": 31.7569,
    "Longitude": -106.375,
}


def test_to_site_derives_timezone_from_coordinates(tf: TimezoneFinder) -> None:
    site, note = to_site(PLANT, tf, None)

    assert site.timezone == "America/Denver"  # El Paso is on Mountain time, unlike most of TX
    assert site.name == "Copper"
    assert site.county == "El Paso"
    assert site.state == "TX"
    assert site.near_river is False
    assert note == "no major river within 1 mi"


def test_to_site_major_river_sets_near_river(tf: TimezoneFinder) -> None:
    site, note = to_site(PLANT, tf, NearestRiver("Rio Grande", 7))
    assert site.near_river is True
    assert note == "Rio Grande (order 7)"


@pytest.mark.parametrize("river", [None, OSError("down"), "skip"])
def test_hydro_plants_are_near_a_river_without_usgs(tf: TimezoneFinder, river) -> None:
    hydro = {**PLANT, "Technology": "Conventional Hydroelectric"}
    site, _ = to_site(hydro, tf, river)
    assert site.near_river is True


def test_failed_lookup_falls_back_and_says_so(tf: TimezoneFinder) -> None:
    site, note = to_site(PLANT, tf, OSError("502 Bad Gateway"))
    assert site.near_river is False
    assert note.startswith("USGS lookup failed")
