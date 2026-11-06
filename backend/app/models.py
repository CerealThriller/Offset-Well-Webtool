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
    source_pages: list[int] = Field(default_factory=list)
    company: Optional[str] = None
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
    access: str = "direct"  # "direct" or "manual" (site blocks automated download)


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
    survey_status: str = "not searched"
    messages: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def key(self) -> str:
        return f"{self.state}-{self.api}"
