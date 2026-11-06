"""Background jobs: search offset wells, find documents, extract surveys.

A job runs in two phases so users control the vision cost:

1. search: parse the EDM, build the search area, ask each state for wells,
   and list each well's survey documents. This phase is free.
2. extract: download the documents of the selected wells and read the
   survey tables. The user picks the wells, or sets auto_extract on upload.

Jobs persist as JSON under OWT_DATA_DIR/jobs/<id>/ so a restart keeps them.
For more than one server process, move this store to Postgres and the
worker to a queue (RQ, Celery). The function boundaries stay the same.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .config import settings
from .connectors.registry import get_connector, states_for_area
from .edm.parser import ParseOptions, parse_edm
from .extraction.pipeline import DocumentResult, process_document
from .geo.buffer import BufferMode, build_search_area, distance_to_plans, polygon_to_geojson
from . import files
from .models import OffsetWell, PlannedWell, SourceFile

log = logging.getLogger(__name__)


class JobOptions(BaseModel):
    radius_miles: float = 1.0
    buffer_mode: BufferMode = BufferMode.PER_WELL
    states: Optional[list[str]] = None
    auto_extract: bool = False
    include_not_drilled: bool = False


class Job(BaseModel):
    id: str
    created: float
    owner: str = ""
    file_name: str = ""
    status: str = "queued"  # queued, searching, ready, extracting, done, error
    options: JobOptions
    parse_options: dict = Field(default_factory=dict)
    planned: list[PlannedWell] = Field(default_factory=list)
    search_area: Optional[dict] = None
    offsets: list[OffsetWell] = Field(default_factory=list)
    log: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class JobStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = root or settings.data_dir / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._cache: dict[str, Job] = {}
        self._guard = threading.Lock()
        # Two pools: a job's search waits on its document tasks, so it must
        # not hold a thread in the pool that runs them.
        self.jobs_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="owt-job")
        self.pool = ThreadPoolExecutor(max_workers=settings.worker_threads, thread_name_prefix="owt")

    def lock(self, job_id: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(job_id, threading.RLock())

    def path(self, job_id: str) -> Path:
        return self.root / job_id / "job.json"

    def save(self, job: Job) -> None:
        with self.lock(job.id):
            p = self.path(job.id)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(job.model_dump_json())
            tmp.replace(p)
            self._cache[job.id] = job

    def get(self, job_id: str) -> Optional[Job]:
        if job_id in self._cache:
            return self._cache[job_id]
        p = self.path(job_id)
        if not p.exists():
            return None
        job = Job.model_validate_json(p.read_text())
        self._cache[job_id] = job
        return job

    def list(self, owner: str = "") -> list[Job]:
        jobs = []
        for p in self.root.glob("*/job.json"):
            try:
                meta = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if owner and meta.get("owner") != owner:
                continue
            jobs.append(self.get(meta["id"]))
        return sorted([j for j in jobs if j], key=lambda j: -j.created)

    def note(self, job: Job, msg: str) -> None:
        with self.lock(job.id):
            job.log.append(f"{time.strftime('%H:%M:%S')} {msg}")
            self.save(job)


store = JobStore()


# ---- phase 1 ---------------------------------------------------------------


def create_job(edm_bytes: bytes, file_name: str, parse_opts: ParseOptions, options: JobOptions, owner: str = "") -> Job:
    """Parse the EDM now (so errors return to the user) and queue the search."""
    planned = parse_edm(edm_bytes, parse_opts)
    area = build_search_area(planned, options.radius_miles, options.buffer_mode)
    job = Job(
        id=uuid.uuid4().hex[:12],
        created=time.time(),
        owner=owner,
        file_name=file_name,
        options=options,
        parse_options={k: (v.value if hasattr(v, "value") else v) for k, v in parse_opts.__dict__.items()},
        planned=planned,
        search_area=polygon_to_geojson(area),
    )
    store.save(job)
    (store.root / job.id / "plan.edm").write_bytes(edm_bytes)
    store.jobs_pool.submit(_run_search, job.id)
    return job


def _run_search(job_id: str) -> None:
    job = store.get(job_id)
    assert job is not None
    from shapely.geometry import shape

    area = shape(job.search_area)
    try:
        job.status = "searching"
        store.note(job, f"Search area: {job.options.radius_miles} mi, {job.options.buffer_mode.value}")
        found: list[OffsetWell] = []
        for code in states_for_area(area, job.options.states):
            conn = get_connector(code)
            store.note(job, f"{conn.name}: searching wells")
            try:
                wells = conn.find_wells(area)
            except Exception as e:  # one state failing must not stop the others
                store.note(job, f"{conn.name}: well search failed: {e}")
                continue
            store.note(job, f"{conn.name}: {len(wells)} wells in the search area")
            found.extend(wells)
        if not job.options.include_not_drilled:
            found = [w for w in found if w.survey_status != "not drilled"]
        for w in found:
            w.distance_to_plan_ft, w.nearest_planned_well = distance_to_plans(w, job.planned)
        found.sort(key=lambda w: w.distance_to_plan_ft or 0.0)
        if len(found) > settings.max_offset_wells:
            store.note(job, f"Kept the nearest {settings.max_offset_wells} of {len(found)} wells")
            found = found[: settings.max_offset_wells]
        job.offsets = found
        store.save(job)

        futures = [store.pool.submit(_find_docs, job_id, w.key) for w in found]
        for f in futures:
            f.result()
        job.status = "ready"
        store.note(job, "Search complete. Select wells to extract surveys.")
        if job.options.auto_extract:
            start_extract(job_id, [w.key for w in job.offsets])
    except Exception as e:
        job.status, job.error = "error", str(e)
        store.note(job, f"Error: {e}")
        log.error(traceback.format_exc())


def _well(job: Job, key: str) -> OffsetWell:
    for w in job.offsets:
        if w.key == key:
            return w
    raise KeyError(key)


def _find_docs(job_id: str, key: str) -> None:
    job = store.get(job_id)
    assert job is not None
    w = _well(job, key)
    conn = get_connector(w.state)
    try:
        digital = conn.digital_surveys(w)
        docs = conn.find_documents(w)
        with store.lock(job_id):
            w.surveys.extend(digital)
            w.documents = docs
            if digital:
                w.survey_status = "digital survey found"
            elif w.state == "NM":
                w.survey_status = f"{len(docs)} well file document(s) to check" if docs else "no documents online"
            elif any(d.likely_survey for d in docs):
                n = sum(d.likely_survey for d in docs)
                manual = any(d.likely_survey and d.access == "manual" for d in docs)
                w.survey_status = f"{n} survey document(s)" + (" - manual download" if manual else "")
            else:
                w.survey_status = "no online survey"
            n_plat = sum(d.likely_plat for d in docs)
            if n_plat:
                w.survey_status += f", {n_plat} plat(s)"
            store.save(job)
    except Exception as e:
        with store.lock(job_id):
            w.survey_status = "document search failed"
            w.messages.append(str(e))
            store.save(job)


# ---- phase 2 ---------------------------------------------------------------


def start_extract(job_id: str, keys: list[str]) -> None:
    job = store.get(job_id)
    assert job is not None
    job.status = "extracting"
    store.note(job, f"Extracting surveys for {len(keys)} well(s)")

    def run() -> None:
        futures = [store.pool.submit(_extract_well, job_id, k) for k in keys]
        for f in futures:
            f.result()
        job.status = "done"
        store.note(job, "Extraction complete")

    threading.Thread(target=run, daemon=True).start()


def _status_text(w: OffsetWell) -> str:
    parts = [f"{len(w.surveys)} survey(s) extracted" if w.surveys else "no survey table found"]
    if w.plats:
        parts.append(f"{len(w.plats)} plat(s)")
    return ", ".join(parts)


def _apply_results(job: Job, w: OffsetWell, sources: set[str], surveys: list, plats: list, kept: list) -> None:
    """Replace earlier results from the same sources and add the new ones."""
    with store.lock(job.id):
        w.surveys = [s for s in w.surveys if s.source not in sources] + surveys
        w.plats = [p for p in w.plats if p.source not in sources] + plats
        new_ids = {f.file_id for f in kept}
        w.files = [f for f in w.files if f.source_url not in sources and f.file_id not in new_ids] + kept
        w.survey_status = _status_text(w)
        store.save(job)


def _file_name(title: str, url: str, data: bytes) -> str:
    base = url.rsplit("/", 1)[-1].split("?")[0] if "/" in url else ""
    if not base or "." not in base:
        base = f"{title or 'document'}.{files.extension(data)}"
    return re.sub(r"[^A-Za-z0-9#._-]+", "_", base).strip("_")


def _keep_file(data: bytes, title: str, url: str, kind: str) -> SourceFile:
    """Store a downloaded or uploaded file so the user can review it."""
    ext = files.extension(data)
    return SourceFile(
        file_id=files.store(data),
        view_file_id=files.pdf_copy(data) if ext in ("tif", "png", "jpg") else "",
        title=title,
        source_url=url,
        kind=kind,
        file_name=_file_name(title, url, data),
        size=len(data),
        pages=files.pages(data),
    )


def _process(job: Job, w: OffsetWell, data: bytes, kept: SourceFile, is_plat: bool) -> DocumentResult:
    """Read one stored file. A failure keeps the file and logs the reason."""
    try:
        res = process_document(
            data,
            kept.source_url,
            title=kept.title,
            is_plat=is_plat,
            progress=lambda m: store.note(job, f"{w.api}: {m}"),
        )
    except Exception as e:
        store.note(job, f"{w.api}: {kept.title}: {e}")
        w.messages.append(f"{kept.title}: {e}")
        return DocumentResult()
    kept.survey_pages, kept.plat_pages = res.survey_pages, res.plat_pages
    return res


def _extract_well(job_id: str, key: str) -> None:
    job = store.get(job_id)
    assert job is not None
    w = _well(job, key)
    conn = get_connector(w.state)
    if w.state == "NM":
        docs = [d for d in w.documents if d.access == "direct"]
    else:
        docs = [d for d in w.documents if (d.likely_survey or d.likely_plat) and d.access == "direct"]
    if not docs:
        return
    with store.lock(job_id):
        w.survey_status = "extracting"
        store.save(job)
    surveys, plats, sources, kept = [], [], set(), []
    for d in docs:
        try:
            data = conn.download(d)
        except Exception as e:
            store.note(job, f"{w.api}: {d.title}: download failed: {e}")
            w.messages.append(f"{d.title}: download failed: {e}")
            continue
        kind = "survey" if d.likely_survey else "plat" if d.likely_plat else "well file"
        f = _keep_file(data, d.title, d.url, kind)
        res = _process(job, w, data, f, is_plat=d.likely_plat and not d.likely_survey)
        surveys.extend(res.surveys)
        plats.extend(res.plats)
        sources.add(d.url)
        kept.append(f)
    _apply_results(job, w, sources, surveys, plats, kept)


def add_uploaded_document(job_id: str, key: str, data: bytes, file_name: str) -> None:
    """Extract a document that the user downloaded by hand (for example from ECMC).

    A file name with "plat" or "C-102" is kept as a plat without page classification.
    """
    job = store.get(job_id)
    assert job is not None
    w = _well(job, key)
    job.status = "extracting"
    store.note(job, f"{w.api}: extracting uploaded file {file_name}")

    def run() -> None:
        source = f"upload: {file_name}"
        f = _keep_file(data, file_name, source, "upload")
        f.file_name = re.sub(r"[^A-Za-z0-9#._-]+", "_", file_name) or f.file_name
        is_plat = "PLAT" in file_name.upper() or "C-102" in file_name.upper()
        res = _process(job, w, data, f, is_plat=is_plat)
        _apply_results(job, w, {source}, res.surveys, res.plats, [f])
        with store.lock(job_id):
            job.status = "done"
            store.save(job)

    store.pool.submit(run)
