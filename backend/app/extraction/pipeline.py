"""Document bytes in, validated surveys and plats out."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Callable, Optional

from pydantic import BaseModel, Field

from ..config import settings
from ..models import NorthReference, Plat, Survey, SurveyStation, SurveyType
from ..survey.mincurve import validate_survey
from . import plats, vision
from .documents import page_count, render_pages
from .text_tables import TEXT_LAYER_NOTE, grid_convergence_from_text, parse_text_pdf

log = logging.getLogger(__name__)

M_TO_FT = 3.280839895013123
CLASSIFY_DPI = 90
EXTRACT_DPI = 200
CACHE_VERSION = 2


def _cache_path(data: bytes):
    h = hashlib.sha256(data).hexdigest()
    p = settings.data_dir / "extract_cache" / f"{h}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class DocumentResult(BaseModel):
    surveys: list[Survey] = Field(default_factory=list)
    plats: list[Plat] = Field(default_factory=list)


def extract_surveys(data: bytes, source: str, progress: Optional[Callable[[str], None]] = None, client=None) -> list[Survey]:
    return process_document(data, source, progress=progress, client=client).surveys


def process_document(
    data: bytes,
    source: str,
    *,
    title: str = "",
    is_plat: bool = False,
    progress: Optional[Callable[[str], None]] = None,
    client=None,
) -> DocumentResult:
    """Find and read every survey table and plat in one document.

    is_plat: the state labels this document as a plat (Texas "Plat"
    attachment), so page classification is not needed.

    Results are cached by the document's hash, so the same file from a
    second user's job costs nothing.
    """
    cpath = _cache_path(data)
    if cpath.exists():
        cached = json.loads(cpath.read_text())
        if isinstance(cached, dict) and cached.get("version") == CACHE_VERSION:
            return DocumentResult.model_validate(cached["result"])
    say = progress or (lambda _m: None)
    n = page_count(data)
    if n > settings.max_pages_per_well:
        say(f"{source}: {n} pages, only the first {settings.max_pages_per_well} were checked")
        n = settings.max_pages_per_well
    result = DocumentResult()
    if is_plat:
        say(f"{source}: reading the plat")
        result.plats.append(plats.make_plat(data, list(range(1, n + 1)), source, title, client=client, say=say))
        _save(cpath, result)
        return result

    say(f"{source}: classifying {n} pages")
    thumbs = render_pages(data, dpi=CLASSIFY_DPI, pages=list(range(1, n + 1)))
    kinds = vision.classify_pages(thumbs, client=client)
    survey_pages = sorted(p for p, k in kinds.items() if k in (vision.PageKind.DIRECTIONAL, vision.PageKind.INCLINATION))
    plat_pages = sorted(p for p, k in kinds.items() if k == vision.PageKind.PLAT)
    if survey_pages:
        # Pages with a text layer: read the numbers directly. Exact and free.
        from_text = parse_text_pdf(data, survey_pages) if data[:4] == b"%PDF" else {}
        need_vision = [h for h in survey_pages if h not in from_text]
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
        result.surveys = merge_pages(sorted(extracted, key=lambda x: x[0]), source)
        conv = grid_convergence_from_text(data, survey_pages) if data[:4] == b"%PDF" else None
        for s in result.surveys:
            if s.grid_convergence_deg is None and conv is not None:
                s.grid_convergence_deg = conv
    for group in _consecutive(plat_pages):
        say(f"{source}: reading plat on page(s) {', '.join(map(str, group))}")
        result.plats.append(plats.make_plat(data, group, source, title or "plat", client=client, say=say))
    _save(cpath, result)
    return result


def _consecutive(pages: list[int]) -> list[list[int]]:
    groups: list[list[int]] = []
    for p in pages:
        if groups and p == groups[-1][-1] + 1 and len(groups[-1]) < plats.MAX_PLAT_PAGES:
            groups[-1].append(p)
        else:
            groups.append([p])
    return groups


def _save(cpath, result: DocumentResult) -> None:
    # Do not cache a result that failed only because the API was not available.
    if any(n.startswith("Not read") for p in result.plats for n in p.notes):
        return
    cpath.write_text(json.dumps({"version": CACHE_VERSION, "result": result.model_dump(mode="json")}))


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
            cur.grid_convergence_deg = d.grid_convergence
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
