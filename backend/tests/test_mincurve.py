import math

from app.models import Survey, SurveyStation, SurveyType
from app.survey.mincurve import compute_positions, min_curve_step, validate_survey


def test_straight_vertical():
    dtvd, dn, de, dls = min_curve_step(0, 0, 0, 1000, 0, 0)
    assert dtvd == 1000 and dn == 0 and de == 0 and dls == 0


def test_quarter_circle_build():
    # 0 to 90 deg over 900 ft of MD: radius = 900 / (pi/2)
    r = 900 / (math.pi / 2)
    dtvd, dn, de, dls = min_curve_step(1000, 0, 90, 1900, 90, 90)
    assert math.isclose(dtvd, r, rel_tol=1e-9)
    assert math.isclose(de, r, rel_tol=1e-9)
    assert abs(dn) < 1e-9
    assert math.isclose(dls, 10.0, rel_tol=1e-9)


def _survey():
    st = [SurveyStation(md=md, inc=inc, azi=45.0) for md, inc in [(0, 0), (1000, 0), (1100, 5), (1200, 10), (1300, 15), (1400, 20)]]
    compute_positions(st)
    for s in st:
        s.tvd, s.ns, s.ew = round(s.calc_tvd, 2), round(s.calc_ns, 2), round(s.calc_ew, 2)
        s.calc_tvd = s.calc_ns = s.calc_ew = None
    return Survey(survey_type=SurveyType.DIRECTIONAL, stations=st)


def test_clean_survey_has_no_flags():
    s = validate_survey(_survey())
    assert s.flagged_count == 0


def test_misread_inclination_is_flagged_once():
    s = _survey()
    s.stations[3].inc = 1.0  # OCR read 10 as 1
    validate_survey(s)
    flagged = [st.md for st in s.stations if st.flags]
    assert 1200 in flagged or 1300 in flagged
    assert len(flagged) <= 3


def test_range_checks():
    s = Survey(survey_type=SurveyType.DIRECTIONAL, stations=[SurveyStation(md=0, inc=0, azi=0), SurveyStation(md=100, inc=200, azi=400)])
    validate_survey(s)
    assert any("Inclination out of range" in f for f in s.stations[1].flags)
    assert any("Azimuth out of range" in f for f in s.stations[1].flags)


def test_inclination_only_tvd():
    st = [SurveyStation(md=0, inc=0), SurveyStation(md=1000, inc=2), SurveyStation(md=2000, inc=2)]
    s = validate_survey(Survey(survey_type=SurveyType.INCLINATION_ONLY, stations=st))
    assert s.stations[-1].calc_ns is None
    assert 1990 < s.stations[-1].calc_tvd < 2000
