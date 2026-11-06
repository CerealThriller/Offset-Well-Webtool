"""Domain models shared by the parser, connectors, extraction and export code."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field


class Datum(str, Enum):
    NAD27 = "NAD27"
    NAD83 = "NAD83"
    WGS84 = "WGS84"


class NorthReference(str, Enum):
    TRUE = "true"
    GRID = "grid"
    MAGNETIC = "magnetic"
    UNKNOWN = "unknown"


class SurveyType(str, Enum):
    DIRECTIONAL = "directional"
    INCLINATION_ONLY = "inclination_only"
    PLAN = "plan"


class SurveyStation(BaseModel):
    """One survey station. Depths and offsets are in feet, angles in degrees."""

    md: float
    inc: float
    azi: Optional[float] = None
    tvd: Optional[float] = None
    ns: Optional[float] = None  # + north
    ew: Optional[float] = None  # + east
    vs: Optional[float] = None
    dls: Optional[float] = None  # deg / 100 ft
    disp: Optional[float] = None  # reported total displacement (inclination-only reports)
    # Values that the tool calculated (minimum curvature) for comparison.
    calc_tvd: Optional[float] = None
    calc_ns: Optional[float] = None
    calc_ew: Optional[float] = None
    calc_dls: Optional[float] = None
    flags: list[str] = Field(default_factory=list)


class Survey(BaseModel):
    survey_type: SurveyType
    stations: list[SurveyStation]
    north_reference: NorthReference = NorthReference.UNKNOWN
    source: str = ""  # URL, file name or "EDM"
    source_file_id: str = ""  # the stored source document, for review
    source_pages: list[int] = Field(default_factory=list)
    company: Optional[str] = None
    grid_convergence_deg: Optional[float] = None  # from the report header, if printed
    notes: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def flagged_count(self) -> int:
        return sum(1 for s in self.stations if s.flags)


class LatLon(BaseModel):
    lat: float
    lon: float


class PlannedWell(BaseModel):
    """A planned well read from the Compass EDM export."""

    name: str
    wellbore: str = ""
    design: str = ""
    surface_nad83: LatLon
    surface_nad27: Optional[LatLon] = None
    north_reference: NorthReference = NorthReference.TRUE
    grid_convergence_deg: Optional[float] = None
    survey: Survey
    path_nad83: list[LatLon] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SurveyDocument(BaseModel):
    """A document on a state site that holds (or can hold) a survey."""

    url: str
    title: str = ""
    doc_type: str = ""  # for example "Directional Survey", "W-12"
    date: Optional[str] = None
    likely_survey: bool = False
    likely_plat: bool = False
    access: str = "direct"  # "direct" or "manual" (site blocks automated download)


class SourceFile(BaseModel):
    """A file that the tool downloaded or a user uploaded, kept for review."""

    file_id: str  # sha256 of the content; the file is in the shared file store
    view_file_id: str = ""  # PDF copy for the browser, when the original is TIFF or an image
    title: str = ""
    source_url: str = ""  # state site URL, or "upload: <name>"
    kind: str = "document"  # survey, plat, well file, upload
    file_name: str = ""
    size: int = 0
    pages: int = 0
    survey_pages: list[int] = Field(default_factory=list)
    plat_pages: list[int] = Field(default_factory=list)


class Plat(BaseModel):
    """A well location plat (Texas plat, New Mexico C-102, Colorado location plat).

    The tool does not read the plat. It keeps the plat pages as their own
    PDF so the engineer can check the locations before the Compass load.
    """

    file_id: str  # the one-plat PDF in the file store
    source: str
    source_file_id: str = ""  # the document that holds the plat pages
    title: str = ""
    pages: list[int] = Field(default_factory=list)  # pages in the source document


class OffsetWell(BaseModel):
    state: str
    api: str
    name: str = ""
    operator: str = ""
    status: str = ""
    well_type: str = ""
    trajectory: str = ""  # vertical / directional / horizontal
    surface_nad83: LatLon
    bottom_hole_nad83: Optional[LatLon] = None
    total_depth_md: Optional[float] = None
    total_depth_tvd: Optional[float] = None
    distance_to_plan_ft: Optional[float] = None
    nearest_planned_well: Optional[str] = None
    detail_url: Optional[str] = None
    documents: list[SurveyDocument] = Field(default_factory=list)
    surveys: list[Survey] = Field(default_factory=list)
    plats: list[Plat] = Field(default_factory=list)
    files: list[SourceFile] = Field(default_factory=list)
    survey_status: str = "not searched"
    messages: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def key(self) -> str:
        return f"{self.state}-{self.api}"
