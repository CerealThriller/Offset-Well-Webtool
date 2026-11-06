"""All states the tool knows about.

IMPLEMENTED holds working connectors. PLANNED lists the other producing
states with their public data portal, so the UI can show what is coming and
where a user can look by hand today. To add a state, write a connector
class (see texas.py or new_mexico.py) and add it to IMPLEMENTED.
"""

from __future__ import annotations

from typing import Optional

from shapely.geometry import Polygon, box

from .base import PoliteClient, StateConnector
from .colorado import ColoradoConnector
from .new_mexico import NewMexicoConnector
from .texas import TexasConnector

IMPLEMENTED: dict[str, type[StateConnector]] = {
    "TX": TexasConnector,
    "NM": NewMexicoConnector,
    "CO": ColoradoConnector,
}

# Rough state extents (lon/lat) so the tool asks only states that touch the search area.
STATE_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    "TX": (-106.65, 25.83, -93.51, 36.50),
    "NM": (-109.05, 31.33, -103.00, 37.00),
    "CO": (-109.06, 36.99, -102.04, 41.00),
    "OK": (-103.00, 33.62, -94.43, 37.00),
    "ND": (-104.05, 45.93, -96.55, 49.00),
    "WY": (-111.06, 40.99, -104.05, 45.01),
    "UT": (-114.05, 36.99, -109.04, 42.00),
    "MT": (-116.05, 44.36, -104.04, 49.00),
    "LA": (-94.04, 28.93, -88.82, 33.02),
    "KS": (-102.05, 36.99, -94.59, 40.00),
    "PA": (-80.52, 39.72, -74.69, 42.27),
    "WV": (-82.64, 37.20, -77.72, 40.64),
    "OH": (-84.82, 38.40, -80.52, 41.98),
    "CA": (-124.41, 32.53, -114.13, 42.01),
    "AK": (-179.15, 51.21, -129.98, 71.39),
    "AR": (-94.62, 33.00, -89.64, 36.50),
    "MS": (-91.66, 30.17, -88.10, 35.00),
    "AL": (-88.47, 30.22, -84.89, 35.01),
    "MI": (-90.42, 41.70, -82.41, 48.31),
    "NE": (-104.05, 40.00, -95.31, 43.00),
    "SD": (-104.06, 42.48, -96.44, 45.95),
}

PLANNED: list[dict] = [
    {"code": "OK", "name": "Oklahoma", "agency": "Oklahoma Corporation Commission",
     "portal_url": "https://oklahoma.gov/occ/divisions/oil-gas/oil-gas-data.html",
     "notes": "Well data and imaged forms (1002A completion reports carry surveys)."},
    {"code": "ND", "name": "North Dakota", "agency": "NDIC Oil and Gas Division",
     "portal_url": "https://www.dmr.nd.gov/oilgas/",
     "notes": "Well locations are public. Well files and digital surveys need a paid subscription."},
    {"code": "WY", "name": "Wyoming", "agency": "Wyoming Oil and Gas Conservation Commission",
     "portal_url": "https://wogcc.wyo.gov/", "notes": "Well data and scanned well files."},
    {"code": "UT", "name": "Utah", "agency": "Division of Oil, Gas and Mining",
     "portal_url": "https://oilgas.ogm.utah.gov/", "notes": "Well data and scanned well files."},
    {"code": "MT", "name": "Montana", "agency": "Board of Oil and Gas Conservation",
     "portal_url": "https://bogapps.dnrc.mt.gov/dataminer/", "notes": "Data miner with well files."},
    {"code": "LA", "name": "Louisiana", "agency": "Department of Energy and Natural Resources (SONRIS)",
     "portal_url": "https://www.sonris.com/", "notes": "SONRIS well data and document images."},
    {"code": "KS", "name": "Kansas", "agency": "Kansas Geological Survey / KCC",
     "portal_url": "https://www.kgs.ku.edu/Magellan/Qualified/", "notes": "Well data and ACO-1 forms."},
    {"code": "PA", "name": "Pennsylvania", "agency": "Department of Environmental Protection",
     "portal_url": "https://greenport.pa.gov/ReportExtracts/OG/", "notes": "Well data. Surveys in eFACTS documents."},
    {"code": "WV", "name": "West Virginia", "agency": "DEP Office of Oil and Gas",
     "portal_url": "https://tagis.dep.wv.gov/oog/", "notes": "Well data and scanned well records."},
    {"code": "OH", "name": "Ohio", "agency": "ODNR Division of Oil and Gas",
     "portal_url": "https://ohiodnr.gov/discover-and-learn/safety-conservation/about-ODNR/oil-gas/oil-gas-resources/",
     "notes": "Well data and scanned well records."},
    {"code": "CA", "name": "California", "agency": "CalGEM",
     "portal_url": "https://www.conservation.ca.gov/calgem/", "notes": "WellSTAR well data and records."},
    {"code": "AK", "name": "Alaska", "agency": "Alaska Oil and Gas Conservation Commission",
     "portal_url": "https://www.commerce.alaska.gov/web/aogcc/", "notes": "Well data and digital surveys for many wells."},
    {"code": "AR", "name": "Arkansas", "agency": "Arkansas Oil and Gas Commission",
     "portal_url": "https://www.aogc.state.ar.us/", "notes": "Well data and scanned well files."},
    {"code": "MS", "name": "Mississippi", "agency": "State Oil and Gas Board",
     "portal_url": "https://www.ogb.state.ms.us/", "notes": "Well data and scanned well files."},
    {"code": "AL", "name": "Alabama", "agency": "State Oil and Gas Board",
     "portal_url": "https://www.gsa.state.al.us/ogb/", "notes": "Well data and scanned well files."},
    {"code": "MI", "name": "Michigan", "agency": "EGLE Oil, Gas and Minerals",
     "portal_url": "https://www.michigan.gov/egle/about/organization/oil-gas-and-minerals", "notes": "Well data."},
    {"code": "NE", "name": "Nebraska", "agency": "Nebraska Oil and Gas Conservation Commission",
     "portal_url": "https://nogcc.ne.gov/", "notes": "Well data and scanned well files."},
    {"code": "SD", "name": "South Dakota", "agency": "DANR Oil and Gas",
     "portal_url": "https://danr.sd.gov/Environment/MineralsMining/OilGas/", "notes": "Well data."},
]


def get_connector(code: str, http: Optional[PoliteClient] = None) -> StateConnector:
    return IMPLEMENTED[code](http)


def states_for_area(area: Polygon, requested: Optional[list[str]] = None) -> list[str]:
    """Return implemented states whose extent touches the search area."""
    codes = requested or list(IMPLEMENTED)
    return [c for c in codes if c in IMPLEMENTED and box(*STATE_BOUNDS[c]).intersects(area)]


def catalog() -> list[dict]:
    out = [cls.__new__(cls).describe() for cls in IMPLEMENTED.values()]
    out += [{**p, "status": "planned", "digital_surveys": False, "document_download": False} for p in PLANNED]
    return out
