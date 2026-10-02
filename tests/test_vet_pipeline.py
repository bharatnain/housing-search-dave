import sqlite3

import doorman_profile
import hunt
import vet_pipeline
from conftest import FIXTURES

SOLO = doorman_profile.load(FIXTURES / "solo-studio.yaml")


def test_targets_use_profile_cap():
    conn = sqlite3.connect(":memory:")
    conn.executescript(hunt.SCHEMA)
    rows = [("a", "10 Example Street", 2700, 3.0), ("b", "20 Example Street", 2900, 5.0),
            ("c", "Ditmars & 31st", 2500, 4.0)]
    for lid, addr, net, score in rows:
        conn.execute("INSERT INTO listings (id, address, unit_key, net_effective, score, status) "
                     "VALUES (?,?,?,?,?, 'active')", (lid, addr, lid, net, score))
    conn.execute("INSERT INTO pipeline (address, stage) VALUES ('30 Example Street', 'viewing')")
    conn.execute("INSERT INTO pipeline (address, stage) VALUES ('40 Example Street', 'dropped')")
    assert vet_pipeline.targets(conn, SOLO) == ["30 Example Street", "10 Example Street"]
