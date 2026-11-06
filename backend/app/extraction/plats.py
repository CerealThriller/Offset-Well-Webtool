"""Store plat pages as their own PDF files and read their location data."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

import pymupdf

from ..config import settings
from ..models import Plat, PlatPoint
from . import vision
from .documents import render_pages

log = logging.getLogger(__name__)

PLAT_DPI = 220
THUMB_DPI = 110
MAX_PLAT_PAGES = 4


def _dir() -> Path:
    d = settings.data_dir / "plats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def plat_pdf_path(file_id: str) -> Path:
    if not file_id.isalnum():
        raise ValueError("bad plat id")
    return _dir() / f"{file_id}.pdf"


def plat_png(file_id: str) -> bytes:
    """First page of a stored plat as PNG (cached)."""
    png = _dir() / f"{file_id}.png"
    if not png.exists():
        (page,) = render_pages(plat_pdf_path(file_id).read_bytes(), dpi=THUMB_DPI, pages=[1])
        png.write_bytes(page.png)
    return png.read_bytes()


def store_pages(data: bytes, pages: list[int]) -> str:
    """Save the given pages (1-based) of a document as one PDF. Return its id."""
    src = pymupdf.open(stream=data, filetype="pdf" if data[:4] == b"%PDF" else None)
    if not src.is_pdf:
        src = pymupdf.open("pdf", src.convert_to_pdf())
    out = pymupdf.open()
    for n in pages:
        if 1 <= n <= src.page_count:
            out.insert_pdf(src, from_page=n - 1, to_page=n - 1)
    pdf = out.tobytes(garbage=3, deflate=True)
    file_id = hashlib.sha256(pdf).hexdigest()
    path = plat_pdf_path(file_id)
    if not path.exists():
        path.write_bytes(pdf)
    return file_id


def make_plat(data: bytes, pages: list[int], source: str, title: str, client=None, say=None) -> Plat:
    """Store the plat pages and read their locations with the vision model.

    If the model is not available, the plat is kept unread, so the user
    can still see it.
    """
    say = say or (lambda _m: None)
    pages = pages[:MAX_PLAT_PAGES]
    plat = Plat(file_id=store_pages(data, pages), source=source, title=title, pages=pages)
    images = render_pages(data, dpi=PLAT_DPI, pages=pages)
    for img in images:
        try:
            d = vision.extract_plat(img, client=client)
        except vision.VisionError as e:
            plat.notes.append(f"Not read: {e}")
            say(f"{source} plat page {img.page_no}: {e}")
            break
        if not d.is_plat:
            continue
        _merge(plat, d, img.page_no)
    return plat


def _merge(plat: Plat, d: "vision.PlatPageData", page_no: int) -> None:
    plat.read = True
    if d.as_drilled is not None:
        plat.as_drilled = d.as_drilled if plat.as_drilled is None else (plat.as_drilled or d.as_drilled)
    if d.datum != "unknown" and not plat.datum:
        plat.datum = d.datum
    plat.crs = plat.crs or d.crs
    have = {p.label.upper() for p in plat.points}
    for p in d.points:
        if p.label.upper() not in have:
            plat.points.append(PlatPoint(**p.model_dump()))
    if d.notes:
        plat.notes.append(f"p{page_no}: {d.notes}")


def find_point(plat: Plat, *names: str) -> Optional[PlatPoint]:
    """First point whose label contains one of the names (checked in order)."""
    for name in names:
        for p in plat.points:
            if name in p.label.upper().replace(" ", ""):
                return p
    return None
