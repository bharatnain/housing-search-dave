"""End to end for two different households over the same sweep page: each profile's
exclusions, scoring, decisions and dashboard stay its own."""
import sqlite3

import doorman_profile
import hunt
import hunt_dashboard
from conftest import FIXTURES

SOLO = FIXTURES / "solo-studio.yaml"
COUPLE = FIXTURES / "couple-2br.yaml"


def run(tmp_path, monkeypatch, capsys, prof_path, details, pursue):
    monkeypatch.setattr(hunt, "DB", tmp_path / "hunt.db")
    monkeypatch.setenv("DOORMAN_PROFILE", str(prof_path))
    prof = doorman_profile.load()
    hunt.cmd_init()
    hunt.cmd_import_sweep("2026-10-01T10:00", [str(FIXTURES / "sweep-page.json")], prof)
    for d in details:
        hunt.cmd_score_details(str(FIXTURES / f"details-{d}.json"), prof)
    hunt.cmd_decide(pursue, "pursue")
    capsys.readouterr()
    conn = sqlite3.connect(hunt.DB)
    conn.row_factory = sqlite3.Row
    active = {r["id"] for r in conn.execute("SELECT id FROM listings WHERE status='active'")}
    pipeline = [r["listing_ref"] for r in conn.execute("SELECT listing_ref FROM pipeline")]
    return prof, active, pipeline, hunt_dashboard.build(conn, prof)


def test_solo_studio(tmp_path, monkeypatch, capsys):
    prof, active, pipeline, html = run(tmp_path, monkeypatch, capsys, SOLO, ["1001"], "1001")
    assert active == {"1001"}
    assert pipeline == ["1001"]
    assert "Sam" in html and "Jo and Lee" not in html
    assert "2,800" in html
    assert "Log debrief" in html   # a pursued (shortlisted) row can take a debrief
    assert "455 7th Avenue" not in html


def test_couple_two_bedroom(tmp_path, monkeypatch, capsys):
    prof, active, pipeline, html = run(tmp_path, monkeypatch, capsys, COUPLE, ["1003", "1007"], "1003")
    assert active == {"1003", "1006"}   # 1004 under 750 sq ft, 1007 a 4th-floor walk-up
    assert pipeline == ["1003"]
    assert "Jo and Lee" in html and "Sam" not in html
    assert "5,400" in html
    assert "30-12 21st Street" not in html

