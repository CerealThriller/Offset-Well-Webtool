"""Document bytes in, validated surveys out."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Callable, Optional

from ..config import settings
from ..models import NorthReference, Survey, SurveyStation, SurveyType
from ..survey.mincurve import validate_survey
from . import vision
from .documents import page_count, render_pages
from .text_tables import TEXT_LAYER_NOTE, parse_text_pdf

log = logging.getLogger(__name__)

M_TO_FT = 3.280839895013123
CLASSIFY_DPI = 90
EXTRACT_DPI = 200


def _cache_path(data: bytes):
    h = hashlib.sha256(data).hexdigest()
    p = settings.data_dir / "extract_cache" / f"{h}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def extract_surveys(
    data: bytes,
    source: str,
    progress: Optional[Callable[[str], None]] = None,
    client=None,
) -> list[Survey]:
    """Find and read every survey table in one document.

    Results are cached by the document's hash, so the same file from a
    second user's job costs nothing.
    """
    cpath = _cache_path(data)
    if cpath.exists():
        return [Survey.model_validate(s) for s in json.loads(cpath.read_text())]
    say = progress or (lambda _m: None)
    n = page_count(data)
    if n > settings.max_pages_per_well:
        say(f"{source}: {n} pages, only the first {settings.max_pages_per_well} were checked")
        n = settings.max_pages_per_well
    say(f"{source}: classifying {n} pages")
    thumbs = render_pages(data, dpi=CLASSIFY_DPI, pages=list(range(1, n + 1)))
    kinds = vision.classify_pages(thumbs, client=client)
    hits = sorted(p for p, k in kinds.items() if k != vision.PageKind.OTHER)
    surveys: list[Survey] = []
    if hits:
        # Pages with a text layer: read the numbers directly. Exact and free.
        from_text = parse_text_pdf(data, hits) if data[:4] == b"%PDF" else {}
        need_vision = [h for h in hits if h not in from_text]
        if from_text:
            say(f"{source}: read {len(from_text)} page(s) from the text layer")
        extracted = list(from_text.items())
        if need_vision:
            say(f"{source}: reading {len(need_vision)} scanned page(s) with the vision model")
            pages = {p.page_no: p for p in render_pages(data, dpi=EXTRACT_DPI, pages=need_vision)}
            for no in need_vision:
                try:
                    extracted.append((no, vision.extract_page(pages[no], kinds[no], client=client)))
                except vision.VisionError as e:
                    say(f"{source} page {no}: {e}")
        surveys = merge_pages(sorted(extracted, key=lambda x: x[0]), source)
    cpath.write_text(json.dumps([s.model_dump(mode="json") for s in surveys]))
    return surveys


def merge_pages(extracted: list[tuple[int, "vision.SurveyPageData"]], source: str) -> list[Survey]:
    """Join consecutive pages of one survey. Start a new survey when MD restarts."""
    surveys: list[Survey] = []
    cur: Optional[Survey] = None
    last_md = -1.0
    for page_no, d in extracted:
        if not d.is_survey_table or not d.rows:
            continue
        kind = SurveyType.INCLINATION_ONLY if d.survey_kind == "inclination_only" else SurveyType.DIRECTIONAL
        scale = M_TO_FT if d.depth_unit == "m" else 1.0
        stations = [
            SurveyStation(
                md=r.md * scale,
                inc=r.inc,
                azi=r.azi,
                tvd=None if r.tvd is None else r.tvd * scale,
                ns=None if r.ns is None else r.ns * scale,
                ew=None if r.ew is None else r.ew * scale,
                vs=None if r.vs is None else r.vs * scale,
                # deg/30 m to deg/100 ft: 100 ft = 30.48 m
                dls=r.dls if scale == 1.0 or r.dls is None else r.dls * 30.48 / 30.0,
                disp=None if r.disp is None else r.disp * scale,
                flags=["Hard to read on the scan"] if r.unclear else [],
            )
            for r in d.rows
        ]
        new_needed = (
            cur is None
            or cur.survey_type != kind
            or (stations and stations[0].md < last_md - 1.0)
        )
        if new_needed:
            cur = Survey(
                survey_type=kind,
                stations=[],
                north_reference=NorthReference(d.north_reference),
                source=source,
                company=d.survey_company,
            )
            surveys.append(cur)
        assert cur is not None
        cur.stations.extend(stations)
        cur.source_pages.append(page_no)
        if d.notes == TEXT_LAYER_NOTE:
            if TEXT_LAYER_NOTE not in cur.notes:
                cur.notes.append(TEXT_LAYER_NOTE)
        elif d.notes:
            cur.notes.append(f"p{page_no}: {d.notes}")
        if d.depth_unit == "m":
            cur.notes.append(f"p{page_no}: depths converted from meters")
        if d.grid_convergence is not None:
            cur.notes.append(f"Grid convergence on report: {d.grid_convergence}")
        if d.vertical_section_azimuth is not None:
            cur.notes.append(f"VS azimuth on report: {d.vertical_section_azimuth}")
        if cur.north_reference == NorthReference.UNKNOWN and d.north_reference != "unknown":
            cur.north_reference = NorthReference(d.north_reference)
        last_md = stations[-1].md if stations else last_md
    for s in surveys:
        _dedupe(s)
        validate_survey(s)
    return surveys


def _dedupe(s: Survey) -> None:
    """Pages often repeat the last row of the previous page. Drop exact repeats."""
    seen: dict[tuple, SurveyStation] = {}
    for st in s.stations:
        seen.setdefault((round(st.md, 2), round(st.inc, 2), None if st.azi is None else round(st.azi, 2)), st)
    s.stations = list(seen.values())
