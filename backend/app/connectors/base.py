"""Base class and shared HTTP code for state commission connectors."""

from __future__ import annotations

import hashlib
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from shapely.geometry import Polygon

from ..config import settings
from ..models import OffsetWell, Survey, SurveyDocument


class PoliteClient:
    """HTTP client with a per-host request interval and a disk cache.

    State sites are public services with small budgets. The tool waits
    between requests to the same host and caches responses, so many users
    who search the same area do not repeat the same downloads.
    """

    def __init__(self, min_interval_s: float | None = None, cache_dir: Path | None = None):
        self.min_interval_s = settings.request_interval_s if min_interval_s is None else min_interval_s
        self.cache_dir = cache_dir or settings.data_dir / "http_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.client = httpx.Client(
            timeout=httpx.Timeout(60.0, connect=20.0),
            follow_redirects=True,
            headers={"User-Agent": settings.user_agent},
        )

    def _wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._last.get(host, 0.0) + self.min_interval_s - now
            self._last[host] = max(now, self._last.get(host, 0.0) + self.min_interval_s)
        if wait > 0:
            time.sleep(wait)

    def _cache_path(self, method: str, url: str, body: str) -> Path:
        h = hashlib.sha256(f"{method} {url} {body}".encode()).hexdigest()
        return self.cache_dir / h[:2] / h

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
        cache_s: float = 0,
        client: Optional[httpx.Client] = None,
    ) -> httpx.Response:
        req = (client or self.client).build_request(method, url, params=params, data=data)
        body = req.content.decode("latin-1") if req.content else ""
        cpath = self._cache_path(method, str(req.url), body)
        if cache_s and cpath.exists() and time.time() - cpath.stat().st_mtime < cache_s:
            return httpx.Response(200, content=cpath.read_bytes(), request=req)
        self._wait(urlparse(str(req.url)).netloc)
        resp = (client or self.client).send(req)
        resp.raise_for_status()
        if cache_s:
            cpath.parent.mkdir(parents=True, exist_ok=True)
            cpath.write_bytes(resp.content)
        return resp

    def get(self, url: str, **kw) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> httpx.Response:
        return self.request("POST", url, **kw)


_shared: Optional[PoliteClient] = None


def shared_client() -> PoliteClient:
    global _shared
    if _shared is None:
        _shared = PoliteClient()
    return _shared


class StateConnector(ABC):
    """One state's public data source.

    find_wells: wells inside the search polygon (NAD83 lon/lat).
    find_documents: survey documents for one well.
    digital_surveys: survey data that the state publishes as data (no OCR).
    download: bytes of one document.
    """

    code: str = ""
    name: str = ""
    agency: str = ""
    portal_url: str = ""
    supports_digital_surveys: bool = False
    supports_document_download: bool = True
    notes: str = ""

    def __init__(self, http: Optional[PoliteClient] = None):
        self.http = http or shared_client()

    @abstractmethod
    def find_wells(self, area: Polygon) -> list[OffsetWell]: ...

    def find_documents(self, well: OffsetWell) -> list[SurveyDocument]:
        return []

    def digital_surveys(self, well: OffsetWell) -> list[Survey]:
        return []

    def download(self, doc: SurveyDocument) -> bytes:
        return self.http.get(doc.url, cache_s=30 * 86400).content

    def describe(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "agency": self.agency,
            "portal_url": self.portal_url,
            "status": "available",
            "digital_surveys": self.supports_digital_surveys,
            "document_download": self.supports_document_download,
            "notes": self.notes,
        }


SURVEY_KEYWORDS = (
    "DIRECTIONAL",
    "DEVIATION",
    "INCLINATION",
    "SURVEY",
    "GYRO",
    "MWD",
    "W-12",
    "W12",
    "DRIFT",
)


def looks_like_survey(title: str) -> bool:
    t = title.upper()
    if "CERTIFICATION" in t or "CERTIFICATE" in t:
        return False  # a signed cover letter, no survey rows
    if "PLAT" in t and "SURVEY" in t and "DIRECTION" not in t:
        return False  # a land survey plat, not a wellbore survey
    return any(k in t for k in SURVEY_KEYWORDS)
