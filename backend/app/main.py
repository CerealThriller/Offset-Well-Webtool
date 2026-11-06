"""HTTP API and static frontend."""

from __future__ import annotations

import io
import logging
import re
import zipfile
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import jobs
from .config import settings
from .connectors.registry import catalog
from .edm.parser import EdmError, ParseOptions
from .export.excel import build_workbook
from .extraction.plats import plat_pdf_path, plat_png
from .geo.buffer import BufferMode
from .geo.crs import COMMON_PROJECTED_CRS, nadcon_grid_available
from .models import Datum, NorthReference

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Offset Well Webtool", version="0.1.0")

MAX_UPLOAD = 100 * 1024 * 1024


def _owner(request: Request) -> str:
    """User identity from an auth proxy in front of the app (oauth2-proxy, Cloudflare Access, IAP)."""
    for h in ("x-forwarded-email", "x-forwarded-user", "cf-access-authenticated-user-email", "x-auth-request-email"):
        if request.headers.get(h):
            return request.headers[h]
    return ""


def _job_or_404(job_id: str, request: Request) -> jobs.Job:
    job = jobs.store.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    owner = _owner(request)
    if job.owner and owner and job.owner != owner:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "nadcon_grid": nadcon_grid_available()}


@app.get("/api/states")
def states() -> list[dict]:
    return catalog()


@app.get("/api/crs")
def crs_list() -> list[dict]:
    return [{"epsg": k, "name": v} for k, v in COMMON_PROJECTED_CRS.items()]


@app.post("/api/jobs")
async def create_job(
    request: Request,
    file: UploadFile = File(...),
    datum: str = Form("auto"),
    epsg: Optional[int] = Form(None),
    north_reference: str = Form("auto"),
    convergence_deg: Optional[float] = Form(None),
    depth_unit: str = Form("ft"),
    prefer: str = Form("plan"),
    radius_miles: float = Form(1.0),
    buffer_mode: BufferMode = Form(BufferMode.PER_WELL),
    states: str = Form(""),
    auto_extract: bool = Form(False),
):
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File is larger than 100 MB")
    if not 0.05 <= radius_miles <= 10:
        raise HTTPException(400, "Radius must be between 0.05 and 10 miles")
    popts = ParseOptions(
        datum=None if datum == "auto" else Datum(datum),
        projected_epsg=epsg or None,
        north_reference=None if north_reference == "auto" else NorthReference(north_reference),
        convergence_deg=convergence_deg,
        depth_unit_ft=0.3048 if depth_unit == "m" else 1.0,
        prefer=prefer,
    )
    opts = jobs.JobOptions(
        radius_miles=radius_miles,
        buffer_mode=buffer_mode,
        states=[s.strip().upper() for s in states.split(",") if s.strip()] or None,
        auto_extract=auto_extract,
    )
    try:
        job = jobs.create_job(data, file.filename or "upload.xml", popts, opts, owner=_owner(request))
    except EdmError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # malformed XML and similar
        raise HTTPException(400, f"Cannot read the EDM file: {e}") from e
    return job.model_dump(mode="json")


@app.get("/api/jobs")
def list_jobs(request: Request) -> list[dict]:
    return [
        {"id": j.id, "created": j.created, "file_name": j.file_name, "status": j.status,
         "wells": len(j.planned), "offsets": len(j.offsets)}
        for j in jobs.store.list(_owner(request))
    ]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict:
    return _job_or_404(job_id, request).model_dump(mode="json")


class ExtractRequest(BaseModel):
    keys: list[str]


@app.post("/api/jobs/{job_id}/extract")
def extract(job_id: str, body: ExtractRequest, request: Request) -> dict:
    job = _job_or_404(job_id, request)
    known = {w.key for w in job.offsets}
    keys = [k for k in body.keys if k in known]
    if not keys:
        raise HTTPException(400, "No known wells selected")
    jobs.start_extract(job_id, keys)
    return {"started": len(keys)}


@app.post("/api/jobs/{job_id}/wells/{key}/upload")
async def upload_doc(job_id: str, key: str, request: Request, file: UploadFile = File(...)) -> dict:
    job = _job_or_404(job_id, request)
    if key not in {w.key for w in job.offsets}:
        raise HTTPException(404, "Well not in job")
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File is larger than 100 MB")
    jobs.add_uploaded_document(job_id, key, data, file.filename or "upload.pdf")
    return {"queued": True}


@app.get("/api/jobs/{job_id}/export.xlsx")
def export(job_id: str, request: Request) -> Response:
    job = _job_or_404(job_id, request)
    info = {
        "Job": job.id,
        "EDM file": job.file_name,
        "Search radius (mi)": job.options.radius_miles,
        "Buffer mode": job.options.buffer_mode.value,
        "Datum (input)": job.parse_options.get("datum") or "from file",
        "Offset wells found": len(job.offsets),
        "Wells with surveys": sum(1 for w in job.offsets if w.surveys),
        "Wells with plats": sum(1 for w in job.offsets if w.plats),
        "Plat checks": "Plat SHL vs state GIS (50 ft), plat BHL vs GIS and survey end (100 ft), BHL MD (150 ft).",
        "Coordinates": "Surface positions in NAD83. Survey offsets in feet as reported.",
        "QC": "Shaded rows disagree with a minimum curvature recalculation. Check them against the source.",
    }
    data = build_workbook(job.planned, job.offsets, info)
    name = (job.file_name.rsplit(".", 1)[0] or "offsets") + "_offset_surveys.xlsx"
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def _plat_or_404(job_id: str, key: str, index: int, request: Request):
    job = _job_or_404(job_id, request)
    well = next((w for w in job.offsets if w.key == key), None)
    if well is None or not 0 <= index < len(well.plats):
        raise HTTPException(404, "Plat not found")
    return well, well.plats[index]


@app.get("/api/jobs/{job_id}/wells/{key}/plats/{index}.pdf")
def plat_pdf(job_id: str, key: str, index: int, request: Request) -> Response:
    well, plat = _plat_or_404(job_id, key, index, request)
    path = plat_pdf_path(plat.file_id)
    if not path.exists():
        raise HTTPException(404, "Plat file not found")
    return Response(
        path.read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{_plat_name(well, index)}"'},
    )


@app.get("/api/jobs/{job_id}/wells/{key}/plats/{index}.png")
def plat_image(job_id: str, key: str, index: int, request: Request) -> Response:
    _, plat = _plat_or_404(job_id, key, index, request)
    try:
        return Response(plat_png(plat.file_id), media_type="image/png")
    except FileNotFoundError as e:
        raise HTTPException(404, "Plat file not found") from e


def _plat_name(well, index: int) -> str:
    safe = re.sub(r"[^A-Za-z0-9#._-]+", "_", f"{well.api}_{well.name}").strip("_")
    return f"{safe}_plat_{index + 1}.pdf"


@app.get("/api/jobs/{job_id}/plats.zip")
def plats_zip(job_id: str, request: Request) -> Response:
    job = _job_or_404(job_id, request)
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for w in job.offsets:
            for i, p in enumerate(w.plats):
                path = plat_pdf_path(p.file_id)
                if path.exists():
                    z.write(path, _plat_name(w, i))
                    count += 1
    if not count:
        raise HTTPException(404, "No plats in this job")
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{job.id}_plats.zip"'},
    )


if settings.frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=settings.frontend_dir), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(settings.frontend_dir / "index.html")
