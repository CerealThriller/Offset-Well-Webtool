"""Read survey tables from PDF pages that have a text layer.

Many recent survey attachments are printed from Compass or a vendor system
to PDF, so the numbers are text. Reading them directly is exact and free.
The parser finds the column headers by keyword, groups the header words
into columns by x position, and assigns each number in a data line to the
nearest column.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import pymupdf

from .vision import Row, SurveyPageData

TEXT_LAYER_NOTE = "Read from the PDF text layer (no OCR)"
HEADER_LINE_GAP = 8.0  # points between header text lines
NUM = re.compile(r"^\(?-?[\d,]*\.?\d+\)?[NSEW]?$")

# Checked in order. Each pattern matches whole words of the stacked header text.
COLUMN_RULES: list[tuple[str, str]] = [
    ("vs", r"\bVERTICAL SECTION\b|\bV\.?S\.?\b|\bSECTION\b"),
    ("tvd", r"\bVERTICAL DEPTH\b|\bTVD\b|\bTRUE VERTICAL\b"),
    ("dls", r"\bDOGLEG\b|\bDLS\b|\bD\.L\.S\.?"),
    ("md", r"\bMEASURED\b|\bMD\b|\bM\.D\.|^DEPTH\b"),
    ("inc", r"\bINCLINATION\b|\bINCL?\b|\bANGLE\b|\bDRIFT\b"),
    ("azi", r"\bAZIMUTH\b|\bAZI\b|\bAZM\b|\bDIRECTION\b|\bBEARING\b"),
    ("ns", r"N/-?S\b|\bN/S\b|\bNORTH\b|\bN\(\+\)|\bLATITUDE\b"),
    ("ew", r"E/-?W\b|\bE/W\b|\bEAST\b|\bE\(\+\)|\bDEPARTURE\b"),
]


@dataclass
class _Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2


def _num(text: str) -> Optional[float]:
    t = text.replace(",", "")
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    hemi = t[-1] if t and t[-1] in "NSEW" else ""
    t = t.rstrip("NSEW")
    try:
        v = float(t)
    except ValueError:
        return None
    if neg or hemi in ("S", "W"):
        v = -v
    return v


def _lines(words: list[_Word], tol: float = 3.0) -> list[list[_Word]]:
    words = sorted(words, key=lambda w: (round(w.y1), w.x0))
    lines: list[list[_Word]] = []
    for w in words:
        if lines and abs(lines[-1][0].y1 - w.y1) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w.x0) for line in lines]


def _is_data_line(line: list[_Word]) -> bool:
    return len(line) >= 3 and all(NUM.match(w.text) for w in line)


def _classify(stack_text: str) -> str:
    t = re.sub(r"\s+", " ", stack_text.upper()).strip()
    if re.search(r"\bBUILD\b|\bTURN\b|\bCLOSURE\b|\bTOOL\b|\bCOMMENT", t):
        return "skip"
    for key, pattern in COLUMN_RULES:
        if re.search(pattern, t):
            return key
    return "skip"


def _columns(header_words: list[_Word]) -> list[tuple[float, float, str]]:
    """Group header words into columns. Return (x0, x1, key) sorted by x."""
    stacks: list[list[_Word]] = []
    for w in sorted(header_words, key=lambda w: w.xc):
        for s in stacks:
            sx0, sx1 = min(x.x0 for x in s), max(x.x1 for x in s)
            if w.x0 <= sx1 + 2 and w.x1 >= sx0 - 2:
                s.append(w)
                break
        else:
            stacks.append([w])
    cols = []
    for s in stacks:
        s.sort(key=lambda w: (w.y0, w.x0))
        text = " ".join(w.text for w in s if not w.text.startswith("("))
        cols.append((min(w.x0 for w in s), max(w.x1 for w in s), _classify(text)))
    return sorted(cols)


def _header_above(lines: list[list[_Word]], first_data: int) -> list[_Word]:
    """Text lines directly above a data block. A larger gap ends the header."""
    header: list[_Word] = []
    prev_y = lines[first_data][0].y0
    for ln in reversed(lines[max(0, first_data - 7) : first_data]):
        if header and prev_y - ln[0].y1 > HEADER_LINE_GAP:
            break
        header.extend(ln)
        prev_y = ln[0].y0
    return header


def _rows_for_block(lines: list[list[_Word]], start: int, centers: list[tuple[float, str]]) -> tuple[list[Row], int]:
    rows: list[Row] = []
    i = start
    while i < len(lines):
        ln = lines[i]
        if not _is_data_line(ln):
            # A blank-ish text line inside the table (page footer, comment)
            # ends the block only if no data follows soon.
            if not any(_is_data_line(x) for x in lines[i + 1 : i + 3]):
                break
            i += 1
            continue
        vals: dict[str, float] = {}
        for w in ln:
            v = _num(w.text)
            if v is None:
                continue
            # Numbers are right-aligned under centered headers. Match the
            # number's right edge or center, whichever is nearer.
            key = min(centers, key=lambda c: min(abs(c[0] - w.xc), abs(c[0] - w.x1)))[1]
            vals.setdefault(key, v)
        if "md" in vals and "inc" in vals:
            rows.append(Row(**vals))
        i += 1
    return rows, i


def parse_text_page(page: "pymupdf.Page") -> Optional[SurveyPageData]:
    """Return the survey rows of a page, or None if no table is found."""
    words = [_Word(*w[:5]) for w in page.get_text("words")]
    lines = _lines(words)
    rows: list[Row] = []
    keys: list[str] = []
    i = 0
    while i < len(lines):
        if not _is_data_line(lines[i]) or (i > 0 and _is_data_line(lines[i - 1])):
            i += 1
            continue
        header = _header_above(lines, i)
        if not re.search(r"INC|ANGLE|DRIFT", " ".join(w.text.upper() for w in header)):
            i += 1
            continue
        cols = [c for c in _columns(header) if c[2] != "skip"]
        block_keys = [c[2] for c in cols]
        if "md" not in block_keys or "inc" not in block_keys:
            i += 1
            continue
        block, i = _rows_for_block(lines, i, [((c[0] + c[1]) / 2, c[2]) for c in cols])
        rows.extend(block)
        keys = keys or block_keys
    if len(rows) < 2:
        return None
    up = page.get_text("text").upper()
    north = "grid" if re.search(r"NORTH REFERENCE[:\s]*GRID|GRID\s*NORTH", up) else (
        "true" if re.search(r"NORTH REFERENCE[:\s]*TRUE|TRUE\s*NORTH", up) else "unknown"
    )
    unit = "m" if re.search(r"\(M\)|METRES|METERS", up) and "USFT" not in up and "(FT)" not in up else "ft"
    kind = "directional" if "azi" in keys else "inclination_only"
    return SurveyPageData(
        is_survey_table=True,
        survey_kind=kind,
        depth_unit=unit,
        north_reference=north,
        rows=rows,
        notes=TEXT_LAYER_NOTE,
    )


def parse_text_pdf(data: bytes, pages: list[int]) -> dict[int, SurveyPageData]:
    out = {}
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for n in pages:
            if 1 <= n <= doc.page_count:
                res = parse_text_page(doc[n - 1])
                if res:
                    out[n] = res
    return out


def grid_convergence_from_text(data: bytes, pages: list[int]) -> Optional[float]:
    """Return the grid convergence printed in a report header, for example "Grid Convergence: -1.63 deg"."""
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for n in pages:
            if not 1 <= n <= doc.page_count:
                continue
            lines = _lines([_Word(*w[:5]) for w in doc[n - 1].get_text("words")])
            for ln in lines:
                for i, w in enumerate(ln):
                    if w.text.upper().startswith("CONVERGENCE"):
                        for nxt in ln[i + 1 :]:
                            v = _num(nxt.text)
                            if v is not None and abs(v) < 10:
                                return v
    return None
