"""Turn PDF and TIFF documents into page images for the vision model."""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

MAX_EDGE_PX = 2000  # the vision model downsamples larger images anyway


@dataclass
class PageImage:
    page_no: int  # 1-based
    png: bytes
    text: str  # text layer, empty for scans


def _render(page: "pymupdf.Page", dpi: int) -> bytes:
    zoom = dpi / 72.0
    w, h = page.rect.width * zoom, page.rect.height * zoom
    if max(w, h) > MAX_EDGE_PX:
        zoom *= MAX_EDGE_PX / max(w, h)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY)
    return pix.tobytes("png")


def page_count(data: bytes) -> int:
    with pymupdf.open(stream=data, filetype=_filetype(data)) as doc:
        return doc.page_count


def render_pages(data: bytes, dpi: int = 150, pages: list[int] | None = None) -> list[PageImage]:
    """Render the selected pages (1-based). PDF, TIFF and PNG/JPEG input work."""
    out = []
    with pymupdf.open(stream=data, filetype=_filetype(data)) as doc:
        if not doc.is_pdf:
            doc = pymupdf.open("pdf", doc.convert_to_pdf())
        wanted = pages or list(range(1, doc.page_count + 1))
        for n in wanted:
            if 1 <= n <= doc.page_count:
                page = doc[n - 1]
                out.append(PageImage(page_no=n, png=_render(page, dpi), text=page.get_text("text") or ""))
    return out


def _filetype(data: bytes) -> str:
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    return "pdf"
