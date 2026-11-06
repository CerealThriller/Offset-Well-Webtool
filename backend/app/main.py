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

from . import files, jobs
from .config import settings
from .connectors.registry import catalog
from .edm.parser import EdmError, ParseOptions
from .export.excel import build_workbook
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


def _job_info(job: jobs.Job) -> dict:
    return {
        "Job": job.id,
        "EDM file": job.file_name,
        "Search radius (mi)": job.options.radius_miles,
        "Buffer mode": job.options.buffer_mode.value,
        "Datum (input)": job.parse_options.get("datum") or "from file",
        "Offset wells found": len(job.offsets),
        "Wells with surveys": sum(1 for w in job.offsets if w.surveys),
        "Wells with plats": sum(1 for w in job.offsets if w.plats),
        "Coordinates": "Surface positions in NAD83. Survey offsets in feet as reported.",
        "QC": "Shaded rows disagree with a minimum curvature recalculation. Check them against the source.",
        "Plats": "The tool does not read plats. Check plat locations against the survey before the Compass load.",
    }


def _workbook_name(job: jobs.Job) -> str:
    return _safe(job.file_name.rsplit(".", 1)[0] or "offsets") + "_offset_surveys.xlsx"


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9#._-]+", "_", text).strip("_") or "file"


@app.get("/api/jobs/{job_id}/export.xlsx")
def export(job_id: str, request: Request) -> Response:
    job = _job_or_404(job_id, request)
    data = build_workbook(job.planned, job.offsets, _job_info(job))
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{_workbook_name(job)}"'},
    )


def _job_files(job: jobs.Job) -> dict[str, tuple[str, str]]:
    """{file_id: (well folder, file name)} for every source file and plat in the job."""
    out: dict[str, tuple[str, str]] = {}
    for w in job.offsets:
        folder = _safe(f"{w.api}_{w.name}")
        used: set[str] = set()

        def unique(name: str) -> str:
            stem, dot, ext = name.rpartition(".")
            if not dot:
                stem, ext = name, ""
            n, cand = 2, name
            while cand.lower() in used:
                cand = f"{stem}_{n}.{ext}" if ext else f"{stem}_{n}"
                n += 1
            used.add(cand.lower())
            return cand

        for i, p in enumerate(w.plats, start=1):
            if p.file_id not in out:
                out[p.file_id] = (folder, unique(f"plat_{i}.pdf"))
        for f in w.files:
            if f.file_id not in out:
                out[f.file_id] = (folder, unique(f.file_name or f.file_id[:12]))
            if f.view_file_id and f.view_file_id not in out:
                stem = (f.file_name or f.file_id[:12]).rpartition(".")[0] or f.file_name
                out[f.view_file_id] = (folder, unique(f"{stem}.pdf"))
    return out


def _stored(file_id: str):
    try:
        return files.path(file_id)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(404, "File not found") from e


@app.get("/api/jobs/{job_id}/files/{file_id}")
def get_file(job_id: str, file_id: str, request: Request, download: bool = False) -> Response:
    """A source file or plat of the job, inline for review in the browser."""
    job = _job_or_404(job_id, request)
    known = _job_files(job)
    if file_id not in known:
        raise HTTPException(404, "File not in job")
    path = _stored(file_id)
    disposition = "attachment" if download else "inline"
    return Response(
        path.read_bytes(),
        media_type=files.content_type(file_id),
        headers={"Content-Disposition": f'{disposition}; filename="{known[file_id][1]}"'},
    )


@app.get("/api/jobs/{job_id}/files/{file_id}/thumb.png")
def get_thumb(job_id: str, file_id: str, request: Request) -> Response:
    job = _job_or_404(job_id, request)
    if file_id not in _job_files(job):
        raise HTTPException(404, "File not in job")
    _stored(file_id)
    return Response(files.thumbnail(file_id), media_type="image/png")


def _zip_response(buf: io.BytesIO, name: str) -> Response:
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/api/jobs/{job_id}/plats.zip")
def plats_zip(job_id: str, request: Request) -> Response:
    job = _job_or_404(job_id, request)
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for w in job.offsets:
            for i, p in enumerate(w.plats, start=1):
                try:
                    z.write(files.path(p.file_id), f"{_safe(f'{w.api}_{w.name}')}_plat_{i}.pdf")
                    count += 1
                except (ValueError, FileNotFoundError):
                    continue
    if not count:
        raise HTTPException(404, "No plats in this job")
    return _zip_response(buf, f"{job.id}_plats.zip")


@app.get("/api/jobs/{job_id}/package.zip")
def package_zip(job_id: str, request: Request) -> Response:
    """The workbook and every source file and plat, with links between them.

    Layout:
      <workbook>.xlsx
      wells/<API>_<name>/<source file>
      wells/<API>_<name>/plat_<n>.pdf
    """
    job = _job_or_404(job_id, request)
    links: dict[str, str] = {}
    entries: list[tuple[str, str]] = []
    for fid, (folder, name) in _job_files(job).items():
        rel = f"wells/{folder}/{name}"
        try:
            entries.append((str(files.path(fid)), rel))
        except (ValueError, FileNotFoundError):
            continue
        links[fid] = rel
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(_workbook_name(job), build_workbook(job.planned, job.offsets, _job_info(job), links))
        used: set[str] = set()
        for src, rel in entries:
            if rel not in used:
                z.write(src, rel)
                used.add(rel)
    return _zip_response(buf, f"{_safe(job.file_name.rsplit('.', 1)[0])}_package.zip")


if settings.frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=settings.frontend_dir), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(settings.frontend_dir / "index.html")
