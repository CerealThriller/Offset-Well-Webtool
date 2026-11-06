import pymupdf
import pytest

from app.extraction import pipeline, vision
from app.extraction.text_tables import parse_text_pdf
from app.extraction.vision import PageKind, Row, SurveyPageData
from app.models import SurveyType


def test_text_layer_compass_report(fixtures):
    data = (fixtures / "tx" / "scrapfile_north_1h_mwd.pdf").read_bytes()
    pages = parse_text_pdf(data, list(range(1, 9)))
    assert sorted(pages) == [2, 3, 4, 5, 6, 7]
    (survey,) = pipeline.merge_pages(sorted(pages.items()), "test")
    assert survey.survey_type == SurveyType.DIRECTIONAL
    assert survey.north_reference.value == "grid"
    assert len(survey.stations) == 190
    assert survey.flagged_count == 0
    last = survey.stations[-1]
    assert (last.md, last.inc, last.azi, last.tvd, last.ns, last.ew) == (22190.0, 86.6, 359.6, 12409.7, 10234.0, 246.0)
    assert last.calc_tvd == pytest.approx(12409.7, abs=0.1)


def test_full_pipeline_text_pdf_needs_no_api(fixtures, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.settings, "data_dir", tmp_path)
    data = (fixtures / "tx" / "scrapfile_north_1h_mwd.pdf").read_bytes()

    class NoClient:
        def __getattr__(self, name):
            raise AssertionError("the API must not be called for a text PDF")

    surveys = pipeline.extract_surveys(data, "x", client=NoClient())
    assert len(surveys) == 1 and len(surveys[0].stations) == 190


def _scanned_pdf() -> bytes:
    """Three image-only pages, like a scanned well file."""
    doc = pymupdf.open()
    for _ in range(3):
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 50, 50), False)
        pix.clear_with(200)
        page.insert_image(page.rect, pixmap=pix)
    return doc.tobytes()


def test_scanned_pages_use_vision(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.settings, "data_dir", tmp_path)

    def fake_classify(pages, client=None):
        return {p.page_no: (PageKind.DIRECTIONAL if p.page_no in (2, 3) else PageKind.OTHER) for p in pages}

    def fake_extract(page, kind, client=None):
        rows = {
            2: [Row(md=0, inc=0, azi=0, tvd=0, ns=0, ew=0), Row(md=100, inc=0, azi=0, tvd=100, ns=0, ew=0)],
            3: [Row(md=100, inc=0, azi=0, tvd=100, ns=0, ew=0), Row(md=200, inc=1, azi=90, tvd=199.99, ns=0, ew=0.87)],
        }[page.page_no]
        return SurveyPageData(
            is_survey_table=True, survey_kind="directional", depth_unit="ft", north_reference="true", rows=rows
        )

    monkeypatch.setattr(vision, "classify_pages", fake_classify)
    monkeypatch.setattr(vision, "extract_page", fake_extract)
    (s,) = pipeline.extract_surveys(_scanned_pdf(), "scan.pdf")
    assert s.source_pages == [2, 3]
    assert [st.md for st in s.stations] == [0, 100, 200]  # repeated row dropped
    assert s.flagged_count == 0


def test_meters_converted():
    d = SurveyPageData(
        is_survey_table=True, survey_kind="directional", depth_unit="m", north_reference="unknown",
        rows=[Row(md=0, inc=0, azi=0), Row(md=1000, inc=0, azi=0, tvd=1000)],
    )
    (s,) = pipeline.merge_pages([(1, d)], "m")
    assert s.stations[1].md == pytest.approx(3280.84, abs=0.01)


def test_text_layer_kind_ignores_scans():
    assert vision.text_layer_kind(vision.PageImage(page_no=1, png=b"", text="")) is None


def test_plat_pages_are_kept_not_read(tmp_path, monkeypatch):
    """A scanned well file: page 2 is a survey, page 3 is a C-102 plat."""
    monkeypatch.setattr(pipeline.settings, "data_dir", tmp_path)
    monkeypatch.setattr(
        vision, "classify_pages",
        lambda pages, client=None: {1: PageKind.OTHER, 2: PageKind.DIRECTIONAL, 3: PageKind.PLAT},
    )
    monkeypatch.setattr(
        vision, "extract_page",
        lambda page, kind, client=None: SurveyPageData(
            is_survey_table=True, survey_kind="directional", depth_unit="ft", north_reference="true",
            rows=[Row(md=0, inc=0, azi=0), Row(md=100, inc=0, azi=0)],
        ),
    )
    assert not hasattr(vision, "extract_plat")
    data = _scanned_pdf()
    res = pipeline.process_document(data, "wellfile.pdf")
    assert res.survey_pages == [2] and res.plat_pages == [3]
    (plat,) = res.plats
    assert plat.pages == [3] and plat.source_file_id == res.file_id
    assert res.surveys[0].source_file_id == res.file_id

    from app import files
    assert files.path(res.file_id).read_bytes() == data
    with pymupdf.open(files.path(plat.file_id)) as doc:
        assert doc.page_count == 1


def test_file_store_rejects_bad_ids(tmp_path, monkeypatch):
    from app import files

    monkeypatch.setattr(files.settings, "data_dir", tmp_path)
    for bad in ("../etc/passwd", "x" * 64, "A" * 64):
        with pytest.raises(ValueError):
            files.path(bad)
    fid = files.store(b"%PDF-1.4 test")
    assert files.path(fid).suffix == ".pdf"
    assert files.store(b"%PDF-1.4 test") == fid  # one copy
