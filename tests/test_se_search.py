import json
from pathlib import Path
from unittest import mock

import doorman_profile as P
import se_api as S
from conftest import FIXTURES


def fake_page(total, n, start=0):
    edge = lambda i: {"__typename": "OrganicRentalEdge", "amenitiesMatch": True,
                      "node": {"id": str(i), "street": f"{i} Test St", "price": 3000, "urlPath": f"/rental/{i}"}}
    return {"searchRentals": {"totalCount": total, "edges": [edge(start + i) for i in range(n)]}}


def test_search_query_comes_from_the_profile():
    couple = P.load(FIXTURES / "couple-2br.yaml")
    with mock.patch.object(S, "gql", return_value=fake_page(1, 1)) as g:
        res = S.search(1, 100, couple)
    q = g.call_args.args[0]
    assert "areas: [319, 401]" in q
    assert "upperBound: 5600" in q
    assert "bedrooms: { lowerBound: 2, upperBound: 2 }" in q
    assert "amenities: []" in q
    assert res["totalCount"] == 1 and res["listings"][0]["address"] == "0 Test St"


def test_solo_query_requires_its_amenities():
    solo = P.load(FIXTURES / "solo-studio.yaml")
    with mock.patch.object(S, "gql", return_value=fake_page(0, 0)) as g:
        S.search(1, 100, solo)
    q = g.call_args.args[0]
    assert "areas: [401, 137]" in q and "amenities: [ELEVATOR]" in q


def test_sweep_keeps_pages_written_before_a_block(tmp_path):
    solo = P.load(FIXTURES / "solo-studio.yaml")
    pages = [fake_page(250, 100), S.Blocked("stop")]
    with mock.patch.object(S, "gql", side_effect=pages):
        paths, total, n = S._write_pages(solo, tmp_path)
    assert len(paths) == 1 and total == 250 and n == 3
    assert json.loads(Path(paths[0]).read_text())["returned"] == 100
