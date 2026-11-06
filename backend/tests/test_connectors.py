from app.connectors.base import looks_like_survey
from app.connectors.colorado import parse_docs_page
from app.connectors.new_mexico import parse_well_file_page
from app.connectors.registry import catalog, states_for_area
from app.connectors.texas import parse_packet_attachments, parse_packet_list
from shapely.geometry import box


def test_survey_keywords():
    assert looks_like_survey("Directional Survey - MWD")
    assert looks_like_survey("W-12")
    assert looks_like_survey("sub-surface directional survey")
    assert not looks_like_survey("Directional Survey - Certification")
    assert not looks_like_survey("Survey Plat")
    assert not looks_like_survey("W-15")


def test_texas_search_page(fixtures):
    packets = parse_packet_list((fixtures / "tx" / "search_result.html").read_text())
    assert len(packets) == 1
    p = packets[0]
    assert p["operator"] == "EOG RESOURCES, INC."
    assert p["lease"] == "RUSTLER A UNIT"
    assert p["href"].startswith("?packetSummaryId=177604")


def test_texas_packet_page(fixtures):
    docs = parse_packet_attachments((fixtures / "tx" / "packet.html").read_text())
    assert [d.title for d in docs] == ["W-15", "P-12", "GAU LETTER", "SWR 13 Letter"]
    assert all(d.url.startswith("https://webapps.rrc.state.tx.us/dpimages/") for d in docs)


def test_new_mexico_well_file(fixtures):
    docs = parse_well_file_page((fixtures / "nm" / "well_file.html").read_text())
    assert len(docs) == 15
    assert docs[0].url.endswith("30-015-27914_40593.pdf")
    assert docs[0].date == "4/29/2003"


def test_colorado_docs(fixtures):
    docs = parse_docs_page((fixtures / "co" / "docs.html").read_text())
    surveys = [d for d in docs if d.likely_survey]
    assert len(surveys) == 1
    assert surveys[0].title == "sub-surface directional survey"
    assert surveys[0].access == "manual"


def test_registry():
    codes = {s["code"]: s["status"] for s in catalog()}
    assert codes["TX"] == codes["NM"] == codes["CO"] == "available"
    assert codes["OK"] == "planned"
    # A box on the TX/NM line near Loving County touches both.
    assert set(states_for_area(box(-103.2, 31.9, -102.9, 32.1))) == {"TX", "NM"}
