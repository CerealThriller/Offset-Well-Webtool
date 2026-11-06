"""Write tests/fixtures/sample_edm.xml: a synthetic Compass EDM export.

The layout follows Compass EDM XML exports (one element per table row,
columns as attributes). It is synthetic. Replace it with a real export to
test the parser against your company's EDM template.
"""

from pathlib import Path


def plan(kop: float, azi: float, td: float, build: float = 10.0) -> list[tuple[float, float, float]]:
    st = [(0.0, 0.0, 0.0), (kop, 0.0, azi)]
    md = kop
    inc = 0.0
    while inc < 90.0:
        md += 100.0
        inc = min(90.0, inc + build)
        st.append((md, inc, azi))
    while md < td:
        md = min(td, md + 500.0)
        st.append((md, 90.0, azi))
    return st


def main() -> None:
    rows = [
        '<CD_PROJECT project_id="P1" project_name="Loving Test" geo_datum_name="NAD27" />',
        '<CD_SITE site_id="S1" project_id="P1" site_name="TEST PAD" loc_lat="31.9300000" loc_long="-103.4900000" '
        'geo_datum_name="North American Datum 1927" />',
    ]
    wells = [("W1", "TEST 1H", 31.93, -103.4900, 0.0), ("W2", "TEST 2H", 31.93, -103.4897, 180.0)]
    for wid, name, lat, lon, azi in wells:
        rows.append(
            f'<CD_WELL well_id="{wid}" site_id="S1" well_common_name="{name}" geo_latitude="{lat}" geo_longitude="{lon}" />'
        )
        rows.append(f'<CD_WELLBORE wellbore_id="{wid}B" well_id="{wid}" wellbore_name="OH" />')
        rows.append(f'<CD_DESIGN design_id="{wid}D" wellbore_id="{wid}B" design_name="Plan 1" phase="PLAN" />')
        for i, (md, inc, az) in enumerate(plan(9000.0, azi, 19000.0)):
            rows.append(
                f'<CD_SURVEY_STATION design_id="{wid}D" wellbore_id="{wid}B" sequence_no="{i}" '
                f'md="{md}" inclination="{inc}" azimuth="{az}" />'
            )
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<export>\n  ' + "\n  ".join(rows) + "\n</export>\n"
    out = Path(__file__).parent / "fixtures" / "sample_edm.xml"
    out.write_text(xml)
    print(out)


if __name__ == "__main__":
    main()
