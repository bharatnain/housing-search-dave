import copy

import pytest
import yaml

import doorman_profile as P
from conftest import FIXTURES


def raw(name):
    return yaml.safe_load((FIXTURES / name).read_text())


def problems_for(mutate, name="solo-studio.yaml"):
    d = raw(name)
    mutate(d)
    return P.validate(d)


def test_fixtures_load_clean():
    for name in ("solo-studio.yaml", "couple-2br.yaml"):
        assert P.load(FIXTURES / name)["household"]["name"]


def test_missing_file_says_set_me_up(tmp_path):
    with pytest.raises(P.ProfileError) as e:
        P.load(tmp_path / "profile.yaml")
    assert "set me up" in str(e.value)


def test_bad_yaml_is_reported(tmp_path):
    f = tmp_path / "profile.yaml"
    f.write_text("household: [unclosed\n")
    with pytest.raises(P.ProfileError) as e:
        P.load(f)
    assert "YAML" in str(e.value)


def test_env_var_picks_the_profile(monkeypatch):
    monkeypatch.setenv("DOORMAN_PROFILE", str(FIXTURES / "couple-2br.yaml"))
    assert P.load()["household"]["pets"] == "cat"


@pytest.mark.parametrize("field,mutate", [
    ("search.max_net_rent", lambda d: d["search"].update(max_net_rent=3000)),
    ("move.window", lambda d: d["move"].update(window=["2026-12-15", "2026-12-01"])),
    ("search.boroughs", lambda d: d["search"].update(boroughs=["jersey city"])),
    ("household.pets", lambda d: d["household"].update(pets="parrot")),
    ("search.areas", lambda d: d["search"].update(areas=[])),
    ("search.areas", lambda d: d["search"].update(areas=[{"name": "ASTORIA", "code": "x"}])),
    ("search.bedrooms", lambda d: d["search"].update(bedrooms={"min": 2, "max": 1})),
    ("streeteasy.daily_cap", lambda d: d["streeteasy"].update(daily_cap=0)),
    ("streeteasy.source", lambda d: d["streeteasy"].update(source="web")),
    ("search.zips", lambda d: d["search"].update(zips=["1002"])),
    ("search.max_gross_rent", lambda d: d["search"].update(max_gross_rent=True)),
    ("dashboard.artifact_url", lambda d: d["dashboard"].update(artifact_url="http://example.com")),
    ("household", lambda d: d.pop("household")),
])
def test_real_errors_name_the_field(field, mutate):
    probs = problems_for(mutate)
    assert probs, f"expected a problem for {field}"
    assert any(p.startswith(field) for p in probs), probs


def test_hand_edited_values_are_normalized_not_rejected():
    d = raw("solo-studio.yaml")
    text = yaml.safe_dump(d).replace("'11102'", "10003").replace("'2026-12-01'", "2026-12-01") \
        .replace("'2026-12-15'", "2026-12-15").replace("ELEVATOR", "elevator")
    d = yaml.safe_load(text)
    assert 10003 in d["search"]["zips"]          # YAML read it as an int
    assert not isinstance(d["move"]["window"][0], str)   # and the date as a date
    assert P.validate(d) == []
    assert "10003" in d["search"]["zips"]
    assert d["move"]["window"] == ["2026-12-01", "2026-12-15"]
    assert d["search"]["required_amenities"] == ["ELEVATOR"]


def test_short_int_zip_is_zero_padded():
    d = raw("solo-studio.yaml")
    d["search"]["zips"] = [7302]   # YAML would read 07302 as octal-ish int; pad back
    assert P.validate(d) == []
    assert d["search"]["zips"] == ["07302"]


def test_sweep_params_couple():
    p = P.sweep_params(P.load(FIXTURES / "couple-2br.yaml"))
    assert p["areas"] == ["PARK_SLOPE", "ASTORIA"]
    assert p["maxPrice"] == 5600
    assert p["minBedrooms"] == 2 and p["maxBedrooms"] == 2
    assert p["petsAllowed"] is True
    assert "amenities" not in p
    assert p["perPage"] == 100 and p["sortBy"] == "PRICE" and p["sortDirection"] == "ASCENDING"
    assert "noFeeOnly" not in p


def test_sweep_params_solo():
    p = P.sweep_params(P.load(FIXTURES / "solo-studio.yaml"))
    assert p["amenities"] == ["ELEVATOR"]
    assert "petsAllowed" not in p


def test_area_codes():
    assert P.area_codes(P.load(FIXTURES / "solo-studio.yaml")) == [401, 137]


def test_validate_does_not_share_state():
    d = raw("couple-2br.yaml")
    before = copy.deepcopy(d)
    assert P.validate(d) == []
    assert d == before
