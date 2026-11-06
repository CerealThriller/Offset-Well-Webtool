"""Content-addressed store for every file the tool reads, and for plat PDFs.

Files are public records. The store keeps one copy of each file, named by
the SHA-256 of its content, so many jobs can share a download. Access goes
through a job: the API serves a file only if the job lists it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf

from .config import settings
from .extraction.documents import page_count, render_pages

THUMB_DPI = 110

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "tif": "image/tiff",
    "png": "image/png",
    "jpg": "image/jpeg",
    "bin": "application/octet-stream",
}


def _dir() -> Path:
    d = settings.data_dir / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def extension(data: bytes) -> str:
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tif"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    return "bin"


def file_id_for(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def path(file_id: str) -> Path:
    if not (len(file_id) == 64 and all(c in "0123456789abcdef" for c in file_id)):
        raise ValueError("bad file id")
    for m in _dir().glob(f"{file_id}.*"):
        if not m.name.endswith(".thumb.png"):
            return m
    raise FileNotFoundError(file_id)


def store(data: bytes) -> str:
    """Save a file once. Return its id."""
    fid = file_id_for(data)
    target = _dir() / f"{fid}.{extension(data)}"
    if not target.exists():
        target.write_bytes(data)
    return fid


def content_type(file_id: str) -> str:
    return CONTENT_TYPES.get(path(file_id).suffix.lstrip("."), "application/octet-stream")


def pages(data: bytes) -> int:
    try:
        return page_count(data)
    except Exception:  # not a document format the renderer knows
        return 0


def store_pages(data: bytes, page_numbers: list[int]) -> str:
    """Save the given pages (1-based) of a document as one PDF. Return its id."""
    src = pymupdf.open(stream=data, filetype="pdf" if data[:4] == b"%PDF" else None)
    if not src.is_pdf:
        src = pymupdf.open("pdf", src.convert_to_pdf())
    out = pymupdf.open()
    for n in page_numbers:
        if 1 <= n <= src.page_count:
            out.insert_pdf(src, from_page=n - 1, to_page=n - 1)
    return store(out.tobytes(garbage=3, deflate=True))


def thumbnail(file_id: str) -> bytes:
    """First page as PNG (cached)."""
    thumb = _dir() / f"{file_id}.thumb.png"
    if not thumb.exists():
        (page,) = render_pages(path(file_id).read_bytes(), dpi=THUMB_DPI, pages=[1])
        thumb.write_bytes(page.png)
    return thumb.read_bytes()
