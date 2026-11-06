"""Search area (area of interest) around the planned wells."""

from __future__ import annotations

from enum import Enum

from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon, mapping
from shapely.geometry.polygon import orient
import numpy as np
import shapely
from shapely.ops import unary_union

from ..models import LatLon, OffsetWell, PlannedWell
from .crs import FT_PER_M, local_aeqd

FT_PER_MILE = 5280.0


class BufferMode(str, Enum):
    PER_WELL = "per_well"  # buffer around each wellbore path, then union
    PAD = "pad"  # convex hull of all paths, then buffer


def _centroid(wells: list[PlannedWell]) -> LatLon:
    pts = [p for w in wells for p in (w.path_nad83 or [w.surface_nad83])]
    return LatLon(lat=sum(p.lat for p in pts) / len(pts), lon=sum(p.lon for p in pts) / len(pts))


def _to_local(center: LatLon):
    crs = local_aeqd(center.lat, center.lon)
    fwd = Transformer.from_crs(4269, crs, always_xy=True).transform
    inv = Transformer.from_crs(crs, 4269, always_xy=True).transform
    return fwd, inv


def _path_geom(w: PlannedWell, fwd):
    pts = w.path_nad83 or [w.surface_nad83]
    xy = [fwd(p.lon, p.lat) for p in pts]
    # Drop repeated points (vertical section of the hole).
    dedup = [xy[0]] + [b for a, b in zip(xy, xy[1:]) if (abs(a[0] - b[0]) + abs(a[1] - b[1])) > 0.01]
    return Point(dedup[0]) if len(dedup) == 1 else LineString(dedup)


def build_search_area(wells: list[PlannedWell], radius_miles: float, mode: BufferMode) -> Polygon:
    """Return the search polygon in NAD83 lon/lat."""
    if not wells:
        raise ValueError("No planned wells to buffer")
    center = _centroid(wells)
    fwd, inv = _to_local(center)
    radius_m = radius_miles * FT_PER_MILE / FT_PER_M
    geoms = [_path_geom(w, fwd) for w in wells]
    if mode == BufferMode.PAD:
        area = unary_union(geoms).convex_hull.buffer(radius_m, quad_segs=32)
    else:
        area = unary_union([g.buffer(radius_m, quad_segs=32) for g in geoms])
    return shapely.transform(area, lambda xy: np.column_stack(inv(xy[:, 0], xy[:, 1])))


def distance_to_plans(offset: OffsetWell, wells: list[PlannedWell]) -> tuple[float, str]:
    """Return the shortest plan-view distance (ft) from the offset well to any planned path.

    The offset geometry is its surface-to-bottom-hole line if the bottom hole is known.
    """
    center = offset.surface_nad83
    fwd, _ = _to_local(center)
    if offset.bottom_hole_nad83:
        og = LineString([fwd(center.lon, center.lat), fwd(offset.bottom_hole_nad83.lon, offset.bottom_hole_nad83.lat)])
    else:
        og = Point(fwd(center.lon, center.lat))
    best, best_name = float("inf"), ""
    for w in wells:
        d = _path_geom(w, fwd).distance(og) * FT_PER_M
        if d < best:
            best, best_name = d, w.name
    return best, best_name


def polygon_to_geojson(poly: Polygon) -> dict:
    return mapping(poly)


def polygon_to_esri(poly: Polygon) -> dict:
    """Return an Esri JSON polygon (WKID 4269) for ArcGIS REST queries."""
    polys = list(poly.geoms) if poly.geom_type == "MultiPolygon" else [poly]
    rings = []
    for p in polys:
        p = orient(p, sign=-1.0)  # Esri wants clockwise outer rings
        rings.append([list(c) for c in p.exterior.coords])
        rings.extend([list(c) for c in i.coords] for i in p.interiors)
    return {"rings": rings, "spatialReference": {"wkid": 4269}}
