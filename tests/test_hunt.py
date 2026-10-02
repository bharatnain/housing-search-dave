import json
import sqlite3

import pytest

import doorman_profile
import hunt
from conftest import FIXTURES

SOLO = doorman_profile.load(FIXTURES / "solo-studio.yaml")
COUPLE = doorman_profile.load(FIXTURES / "couple-2br.yaml")
PAGE = FIXTURES / "sweep-page.json"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(hunt, "DB", tmp_path / "hunt.db")
    hunt.cmd_init()
    return tmp_path


def rows(sql, *a):
    conn = sqlite3.connect(hunt.DB)
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, a)]


def sweep(store, capsys, prof, ran_at, listings=None, total=None):
    page = json.loads(PAGE.read_text())
    if listings is not None:
        page["listings"] = [l for l in page["listings"] if l["id"] in listings]
    if total is not None:
        page["totalCount"] = total
    p = store / f"page-{ran_at}.json"
    p.write_text(json.dumps(page))
    capsys.readouterr()
    hunt.cmd_import_sweep(ran_at, [str(p)], prof)
    return json.loads(capsys.readouterr().out)


def status(lid):
    return rows("SELECT status, exclude_reason FROM listings WHERE id=?", lid)[0]


def test_north_rule_reads_manhattan_only():
    assert hunt.exclusion_reason(SOLO, "245 East 98th Street", 2500, borough="Brooklyn") is None
    assert hunt.exclusion_reason(SOLO, "245 East 98th Street", 2500, lat=40.66) is None
    assert hunt.exclusion_reason(SOLO, "245 East 98th Street", 2500, borough="Manhattan").startswith("north")
    assert hunt.exclusion_reason(COUPLE, "245 East 98th Street", 2500, beds=2, borough="Manhattan") is None


def test_coop_cap_beds_sqft_lease():
    assert hunt.exclusion_reason(SOLO, "1 Main St", 2500, building_type="CO_OP") == "co-op"
    assert hunt.exclusion_reason(COUPLE, "1 Main St", 5000, beds=2, sqft=800, building_type="CO_OP") is None
    assert "over" in hunt.exclusion_reason(SOLO, "1 Main St", 2850)
    assert hunt.exclusion_reason(SOLO, "1 Main St", 2500, beds=1) == "1 bedrooms"
    assert "under 750" in hunt.exclusion_reason(COUPLE, "1 Main St", 5000, beds=2, sqft=700)
    assert hunt.exclusion_reason(COUPLE, "1 Main St", 5000, beds=2, sqft=None) is None
    assert hunt.exclusion_reason(SOLO, "1 Main St", 2500, lease_months=6) == "6-month lease"


def test_import_applies_profile(store, capsys):
    out = sweep(store, capsys, SOLO, "2026-10-01T10:00")
    assert [n["id"] for n in out["new"]] == ["1001"]
    assert status("1002")["exclude_reason"] == "north of street 96"
    assert status("1005")["exclude_reason"] == "co-op"
    assert status("1008")["status"] == "excluded"
    assert out["partial"] is False


def test_couple_import_and_net_effective(store, capsys):
    out = sweep(store, capsys, COUPLE, "2026-10-01T10:00")
    assert {n["id"] for n in out["new"]} == {"1003", "1006", "1007"}
    assert rows("SELECT net_effective FROM listings WHERE id='1003'")[0]["net_effective"] == 4767
    assert "under 750" in status("1004")["exclude_reason"]


def test_price_change_pass_and_gone(store, capsys):
    sweep(store, capsys, SOLO, "2026-10-01T10:00")
    hunt.cmd_decide("1001", "pass")
    page = json.loads(PAGE.read_text())
    page["listings"][0]["price"] = 2300
    p = store / "p2.json"
    p.write_text(json.dumps(page))
    capsys.readouterr()
    hunt.cmd_import_sweep("2026-10-02T10:00", [str(p)], SOLO)
    out = json.loads(capsys.readouterr().out)
    assert out["price_changes"][0]["new"] == 2300
    assert status("1001")["status"] == "passed"


def test_full_sweep_marks_gone_partial_does_not(store, capsys):
    sweep(store, capsys, COUPLE, "2026-10-01T10:00")
    out = sweep(store, capsys, COUPLE, "2026-10-02T10:00", listings=["1001", "1002", "1003", "1004", "1005"], total=10)
    assert out["partial"] is True and out["gone"] == []
    assert status("1006")["status"] == "active"
    out = sweep(store, capsys, COUPLE, "2026-10-03T10:00", listings=["1003", "1004"], total=2)
    assert {g["id"] for g in out["gone"]} == {"1006", "1007"}


def test_walkup_exclusion_survives_resweep(store, capsys):
    sweep(store, capsys, COUPLE, "2026-10-01T10:00")
    hunt.cmd_score_details(str(FIXTURES / "details-1007.json"), COUPLE)
    assert status("1007")["exclude_reason"] == "walk-up floor 4"
    out = sweep(store, capsys, COUPLE, "2026-10-02T10:00")
    assert status("1007")["exclude_reason"] == "walk-up floor 4"
    assert "1007" not in {n["id"] for n in out["new"]}


def test_score_listing():
    W = hunt.DEFAULT_WEIGHTS
    score, stab, notes = hunt.score_listing(json.loads((FIXTURES / "details-1001.json").read_text()), W)
    assert score == 2.5   # floor 4 +1, city view +1, modern +0.5
    score, stab, _ = hunt.score_listing(json.loads((FIXTURES / "details-1003.json").read_text()), W)
    assert stab == "stated" and score == 2   # floor 3 with no light evidence -1, stated +3
    assert hunt.unit_floor("1203") == 12 and hunt.unit_floor("PH") is None


def test_decide_pursue(store, capsys):
    sweep(store, capsys, COUPLE, "2026-10-01T10:00")
    hunt.cmd_decide("1003", "pursue")
    p = rows("SELECT * FROM pipeline")
    assert p[0]["stage"] == "shortlisted" and p[0]["price"] == 4767
    conn = sqlite3.connect(hunt.DB)
    conn.execute("UPDATE pipeline SET stage='viewing'")
    conn.commit()
    hunt.cmd_decide("1003", "pursue")
    assert rows("SELECT stage FROM pipeline")[0]["stage"] == "viewing"


def test_relisted_unit_takes_new_id(store, capsys):
    sweep(store, capsys, SOLO, "2026-10-01T10:00")
    hunt.cmd_decide("1001", "pursue")
    page = json.loads(PAGE.read_text())
    page["listings"][0]["id"] = 9999
    page["listings"][0]["price"] = 2600
    p = store / "relist.json"
    p.write_text(json.dumps(page))
    for day in ("2026-10-02T10:00", "2026-10-03T10:00"):
        capsys.readouterr()
        hunt.cmd_import_sweep(day, [str(p)], SOLO)
        out = json.loads(capsys.readouterr().out)
        assert "9999" not in {n["id"] for n in out["new"]}
    assert rows("SELECT price FROM listings WHERE id='9999'")[0]["price"] == 2600
    assert rows("SELECT COUNT(*) n FROM listings WHERE id='1001'")[0]["n"] == 0
    assert rows("SELECT listing_ref FROM pipeline")[0]["listing_ref"] == "9999"


def test_gone_unit_back_is_new(store, capsys):
    sweep(store, capsys, SOLO, "2026-10-01T10:00")
    sweep(store, capsys, SOLO, "2026-10-02T10:00", listings=["1002"], total=1)
    assert status("1001")["status"] == "gone"
    out = sweep(store, capsys, SOLO, "2026-10-03T10:00")
    assert "1001" in {n["id"] for n in out["new"]} and status("1001")["status"] == "active"


def test_score_uses_given_weights_without_store(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hunt, "DB", tmp_path / "hunt.db")   # never created
    w = tmp_path / "draft.json"
    w.write_text(json.dumps({"views_each": 3}))
    hunt.cmd_score(str(FIXTURES / "details-1001.json"), str(w))
    assert json.loads(capsys.readouterr().out)["score"] == 4.5
    assert not (tmp_path / "hunt.db").exists()


def test_inline_json_rows(store, capsys):
    hunt.cmd_seed_pipeline('[{"address": "1 Main St", "unit": "2A", "stage": "shortlisted"}]')
    assert rows("SELECT stage FROM pipeline")[0]["stage"] == "shortlisted"
