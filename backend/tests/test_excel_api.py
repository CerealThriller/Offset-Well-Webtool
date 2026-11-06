import io
import time

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import jobs
from app.connectors import registry
from app.connectors.base import StateConnector
from app.main import app
from app.models import LatLon, OffsetWell, Survey, SurveyDocument, SurveyStation, SurveyType


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
        return [SurveyDocument(url="https://example.invalid/s.pdf", title="Directional Survey - MWD", likely_survey=True)]

    def download(self, doc):
        return b"%PDF-fake"


def test_job_flow(fixtures, monkeypatch):
    monkeypatch.setitem(registry.IMPLEMENTED, "TX", FakeTexas)
    monkeypatch.setitem(registry.IMPLEMENTED, "NM", FakeTexas)
    survey = Survey(survey_type=SurveyType.DIRECTIONAL, stations=[SurveyStation(md=0, inc=0, azi=0), SurveyStation(md=100, inc=1, azi=90, flags=["x"])])
    monkeypatch.setattr(jobs, "extract_surveys", lambda data, source, progress=None: [survey])

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
    assert job["offsets"][0]["survey_status"] == "1 survey document(s)"

    assert client.post(f"/api/jobs/{job_id}/extract", json={"keys": ["TX-42-301-00001"]}).status_code == 200
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "done":
            break
        time.sleep(0.05)
    assert job["offsets"][0]["survey_status"] == "1 survey(s) extracted"

    x = client.get(f"/api/jobs/{job_id}/export.xlsx")
    assert x.status_code == 200
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames[0] == "Summary"
    assert "PLAN TEST 1H" in wb.sheetnames
    assert "42-301-00001 NEAR 1H" in wb.sheetnames
    ws = wb["42-301-00001 NEAR 1H"]
    values = [c.value for row in ws.iter_rows() for c in row]
    assert "MD (ft)" in values and "x" in values


def test_bad_upload():
    client = TestClient(app)
    r = client.post("/api/jobs", files={"file": ("a.xml", b"<nope/>", "text/xml")})
    assert r.status_code == 400


def test_states_endpoint():
    client = TestClient(app)
    codes = [s["code"] for s in client.get("/api/states").json()]
    assert codes[:3] == ["TX", "NM", "CO"]
