"""Keep plats as files for review. The tool does not read plats.

A plat that is a whole file (a Texas "Plat" attachment, an upload) stays
in its original format, TIFF or PDF, with a PDF copy for the browser when
it is TIFF. A plat that is some pages of a larger document (a New Mexico
C-102 in a well file) becomes a PDF of those pages.
"""

from __future__ import annotations

from .. import files
from ..models import Plat

MAX_PLAT_PAGES = 4


def make_plat(data: bytes, pages: list[int], source: str, title: str, source_file_id: str, total_pages: int) -> Plat:
    if pages == list(range(1, total_pages + 1)):
        return Plat(
            file_id=source_file_id or files.store(data),
            view_file_id=files.pdf_copy(data),
            source=source,
            source_file_id=source_file_id,
            title=title,
            pages=pages,
        )
    pages = pages[:MAX_PLAT_PAGES]
    return Plat(
        file_id=files.store_pages(data, pages),
        source=source,
        source_file_id=source_file_id,
        title=title,
        pages=pages,
    )
