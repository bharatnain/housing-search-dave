#!/usr/bin/env python3
"""Vet every building on the radar and store the dossier in data/hunt.db (table vets).

Targets: pipeline rows in viewing/replied/inquired/applying/applied/shortlisted, plus the
listings the dashboard shows (top-ranked, latest new matches, other-portal rows with a
street address). Skips buildings vetted within MAX_AGE_DAYS unless --force.

Usage: python3 scripts/vet_pipeline.py [--force] [--only "<address>"]
"""
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vet import vet  # noqa: E402
import doorman_profile  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "hunt.db"
MAX_AGE_DAYS = 14
ACTIVE_STAGES = ("viewing", "replied", "inquired", "applying", "applied", "shortlisted", "viewed")


def looks_like_street(a):
    return bool(re.match(r"^\d[\d\-]*\s+\S", a or ""))


def se_units(conn, address):
    """Largest residentialUnitCount StreetEasy reports for this address (cached details)."""
    best = 0
    for r in conn.execute("SELECT id FROM listings WHERE address=?", (address,)):
        f = ROOT / "data" / "details" / f"{r[0]}.json"
        if f.exists():
            try:
                n = (json.load(open(f)).get("buildingByRentalListingId") or {}).get("residentialUnitCount") or 0
                best = max(best, int(n))
            except Exception:
                pass
    return best or None


def apply_se_units(conn, address, d):
    """PLUTO under-counts towers on condo/complex lots (a tower can show as 3 units); trust StreetEasy when larger."""
    from vet import grade
    n = se_units(conn, address)
    pl = d.get("pluto") or {}
    have = int(float(pl.get("unitsres") or 0))
    if n and n > have and "hpd_violations" in d:
        pl["unitsres"] = str(n); pl["units_source"] = "streeteasy"; d["pluto"] = pl
        oc = sum(int(v["n"]) for v in d["hpd_violations"] if v["class"] == "C" and v["violationstatus"] == "Open")
        ob = sum(int(v["n"]) for v in d["hpd_violations"] if v["class"] == "B" and v["violationstatus"] == "Open")
        d["grade"] = grade(oc, ob, n, d.get("flags", []), d.get("open_c_recent"))
        d["summary"] = re.sub(r"\b\d+ units\b", f"{n} units", d.get("summary", ""), count=1)
        d["summary"] = re.sub(r"\(per \d+ units\)", f"(per {n} units)", d["summary"], count=1)
    return d


def targets(conn, prof):
    cap = prof["search"]["max_net_rent"]
    out = []
    for r in conn.execute(f"SELECT DISTINCT address FROM pipeline WHERE stage IN ({','.join('?'*len(ACTIVE_STAGES))})", ACTIVE_STAGES):
        out.append(r[0])
    for r in conn.execute("SELECT address FROM listings WHERE status='active' AND net_effective<=? AND score IS NOT NULL ORDER BY score DESC, net_effective LIMIT 20", (cap,)):
        out.append(r[0])
    sweep = conn.execute("SELECT ran_at FROM sweeps ORDER BY id DESC LIMIT 1").fetchone()
    if sweep:
        for r in conn.execute("SELECT l.address FROM events e JOIN listings l ON l.id=e.listing_id WHERE e.at=? AND e.kind='appeared' AND l.status='active'", (sweep[0],)):
            out.append(r[0])
    for r in conn.execute("SELECT address FROM listings WHERE status='active' AND source NOT IN ('streeteasy','openigloo') AND net_effective<=?", (cap,)):
        out.append(r[0])
    seen, uniq = set(), []
    for a in out:
        if a and looks_like_street(a) and a not in seen:
            seen.add(a); uniq.append(a)
    return uniq


def main():
    force = "--force" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS vets (address TEXT PRIMARY KEY, ran_at TEXT, bbl TEXT, bin TEXT,
        matched TEXT, grade TEXT, summary TEXT, flags TEXT, dossier TEXT)""")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).isoformat()
    addrs = [only] if only else targets(conn, doorman_profile.load())
    done = skipped = failed = 0
    for a in addrs:
        row = conn.execute("SELECT ran_at, grade FROM vets WHERE address=?", (a,)).fetchone()
        if row and row["ran_at"] and row["ran_at"] > cutoff and not force:
            skipped += 1
            continue
        try:
            d = apply_se_units(conn, a, vet(a))
        except Exception as e:
            d = {"error": str(e)}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if "error" in d:
            failed += 1
            print(f"FAIL  {a}: {d['error']}", flush=True)
            conn.execute("INSERT OR REPLACE INTO vets (address, ran_at, grade, summary, flags, dossier) VALUES (?,?,?,?,?,?)",
                         (a, now, "unknown", d["error"], "[]", json.dumps(d)))
        else:
            done += 1
            conn.execute("INSERT OR REPLACE INTO vets (address, ran_at, bbl, bin, matched, grade, summary, flags, dossier) VALUES (?,?,?,?,?,?,?,?,?)",
                         (a, now, d.get("bbl"), d.get("bin"), d.get("matched"), d.get("grade"),
                          d.get("summary"), json.dumps(d.get("flags", [])), json.dumps(d)))
            print(f"{d.get('grade','?'):8} {a}: {d.get('summary','')}", flush=True)
        conn.commit()
        time.sleep(0.5)
    print(f"vetted {done}, skipped {skipped} (fresh), failed {failed}")


if __name__ == "__main__":
    main()
