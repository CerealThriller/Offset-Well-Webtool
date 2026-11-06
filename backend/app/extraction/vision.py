"""Find survey pages in scanned well files and read their tables with Claude.

Two steps keep the cost down:

1. classify_pages: low-resolution images, several pages per request, low
   effort. Returns the pages that hold a directional survey table or an
   inclination (deviation) report.
2. extract_page: one high-resolution page per request, high effort, with a
   strict output schema. The QC step in survey/mincurve.py then checks every
   row.

Pages with a text layer that already shows survey words skip step 1.
"""

from __future__ import annotations

import base64
import logging
import re
from enum import Enum
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, Field

from ..config import settings
from .documents import PageImage

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
CLASSIFY_BATCH = 8

# ---- output schemas ------------------------------------------------------


class PageKind(str, Enum):
    DIRECTIONAL = "directional_survey"
    INCLINATION = "inclination_only"
    PLAT = "plat"
    OTHER = "other"


class PageVerdict(BaseModel):
    page_no: int
    kind: PageKind


class ClassifyResult(BaseModel):
    pages: list[PageVerdict]


class Row(BaseModel):
    md: float = Field(description="Measured depth")
    inc: float = Field(description="Inclination in decimal degrees")
    azi: Optional[float] = Field(None, description="Azimuth in decimal degrees, null if the report has none")
    tvd: Optional[float] = None
    ns: Optional[float] = Field(None, description="North (+) / South (-) offset")
    ew: Optional[float] = Field(None, description="East (+) / West (-) offset")
    vs: Optional[float] = Field(None, description="Vertical section")
    dls: Optional[float] = Field(None, description="Dogleg severity")
    disp: Optional[float] = Field(None, description="Total or accumulated displacement, if reported")
    unclear: bool = Field(False, description="True if any value in this row was hard to read")


class SurveyPageData(BaseModel):
    is_survey_table: bool
    survey_kind: Literal["directional", "inclination_only", "none"]
    depth_unit: Literal["ft", "m", "unknown"]
    north_reference: Literal["true", "grid", "magnetic", "unknown"]
    well_name: Optional[str] = None
    api_number: Optional[str] = None
    operator: Optional[str] = None
    survey_company: Optional[str] = None
    survey_date: Optional[str] = None
    vertical_section_azimuth: Optional[float] = None
    grid_convergence: Optional[float] = None
    magnetic_declination: Optional[float] = None
    rows: list[Row]
    notes: Optional[str] = Field(None, description="Anything the engineer must know, such as a tie-in or a sidetrack")


# ---- prompts ---------------------------------------------------------------

CLASSIFY_SYSTEM = (
    "You review scanned oil and gas well file pages for a drilling engineer. For each page image, decide if "
    "it holds a wellbore survey table. Use these kinds:\n"
    "- directional_survey: a table with measured depth, inclination and azimuth columns (MWD, gyro, "
    "directional survey report, survey calculation sheet).\n"
    "- inclination_only: a deviation or inclination record with depth and inclination but no azimuth "
    "(for example Texas Form W-12, a drift survey, a totco record, a 'deviation report').\n"
    "- plat: a well location plat drawing that shows the well location on the section (Texas plat, New "
    "Mexico Form C-102, Colorado well location plat, as-drilled plat).\n"
    "- other: anything else, including permits, logs, cement reports, and cover letters.\n"
    "A cover page or certification for a survey that has no rows of data is 'other'."
)

EXTRACT_SYSTEM = (
    "You transcribe wellbore survey tables from scanned well records for a drilling engineer. Copy the "
    "numbers exactly as printed. Do not calculate, interpolate, or fill in values that the page does not show. "
    "Rules:\n"
    "- One output row per survey station, in the order on the page. Skip column headers, subtotal lines, and "
    "rows of dashes.\n"
    "- Convert degree-minute or fraction angles to decimal degrees (1 1/4 -> 1.25, 2°30' -> 2.5).\n"
    "- Quadrant bearings: convert to azimuth (N45E -> 45, S30E -> 150, S20W -> 200, N10W -> 350).\n"
    "- N/S and E/W columns: South and West are negative. If the page writes 125.3 S, output -125.3.\n"
    "- Remove thousands separators. Keep the reported precision.\n"
    "- If a value is unreadable, use null for it and set unclear to true for that row.\n"
    "- Read the header for the north reference (true, grid or magnetic), the depth unit, and other fields.\n"
    "- If the page is not a survey table, set is_survey_table to false and return no rows."
)


# ---- client ----------------------------------------------------------------


class VisionError(RuntimeError):
    pass


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(max_retries=4)


def _image_block(png: bytes) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(png).decode()},
    }


def _parse(client: anthropic.Anthropic, *, model: str, effort: str, system: str, content: list, schema, max_tokens: int):
    try:
        resp = client.beta.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=schema,
            output_config={"effort": effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
    except anthropic.RateLimitError as e:
        raise VisionError(f"Rate limited by the Claude API: {e}") from e
    except anthropic.APIStatusError as e:
        raise VisionError(f"Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise VisionError(f"Cannot reach the Claude API: {e}") from e
    except TypeError as e:
        if "authentication" in str(e).lower():
            raise VisionError("No Claude API credentials. Set ANTHROPIC_API_KEY to read scanned pages.") from e
        raise
    if resp.stop_reason == "refusal":
        raise VisionError("The model declined this page.")
    if resp.stop_reason == "max_tokens":
        raise VisionError("The output reached max_tokens. The page has too many rows for one request.")
    if resp.parsed_output is None:
        raise VisionError("The model returned no structured output.")
    return resp.parsed_output


# ---- step 1 ----------------------------------------------------------------

_TEXT_SURVEY = re.compile(r"\b(AZIMUTH|AZM|INCLINATION|INCL?\b|DEVIATION|DOGLEG|MEASURED\s+DEPTH)", re.I)


_TEXT_PLAT = re.compile(r"\bC-102\b|ACREAGE DEDICATION PLAT|WELL LOCATION PLAT|AS[- ]DRILLED PLAT", re.I)


def text_layer_kind(page: PageImage) -> Optional[PageKind]:
    """Decide from the text layer alone, when the page has one."""
    t = page.text
    if len(t.strip()) < 80:
        return None  # a scan, no usable text
    if _TEXT_PLAT.search(t):
        return PageKind.PLAT
    hits = {m.group(1).upper() for m in _TEXT_SURVEY.finditer(t)}
    numbers = len(re.findall(r"\d+\.\d+", t))
    if numbers < 20:
        return PageKind.OTHER
    if any(h.startswith("AZ") for h in hits):
        return PageKind.DIRECTIONAL
    if hits & {"INCLINATION", "INCL", "INC", "DEVIATION"}:
        return PageKind.INCLINATION
    return PageKind.OTHER


def classify_pages(pages: list[PageImage], client: Optional[anthropic.Anthropic] = None) -> dict[int, PageKind]:
    """Return {page_no: kind} for every page."""
    out: dict[int, PageKind] = {}
    todo = []
    for p in pages:
        k = text_layer_kind(p)
        if k is None:
            todo.append(p)
        else:
            out[p.page_no] = k
    if not todo:
        return out
    client = client or _client()
    for i in range(0, len(todo), CLASSIFY_BATCH):
        batch = todo[i : i + CLASSIFY_BATCH]
        content: list = []
        for p in batch:
            content.append({"type": "text", "text": f"Page {p.page_no}:"})
            content.append(_image_block(p.png))
        content.append({"type": "text", "text": "Classify each page above. Return one entry per page number."})
        res: ClassifyResult = _parse(
            client,
            model=settings.classify_model,
            effort=settings.classify_effort,
            system=CLASSIFY_SYSTEM,
            content=content,
            schema=ClassifyResult,
            max_tokens=4000,
        )
        for v in res.pages:
            out[v.page_no] = v.kind
        for p in batch:
            out.setdefault(p.page_no, PageKind.OTHER)
    return out


# ---- step 2 ----------------------------------------------------------------


def extract_page(page: PageImage, kind: PageKind, client: Optional[anthropic.Anthropic] = None) -> SurveyPageData:
    client = client or _client()
    hint = (
        "This page was classified as a directional survey."
        if kind == PageKind.DIRECTIONAL
        else "This page was classified as an inclination-only (deviation) record. Azimuth is null."
    )
    content = [_image_block(page.png), {"type": "text", "text": f"{hint} Transcribe the survey table."}]
    return _parse(
        client,
        model=settings.vision_model,
        effort=settings.extract_effort,
        system=EXTRACT_SYSTEM,
        content=content,
        schema=SurveyPageData,
        max_tokens=16000,
    )

