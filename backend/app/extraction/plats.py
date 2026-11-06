"""Keep plat pages as their own PDF files for review. The tool does not read plats."""

from __future__ import annotations

from .. import files
from ..models import Plat

MAX_PLAT_PAGES = 4


def make_plat(data: bytes, pages: list[int], source: str, title: str, source_file_id: str = "") -> Plat:
    pages = pages[:MAX_PLAT_PAGES]
    return Plat(
        file_id=files.store_pages(data, pages),
        source=source,
        source_file_id=source_file_id,
        title=title,
        pages=pages,
    )
