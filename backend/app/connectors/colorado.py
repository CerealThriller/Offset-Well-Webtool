"""Colorado Energy & Carbon Management Commission (ECMC, formerly COGCC).

Well locations: the ECMC statewide well and directional bottom-hole
shapefiles (daily downloads). The tool caches them for one week.
Documents: the ECMC facility document list (Docs.aspx). Document names
show "directional survey" directly.

Limitation: ECMC WebLink, which serves the document files, puts a browser
check (CAPTCHA) in front of each download. The tool does not bypass it.
For Colorado, the tool lists the survey documents with links. The user
opens each link, saves the PDF, and uploads it to the job for extraction.
"""

from __future__ import annotations

import io
import re
import threading
import time
import zipfile
from pathlib import Path
from typing import Optional

import shapefile  # pyshp
from lxml import html
from shapely.geometry import Point, Polygon
from shapely.prepared import prep

from ..config import settings
from ..models import LatLon, OffsetWell, SurveyDocument
from .base import StateConnector, looks_like_survey

BASE = "https://ecmc.state.co.us"
WELLS_ZIP = f"{BASE}/documents/data/downloads/gis/WELLS_SHP.ZIP"
BH_ZIP = f"{BASE}/documents/data/downloads/gis/DIRECTIONAL_BOTTOMHOLE_LOCATIONS_SHP.ZIP"
DOCS = f"{BASE}/cogisdb/Resources/Docs.aspx?classid=07~08&id={{api8}}"
FACILITY = f"{BASE}/cogisdb/Facility/FacilityDetail?api={{api8}}"
CACHE_S = 7 * 86400
SKIP_STATUS = ("AL", "XX")  # abandoned location, permit only


class ColoradoConnector(StateConnector):
    code = "CO"
    name = "Colorado"
    agency = "Energy & Carbon Management Commission"
    portal_url = "https://ecmc.state.co.us/data2.html"
    supports_document_download = False
    notes = (
        "ECMC protects document downloads with a browser check. The tool lists Colorado survey "
        "documents; upload the saved PDFs to extract them."
    )

    _lock = threading.Lock()
    _wells: Optional[list[tuple[float, float, dict]]] = None
    _bh: Optional[dict[str, dict]] = None
    _loaded_at = 0.0

    def _load(self) -> None:
        with self._lock:
            if self._wells is not None and time.time() - self._loaded_at < CACHE_S:
                return
            wells = []
            for rec in _read_shapefile(self._download(WELLS_ZIP, "co_wells.zip")):
                lat, lon = rec.get("Latitude"), rec.get("Longitude")
                if lat and lon:
                    wells.append((lon, lat, rec))
            bh: dict[str, dict] = {}
            for rec in _read_shapefile(self._download(BH_ZIP, "co_bh.zip")):
                api8 = (rec.get("API") or "")[:8]
                if rec.get("BH_Status") != "Planned" or api8 not in bh:
                    bh[api8] = rec
            type(self)._wells, type(self)._bh, type(self)._loaded_at = wells, bh, time.time()

    def _download(self, url: str, name: str) -> bytes:
        path: Path = settings.data_dir / "state_cache" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or time.time() - path.stat().st_mtime > CACHE_S:
            path.write_bytes(self.http.get(url).content)
        return path.read_bytes()

    def find_wells(self, area: Polygon) -> list[OffsetWell]:
        self._load()
        assert self._wells is not None and self._bh is not None
        minx, miny, maxx, maxy = area.bounds
        pa = prep(area)
        out = []
        for lon, lat, r in self._wells:
            if not (minx <= lon <= maxx and miny <= lat <= maxy) or not pa.contains(Point(lon, lat)):
                continue
            api8 = r["API"]
            bh = self._bh.get(api8)
            w = OffsetWell(
                state=self.code,
                api=r.get("API_Label") or f"05-{api8[:3]}-{api8[3:]}",
                name=r.get("Well_Title") or r.get("Well_Name") or "",
                operator=r.get("Operator") or "",
                status=r.get("Facil_Stat") or "",
                well_type=r.get("Well_Class") or "",
                trajectory=(bh.get("Deviation") or "").lower() if bh else "vertical",
                surface_nad83=LatLon(lat=lat, lon=lon),
                bottom_hole_nad83=LatLon(lat=bh["Lat"], lon=bh["Long"]) if bh and bh.get("Lat") else None,
                total_depth_md=r.get("Max_MD") or None,
                total_depth_tvd=r.get("Max_TVD") or None,
                detail_url=FACILITY.format(api8=api8),
            )
            if w.status in SKIP_STATUS:
                w.survey_status = "not drilled"
            out.append(w)
        return out

    def find_documents(self, well: OffsetWell) -> list[SurveyDocument]:
        if well.survey_status == "not drilled":
            return []
        api8 = well.api.replace("-", "")[2:10]
        page = self.http.get(DOCS.format(api8=api8), cache_s=86400).text
        return [d for d in parse_docs_page(page) if d.likely_survey]


def parse_docs_page(page: str) -> list[SurveyDocument]:
    doc = html.fromstring(page)
    out: list[SurveyDocument] = []
    seen: set[str] = set()
    for row in doc.xpath("//tr[td]"):
        cells = row.xpath("./td")
        links = row.xpath(".//a[contains(@href,'DownloadDocument')]")
        if len(cells) < 6 or not links or links[0].get("href") in seen:
            continue
        seen.add(links[0].get("href"))
        name = re.sub(r"\s+", " ", cells[3].text_content()).strip()
        out.append(
            SurveyDocument(
                url=links[0].get("href"),
                title=name,
                doc_type=name,
                date=cells[5].text_content().strip() or None,
                likely_survey=looks_like_survey(name),
                access="manual",
            )
        )
    return out


def _read_shapefile(zip_bytes: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = z.namelist()
        stem = next(n[:-4] for n in names if n.lower().endswith(".shp"))
        dbf = io.BytesIO(z.read(next(n for n in names if n.lower() == stem.lower() + ".dbf")))
    reader = shapefile.Reader(dbf=dbf)
    fields = [f[0] for f in reader.fields[1:]]
    return [dict(zip(fields, rec)) for rec in reader.iterRecords()]
