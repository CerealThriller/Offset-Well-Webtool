"""Minimum curvature calculations and QC checks for survey tables.

The QC step is the main defense against OCR and vision errors. The tool
recalculates TVD, N/S and E/W from MD, inclination and azimuth, then compares
the result to the values that the scanned report shows. A misread digit in
any column makes the two sets disagree from that station on.
"""

from __future__ import annotations

import math
from typing import Iterable

from ..models import Survey, SurveyStation, SurveyType

# Tolerances for the QC check. Reports round to 0.01 ft or 0.1 ft, and some
# vendors use a different tie-in method, so the position tolerance grows
# with depth.
POSITION_TOL_FT = 2.0
POSITION_TOL_PER_1000FT = 1.0
MAX_REASONABLE_DLS = 25.0  # deg / 100 ft


def _dogleg(inc1: float, azi1: float, inc2: float, azi2: float) -> float:
    """Return the dogleg angle in radians between two stations (degrees in)."""
    i1, i2 = math.radians(inc1), math.radians(inc2)
    da = math.radians(azi2 - azi1)
    cos_dl = math.cos(i2 - i1) - math.sin(i1) * math.sin(i2) * (1.0 - math.cos(da))
    return math.acos(max(-1.0, min(1.0, cos_dl)))


def min_curve_step(
    md1: float, inc1: float, azi1: float, md2: float, inc2: float, azi2: float
) -> tuple[float, float, float, float]:
    """Return (dTVD, dN, dE, DLS deg/100ft) between two stations."""
    dmd = md2 - md1
    dl = _dogleg(inc1, azi1, inc2, azi2)
    rf = 1.0 if dl < 1e-9 else (2.0 / dl) * math.tan(dl / 2.0)
    i1, i2 = math.radians(inc1), math.radians(inc2)
    a1, a2 = math.radians(azi1), math.radians(azi2)
    half = dmd / 2.0 * rf
    dn = half * (math.sin(i1) * math.cos(a1) + math.sin(i2) * math.cos(a2))
    de = half * (math.sin(i1) * math.sin(a1) + math.sin(i2) * math.sin(a2))
    dtvd = half * (math.cos(i1) + math.cos(i2))
    dls = 0.0 if dmd <= 0 else math.degrees(dl) * 100.0 / dmd
    return dtvd, dn, de, dls


def compute_positions(
    stations: list[SurveyStation],
    tie_tvd: float | None = None,
    tie_ns: float | None = None,
    tie_ew: float | None = None,
) -> None:
    """Fill calc_tvd, calc_ns, calc_ew and calc_dls in place.

    The first station is the tie-in point. If the report gives TVD/NS/EW at
    the tie-in, those values are used. Otherwise the tie-in is a vertical
    hole from surface to the first MD.

    Stations without azimuth (inclination-only surveys) use the tangential
    TVD calculation and do not get N/S or E/W values.
    """
    if not stations:
        return
    first = stations[0]
    tvd = tie_tvd if tie_tvd is not None else (first.tvd if first.tvd is not None else _vertical_tie(first))
    ns = tie_ns if tie_ns is not None else (first.ns if first.ns is not None else 0.0)
    ew = tie_ew if tie_ew is not None else (first.ew if first.ew is not None else 0.0)
    first.calc_tvd, first.calc_ns, first.calc_ew, first.calc_dls = tvd, ns, ew, 0.0
    has_azi = all(s.azi is not None for s in stations)

    for prev, cur in zip(stations, stations[1:]):
        if has_azi:
            dtvd, dn, de, dls = min_curve_step(prev.md, prev.inc, prev.azi, cur.md, cur.inc, cur.azi)  # type: ignore[arg-type]
            ns += dn
            ew += de
            cur.calc_ns, cur.calc_ew = ns, ew
        else:
            # Average angle for inclination only. Direction is unknown.
            dmd = cur.md - prev.md
            avg = math.radians((prev.inc + cur.inc) / 2.0)
            dtvd = dmd * math.cos(avg)
            dls = 0.0 if dmd <= 0 else abs(cur.inc - prev.inc) * 100.0 / dmd
            cur.calc_ns = cur.calc_ew = None
        tvd += dtvd
        cur.calc_tvd = tvd
        cur.calc_dls = dls


def _vertical_tie(first: SurveyStation) -> float:
    return first.md * math.cos(math.radians(first.inc)) if first.inc else first.md


def horizontal_displacement(st: SurveyStation) -> float | None:
    if st.calc_ns is None or st.calc_ew is None:
        return None
    return math.hypot(st.calc_ns, st.calc_ew)


def validate_survey(survey: Survey) -> Survey:
    """Run the QC checks and add flags to each station. Return the survey."""
    st = survey.stations
    st.sort(key=lambda s: s.md)
    _flag_basic(st)
    compute_positions(st)
    for s in st:
        tol = POSITION_TOL_FT + POSITION_TOL_PER_1000FT * s.md / 1000.0
        if s.tvd is not None and s.calc_tvd is not None and abs(s.tvd - s.calc_tvd) > tol:
            s.flags.append(f"TVD differs from calculated by {s.tvd - s.calc_tvd:+.1f} ft")
        if s.ns is not None and s.calc_ns is not None and abs(s.ns - s.calc_ns) > tol:
            s.flags.append(f"N/S differs from calculated by {s.ns - s.calc_ns:+.1f} ft")
        if s.ew is not None and s.calc_ew is not None and abs(s.ew - s.calc_ew) > tol:
            s.flags.append(f"E/W differs from calculated by {s.ew - s.calc_ew:+.1f} ft")
        if s.calc_dls is not None and s.calc_dls > MAX_REASONABLE_DLS:
            s.flags.append(f"Dogleg {s.calc_dls:.1f} deg/100ft is above {MAX_REASONABLE_DLS}")
    _localize_position_flags(st)
    if survey.survey_type == SurveyType.DIRECTIONAL and any(s.azi is None for s in st):
        survey.notes.append("Some stations have no azimuth. Positions are not calculated.")
    return survey


def _flag_basic(st: list[SurveyStation]) -> None:
    seen: set[float] = set()
    for s in st:
        if not 0.0 <= s.inc <= 180.0:
            s.flags.append("Inclination out of range 0-180")
        if s.azi is not None and not 0.0 <= s.azi <= 360.0:
            s.flags.append("Azimuth out of range 0-360")
        if s.md in seen:
            s.flags.append("Duplicate MD")
        seen.add(s.md)
        if s.md < 0:
            s.flags.append("Negative MD")


def _localize_position_flags(st: list[SurveyStation]) -> None:
    """A single misread MD/INC/AZI moves every later calculated position.

    If a position mismatch starts at one station and the reported increments
    after it agree with the calculated increments, keep the flag only on the
    first station and note that later rows inherit the offset.
    """
    first_bad = None
    for idx, s in enumerate(st):
        if any(f.startswith(("TVD differs", "N/S differs", "E/W differs")) for f in s.flags):
            first_bad = idx
            break
    if first_bad is None or first_bad + 1 >= len(st):
        return
    consistent_after = True
    for prev, cur in zip(st[first_bad:], st[first_bad + 1 :]):
        for rep_a, rep_b, cal_a, cal_b in (
            (prev.tvd, cur.tvd, prev.calc_tvd, cur.calc_tvd),
            (prev.ns, cur.ns, prev.calc_ns, cur.calc_ns),
            (prev.ew, cur.ew, prev.calc_ew, cur.calc_ew),
        ):
            if None in (rep_a, rep_b, cal_a, cal_b):
                continue
            if abs((rep_b - rep_a) - (cal_b - cal_a)) > POSITION_TOL_FT:  # type: ignore[operator]
                consistent_after = False
                break
        if not consistent_after:
            break
    if consistent_after:
        for s in st[first_bad + 1 :]:
            s.flags = [f for f in s.flags if not f.startswith(("TVD differs", "N/S differs", "E/W differs"))]
        st[first_bad].flags.append("Later rows carry this offset; check this row and the row above it")


def stations_from_rows(rows: Iterable[dict]) -> list[SurveyStation]:
    out = []
    for r in rows:
        if r.get("md") is None or r.get("inc") is None:
            continue
        out.append(SurveyStation(**{k: r.get(k) for k in ("md", "inc", "azi", "tvd", "ns", "ew", "vs", "dls", "disp")}))
    return out
