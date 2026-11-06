import io
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.connectors import registry
from app.connectors.base import StateConnector
from app.main import app
from app.models import LatLon, OffsetWell, SurveyDocument

TX = Path(__file__).parent / "fixtures" / "tx"
SURVEY_URL = "https://example.invalid/dpimages/r/4380385"
PLAT_URL = "https://example.invalid/dpimages/r/4714781"


class FakeTexas(StateConnector):
    """Serves the real Scrapfile North #1H survey and plat. Both need no API call."""

    code, name = "TX", "Texas"

    def __init__(self, http=None):
        pass

    def find_wells(self, area):
        c = area.centroid
        return [
            OffsetWell(state="TX", api="42-301-34197", name="SCRAPFILE NORTH #1H", surface_nad83=LatLon(lat=c.y, lon=c.x)),
            OffsetWell(state="TX", api="42-301-00002", name="PERMIT", surface_nad83=LatLon(lat=c.y, lon=c.x), survey_status="not drilled"),
        ]

    def find_documents(self, well):
        return [
            SurveyDocument(url=SURVEY_URL, title="Directional Survey - MWD", likely_survey=True),
            SurveyDocument(url=PLAT_URL, title="Plat", likely_plat=True),
        ]

    def download(self, doc):
        name = "scrapfile_north_1h_mwd.pdf" if doc.url == SURVEY_URL else "scrapfile_north_1h_plat.tif"
        return (TX / name).read_bytes()


def _wait(client, job_id, status):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == status:
            return job
        time.sleep(0.05)
    raise AssertionError(job["log"])


def _run_job(fixtures, monkeypatch, client):
    monkeypatch.setitem(registry.IMPLEMENTED, "TX", FakeTexas)
    monkeypatch.setitem(registry.IMPLEMENTED, "NM", FakeTexas)
    with open(fixtures / "sample_edm.xml", "rb") as f:
        r = client.post("/api/jobs", files={"file": ("pad.xml", f, "text/xml")}, data={"radius_miles": "0.5", "states": "TX"})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    job = _wait(client, job_id, "ready")
    assert [w["name"] for w in job["offsets"]] == ["SCRAPFILE NORTH #1H"]  # not-drilled well removed
    w = job["offsets"][0]
    assert w["key"] == "TX-42-301-34197"  # the UI needs the key in the JSON
    assert w["survey_status"] == "1 survey document(s), 1 plat(s)"
    assert client.post(f"/api/jobs/{job_id}/extract", json={"keys": [w["key"]]}).status_code == 200
    return job_id, _wait(client, job_id, "done")


def test_job_flow_keeps_files_for_review(fixtures, monkeypatch):
    client = TestClient(app)
    job_id, job = _run_job(fixtures, monkeypatch, client)
    w = job["offsets"][0]
    assert w["survey_status"] == "1 survey(s) extracted, 1 plat(s)"

    # Both downloaded files are kept, with the pages that the tool used.
    files = {f["kind"]: f for f in w["files"]}
    assert files["survey"]["survey_pages"] == [2, 3, 4, 5, 6, 7]
    assert files["survey"]["file_name"] == "Directional_Survey_-_MWD.pdf"
    assert files["plat"]["plat_pages"] == [1]
    assert files["plat"]["file_name"] == "Plat.tif"  # RRC serves this plat as TIFF
    assert files["plat"]["view_file_id"]  # PDF copy for the browser
    assert files["survey"]["view_file_id"] == ""  # already a PDF
    r = client.get(f"/api/jobs/{job_id}/files/{files['plat']['file_id']}")
    assert r.headers["content-type"] == "image/tiff"
    r = client.get(f"/api/jobs/{job_id}/files/{files['plat']['view_file_id']}")
    assert r.headers["content-type"] == "application/pdf"
    (survey,) = w["surveys"]
    assert survey["source_file_id"] == files["survey"]["file_id"]
    (plat,) = w["plats"]
    assert set(plat) >= {"file_id", "title", "pages"} and "points" not in plat  # plats are not read
    # A whole-file plat keeps its original format; the browser gets the PDF copy.
    assert plat["file_id"] == files["plat"]["file_id"]
    assert plat["view_file_id"] == files["plat"]["view_file_id"]

    # The stored copies are the downloaded bytes.
    r = client.get(f"/api/jobs/{job_id}/files/{files['survey']['file_id']}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content == (TX / "scrapfile_north_1h_mwd.pdf").read_bytes()
    assert "inline" in r.headers["content-disposition"]
    r = client.get(f"/api/jobs/{job_id}/files/{files['survey']['file_id']}?download=true")
    assert "attachment" in r.headers["content-disposition"]
    assert client.get(f"/api/jobs/{job_id}/files/{plat['file_id']}").content[:4] == b"II*\x00"  # TIFF as received
    assert client.get(f"/api/jobs/{job_id}/files/{plat['view_file_id']}").content[:4] == b"%PDF"
    assert client.get(f"/api/jobs/{job_id}/files/{plat['file_id']}/thumb.png").content[:4] == b"\x89PNG"
    # A file that is not in the job is not served.
    assert client.get(f"/api/jobs/{job_id}/files/{'0' * 64}").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/files/..%2F..%2Fetc").status_code == 404


def test_package_links_resolve(fixtures, monkeypatch):
    client = TestClient(app)
    job_id, job = _run_job(fixtures, monkeypatch, client)
    r = client.get(f"/api/jobs/{job_id}/package.zip")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    folder = "wells/42-301-34197_SCRAPFILE_NORTH_#1H"
    # The plat as received from RRC (TIFF), its PDF copy for viewing, and the survey.
    assert names == {
        "pad_offset_surveys.xlsx",
        f"{folder}/plat_1.tif",
        f"{folder}/plat_1.pdf",
        f"{folder}/Directional_Survey_-_MWD.pdf",
    }
    assert z.read(f"{folder}/plat_1.tif") == (TX / "scrapfile_north_1h_plat.tif").read_bytes()
    (xlsx,) = [n for n in names if n.endswith(".xlsx")]
    wb = load_workbook(io.BytesIO(z.read(xlsx)))
    ws = wb["42-301-34197 SCRAPFILE NORTH #1"]
    targets = {c.hyperlink.target for row in ws.iter_rows() for c in row if c.hyperlink}
    assert f"{folder}/Directional_Survey_-_MWD.pdf" in targets
    assert f"{folder}/plat_1.tif" in targets
    assert targets <= names  # every link in the workbook opens a file in the package
    values = [c.value for row in ws.iter_rows() for c in row]
    assert "Source files" in values and "Plat checks" not in values
    assert len(ws._images) == 1  # the plat image

    plain = load_workbook(io.BytesIO(client.get(f"/api/jobs/{job_id}/export.xlsx").content))
    assert plain.sheetnames[0] == "Summary"
    assert "PLAN TEST 1H" in plain.sheetnames

    pz = zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{job_id}/plats.zip").content))
    assert pz.namelist() == ["42-301-34197_SCRAPFILE_NORTH_#1H_plat_1.tif"]


def test_upload_is_kept(fixtures, monkeypatch):
    client = TestClient(app)
    job_id, job = _run_job(fixtures, monkeypatch, client)
    key = job["offsets"][0]["key"]
    data = (TX / "scrapfile_north_1h_plat.tif").read_bytes()
    r = client.post(f"/api/jobs/{job_id}/wells/{key}/upload", files={"file": ("my location plat.tif", data, "image/tiff")})
    assert r.status_code == 200
    time.sleep(0.2)
    w = _wait(client, job_id, "done")["offsets"][0]
    up = [f for f in w["files"] if f["kind"] == "upload"]
    assert up and up[0]["file_name"] == "my_location_plat.tif" and up[0]["plat_pages"] == [1]


def test_bad_upload():
    client = TestClient(app)
    r = client.post("/api/jobs", files={"file": ("a.xml", b"<nope/>", "text/xml")})
    assert r.status_code == 400


def test_states_endpoint():
    client = TestClient(app)
    codes = [s["code"] for s in client.get("/api/states").json()]
    assert codes[:3] == ["TX", "NM", "CO"]
