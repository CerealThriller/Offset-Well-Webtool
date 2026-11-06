import io
import math
import zipfile

import pytest

from app.edm.parser import EdmError, ParseOptions, parse_edm
from app.geo.crs import haversine_ft
from app.models import Datum, NorthReference


def test_sample_pad(fixtures):
    wells = parse_edm((fixtures / "sample_edm.xml").read_bytes())
    assert [w.name for w in wells] == ["TEST 1H", "TEST 2H"]
    w1 = wells[0]
    assert w1.design == "Plan 1"
    assert w1.surface_nad27.lat == pytest.approx(31.93)
    # NAD27 to NAD83 in west Texas moves the point about 15 m north and 40 m west.
    shift = haversine_ft(w1.surface_nad27, w1.surface_nad83)
    assert 50 < shift < 250
    # 1H laterals go north about 9,700 ft.
    north_ft = (w1.path_nad83[-1].lat - w1.surface_nad83.lat) * 364_000
    assert 9_000 < north_ft < 10_500
    assert wells[1].path_nad83[-1].lat < wells[1].surface_nad83.lat


def test_zip_input(fixtures):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("plans.xml", (fixtures / "sample_edm.xml").read_bytes())
    assert len(parse_edm(buf.getvalue())) == 2


def _projected_edm() -> bytes:
    rows = [
        '<CD_SITE site_id="S" site_name="P" easting="1019428.0" northing="830558.0" />',
        '<CD_WELL well_id="W" site_id="S" well_common_name="GRID 1H" offset_east="0" offset_north="0" />',
        '<CD_WELLBORE wellbore_id="B" well_id="W" wellbore_name="OH" />',
        '<CD_DESIGN design_id="D" wellbore_id="B" design_name="Plan" phase="PLAN" />',
        '<CD_SURVEY_STATION design_id="D" wellbore_id="B" md="0" inclination="0" azimuth="0" />',
        '<CD_SURVEY_STATION design_id="D" wellbore_id="B" md="5000" inclination="0" azimuth="0" />',
        '<CD_SURVEY_STATION design_id="D" wellbore_id="B" md="5900" inclination="90" azimuth="0" />',
        '<CD_SURVEY_STATION design_id="D" wellbore_id="B" md="15900" inclination="90" azimuth="0" />',
    ]
    return ("<export>" + "".join(rows) + "</export>").encode()


def test_projected_coordinates_need_crs():
    with pytest.raises(EdmError):
        parse_edm(_projected_edm())


def test_projected_grid_north():
    # Scrapfile North #1H site from a real survey report: Texas Central NAD27,
    # 31 54' 43.149" N, 103 29' 37.037" W, convergence -1.63 deg.
    opts = ParseOptions(projected_epsg=32039, north_reference=NorthReference.GRID)
    (w,) = parse_edm(_projected_edm(), opts)
    lat27 = 31 + 54 / 60 + 43.149 / 3600
    lon27 = -(103 + 29 / 60 + 37.037 / 3600)
    assert w.surface_nad27.lat == pytest.approx(lat27, abs=2e-6)
    assert w.surface_nad27.lon == pytest.approx(lon27, abs=2e-6)
    assert w.grid_convergence_deg == pytest.approx(-1.63, abs=0.01)
    # A grid-north lateral points 1.63 deg west of true north.
    end, start = w.path_nad83[-1], w.path_nad83[0]
    dn = (end.lat - start.lat) * 364_000
    de = (end.lon - start.lon) * 364_000 * math.cos(math.radians(start.lat))
    assert math.degrees(math.atan2(de, dn)) == pytest.approx(-1.63, abs=0.05)


def test_datum_override(fixtures):
    wells = parse_edm((fixtures / "sample_edm.xml").read_bytes(), ParseOptions(datum=Datum.NAD83))
    assert wells[0].surface_nad83.lat == pytest.approx(31.93)


def test_not_edm():
    with pytest.raises(EdmError):
        parse_edm(b"<root><a x='1'/></root>")
