"""Datum and coordinate reference system helpers.

All internal geometry uses NAD83 geographic coordinates (EPSG:4269). For a
search buffer of 0.5 mile or more, the NAD83 / WGS84 difference (about 1 m)
does not matter, so state GIS layers in WGS84 are used without a shift.

NAD27 to NAD83 needs the NOAA NADCON5 grid (us_noaa_nadcon5_nad27_nad83_1986_conus.tif)
or the older us_noaa_conus.tif grid. If PROJ cannot find a grid, it falls back
to a less accurate transformation. The tool records that in the warnings.
"""

from __future__ import annotations

import math
import warnings
from functools import lru_cache

from pyproj import CRS, Proj, Transformer
from pyproj.aoi import AreaOfInterest
from pyproj.transformer import TransformerGroup

from ..models import Datum, LatLon

GEOG_EPSG = {Datum.NAD27: 4267, Datum.NAD83: 4269, Datum.WGS84: 4326}

# State plane zones that operators in the supported basins use most often.
# The user can type any EPSG code in the UI. This list fills the dropdown.
COMMON_PROJECTED_CRS = {
    # Texas
    32037: "NAD27 / Texas North",
    32038: "NAD27 / Texas North Central",
    32039: "NAD27 / Texas Central",
    32040: "NAD27 / Texas South Central",
    32041: "NAD27 / Texas South",
    2275: "NAD83 / Texas North (ftUS)",
    2276: "NAD83 / Texas North Central (ftUS)",
    2277: "NAD83 / Texas Central (ftUS)",
    2278: "NAD83 / Texas South Central (ftUS)",
    2279: "NAD83 / Texas South (ftUS)",
    # New Mexico
    32012: "NAD27 / New Mexico East",
    32013: "NAD27 / New Mexico Central",
    32014: "NAD27 / New Mexico West",
    2257: "NAD83 / New Mexico East (ftUS)",
    2258: "NAD83 / New Mexico Central (ftUS)",
    2259: "NAD83 / New Mexico West (ftUS)",
    # Colorado
    26753: "NAD27 / Colorado North",
    26754: "NAD27 / Colorado Central",
    26755: "NAD27 / Colorado South",
    2231: "NAD83 / Colorado North (ftUS)",
    2232: "NAD83 / Colorado Central (ftUS)",
    2233: "NAD83 / Colorado South (ftUS)",
    # North Dakota, Oklahoma, Wyoming, Utah
    32064: "NAD27 / North Dakota North",
    2265: "NAD83 / North Dakota North (ft)",
    32024: "NAD27 / Oklahoma North",
    32025: "NAD27 / Oklahoma South",
    2267: "NAD83 / Oklahoma North (ftUS)",
    2268: "NAD83 / Oklahoma South (ftUS)",
    32055: "NAD27 / Wyoming East",
    32056: "NAD27 / Wyoming East Central",
    3736: "NAD83 / Wyoming East (ftUS)",
    3737: "NAD83 / Wyoming East Central (ftUS)",
    32042: "NAD27 / Utah North",
    3560: "NAD83 / Utah North (ftUS)",
}

FT_PER_M = 3.280839895013123


@lru_cache(maxsize=64)
def _transformer(src: int | str, dst: int | str) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


@lru_cache(maxsize=256)
def _datum_shift(src: int, dst: int, lat_r: float, lon_r: float) -> tuple[Transformer, bool]:
    """Best available transformer for a 0.1 degree cell around a point.

    Returns (transformer, exact). exact is False if PROJ has to skip a better
    transformation because its grid file is missing.
    """
    aoi = AreaOfInterest(lon_r - 0.1, lat_r - 0.1, lon_r + 0.1, lat_r + 0.1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        group = TransformerGroup(src, dst, always_xy=True, area_of_interest=aoi)
    if group.transformers and group.best_available:
        return group.transformers[0], True
    # Without the grid, the default pipeline is still better than no shift.
    return _transformer(src, dst), False


def _shift(src: int, dst: int, lat: float, lon: float) -> tuple[float, float, bool]:
    """Return (lat, lon, exact)."""
    t, exact = _datum_shift(src, dst, round(lat, 1), round(lon, 1))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        x, y = t.transform(lon, lat)
    if not math.isfinite(x):  # the grid exists but does not cover the point
        x, y = _transformer(src, dst).transform(lon, lat)
        exact = False
    return y, x, exact


def nadcon_grid_available() -> bool:
    """Return True if PROJ makes an exact NAD27 to NAD83 shift in west Texas."""
    return _shift(4267, 4269, 31.9, -103.5)[2]


def to_nad83(lat: float, lon: float, datum: Datum) -> tuple[LatLon, list[str]]:
    """Convert a geographic position on any supported datum to NAD83."""
    notes: list[str] = []
    if datum in (Datum.NAD83, Datum.WGS84):
        return LatLon(lat=lat, lon=lon), notes
    y, x, exact = _shift(GEOG_EPSG[datum], 4269, lat, lon)
    if not exact:
        notes.append(
            "NADCON grid not installed. The NAD27 to NAD83 shift is approximate (can be off by 10 m or more)."
        )
    return LatLon(lat=y, lon=x), notes


def nad83_to_nad27(p: LatLon) -> LatLon:
    y, x, _ = _shift(4269, 4267, p.lat, p.lon)
    return LatLon(lat=y, lon=x)


def projected_to_geographic(x: float, y: float, epsg: int) -> tuple[float, float, Datum]:
    """Convert map coordinates (in the CRS's own units) to lat/lon on the CRS's own datum."""
    crs = CRS.from_epsg(epsg)
    geog = crs.geodetic_crs
    datum = datum_from_crs(crs)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        lon, lat = _transformer(epsg, geog.to_epsg() or GEOG_EPSG[datum]).transform(x, y)
    return lat, lon, datum


def datum_from_crs(crs: CRS) -> Datum:
    name = (crs.datum.name if crs.datum else crs.name).upper().replace(" ", "")
    if "1927" in name or "NAD27" in name:
        return Datum.NAD27
    if "WGS" in name:
        return Datum.WGS84
    return Datum.NAD83


def crs_linear_unit_to_ft(epsg: int) -> float:
    """Return the factor that converts the CRS's linear unit to international feet."""
    crs = CRS.from_epsg(epsg)
    axis = crs.axis_info[0]
    factor_to_m = axis.unit_conversion_factor or 1.0
    return factor_to_m * FT_PER_M


def grid_convergence(lat: float, lon: float, epsg: int) -> float:
    """Return the grid convergence angle in degrees at a point for a projected CRS.

    The sign follows the common oilfield convention: true azimuth = grid azimuth + convergence.
    In the northern hemisphere, east of the central meridian, grid north is east of true
    north and the convergence is positive.
    """
    proj = Proj(CRS.from_epsg(epsg))
    # PROJ uses the same sign: negative west of the central meridian.
    # Check: Texas Central at -103.5 deg gives about -1.6 deg.
    return proj.get_factors(lon, lat).meridian_convergence


def local_aeqd(lat: float, lon: float) -> CRS:
    """Azimuthal equidistant CRS centered on a point, in meters, on NAD83.

    Distances and true-north bearings from the center are exact, so the tool
    uses this CRS to place survey offsets and to draw buffers.
    """
    return CRS.from_proj4(f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=NAD83 +units=m +no_defs")


def offsets_to_latlon(origin: LatLon, offsets_ft: list[tuple[float, float]]) -> list[LatLon]:
    """Convert true-north (N, E) offsets in feet from an origin to NAD83 lat/lon."""
    t = Transformer.from_crs(local_aeqd(origin.lat, origin.lon), 4269, always_xy=True)
    out = []
    for n_ft, e_ft in offsets_ft:
        lon, lat = t.transform(e_ft / FT_PER_M, n_ft / FT_PER_M)
        out.append(LatLon(lat=lat, lon=lon))
    return out


def rotate_grid_to_true(azi_grid: float, convergence_deg: float) -> float:
    return (azi_grid + convergence_deg) % 360.0


def rotate_offsets(n: float, e: float, convergence_deg: float) -> tuple[float, float]:
    """Rotate grid-north (N, E) offsets to true north. True azimuth = grid azimuth + convergence."""
    c = math.radians(convergence_deg)
    return n * math.cos(c) - e * math.sin(c), n * math.sin(c) + e * math.cos(c)


def haversine_ft(a: LatLon, b: LatLon) -> float:
    r_ft = 20_902_231.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = p2 - p1
    dl = math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r_ft * math.asin(math.sqrt(h))
