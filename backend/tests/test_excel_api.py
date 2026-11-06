import io
import time

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import jobs
from app.connectors import registry
from app.connectors.base import StateConnector
from app.main import app
from app.extraction import plats
from app.extraction.pipeline import DocumentResult
from app.models import LatLon, OffsetWell, Plat, PlatPoint, Survey, SurveyDocument, SurveyStation, SurveyType


class FakeTexas(StateConnector):
    code, name = "TX", "Texas"

    def __init__(self, http=None):
        pass

    def find_wells(self, area):
        c = area.centroid
        return [
            OffsetWell(state="TX", api="42-301-00001", name="NEAR 1H", surface_nad83=LatLon(lat=c.y, lon=c.x)),
            OffsetWell(state="TX", api="42-301-00002", name="PERMIT", surface_nad83=LatLon(lat=c.y, lon=c.x), survey_status="not drilled"),
        ]

    def find_documents(self, well):
        return [
            SurveyDocument(url="https://example.invalid/s.pdf", title="Directional Survey - MWD", likely_survey=True),
            SurveyDocument(url="https://example.invalid/p.pdf", title="Plat", likely_plat=True),
        ]

    def download(self, doc):
        return b"%PDF-fake"


well_pos: list = []


def test_job_flow(fixtures, monkeypatch):
    monkeypatch.setitem(registry.IMPLEMENTED, "TX", FakeTexas)
    monkeypatch.setitem(registry.IMPLEMENTED, "NM", FakeTexas)
    survey = Survey(survey_type=SurveyType.DIRECTIONAL, stations=[SurveyStation(md=0, inc=0, azi=0), SurveyStation(md=100, inc=1, azi=90, flags=["x"])])
    plat_id = plats.store_pages((fixtures / "tx" / "scrapfile_north_1h_plat.pdf").read_bytes(), [1])

    def fake_process(data, source, title="", is_plat=False, progress=None):
        if is_plat:
            # The plat SHL sits on the GIS point, so the check passes.
            plat = Plat(file_id=plat_id, source=source, title=title, read=True, datum="NAD83",
                        points=[PlatPoint(label="SHL", lat=well_pos[0], lon=well_pos[1])])
            return DocumentResult(plats=[plat])
        return DocumentResult(surveys=[survey])

    monkeypatch.setattr(jobs, "process_document", fake_process)

    client = TestClient(app)
    with open(fixtures / "sample_edm.xml", "rb") as f:
        r = client.post("/api/jobs", files={"file": ("pad.xml", f, "text/xml")}, data={"radius_miles": "0.5", "states": "TX"})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "ready":
            break
        time.sleep(0.05)
    assert job["status"] == "ready", job["log"]
    assert [w["name"] for w in job["offsets"]] == ["NEAR 1H"]  # not-drilled well removed
    assert job["offsets"][0]["key"] == "TX-42-301-00001"  # the UI needs the key in the JSON
    assert job["offsets"][0]["survey_status"] == "1 survey document(s), 1 plat(s)"
    well_pos[:] = [job["offsets"][0]["surface_nad83"]["lat"], job["offsets"][0]["surface_nad83"]["lon"]]

    assert client.post(f"/api/jobs/{job_id}/extract", json={"keys": ["TX-42-301-00001"]}).status_code == 200
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "done":
            break
        time.sleep(0.05)
    w = job["offsets"][0]
    assert w["survey_status"] == "1 survey(s) extracted, 1 plat(s)"
    assert w["plat_checks"][0]["ok"] is True

    key = w["key"]
    png = client.get(f"/api/jobs/{job_id}/wells/{key}/plats/0.png")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    assert client.get(f"/api/jobs/{job_id}/wells/{key}/plats/0.pdf").content[:4] == b"%PDF"
    assert client.get(f"/api/jobs/{job_id}/wells/{key}/plats/5.pdf").status_code == 404
    z = client.get(f"/api/jobs/{job_id}/plats.zip")
    assert z.status_code == 200
    import zipfile
    assert zipfile.ZipFile(io.BytesIO(z.content)).namelist() == ["42-301-00001_NEAR_1H_plat_1.pdf"]

    x = client.get(f"/api/jobs/{job_id}/export.xlsx")
    assert x.status_code == 200
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames[0] == "Summary"
    assert "PLAN TEST 1H" in wb.sheetnames
    assert "42-301-00001 NEAR 1H" in wb.sheetnames
    ws = wb["42-301-00001 NEAR 1H"]
    values = [c.value for row in ws.iter_rows() for c in row]
    assert "MD (ft)" in values and "x" in values
    assert "Plat checks" in values
    assert len(ws._images) == 1


def test_bad_upload():
    client = TestClient(app)
    r = client.post("/api/jobs", files={"file": ("a.xml", b"<nope/>", "text/xml")})
    assert r.status_code == 400


def test_states_endpoint():
    client = TestClient(app)
    codes = [s["code"] for s in client.get("/api/states").json()]
    assert codes[:3] == ["TX", "NM", "CO"]
