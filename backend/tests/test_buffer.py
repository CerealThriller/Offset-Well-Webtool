import pytest
from shapely.geometry import Point

from app.edm.parser import parse_edm
from app.geo.buffer import BufferMode, build_search_area, distance_to_plans, polygon_to_esri
from app.models import LatLon, OffsetWell


@pytest.fixture
def wells(fixtures):
    return parse_edm((fixtures / "sample_edm.xml").read_bytes())


def test_radius(wells):
    area = build_search_area(wells, 1.0, BufferMode.PER_WELL)
    s = wells[0].surface_nad83
    # 1 mile east of the surface hole is on the boundary; 0.9 mile inside, 1.1 outside.
    deg_per_mile_lon = 5280 / (364_000 * 0.8487)
    assert area.contains(Point(s.lon + 0.9 * deg_per_mile_lon, s.lat))
    assert not area.contains(Point(s.lon + 1.1 * deg_per_mile_lon, s.lat))


def test_pad_mode_is_larger(wells):
    a = build_search_area(wells, 0.5, BufferMode.PER_WELL)
    b = build_search_area(wells, 0.5, BufferMode.PAD)
    assert b.area >= a.area


def test_distance(wells):
    s = wells[0].surface_nad83
    off = OffsetWell(state="TX", api="x", surface_nad83=LatLon(lat=s.lat + 0.01, lon=s.lon + 0.005))
    d, name = distance_to_plans(off, wells)
    assert name in ("TEST 1H", "TEST 2H")
    assert 1400 < d < 1700  # about 0.005 deg of longitude


def test_esri_rings_clockwise(wells):
    esri = polygon_to_esri(build_search_area(wells, 0.5, BufferMode.PAD))
    ring = esri["rings"][0]
    area2 = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:]))
    assert area2 < 0  # clockwise
