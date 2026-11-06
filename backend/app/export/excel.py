"""Excel workbook: a summary tab, a tab per planned well, a tab per offset well."""

from __future__ import annotations

import io
import re
from typing import Iterable

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from ..extraction.plats import plat_png
from ..models import OffsetWell, Plat, PlannedWell, Survey
from ..survey.verify import summary as check_summary

HEAD = Font(bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor="1F4E79")
FLAG_FILL = PatternFill("solid", fgColor="FFF2CC")
OK_FILL = PatternFill("solid", fgColor="E2EFDA")
BAD_FILL = PatternFill("solid", fgColor="F8CBAD")
PLAT_IMAGE_COL = "P"
PLAT_IMAGE_WIDTH_PX = 640
TITLE = Font(bold=True, size=13)
BOLD = Font(bold=True)

STATION_COLS = [
    ("MD (ft)", "md"),
    ("Inc (deg)", "inc"),
    ("Azi (deg)", "azi"),
    ("TVD (ft)", "tvd"),
    ("N/S (ft)", "ns"),
    ("E/W (ft)", "ew"),
    ("VS (ft)", "vs"),
    ("DLS (deg/100ft)", "dls"),
    ("Disp (ft)", "disp"),
    ("Calc TVD (ft)", "calc_tvd"),
    ("Calc N/S (ft)", "calc_ns"),
    ("Calc E/W (ft)", "calc_ew"),
    ("Calc DLS", "calc_dls"),
    ("QC flags", "flags"),
]


def _safe_title(name: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "-", name).strip()[:31] or "Sheet"
    title, n = base, 2
    while title.lower() in used:
        suffix = f" ({n})"
        title = base[: 31 - len(suffix)] + suffix
        n += 1
    used.add(title.lower())
    return title


def _header(ws: Worksheet, row: int, labels: Iterable[str]) -> None:
    for col, label in enumerate(labels, start=1):
        c = ws.cell(row=row, column=col, value=label)
        c.font, c.fill = HEAD, HEAD_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center")


def _kv(ws: Worksheet, row: int, pairs: list[tuple[str, object]]) -> int:
    for k, v in pairs:
        ws.cell(row=row, column=1, value=k).font = BOLD
        ws.cell(row=row, column=2, value=v)
        row += 1
    return row


def _survey_block(ws: Worksheet, row: int, survey: Survey, title: str) -> int:
    ws.cell(row=row, column=1, value=title).font = TITLE
    row += 1
    row = _kv(
        ws,
        row,
        [
            ("Survey type", survey.survey_type.value),
            ("North reference", survey.north_reference.value),
            ("Source", survey.source),
            ("Source pages", ", ".join(map(str, survey.source_pages)) or "-"),
            ("Survey company", survey.company or "-"),
            ("Stations", len(survey.stations)),
            ("Rows with QC flags", survey.flagged_count),
        ],
    )
    for note in survey.notes:
        ws.cell(row=row, column=1, value="Note").font = BOLD
        ws.cell(row=row, column=2, value=note)
        row += 1
    row += 1
    _header(ws, row, [c[0] for c in STATION_COLS])
    ws.freeze_panes = ws.cell(row=row + 1, column=1) if ws.freeze_panes is None else ws.freeze_panes
    row += 1
    for st in survey.stations:
        for col, (_, attr) in enumerate(STATION_COLS, start=1):
            val = getattr(st, attr)
            if attr == "flags":
                val = "; ".join(val) if val else None
            elif isinstance(val, float):
                val = round(val, 2)
            c = ws.cell(row=row, column=col, value=val)
            if st.flags:
                c.fill = FLAG_FILL
        row += 1
    return row + 2


def _plat_block(ws: Worksheet, row: int, o: OffsetWell) -> int:
    ws.cell(row=row, column=1, value="Plat checks").font = TITLE
    row += 1
    if o.plat_checks:
        _header(ws, row, ["Check", "Plat", "Other source", "Difference (ft)", "Tolerance (ft)", "Result", "Note"])
        row += 1
        for c in o.plat_checks:
            result = "ok" if c.ok else ("check" if c.ok is False else "-")
            vals = [c.item, c.plat_value, c.other_value, c.difference_ft, c.tolerance_ft, result, c.note]
            for col, v in enumerate(vals, start=1):
                cell = ws.cell(row=row, column=col, value=v)
                if col == 6 and c.ok is not None:
                    cell.fill = OK_FILL if c.ok else BAD_FILL
            row += 1
    else:
        ws.cell(row=row, column=1, value="No plat locations to compare.")
        row += 1
    row += 1
    for i, p in enumerate(o.plats, start=1):
        ws.cell(row=row, column=1, value=f"Plat {i}").font = BOLD
        kind = {True: "as drilled", False: "permit"}.get(p.as_drilled, "type not stated")
        ws.cell(row=row, column=2, value=f"{p.title} ({kind}), datum {p.datum or 'not printed'}")
        ws.cell(row=row, column=3, value=p.source)
        row += 1
        if p.points:
            _header(ws, row, ["Point", "Latitude", "Longitude", "MD (ft)", "X", "Y", "Footage"])
            row += 1
            for pt in p.points:
                for col, v in enumerate([pt.label, pt.lat, pt.lon, pt.md, pt.x, pt.y, pt.footage], start=1):
                    ws.cell(row=row, column=col, value=v)
                row += 1
        for n in p.notes:
            ws.cell(row=row, column=1, value="Note").font = BOLD
            ws.cell(row=row, column=2, value=n)
            row += 1
        row += 1
    return row + 1


def _plat_images(ws: Worksheet, plats: list[Plat]) -> None:
    """Put each plat image to the right of the tables."""
    anchor_row = 1
    for p in plats:
        try:
            img = XLImage(io.BytesIO(plat_png(p.file_id)))
        except Exception:  # a missing file must not break the export
            continue
        scale = PLAT_IMAGE_WIDTH_PX / img.width
        img.width, img.height = PLAT_IMAGE_WIDTH_PX, int(img.height * scale)
        ws.add_image(img, f"{PLAT_IMAGE_COL}{anchor_row}")
        anchor_row += int(img.height / 20) + 2  # about 20 px for each row


def _widths(ws: Worksheet, widths: dict[int, float]) -> None:
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w


def build_workbook(
    planned: list[PlannedWell],
    offsets: list[OffsetWell],
    job_info: dict,
) -> bytes:
    wb = Workbook()
    used: set[str] = set()

    ws = wb.active
    ws.title = _safe_title("Summary", used)
    ws.cell(row=1, column=1, value="Offset well survey search").font = TITLE
    row = _kv(ws, 3, [(k, v) for k, v in job_info.items()])
    row += 1
    ws.cell(row=row, column=1, value="Planned wells").font = TITLE
    row += 1
    _header(ws, row, ["Name", "Design", "Lat NAD83", "Lon NAD83", "Lat NAD27", "Lon NAD27", "North ref", "Convergence", "Warnings"])
    row += 1
    for p in planned:
        vals = [
            p.name,
            p.design,
            round(p.surface_nad83.lat, 7),
            round(p.surface_nad83.lon, 7),
            round(p.surface_nad27.lat, 7) if p.surface_nad27 else None,
            round(p.surface_nad27.lon, 7) if p.surface_nad27 else None,
            p.north_reference.value,
            p.grid_convergence_deg,
            "; ".join(p.warnings) or None,
        ]
        for col, v in enumerate(vals, start=1):
            ws.cell(row=row, column=col, value=v)
        row += 1
    row += 1
    ws.cell(row=row, column=1, value="Offset wells").font = TITLE
    row += 1
    off_cols = [
        "Tab", "State", "API", "Name", "Operator", "Status", "Trajectory", "Distance to plan (ft)",
        "Nearest plan", "Surface lat NAD83", "Surface lon NAD83", "Survey status", "Surveys", "Flagged rows",
        "Survey documents", "Record link",
    ]
    _header(ws, row, off_cols)
    row += 1
    tab_names = {}
    for o in offsets:
        if o.surveys or o.plats:
            tab_names[o.key] = _safe_title(f"{o.api} {o.name}", used)
    for o in sorted(offsets, key=lambda x: x.distance_to_plan_ft or 0):
        vals = [
            tab_names.get(o.key),
            o.state,
            o.api,
            o.name,
            o.operator,
            o.status,
            o.trajectory,
            round(o.distance_to_plan_ft) if o.distance_to_plan_ft is not None else None,
            o.nearest_planned_well,
            round(o.surface_nad83.lat, 7),
            round(o.surface_nad83.lon, 7),
            o.survey_status,
            len(o.surveys),
            sum(s.flagged_count for s in o.surveys),
            len(o.plats),
            check_summary(o.plat_checks),
            "\n".join(f"{d.title}: {d.url}" for d in o.documents if d.likely_survey) or None,
            o.detail_url,
        ]
        for col, v in enumerate(vals, start=1):
            c = ws.cell(row=row, column=col, value=v)
            if col == 1 and v:
                c.hyperlink = f"#'{v}'!A1"
                c.font = Font(color="0563C1", underline="single")
        row += 1
    _widths(ws, {1: 26, 2: 18, 3: 16, 4: 30, 5: 30, 6: 18, 7: 16, 8: 12, 9: 22, 12: 18, 17: 60, 18: 40})

    for p in planned:
        wsp = wb.create_sheet(_safe_title(f"PLAN {p.name}", used))
        _survey_block(wsp, 1, p.survey, f"Planned well: {p.name}  {p.design}")
        _widths(wsp, {1: 18, 2: 14, 14: 60})

    for o in offsets:
        if o.key not in tab_names:
            continue
        wso = wb.create_sheet(tab_names[o.key])
        r = _kv(
            wso,
            1,
            [
                ("Well", o.name),
                ("API", o.api),
                ("State", o.state),
                ("Operator", o.operator),
                ("Status", o.status),
                ("Distance to plan (ft)", round(o.distance_to_plan_ft) if o.distance_to_plan_ft is not None else None),
                ("Surface NAD83 (GIS)", f"{o.surface_nad83.lat:.7f}, {o.surface_nad83.lon:.7f}"),
                ("Bottom hole NAD83 (GIS)",
                 f"{o.bottom_hole_nad83.lat:.7f}, {o.bottom_hole_nad83.lon:.7f}" if o.bottom_hole_nad83 else "-"),
            ],
        )
        r += 1
        if o.plats:
            r = _plat_block(wso, r, o)
        for i, s in enumerate(o.surveys, start=1):
            r = _survey_block(wso, r, s, f"Survey {i} of {len(o.surveys)}")
        _widths(wso, {1: 22, 2: 30, 3: 30, 4: 14, 14: 60})
        _plat_images(wso, o.plats)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
