"""Texas Railroad Commission (RRC).

Well locations: RRC public GIS viewer service (layer 1 "Well Locations").
Documents: RRC Online Completions query. Each completion packet lists its
attachments by label (for example "Directional Survey", "W-12" inclination
report). Attachment images download as PDF from webapps.rrc.state.tx.us.

Limitation: completions filed on paper before the online system (about
2008 and older) are not in the Completions query. Those records are in the
RRC imaged well records. The tool lists such wells with status
"no online survey" so the user can request them.
"""

from __future__ import annotations

import re
from typing import Optional

import httpx
from lxml import html
from shapely.geometry import Polygon

from ..models import LatLon, OffsetWell, SurveyDocument
from .arcgis import query_layer
from .base import StateConnector, looks_like_survey

GIS = "https://gis.rrc.texas.gov/server/rest/services/rrc_public/RRC_Public_Viewer_Srvs/MapServer"
CMPL = "https://webapps.rrc.texas.gov/CMPL/publicSearchAction.do"
SKIP_SYMBOLS = ("PERMITTED", "CANCELED", "CANCELLED")


class TexasConnector(StateConnector):
    code = "TX"
    name = "Texas"
    agency = "Railroad Commission of Texas"
    portal_url = "https://www.rrc.texas.gov/resource-center/research/"
    notes = "Surveys come from completion packet attachments. Paper-era wells need a records request."

    def find_wells(self, area: Polygon) -> list[OffsetWell]:
        wells: dict[str, OffsetWell] = {}
        horizontal = {
            f["attributes"].get("API")
            for f in query_layer(self.http, f"{GIS}/9", area, out_fields="API")
        }
        for f in query_layer(self.http, f"{GIS}/1", area):
            a, g = f["attributes"], f.get("geometry") or {}
            api8 = (a.get("API") or "").strip()
            if not api8 or "x" not in g:
                continue
            api = f"42-{api8[:3]}-{api8[3:]}"
            symbol = a.get("GIS_SYMBOL_DESCRIPTION") or ""
            w = OffsetWell(
                state=self.code,
                api=api,
                name=f"#{a.get('GIS_WELL_NUMBER') or ''}".strip(),
                status=symbol,
                trajectory="horizontal/directional" if api8 in horizontal else "",
                surface_nad83=LatLon(lat=a.get("GIS_LAT83") or g["y"], lon=a.get("GIS_LONG83") or g["x"]),
                detail_url=f"https://gis.rrc.texas.gov/GISViewer/index.html?api={api8}",
            )
            if any(s in symbol.upper() for s in SKIP_SYMBOLS):
                w.survey_status = "not drilled"
            # Several symbols can share one API (sidetracks). Keep the first.
            wells.setdefault(api, w)
        return list(wells.values())

    # ---- documents -------------------------------------------------------

    def _new_session(self) -> tuple[httpx.Client, str]:
        client = httpx.Client(follow_redirects=True, timeout=60, headers=self.http.client.headers)
        resp = self.http.get(CMPL, client=client)
        m = re.search(r'name="externalPacketForm"[^>]*action="([^"]+)"', resp.text)
        action = "https://webapps.rrc.texas.gov" + m.group(1) if m else CMPL
        return client, action

    def find_documents(self, well: OffsetWell) -> list[SurveyDocument]:
        if well.survey_status == "not drilled":
            return []
        api8 = well.api.replace("-", "")[2:]
        client, action = self._new_session()
        try:
            resp = self.http.post(
                action,
                data={
                    "formData.methodHndlr.inputValue": "search",
                    "formData.actionFlagHndlr.inputValue": "0",
                    "searchArgs.statusCodeArgHndlr.inputValue": "",
                    "searchArgs.apiNoHndlr.inputValue": api8,
                },
                client=client,
            )
            packets = parse_packet_list(resp.text)
            docs: list[SurveyDocument] = []
            for pk in packets:
                if pk.get("operator") and not well.operator:
                    well.operator = pk["operator"]
                if pk.get("lease") and pk["lease"] not in well.name:
                    well.name = f"{pk['lease']} {well.name}".strip()
                page = self.http.get(f"{CMPL}{pk['href']}", client=client)
                for d in parse_packet_attachments(page.text):
                    d.date = pk.get("date")
                    docs.append(d)
            return docs
        finally:
            client.close()


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.text_content()).strip()


def parse_packet_list(page: str) -> list[dict]:
    """Return packets from a Completions search result page."""
    doc = html.fromstring(page)
    out = []
    for a in doc.xpath("//a[starts-with(@href, '?packetSummaryId=')]"):
        row = a.getparent()
        while row is not None and row.tag != "tr":
            row = row.getparent()
        cells = [_text(td) for td in row.xpath("./td")] if row is not None else []
        out.append(
            {
                "href": a.get("href"),
                "tracking": cells[0] if cells else "",
                "type": cells[2] if len(cells) > 2 else "",
                "date": cells[6] if len(cells) > 6 else None,
                "operator": re.sub(r"\s*\(\d+\)$", "", cells[7]) if len(cells) > 7 else "",
                "lease": cells[9] if len(cells) > 9 else "",
            }
        )
    return out


def parse_packet_attachments(page: str) -> list[SurveyDocument]:
    """Return the attachments of one completion packet."""
    doc = html.fromstring(page)
    out = []
    for a in doc.xpath("//a[contains(@href, '/dpimages/')]"):
        label = _label_before(a)
        out.append(
            SurveyDocument(
                url=a.get("href").replace("http://", "https://"),
                title=label,
                doc_type=label,
                likely_survey=looks_like_survey(label),
            )
        )
    return out


def _label_before(a) -> str:
    """The attachment label is the first cell of the link's table row."""
    row = a.getparent()
    while row is not None and row.tag != "tr":
        row = row.getparent()
    if row is not None:
        cells = row.xpath("./td")
        if cells:
            return _text(cells[0])
    prev: Optional[str] = a.getprevious().text_content() if a.getprevious() is not None else None
    return (prev or "attachment").strip()
