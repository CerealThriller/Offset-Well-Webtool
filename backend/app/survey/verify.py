"""Check a plat against the state GIS location and the extracted survey.

Checks:
1. Plat surface hole vs. state GIS surface location.
2. Plat bottom hole vs. state GIS bottom hole (when the state publishes one).
3. Plat bottom hole vs. the last survey station, placed from the plat surface hole.
4. Plat bottom-hole MD vs. the last survey MD.

A large difference in check 1 that is close to the NAD27/NAD83 shift
usually means a datum mix-up on one side. The check says so.
"""

from __future__ import annotations

from typing import Optional

from ..extraction.plats import find_point
from ..geo import crs as geo
from ..models import Datum, LatLon, NorthReference, OffsetWell, Plat, PlatCheck, PlatPoint, Survey

SHL_TOL_FT = 50.0
BHL_TOL_FT = 100.0
MD_TOL_FT = 150.0  # the last survey is often above TD; the plat BHL can be at TD

SHL_NAMES = ("SHL", "SURFACE")
BHL_NAMES = ("BHL", "BOTTOM", "LTP", "LASTTAKE")


def _fmt(p: LatLon) -> str:
    return f"{p.lat:.6f}, {p.lon:.6f}"


def _point_nad83(pt: PlatPoint, datum: Optional[str], near: Optional[LatLon]) -> tuple[Optional[LatLon], str]:
    """Plat point in NAD83. If the plat prints no datum, pick the one that lands nearer the GIS point."""
    if pt.lat is None or pt.lon is None:
        return None, ""
    lon = -abs(pt.lon)  # plats print "W 103.49"; the model can drop the sign
    if datum in ("NAD27", "NAD83", "WGS84"):
        p, _ = geo.to_nad83(pt.lat, lon, Datum(datum))
        return p, ""
    as27, _ = geo.to_nad83(pt.lat, lon, Datum.NAD27)
    as83 = LatLon(lat=pt.lat, lon=lon)
    if near is None:
        return as83, "Plat datum not printed. Assumed NAD83."
    if geo.haversine_ft(as27, near) < geo.haversine_ft(as83, near):
        return as27, "Plat datum not printed. NAD27 fits the GIS location better."
    return as83, "Plat datum not printed. NAD83 fits the GIS location better."


def survey_end(origin: LatLon, survey: Survey) -> tuple[Optional[LatLon], Optional[float], str]:
    """Last station of a survey on the map, from the surface hole."""
    if not survey.stations:
        return None, None, ""
    last = survey.stations[-1]
    n = last.ns if last.ns is not None else last.calc_ns
    e = last.ew if last.ew is not None else last.calc_ew
    if n is None or e is None:
        return None, last.md, "Survey has no N/S, E/W values"
    note = ""
    if survey.north_reference == NorthReference.GRID:
        if survey.grid_convergence_deg is None:
            note = "Grid north survey without a printed convergence. Offsets not rotated."
        else:
            n, e = geo.rotate_offsets(n, e, survey.grid_convergence_deg)
    elif survey.north_reference == NorthReference.MAGNETIC:
        note = "Magnetic north survey. Offsets not corrected for declination."
    (p,) = geo.offsets_to_latlon(origin, [(n, e)])
    return p, last.md, note


def best_plat(plats: list[Plat]) -> Optional[Plat]:
    """Prefer an as-drilled plat that was read. Later documents win ties."""
    read = [p for p in plats if p.read and p.points]
    if not read:
        return None
    drilled = [p for p in read if p.as_drilled]
    return (drilled or read)[-1]


def best_survey(surveys: list[Survey]) -> Optional[Survey]:
    """The survey that reaches the deepest MD."""
    with_rows = [s for s in surveys if s.stations]
    return max(with_rows, key=lambda s: s.stations[-1].md) if with_rows else None


def _check(item: str, a: LatLon, a_label: str, b: LatLon, b_label: str, tol: float, note: str) -> PlatCheck:
    d = geo.haversine_ft(a, b)
    return PlatCheck(
        item=item,
        plat_value=f"{a_label}: {_fmt(a)}",
        other_value=f"{b_label}: {_fmt(b)}",
        difference_ft=round(d, 1),
        tolerance_ft=tol,
        ok=d <= tol,
        note=note,
    )


def _datum_hint(plat_pt: LatLon, gis: LatLon, tol: float) -> str:
    """Explain a mismatch that goes away if one side used the other datum."""
    as_if_27, _ = geo.to_nad83(plat_pt.lat, plat_pt.lon, Datum.NAD27)
    as_if_83 = geo.nad83_to_nad27(plat_pt)
    if min(geo.haversine_ft(as_if_27, gis), geo.haversine_ft(as_if_83, gis)) <= tol:
        return " The difference matches the NAD27/NAD83 shift. Check the datum on both sides."
    return ""


def verify_well(w: OffsetWell) -> list[PlatCheck]:
    plat = best_plat(w.plats)
    if plat is None:
        if w.plats:
            return [PlatCheck(item="Plat", plat_value="not read", other_value="", note="; ".join(w.plats[-1].notes))]
        return []
    checks: list[PlatCheck] = []
    shl_pt = find_point(plat, *SHL_NAMES)
    bhl_pt = find_point(plat, *BHL_NAMES)
    shl, shl_note = _point_nad83(shl_pt, plat.datum, w.surface_nad83) if shl_pt else (None, "")
    bhl, bhl_note = _point_nad83(bhl_pt, plat.datum, w.bottom_hole_nad83 or w.surface_nad83) if bhl_pt else (None, "")

    if shl:
        c = _check("Surface hole: plat vs state GIS", shl, "Plat SHL", w.surface_nad83, "GIS", SHL_TOL_FT, shl_note)
        if not c.ok:
            c.note += _datum_hint(shl, w.surface_nad83, SHL_TOL_FT)
        checks.append(c)
    else:
        checks.append(PlatCheck(item="Surface hole: plat vs state GIS", plat_value="no SHL lat/long on plat", other_value=_fmt(w.surface_nad83)))

    if bhl and w.bottom_hole_nad83:
        checks.append(_check("Bottom hole: plat vs state GIS", bhl, "Plat BHL", w.bottom_hole_nad83, "GIS", BHL_TOL_FT, bhl_note))

    survey = best_survey(w.surveys)
    if survey is not None:
        origin = shl or w.surface_nad83
        end, end_md, note = survey_end(origin, survey)
        if not shl:
            note = (note + " Survey placed from the GIS surface location.").strip()
        if bhl and end:
            checks.append(_check("Bottom hole: plat vs survey end", bhl, "Plat BHL", end, f"Survey at MD {end_md:,.0f}", BHL_TOL_FT, note))
        if bhl_pt and bhl_pt.md is not None and end_md is not None:
            diff = abs(bhl_pt.md - end_md)
            checks.append(
                PlatCheck(
                    item="Bottom-hole MD: plat vs last survey",
                    plat_value=f"{bhl_pt.md:,.0f} ft",
                    other_value=f"{end_md:,.0f} ft",
                    difference_ft=round(diff, 1),
                    tolerance_ft=MD_TOL_FT,
                    ok=diff <= MD_TOL_FT,
                    note="The plat MD is often TD. The last survey is often above TD.",
                )
            )
    return checks


def summary(checks: list[PlatCheck]) -> str:
    """Short text for tables: "ok", "2 of 3 ok", or "-"."""
    scored = [c for c in checks if c.ok is not None]
    if not scored:
        return "-" if not checks else "not checked"
    good = sum(1 for c in scored if c.ok)
    return "ok" if good == len(scored) else f"{good} of {len(scored)} ok"
