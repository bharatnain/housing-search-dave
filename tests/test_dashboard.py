import sqlite3
from html.parser import HTMLParser

import pytest

import doorman_profile
import hunt
import hunt_dashboard
from conftest import FIXTURES

TOUGH = 'O\'Brien & Sons "Tower", 12 West 3rd St'


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(hunt.SCHEMA)
    return c


@pytest.fixture
def solo():
    return doorman_profile.load(FIXTURES / "solo-studio.yaml")


@pytest.fixture
def couple():
    return doorman_profile.load(FIXTURES / "couple-2br.yaml")


def listing(conn, lid, address, score, net, status="active", unit="1A", reason=None, source="streeteasy"):
    conn.execute(
        "INSERT INTO listings (id, source, address, unit, unit_key, neighborhood, beds, price, net_effective,"
        " url, status, exclude_reason, score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (lid, source, address, unit, lid, "Testville", 0, net, net, f"https://example.com/{lid}",
         status, reason, score))


class Attrs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        self.found.append((tag, dict(attrs)))


def parse(page):
    p = Attrs()
    p.feed(page)
    return p.found


def test_profile_name_and_cap(conn, solo, couple):
    page = hunt_dashboard.build(conn, solo)
    assert "Sam" in page and "$2,800" in page
    assert couple["household"]["name"] not in page
    assert "Sam" not in hunt_dashboard.build(conn, couple)


def test_ranking_by_score_then_net(conn, solo):
    listing(conn, "a", "1 Low Rd", 2, 2000)
    listing(conn, "b", "2 High Rd", 9, 2700)
    listing(conn, "c", "3 Mid Rd", 5, 2500)
    listing(conn, "d", "4 Tie Rd", 5, 2300)
    page = hunt_dashboard.build(conn, solo)
    order = [page.index(a) for a in ("2 High Rd", "4 Tie Rd", "3 Mid Rd", "1 Low Rd")]
    assert order == sorted(order)


def test_address_escaped_in_text_and_attributes(conn, solo):
    listing(conn, "t1", TOUGH, 7, 2500)
    conn.execute("INSERT INTO pipeline (address, unit, stage, notes) VALUES (?,?,?,?)",
                 (TOUGH, "2B", "viewing", 'it\'s "loud" & bright'))
    page = hunt_dashboard.build(conn, solo)
    assert TOUGH not in page
    assert "O&#x27;Brien &amp; Sons &quot;Tower&quot;" in page
    tags = parse(page)
    decide = [a for t, a in tags if "decide" in (a.get("class") or "").split()]
    assert decide[0]["data-addr"] == TOUGH + " 1A"
    fb = [a for t, a in tags if "fb" == a.get("class")]
    assert fb[0]["data-addr"] == TOUGH and fb[0]["data-unit"] == "2B"
    assert fb[0]["data-key"] == TOUGH.replace(" ", "_") + "_2B"


def test_excluded_and_passed_not_ranked(conn, solo):
    listing(conn, "ok", "1 Good St", 5, 2500)
    listing(conn, "ex", "2 Excluded St", 9, 2500, status="excluded", reason="co-op")
    listing(conn, "ps", "3 Passed St", 9, 2500, status="passed")
    page = hunt_dashboard.build(conn, solo)
    assert "1 Good St" in page
    assert "2 Excluded St" not in page and "3 Passed St" not in page
    assert '<div class="stat"><b>1</b><span>excluded by your rules' in page


def test_vet_grade_on_pipeline_row(conn, solo):
    conn.execute("INSERT INTO pipeline (address, unit, stage) VALUES ('5 Vetted Ave', '3C', 'inquired')")
    conn.execute("INSERT INTO vets (address, grade, summary, flags) VALUES (?,?,?,?)",
                 ("5 Vetted Ave", "red", "many violations", '["heat complaints"]'))
    page = hunt_dashboard.build(conn, solo)
    assert "vet-red" in page and "bldg RED" in page and "heat complaints" in page


def test_dom_contract_and_empty_db(conn, solo):
    page = hunt_dashboard.build(conn, solo)
    for needle in ('class="askbox"', 'id="asksend"', 'collection("requests")',
                   'db.doc("decisions/"', 'db.doc("feedback/"', "processed: false", "acted: false"):
        assert needle in page
    listing(conn, "x", "9 Row St", 4, 2400)
    conn.execute("INSERT INTO pipeline (address, unit, stage) VALUES ('9 Row St', '1A', 'viewed')")
    page = hunt_dashboard.build(conn, solo)
    assert 'data-id="x"' in page and 'class="fbd"' in page and 'data-v="loved"' in page
