import csv
from pathlib import Path

import pytest
from openpyxl import Workbook
from timezonefinder import TimezoneFinder

from scripts.import_sites import EIA_860M_COLUMNS, NearestRiver, read_plants, to_site

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


def eia_row(**values: object) -> list[object]:
    """A full 37-column EIA-860M row with the given fields set."""
    row = dict.fromkeys(EIA_860M_COLUMNS, "")
    row.update(values)
    return [row[c] for c in EIA_860M_COLUMNS]


def write_csv(path: Path, rows: list[list[object]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    return path


ST_LUCIE = {
    "Entity Name": "Florida Power & Light Co",
    "Plant ID": "6045",
    "Plant Name": "St Lucie",
    "Plant State": "FL",
    "County": "St Lucie",
    "Technology": "Nuclear",
    "Latitude": "27.348611",
    "Longitude": "-80.24639",
}


def test_read_plants_csv_without_header_uses_eia_layout(tmp_path: Path) -> None:
    sheet = write_csv(
        tmp_path / "sites.csv",
        [
            eia_row(**{**ST_LUCIE, "Nameplate Capacity (MW)": "1,080.00"}),
            eia_row(**{**ST_LUCIE, "Nameplate Capacity (MW)": "5", "Technology": "Petroleum"}),
        ],
    )

    (plant,) = read_plants(sheet)

    assert plant["Plant ID"] == "6045"
    assert plant["Technology"] == "Nuclear"  # "1,080.00" read as 1080, the largest generator
    assert plant["Latitude"] == "27.348611"


def test_read_plants_csv_with_header(tmp_path: Path) -> None:
    sheet = write_csv(tmp_path / "sites.csv", [EIA_860M_COLUMNS, eia_row(**ST_LUCIE)])
    assert [p["Plant Name"] for p in read_plants(sheet)] == ["St Lucie"]


def test_read_plants_rejects_unrecognized_headerless_file(tmp_path: Path) -> None:
    sheet = write_csv(tmp_path / "odd.csv", [["6045", "St Lucie", "FL"]])
    with pytest.raises(SystemExit, match="doesn't match"):
        read_plants(sheet)


def test_to_site_accepts_csv_text_values(tf: TimezoneFinder) -> None:
    site, _ = to_site(read_plants_row(ST_LUCIE), tf, None)
    assert (site.latitude, site.longitude) == (27.348611, -80.24639)
    assert site.timezone == "America/New_York"


def read_plants_row(values: dict) -> dict:
    return dict(zip(EIA_860M_COLUMNS, eia_row(**values), strict=True))
