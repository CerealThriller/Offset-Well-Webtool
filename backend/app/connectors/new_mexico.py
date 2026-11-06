"""New Mexico Oil Conservation Division (OCD).

Well locations: OCD public ArcGIS service "OCDView/Wells_Public".
Documents: OCD imaging well file page. It lists every scanned PDF of the
well file with no document type, so the extraction step classifies the
pages to find the survey pages.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from lxml import html
from shapely.geometry import Polygon

from ..models import LatLon, OffsetWell, SurveyDocument
from .arcgis import query_layer
from .base import StateConnector

LAYER = "https://gis.emnrd.nm.gov/arcgis/rest/services/OCDView/Wells_Public/FeatureServer/0"
WELL_FILE = "https://ocdimage.emnrd.nm.gov/imaging/WellFileView.aspx?RefType=WF&RefID={ref}"
SKIP_STATUS = ("CANCELLED", "NEVER DRILLED")
TRAJ = {"V": "vertical", "H": "horizontal", "D": "directional"}


class NewMexicoConnector(StateConnector):
    code = "NM"
    name = "New Mexico"
    agency = "Oil Conservation Division (EMNRD)"
    portal_url = "https://www.emnrd.nm.gov/ocd/ocd-data/"
    notes = "Well files are scanned PDFs with no document type. The tool classifies each page."

    def find_wells(self, area: Polygon) -> list[OffsetWell]:
        out = []
        for f in query_layer(self.http, LAYER, area, page_size=2000):
            a, g = f["attributes"], f.get("geometry") or {}
            if "x" not in g or not a.get("id"):
                continue
            status = a.get("status") or ""
            w = OffsetWell(
                state=self.code,
                api=a["id"],
                name=a.get("name") or "",
                operator=a.get("ogrid_name") or "",
                status=status,
                well_type=a.get("type") or "",
                trajectory=TRAJ.get((a.get("directional_status") or "").upper()[:1], a.get("directional_status") or ""),
                surface_nad83=LatLon(lat=g["y"], lon=g["x"]),
                total_depth_md=a.get("measured_vertical_depth") or None,
                total_depth_tvd=a.get("true_vertical_depth") or None,
                detail_url=a.get("details"),
            )
            if status.upper() in SKIP_STATUS or (status.upper() == "NEW" and not a.get("spud_date")):
                w.survey_status = "not drilled"
            if a.get("spud_date"):
                w.messages.append(
                    "Spud " + datetime.fromtimestamp(a["spud_date"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                )
            out.append(w)
        return out

    def find_documents(self, well: OffsetWell) -> list[SurveyDocument]:
        if well.survey_status == "not drilled":
            return []
        ref = well.api.replace("-", "").ljust(14, "0")
        page = self.http.get(WELL_FILE.format(ref=ref), cache_s=86400).text
        return parse_well_file_page(page)


_SIZE_DATE = re.compile(r"\(([\d.,]+\s*[kKmM]?B)\s*-\s*([\d/]+)\)")


def parse_well_file_page(page: str) -> list[SurveyDocument]:
    doc = html.fromstring(page)
    out = []
    for a in doc.xpath("//div[contains(@class,'image_tile')]/a[@href]"):
        url = a.get("href").replace("http://", "https://")
        tile = a.getparent()
        m = _SIZE_DATE.search(tile.text_content())
        out.append(
            SurveyDocument(
                url=url,
                title=url.rsplit("/", 1)[-1],
                doc_type="well file",
                date=m.group(2) if m else None,
                # Type is unknown. Classification of the pages decides.
                likely_survey=False,
            )
        )
    return out
