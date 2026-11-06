"""Plat storage, plat checks, and plat pages in the pipeline.

The plat values are from the as-completed plat of Scrapfile North #1H
(42-301-34197), tests/fixtures/tx/scrapfile_north_1h_plat.pdf.
"""

import pymupdf
import pytest

from app.extraction import pipeline, plats, vision
from app.extraction.vision import PageKind, PlatPageData, PlatPointOut
from app.models import LatLon, OffsetWell, Plat, PlatPoint
from app.survey.verify import summary, verify_well

SHL_27 = (31.9119857, -103.4936217)
BHL_27 = (31.9401253, -103.4937662)
GIS_SURFACE = LatLon(lat=31.91220483, lon=-103.4940812)  # RRC layer 9
GIS_BOTTOM = LatLon(lat=31.94040683, lon=-103.49434051)  # RRC layer 1


def _plat(datum="NAD27") -> Plat:
    return Plat(
        file_id="x",
        source="plat",
        read=True,
        as_drilled=True,
        datum=datum,
        points=[
            PlatPoint(label="SHL", lat=SHL_27[0], lon=SHL_27[1]),
            PlatPoint(label="BHL/LTP", lat=BHL_27[0], lon=BHL_27[1], md=22189),
        ],
    )


@pytest.fixture
def scrapfile_survey(fixtures, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.settings, "data_dir", tmp_path)
    data = (fixtures / "tx" / "scrapfile_north_1h_mwd.pdf").read_bytes()
    return pipeline.extract_surveys(data, "mwd")


def _well(surveys, plat):
    return OffsetWell(
        state="TX", api="42-301-34197", surface_nad83=GIS_SURFACE, bottom_hole_nad83=GIS_BOTTOM,
        surveys=surveys, plats=[plat],
    )


def test_survey_convergence_from_report(scrapfile_survey):
    assert scrapfile_survey[0].grid_convergence_deg == pytest.approx(-1.63)


def test_checks_pass_on_real_well(scrapfile_survey):
    checks = {c.item: c for c in verify_well(_well(scrapfile_survey, _plat()))}
    assert checks["Bottom hole: plat vs survey end"].difference_ft < 5
    assert checks["Surface hole: plat vs state GIS"].ok
    assert checks["Bottom hole: plat vs state GIS"].ok
    assert checks["Bottom-hole MD: plat vs last survey"].difference_ft == pytest.approx(1.0)
    assert summary(list(checks.values())) == "ok"


def test_grid_rotation_matters(scrapfile_survey):
    scrapfile_survey[0].grid_convergence_deg = None
    checks = {c.item: c for c in verify_well(_well(scrapfile_survey, _plat()))}
    c = checks["Bottom hole: plat vs survey end"]
    assert not c.ok and c.difference_ft > 250  # 10,234 ft x sin(1.63 deg)
    assert "convergence" in c.note


def test_unknown_datum_is_resolved(scrapfile_survey):
    checks = verify_well(_well(scrapfile_survey, _plat(datum=None)))
    assert "NAD27 fits" in checks[0].note
    assert checks[0].ok


def test_wrong_datum_hint(scrapfile_survey):
    # The plat says NAD83, but the numbers are NAD27: about 150 ft off.
    c = verify_well(_well(scrapfile_survey, _plat(datum="NAD83")))[0]
    assert not c.ok
    assert "NAD27/NAD83 shift" in c.note


def test_store_and_render(fixtures, tmp_path, monkeypatch):
    monkeypatch.setattr(plats.settings, "data_dir", tmp_path)
    data = (fixtures / "tx" / "scrapfile_north_1h_plat.pdf").read_bytes()
    fid = plats.store_pages(data, [1])
    assert plats.plat_pdf_path(fid).exists()
    assert plats.plat_png(fid)[:8] == b"\x89PNG\r\n\x1a\n"
    with pytest.raises(ValueError):
        plats.plat_pdf_path("../etc")


def test_plat_without_api_is_kept_unread(fixtures, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.settings, "data_dir", tmp_path)

    def no_api(page, client=None):
        raise vision.VisionError("No Claude API credentials.")

    monkeypatch.setattr(vision, "extract_plat", no_api)
    data = (fixtures / "tx" / "scrapfile_north_1h_plat.pdf").read_bytes()
    res = pipeline.process_document(data, "plat-url", title="Plat", is_plat=True)
    (p,) = res.plats
    assert not p.read and p.notes[0].startswith("Not read")
    assert plats.plat_pdf_path(p.file_id).exists()
    # The failed read is not cached, so a later run with a key reads it.
    assert not list((tmp_path / "extract_cache").glob("*.json"))
    w = OffsetWell(state="TX", api="1", surface_nad83=GIS_SURFACE, plats=[p])
    assert verify_well(w)[0].plat_value == "not read"


def test_classified_plat_page(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.settings, "data_dir", tmp_path)
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    data = doc.tobytes()
    monkeypatch.setattr(vision, "classify_pages", lambda pages, client=None: {1: PageKind.OTHER, 2: PageKind.PLAT, 3: PageKind.OTHER})
    monkeypatch.setattr(
        vision,
        "extract_plat",
        lambda page, client=None: PlatPageData(
            is_plat=True, as_drilled=False, datum="NAD83", points=[PlatPointOut(label="SHL", lat=32.3, lon=103.7)]
        ),
    )
    res = pipeline.process_document(data, "wellfile.pdf")
    (p,) = res.plats
    assert p.pages == [2] and p.read and p.datum == "NAD83" and not p.as_drilled
    w = OffsetWell(state="NM", api="1", surface_nad83=LatLon(lat=32.3, lon=-103.7), plats=[p])
    # The sign of a west longitude is fixed, so the check passes.
    assert verify_well(w)[0].ok


def test_text_layer_plat():
    page = vision.PageImage(page_no=1, png=b"", text="State of New Mexico Form C-102 " + "WELL LOCATION AND ACREAGE " * 5)
    assert vision.text_layer_kind(page) == PageKind.PLAT
