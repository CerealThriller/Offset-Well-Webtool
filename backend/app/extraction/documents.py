"""Turn PDF and TIFF documents into page images for the vision model."""

from __future__ import annotations

import io
from dataclasses import dataclass

import pymupdf
from PIL import Image, ImageSequence

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


JPEG_QUALITY = 85


def as_pdf(data: bytes) -> bytes:
    """Return the document as PDF bytes. A PDF comes back unchanged.

    TIFF, PNG and JPEG pages become PDF pages, one page per TIFF frame.
    Pillow decodes the image, because it reads more TIFF compression
    types (old-style JPEG, CCITT). Black-and-white pages are stored
    lossless, other pages as JPEG, so the copy stays near the original
    size. If Pillow fails, PyMuPDF converts the file.

    The output is the same for the same input, so the store keeps one copy.
    """
    if _filetype(data) == "pdf":
        return data
    try:
        return _assemble(_frames(data))
    except Exception:
        with pymupdf.open(stream=data, filetype=_filetype(data)) as img:
            converted = img.convert_to_pdf()
        with pymupdf.open("pdf", converted) as pdf:
            return pdf.tobytes(garbage=3, deflate=True, no_new_id=True)


def _frames(data: bytes) -> list[tuple[bytes, float, float]]:
    """Return (encoded image, width pt, height pt) for each frame."""
    out = []
    with Image.open(io.BytesIO(data)) as im:
        for frame in ImageSequence.Iterator(im):
            dpi = frame.info.get("dpi") or im.info.get("dpi") or (200, 200)
            xdpi, ydpi = (float(dpi[0]) or 200.0, float(dpi[1]) or 200.0)
            buf = io.BytesIO()
            if frame.mode == "1":
                frame.save(buf, "PNG", optimize=True)
            else:
                img = frame.convert("L" if frame.mode in ("L", "LA", "I;16", "I") else "RGB")
                img.save(buf, "JPEG", quality=JPEG_QUALITY)
            out.append((buf.getvalue(), frame.width * 72.0 / xdpi, frame.height * 72.0 / ydpi))
    if not out:
        raise ValueError("no frames")
    return out


def _assemble(frames: list[tuple[bytes, float, float]]) -> bytes:
    doc = pymupdf.open()
    for img, w, h in frames:
        page = doc.new_page(width=w, height=h)
        page.insert_image(page.rect, stream=img)
    return doc.tobytes(garbage=3, deflate=True, no_new_id=True)


def page_count(data: bytes) -> int:
    with pymupdf.open("pdf", as_pdf(data)) as doc:
        return doc.page_count


def render_pages(data: bytes, dpi: int = 150, pages: list[int] | None = None) -> list[PageImage]:
    """Render the selected pages (1-based). PDF, TIFF and PNG/JPEG input work."""
    out = []
    with pymupdf.open("pdf", as_pdf(data)) as doc:
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
