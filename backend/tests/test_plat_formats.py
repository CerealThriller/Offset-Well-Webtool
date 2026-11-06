"""Plats arrive as TIFF or PDF. Both stay in their original format."""

import io

import pymupdf
import pytest
from PIL import Image

from app import files
from app.extraction import documents, pipeline
from app.extraction.documents import as_pdf, page_count, render_pages


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(files.settings, "data_dir", tmp_path)


@pytest.fixture
def tif(fixtures) -> bytes:
    return (fixtures / "tx" / "scrapfile_north_1h_plat.tif").read_bytes()


def _multi_page_tiff(n=3) -> bytes:
    frames = [Image.new("L", (400, 600), color=60 * i) for i in range(n)]
    buf = io.BytesIO()
    frames[0].save(buf, "TIFF", save_all=True, append_images=frames[1:], compression="tiff_lzw")
    return buf.getvalue()


def test_tif_plat_stays_tif(tif):
    res = pipeline.process_document(tif, "plat-url", title="Plat", is_plat=True)
    (plat,) = res.plats
    assert plat.file_id == res.file_id  # the original file, not a converted copy
    assert files.ext_of(plat.file_id) == "tif"
    assert files.path(plat.file_id).read_bytes() == tif
    assert files.ext_of(plat.view_file_id) == "pdf"
    assert files.thumbnail(plat.file_id)[:4] == b"\x89PNG"


def test_pdf_plat_stays_pdf(tif):
    pdf = as_pdf(tif)
    res = pipeline.process_document(pdf, "plat-url", title="Plat", is_plat=True)
    (plat,) = res.plats
    assert plat.file_id == res.file_id
    assert files.ext_of(plat.file_id) == "pdf"
    assert plat.view_file_id == ""  # a PDF opens in the browser as is


def test_pdf_copy_is_small_and_stable(tif):
    a, b = as_pdf(tif), as_pdf(tif)
    assert a == b  # same TIFF, same copy, one stored file
    assert len(a) < 3 * len(tif)  # compressed


def test_multi_page_tiff():
    data = _multi_page_tiff(3)
    assert page_count(data) == 3
    assert [p.page_no for p in render_pages(data, dpi=30)] == [1, 2, 3]
    res = pipeline.process_document(data, "upload: plat.tif", title="plat.tif", is_plat=True)
    assert res.plats[0].pages == [1, 2, 3]
    with pymupdf.open(files.path(res.plats[0].view_file_id)) as doc:
        assert doc.page_count == 3


def test_pymupdf_fallback(tif, monkeypatch):
    def broken(data):
        raise OSError("Pillow cannot read this TIFF")

    monkeypatch.setattr(documents, "_frames", broken)
    pdf = as_pdf(tif)
    assert pdf[:4] == b"%PDF"
    assert page_count(tif) == 1


def test_black_and_white_scan_is_lossless():
    im = Image.new("1", (1700, 2200), color=1)
    for x in range(100, 1600):
        im.putpixel((x, 1000), 0)
    buf = io.BytesIO()
    im.save(buf, "TIFF", compression="group4", dpi=(200, 200))
    pdf = as_pdf(buf.getvalue())
    with pymupdf.open("pdf", pdf) as doc:
        page = doc[0]
        assert round(page.rect.width) == 612 and round(page.rect.height) == 792  # 8.5 x 11 in at 200 dpi
    assert len(pdf) < 50_000
