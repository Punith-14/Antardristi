"""
The boundary set (GAUL 2015 or 2025) chosen in one place, and the measured
rule for switching. GAUL 2015 is deprecated in Earth Engine; GAUL 2025 renames
the fields. Nothing outside geo/boundaries.py may name either set's fields.
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

pytest.importorskip("ee")

from geo import boundaries as B  # noqa: E402


def test_the_default_is_still_2015(monkeypatch):
    monkeypatch.delenv("BOUNDARY_SET", raising=False)
    assert B.source() == "FAO/GAUL/2015/level2"
    assert B.name_field("district") == "ADM2_NAME"
    assert B.cache_tag() is None, "the default must not re-key any cached result"


def test_2025_uses_its_own_fields_and_an_iso_country_filter(monkeypatch):
    monkeypatch.setenv("BOUNDARY_SET", "2025")
    assert B.source() == "FAO/GAUL/2025/level2"
    assert B.name_field("state") == "GAUL1_NAME" and B.name_field("district") == "GAUL2_NAME"
    assert B.active()["country"] == ("ISO3_CODE", "IND")
    assert B.cache_tag() == "gaul_2025"


def test_an_unknown_set_is_refused(monkeypatch):
    monkeypatch.setenv("BOUNDARY_SET", "2030")
    with pytest.raises(ValueError, match="not one of"):
        B.active()


def test_no_module_hard_codes_a_sets_field_names():
    for path in ("geo/regions.py", "geo/districts.py", "pipeline/analysis.py"):
        text = (BACKEND / path).read_text(encoding="utf-8")
        for field in ("ADM0_NAME", "ADM1_NAME", "ADM2_NAME", "GAUL1_NAME", "FAO/GAUL/2015/level"):
            assert f'"{field}' not in text, f"{path} hard-codes {field}"


def test_a_named_result_is_never_served_across_boundary_sets(monkeypatch):
    pytest.importorskip("fastapi")
    import main
    named = main.FloodRequest(region="kerala", post_start="2018-08-01", post_end="2018-08-31")
    drawn = main.FloodRequest(bbox=[76, 9, 77, 10], post_start="2018-08-01", post_end="2018-08-31")
    monkeypatch.delenv("BOUNDARY_SET", raising=False)
    assert "boundary_set" not in named.cache_key()
    monkeypatch.setenv("BOUNDARY_SET", "2025")
    assert named.cache_key()["boundary_set"] == "gaul_2025"
    assert "boundary_set" not in drawn.cache_key(), "a drawn area does not depend on the set"


def test_not_found_names_the_active_set(monkeypatch):
    from geo import regions
    monkeypatch.setenv("BOUNDARY_SET", "2025")
    assert "FAO/GAUL/2025/level2" in str(regions.RegionNotFound("Ladakh"))


# ------------------------------------------------------- the switching rule

from scripts.compare_boundaries import decide, states_lost  # noqa: E402

GOOD = {
    "post_2015": {"tested": 30, "2015": 0, "2025": 24},
    "states_lost": [],
    "kerala": {"districts": {"2015": 14, "2025": 14}, "area_change": 0.004},
}


def test_switch_when_all_three_hold():
    decision = decide(GOOD)
    assert decision["switch"] is True
    assert decision["reasons"][0] == "post-2015 districts resolved: 0 -> 24 of 30"


@pytest.mark.parametrize("change,why", [
    ({"post_2015": {"tested": 30, "2015": 0, "2025": 10}}, "at least 50%"),
    ({"post_2015": {"tested": 30, "2015": 12, "2025": 12}}, "needs more than 12"),
    ({"states_lost": ["Jammu and Kashmir"]}, "lost under 2025: Jammu and Kashmir"),
    ({"kerala": {"districts": {"2015": 14, "2025": 15}, "area_change": 0.0}}, "districts 14 -> 15"),
    ({"kerala": {"districts": {"2015": 14, "2025": 14}, "area_change": 0.05}}, "+5.0%"),
])
def test_stay_when_any_fails(change, why):
    decision = decide({**GOOD, **change})
    assert decision["switch"] is False
    assert any(why in r for r in decision["reasons"]), decision["reasons"]


def test_a_rename_is_not_a_loss():
    aliases = {"odisha": "orissa", "uttarakhand": "uttaranchal"}
    assert states_lost(["Orissa", "Kerala", "Uttaranchal"], ["Odisha", "Kerala", "Uttarakhand"], aliases) == []
    assert states_lost(["Kerala", "Goa"], ["Kerala"], aliases) == ["Goa"]
