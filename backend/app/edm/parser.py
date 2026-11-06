"""Parser for Landmark Compass EDM XML export files.

A Compass "Export > EDM XML" file is a flat list of EDM table rows. Each row
is an element named after its table (CD_SITE, CD_WELL, CD_WELLBORE,
CD_DESIGN, CD_SURVEY_HEADER, CD_SURVEY_STATION, CD_DEFINITIVE_SURVEY_STATION
and others). Columns are XML attributes. Rows link to each other by id
columns (site_id, well_id, wellbore_id, design_id, ...).

Attribute names change between EDM versions, and company templates add
columns. For that reason the parser looks for a list of candidate names for
each value. When the file does not give a value, the caller supplies it
(datum, CRS, north reference, units) from the upload form.

EDM stores depths in feet and angles in degrees in the database, whatever the
display units are. A file can override that with ParseOptions.depth_unit_ft.
"""

from __future__ import annotations

import gzip
import io
import math
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from lxml import etree

from ..geo import crs as geo
from ..models import Datum, LatLon, NorthReference, PlannedWell, Survey, SurveyStation, SurveyType
from ..survey.mincurve import compute_positions


@dataclass
class ParseOptions:
    """Values from the upload form. None means "read it from the file"."""

    datum: Optional[Datum] = None
    projected_epsg: Optional[int] = None  # CRS of map coordinates in the file
    north_reference: Optional[NorthReference] = None
    convergence_deg: Optional[float] = None
    depth_unit_ft: float = 1.0  # multiply EDM depths by this to get feet
    prefer: str = "plan"  # "plan" or "definitive" when both exist


@dataclass
class _Row:
    tag: str
    attrs: dict[str, str]


@dataclass
class EdmFile:
    rows_by_tag: dict[str, list[_Row]] = field(default_factory=lambda: defaultdict(list))

    def rows(self, *suffixes: str) -> list[_Row]:
        out: list[_Row] = []
        for tag, rows in self.rows_by_tag.items():
            if any(tag.endswith(s) for s in suffixes):
                out.extend(rows)
        return out

    def by_id(self, suffix: str, id_col: str) -> dict[str, _Row]:
        return {r.attrs[id_col]: r for r in self.rows(suffix) if id_col in r.attrs}


LAT_KEYS = ("geo_latitude", "latitude", "loc_lat", "lat", "surface_latitude", "lat_decimal")
LON_KEYS = ("geo_longitude", "longitude", "loc_long", "loc_lon", "lon", "long", "surface_longitude", "long_decimal")
EAST_KEYS = ("easting", "map_easting", "loc_easting", "x", "map_x", "geo_easting")
NORTH_KEYS = ("northing", "map_northing", "loc_northing", "y", "map_y", "geo_northing")
WELL_NAME_KEYS = ("well_common_name", "well_legal_name", "well_name", "name")
MD_KEYS = ("md", "measured_depth")
INC_KEYS = ("inclination", "inc", "incl")
AZI_KEYS = ("azimuth", "azi", "azim")
TVD_KEYS = ("tvd", "true_vertical_depth")
NS_KEYS = ("offset_north", "ns", "local_north", "north")
EW_KEYS = ("offset_east", "ew", "local_east", "east")
DLS_KEYS = ("dogleg_severity", "dls")
VS_KEYS = ("vertical_section", "vs")


def _get(attrs: dict[str, str], keys: tuple[str, ...]) -> Optional[str]:
    for k in keys:
        v = attrs.get(k)
        if v not in (None, ""):
            return v
    return None


def _num(attrs: dict[str, str], keys: tuple[str, ...]) -> Optional[float]:
    v = _get(attrs, keys)
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return _parse_dms(v)


_DMS = re.compile(r"(-?\d+(?:\.\d+)?)[^\d.\-]+(\d+(?:\.\d+)?)[^\d.]+(\d+(?:\.\d+)?)[^NSEWnsew]*([NSEWnsew])?")


def _parse_dms(text: str) -> Optional[float]:
    m = _DMS.search(text)
    if not m:
        return None
    d, mnt, sec, hemi = float(m.group(1)), float(m.group(2)), float(m.group(3)), m.group(4)
    val = abs(d) + mnt / 60.0 + sec / 3600.0
    if d < 0 or (hemi and hemi.upper() in "SW"):
        val = -val
    return val


def _read_bytes(data: bytes) -> bytes:
    """Accept raw XML, gzip, or a zip file that holds one XML file."""
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.lower().endswith((".xml", ".edm"))] or z.namelist()
            return z.read(names[0])
    return data


def load_edm(data: bytes) -> EdmFile:
    raw = _read_bytes(data)
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, recover=True)
    root = etree.fromstring(raw, parser=parser)
    edm = EdmFile()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        tag = etree.QName(el).localname.upper()
        attrs = {k.lower(): v for k, v in el.attrib.items()}
        # Some exports write columns as child elements instead of attributes.
        if not attrs and len(el):
            attrs = {etree.QName(c).localname.lower(): (c.text or "") for c in el if isinstance(c.tag, str) and not len(c)}
        if attrs:
            edm.rows_by_tag[tag].append(_Row(tag, attrs))
    return edm


def _detect_datum(edm: EdmFile) -> Optional[Datum]:
    text = " ".join(v for r in edm.rows("SITE", "DATUM", "PROJECT", "COORDINATE_SYSTEM", "GEO") for v in r.attrs.values()).upper()
    text = text.replace(" ", "")
    if "NAD27" in text or "NAD1927" in text or "NORTHAMERICAN1927" in text:
        return Datum.NAD27
    if "NAD83" in text or "NAD1983" in text or "NORTHAMERICAN1983" in text:
        return Datum.NAD83
    if "WGS84" in text or "WGS1984" in text:
        return Datum.WGS84
    return None


def _detect_epsg(edm: EdmFile) -> Optional[int]:
    for r in edm.rows("SITE", "PROJECT", "COORDINATE_SYSTEM"):
        for k, v in r.attrs.items():
            if "epsg" in k and v.strip().isdigit():
                return int(v)
    return None


def _detect_north_ref(edm: EdmFile) -> Optional[NorthReference]:
    for r in edm.rows("SITE", "PROJECT", "WELL", "DESIGN", "SURVEY_HEADER"):
        for k, v in r.attrs.items():
            if "north" in k and ("ref" in k or "azimuth" in k):
                val = v.strip().upper()
                if val.startswith("G"):
                    return NorthReference.GRID
                if val.startswith("T"):
                    return NorthReference.TRUE
    return None


def _surface_location(
    well: _Row, site: Optional[_Row], epsg: Optional[int], datum: Optional[Datum], warnings: list[str]
):
    """Return (lat, lon, datum) of the well's surface location."""
    lat, lon = _num(well.attrs, LAT_KEYS), _num(well.attrs, LON_KEYS)
    if lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180:
        return lat, lon, datum
    east, north = _num(well.attrs, EAST_KEYS), _num(well.attrs, NORTH_KEYS)
    if east is None and site is not None:
        s_e, s_n = _num(site.attrs, EAST_KEYS), _num(site.attrs, NORTH_KEYS)
        off_e = _num(well.attrs, ("offset_east", "ew_offset", "site_offset_east")) or 0.0
        off_n = _num(well.attrs, ("offset_north", "ns_offset", "site_offset_north")) or 0.0
        if s_e is not None and s_n is not None:
            east, north = s_e + off_e, s_n + off_n
    if east is not None and north is not None:
        if not epsg:
            raise EdmError(
                "The EDM file gives map coordinates (easting/northing) but no CRS. Select the state plane zone."
            )
        lat, lon, crs_datum = geo.projected_to_geographic(east, north, epsg)
        return lat, lon, crs_datum
    if site is not None:
        lat, lon = _num(site.attrs, LAT_KEYS), _num(site.attrs, LON_KEYS)
        if lat is not None and lon is not None:
            warnings.append("Well has no own location. Used the site location.")
            return lat, lon, datum
    raise EdmError(f"No surface location for well {well.attrs.get('well_id', '?')}")



class EdmError(ValueError):
    pass


def _stations_from(rows: list[_Row], depth_ft: float) -> list[SurveyStation]:
    out = []
    for r in rows:
        md, inc, azi = _num(r.attrs, MD_KEYS), _num(r.attrs, INC_KEYS), _num(r.attrs, AZI_KEYS)
        if md is None or inc is None:
            continue
        tvd, ns, ew = _num(r.attrs, TVD_KEYS), _num(r.attrs, NS_KEYS), _num(r.attrs, EW_KEYS)
        out.append(
            SurveyStation(
                md=md * depth_ft,
                inc=inc,
                azi=azi,
                tvd=None if tvd is None else tvd * depth_ft,
                ns=None if ns is None else ns * depth_ft,
                ew=None if ew is None else ew * depth_ft,
                dls=_num(r.attrs, DLS_KEYS),
                vs=None if _num(r.attrs, VS_KEYS) is None else _num(r.attrs, VS_KEYS) * depth_ft,  # type: ignore[operator]
            )
        )
    out.sort(key=lambda s: s.md)
    return out


def _group_stations(edm: EdmFile) -> dict[str, list[tuple[str, str, list[_Row]]]]:
    """Group station rows by wellbore id.

    Returns {wellbore_id: [(kind, group_id, rows), ...]} where kind is
    "definitive", "plan" or "survey".
    """
    designs = edm.by_id("CD_DESIGN", "design_id")
    headers = edm.by_id("SURVEY_HEADER", "survey_header_id")
    def_headers = edm.by_id("DEFINITIVE_SURVEY_HEADER", "def_survey_header_id")
    groups: dict[tuple[str, str, str], list[_Row]] = defaultdict(list)
    for r in edm.rows("STATION"):
        a = r.attrs
        if _get(a, MD_KEYS) is None or _get(a, INC_KEYS) is None:
            continue
        if "DEFINITIVE" in r.tag:
            kind, gid = "definitive", a.get("def_survey_header_id") or a.get("design_id") or ""
        elif "PLAN" in r.tag or ("design_id" in a and "survey_header_id" not in a):
            kind, gid = "plan", a.get("design_id") or a.get("plan_id") or ""
        else:
            kind, gid = "survey", a.get("survey_header_id") or a.get("design_id") or ""
        wb = a.get("wellbore_id")
        if not wb and kind == "definitive" and gid in def_headers:
            wb = def_headers[gid].attrs.get("wellbore_id")
        if not wb and gid in headers:
            wb = headers[gid].attrs.get("wellbore_id")
        if not wb and a.get("design_id") in designs:
            wb = designs[a["design_id"]].attrs.get("wellbore_id")
        if a.get("design_id") in designs and kind == "survey":
            phase = (designs[a["design_id"]].attrs.get("phase") or "").upper()
            if "PLAN" in phase or "PROTO" in phase:
                kind = "plan"
        groups[(wb or "", kind, gid)].append(r)
    out: dict[str, list[tuple[str, str, list[_Row]]]] = defaultdict(list)
    for (wb, kind, gid), rows in groups.items():
        out[wb].append((kind, gid, rows))
    return out


def parse_edm(data: bytes, opts: Optional[ParseOptions] = None) -> list[PlannedWell]:
    """Parse a Compass EDM XML export into planned wells with NAD83 paths."""
    opts = opts or ParseOptions()
    edm = load_edm(data)
    wells = edm.rows("CD_WELL")
    if not wells:
        raise EdmError("No CD_WELL rows found. Check that the file is a Compass EDM XML export.")
    sites = edm.by_id("CD_SITE", "site_id")
    wellbores = edm.rows("CD_WELLBORE")
    designs = edm.by_id("CD_DESIGN", "design_id")
    datum = opts.datum or _detect_datum(edm)
    epsg = opts.projected_epsg or _detect_epsg(edm)
    north_ref = opts.north_reference or _detect_north_ref(edm) or NorthReference.TRUE
    groups = _group_stations(edm)

    planned: list[PlannedWell] = []
    for w in wells:
        warnings: list[str] = []
        wid = w.attrs.get("well_id", "")
        site = sites.get(w.attrs.get("site_id", ""))
        lat, lon, loc_datum = _surface_location(w, site, epsg, datum, warnings)
        if loc_datum is None:
            loc_datum = Datum.NAD83
            warnings.append("Datum not found in the file. Assumed NAD83. Select the datum on upload to change this.")
        surface, notes = geo.to_nad83(lat, lon, loc_datum)
        warnings.extend(notes)
        nad27 = LatLon(lat=lat, lon=lon) if loc_datum == Datum.NAD27 else geo.nad83_to_nad27(surface)
        name = _get(w.attrs, WELL_NAME_KEYS) or wid

        my_wbs = [wb for wb in wellbores if wb.attrs.get("well_id") == wid] or [
            _Row("CD_WELLBORE", {"wellbore_id": "", "wellbore_name": name})
        ]
        for wb in my_wbs:
            wbid = wb.attrs.get("wellbore_id", "")
            cands = groups.get(wbid) or (groups.get("") if len(wells) == 1 else None) or []
            if not cands:
                continue
            order = {"plan": 0, "definitive": 1, "survey": 2} if opts.prefer == "plan" else {"definitive": 0, "plan": 1, "survey": 2}
            cands.sort(key=lambda c: (order[c[0]], -len(c[2])))
            kind, gid, rows = cands[0]
            stations = _stations_from(rows, opts.depth_unit_ft)
            if len(stations) < 2:
                continue
            conv = opts.convergence_deg
            if conv is None and north_ref == NorthReference.GRID:
                conv = _convergence_from_file(site, w)
                if conv is None and epsg:
                    conv = geo.grid_convergence(lat, lon, epsg)
                if conv is None:
                    warnings.append("Grid north selected but no convergence found. Used 0 deg.")
                    conv = 0.0
            survey = Survey(
                survey_type=SurveyType.PLAN if kind == "plan" else SurveyType.DIRECTIONAL,
                stations=stations,
                north_reference=north_ref,
                source="EDM",
            )
            path = _path_latlon(surface, stations, north_ref, conv or 0.0)
            design_name = designs.get(gid).attrs.get("design_name", "") if gid in designs else gid
            planned.append(
                PlannedWell(
                    name=name if len(my_wbs) == 1 else f"{name} / {wb.attrs.get('wellbore_name', wbid)}",
                    wellbore=wb.attrs.get("wellbore_name", wbid),
                    design=design_name,
                    surface_nad83=surface,
                    surface_nad27=nad27,
                    north_reference=north_ref,
                    grid_convergence_deg=conv,
                    survey=survey,
                    path_nad83=path,
                    warnings=warnings,
                )
            )
    if not planned:
        raise EdmError("Found wells but no survey or plan stations with MD and inclination.")
    return planned


def _convergence_from_file(site: Optional[_Row], well: _Row) -> Optional[float]:
    for r in (well, site):
        if r is None:
            continue
        for k, v in r.attrs.items():
            if "convergence" in k:
                try:
                    return float(v)
                except ValueError:
                    continue
    return None


def _path_latlon(surface: LatLon, stations: list[SurveyStation], north_ref: NorthReference, conv: float) -> list[LatLon]:
    """Place each station on the map. Offsets are rotated to true north first."""
    if all(s.ns is not None and s.ew is not None for s in stations):
        ne = [(s.ns, s.ew) for s in stations]
    else:
        tmp = [s.model_copy() for s in stations]
        if any(s.azi is None for s in tmp):
            return [surface]
        compute_positions(tmp, tie_tvd=tmp[0].tvd if tmp[0].tvd is not None else tmp[0].md, tie_ns=0.0, tie_ew=0.0)
        ne = [(s.calc_ns, s.calc_ew) for s in tmp]
    if north_ref == NorthReference.GRID and conv:
        c = math.radians(conv)
        # Grid-referenced offsets: rotate by the convergence to true north.
        ne = [(n * math.cos(c) - e * math.sin(c), n * math.sin(c) + e * math.cos(c)) for n, e in ne]  # type: ignore[operator]
    return geo.offsets_to_latlon(surface, ne)  # type: ignore[arg-type]
