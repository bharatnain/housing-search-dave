#!/usr/bin/env python3
"""Doorman hunt store: SQLite-backed listing tracker. Every search criterion comes from
profile.yaml (scripts/doorman_profile.py); taste weights from data/weights.json.

Subcommands:
  init                          create schema (idempotent)
  import-sweep <date> <json..>  import StreetEasy sweep page files (MCP or se_api output), diff against store
  add-listings <json>           import other-portal listings (JSON array)
  score-details <json>          score one saved get_rental_details output
  decide <id> pursue|pass       a dashboard tap: pursue -> pipeline 'shortlisted'; pass -> listing 'passed'
  seed-pipeline <json>          upsert pipeline rows from a JSON array
  add-feedback <json>           record viewing feedback
  feedback                      print all feedback
  nudges                        pipeline rows waiting on an agent for 48 h+
  digest                        print latest sweep diff + pipeline summary as JSON
"""
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import doorman_profile

DB = Path(__file__).resolve().parent.parent / "data" / "hunt.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL DEFAULT 'streeteasy',
  address TEXT, unit TEXT, unit_key TEXT UNIQUE,
  neighborhood TEXT, beds REAL, baths REAL, sqft INTEGER,
  price INTEGER, net_effective INTEGER, months_free REAL, no_fee INTEGER,
  building_type TEXT, new_dev INTEGER, available_at TEXT,
  url TEXT, src_label TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  exclude_reason TEXT,     -- why status is 'excluded' (profile rule that matched)
  lat REAL, lon REAL,
  score REAL, stab TEXT,
  first_seen TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  listing_id TEXT, at TEXT, kind TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS pipeline (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  listing_ref TEXT, address TEXT NOT NULL, unit TEXT,
  neighborhood TEXT, price INTEGER,
  stage TEXT NOT NULL,
  agent TEXT, agent_email TEXT, agency TEXT,
  next_action TEXT, next_action_at TEXT,
  todo TEXT,               -- what the user owes: schedule | answer | review | decide; NULL = ball in their court
  notes TEXT, updated_at TEXT, last_outreach_at TEXT,
  UNIQUE(address, unit)
);
CREATE TABLE IF NOT EXISTS vets (
  address TEXT PRIMARY KEY,   -- building street address as written in pipeline/listings
  ran_at TEXT, bbl TEXT, bin TEXT, matched TEXT,
  grade TEXT,                 -- clean | caution | red
  summary TEXT, flags TEXT,   -- flags = JSON list
  dossier TEXT                -- full JSON from vet.py
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  address TEXT NOT NULL, unit TEXT,
  at TEXT NOT NULL,
  verdict TEXT,            -- loved | maybe | no
  liked TEXT, disliked TEXT, dealbreakers TEXT, raw TEXT,
  UNIQUE(address, unit, at)
);
CREATE TABLE IF NOT EXISTS sweeps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ran_at TEXT, source TEXT, total INTEGER,
  new_count INTEGER, price_changes INTEGER, gone INTEGER
);
"""

STAGES = ["discovered", "shortlisted", "inquired", "replied", "viewing",
          "viewed", "applying", "applied", "decision", "dropped"]


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def unit_key(address, unit):
    s = f"{address} {unit or ''}".lower()
    s = re.sub(r"\b(street|st)\b", "st", s)
    s = re.sub(r"\b(avenue|ave)\b", "ave", s)
    s = re.sub(r"\b(boulevard|blvd)\b", "blvd", s)
    s = re.sub(r"\b(place|pl)\b", "pl", s)
    s = re.sub(r"\b(east|e)\b", "e", s)
    s = re.sub(r"\b(west|w)\b", "w", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def net_effective(l):
    if (l.get("netEffectivePrice") or 0) > 0:
        return round(l["netEffectivePrice"])
    term = l.get("leaseTermMonths") or 12
    free = l.get("monthsFree") or 0
    if free > 0:
        return round(l["price"] * (term - free) / term)
    return l["price"]


NUMBERED_ST = re.compile(r"\b(East|West|E\.?|W\.?)\s+(\d{1,3})(st|nd|rd|th)?\s+St", re.I)


def exclusion_reason(prof, street, net, beds=None, sqft=None, lease_months=None, building_type=None,
                     lat=None, borough=None):
    """The first profile rule this listing breaks, or None. The north-of-street rule reads
    Manhattan's numbered streets only: it is skipped when the borough is known and not
    Manhattan, or the point is south of 40.70 (outer-borough "East 98th St" exists), and
    avenue addresses are not caught by it."""
    s = prof["search"]
    ex = s["exclude"]
    if ex.get("coops") and building_type == "CO_OP":
        return "co-op"
    n = ex.get("north_of_street")
    if n and not (borough and borough.lower() != "manhattan") and not (lat is not None and lat < 40.70):
        m = NUMBERED_ST.search(street or "")
        if m and int(m.group(2)) > n:
            return f"north of street {n}"
    if net is not None and net > s["max_net_rent"]:
        return f"net ${net:,} over ${s['max_net_rent']:,}"
    if beds is not None and not (s["bedrooms"]["min"] <= beds <= s["bedrooms"]["max"]):
        return f"{beds:g} bedrooms"
    if sqft and s.get("min_sqft") and sqft < s["min_sqft"]:
        return f"{sqft} sq ft under {s['min_sqft']}"
    if lease_months and s.get("min_lease_months") and lease_months < s["min_lease_months"]:
        return f"{lease_months}-month lease"
    return None


def cmd_init():
    DB.parent.mkdir(exist_ok=True)
    conn = db()
    conn.executescript(SCHEMA)
    conn.commit()
    print(f"schema ready at {DB}")


def _event(conn, lid, at, kind, detail):
    conn.execute("INSERT INTO events (listing_id, at, kind, detail) VALUES (?,?,?,?)", (lid, at, kind, detail))


def cmd_import_sweep(ran_at, paths, prof):
    conn = db()
    seen_ids = set()
    total_count = 0
    new, price_changes, confirmed = [], [], []
    for p in paths:
        data = json.loads(Path(p).read_text())
        total_count = max(total_count, data.get("totalCount") or 0)
        for l in data["listings"]:
            lid = str(l["id"])
            if lid in seen_ids:
                continue
            seen_ids.add(lid)
            net = net_effective(l)
            geo = l.get("geo") or {}
            row = conn.execute("SELECT price, status, exclude_reason FROM listings WHERE id=?", (lid,)).fetchone()
            reason = exclusion_reason(prof, l["street"], net, l.get("bedrooms"), l.get("livingAreaSize"),
                                      l.get("leaseTermMonths"), l.get("buildingType"), geo.get("latitude"))
            # a walk-up exclusion comes from details (score-details), not the sweep: keep it
            if reason is None and row and (row["exclude_reason"] or "").startswith("walk-up"):
                reason = row["exclude_reason"]
            brief = {"id": lid, "address": l["street"], "unit": l.get("unit"), "hood": l.get("neighborhood"),
                     "price": l["price"], "net": net, "beds": l["bedrooms"]}
            if row is None:
                conn.execute(
                    """INSERT INTO listings (id, source, address, unit, unit_key, neighborhood,
                       beds, baths, sqft, price, net_effective, months_free, no_fee, building_type,
                       new_dev, available_at, url, src_label, status, exclude_reason, lat, lon, first_seen, last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(unit_key) DO UPDATE SET last_seen=excluded.last_seen""",
                    (lid, "streeteasy", l["street"], l.get("unit"), unit_key(l["street"], l.get("unit")),
                     l.get("neighborhood"), l.get("bedrooms"), l.get("bathrooms"),
                     l.get("livingAreaSize") or None, l["price"], net, l.get("monthsFree") or 0,
                     1 if l.get("noFee") else 0, l.get("buildingType"),
                     1 if l.get("isNewDevelopment") else 0, l.get("availableAt"), l.get("url"),
                     l.get("sourceGroupLabel"), "excluded" if reason else "active", reason,
                     geo.get("latitude"), geo.get("longitude"), ran_at, ran_at))
                if reason:
                    continue
                _event(conn, lid, ran_at, "appeared", f"${l['price']} {l['street']} {l.get('unit') or ''}")
                new.append(brief)
            else:
                if row["price"] != l["price"]:
                    _event(conn, lid, ran_at, "price_change", f"{row['price']} -> {l['price']}")
                    price_changes.append({"id": lid, "address": l["street"], "unit": l.get("unit"),
                                          "hood": l.get("neighborhood"), "old": row["price"], "new": l["price"]})
                if row["status"] == "stale" and not reason:
                    # an old row is live again only once a sweep sees it
                    _event(conn, lid, ran_at, "confirmed", f"${l['price']} {l['street']} {l.get('unit') or ''}")
                    confirmed.append(brief)
                if row["status"] == "excluded" and not reason:
                    # was out of scope (e.g. over the cap before a price cut); it is back in scope now
                    _event(conn, lid, ran_at, "appeared", f"${l['price']} {l['street']} {l.get('unit') or ''}")
                    new.append(brief)
                conn.execute(
                    """UPDATE listings SET price=?, net_effective=?, months_free=?, available_at=?,
                       status=CASE WHEN status='passed' THEN 'passed' WHEN ? THEN 'excluded' ELSE 'active' END,
                       exclude_reason=?, lat=?, lon=?, last_seen=? WHERE id=?""",
                    (l["price"], net, l.get("monthsFree") or 0, l.get("availableAt"), 1 if reason else 0,
                     reason, geo.get("latitude"), geo.get("longitude"), ran_at, lid))
    # Only a complete sweep can say a listing is gone: a guard stop or block mid-way leaves
    # pages unread, and their listings would wrongly go 'gone'.
    partial = len(seen_ids) < 0.9 * total_count
    gone = [] if partial else conn.execute(
        "SELECT id, address, unit, neighborhood, price FROM listings "
        "WHERE source='streeteasy' AND status='active' AND last_seen < ?", (ran_at,)).fetchall()
    for g in gone:
        conn.execute("UPDATE listings SET status='gone' WHERE id=?", (g["id"],))
        _event(conn, g["id"], ran_at, "disappeared", f"${g['price']} {g['address']} {g['unit'] or ''}")
    conn.execute("INSERT INTO sweeps (ran_at, source, total, new_count, price_changes, gone) VALUES (?,?,?,?,?,?)",
                 (ran_at, "streeteasy", len(seen_ids), len(new), len(price_changes), len(gone)))
    conn.commit()
    print(json.dumps({
        "ran_at": ran_at, "total": len(seen_ids), "totalCount": total_count, "partial": partial,
        "new": new, "confirmed": confirmed, "price_changes": price_changes,
        "gone": [dict(g) for g in gone],
    }, indent=2))


def cmd_add_listings(path, prof):
    """Import listings from other portals (openigloo, listingsproject, ...).
    JSON array of {source, address, unit, neighborhood, borough?, beds, baths, price,
    net_effective?, url?, src_label?, available_at?}."""
    conn = db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = json.loads(Path(path).read_text())
    added = updated = 0
    for r in rows:
        uk = unit_key(r["address"], r.get("unit"))
        lid = f"{r['source']}:{uk}"
        existing = conn.execute("SELECT id, price FROM listings WHERE unit_key=?", (uk,)).fetchone()
        if existing:
            conn.execute("UPDATE listings SET last_seen=?, status=CASE WHEN status IN ('passed','excluded') THEN status ELSE 'active' END WHERE unit_key=?", (now, uk))
            if r.get("price") and existing["price"] != r["price"]:
                _event(conn, existing["id"], now, "price_change",
                       f"{existing['price']} -> {r['price']} (via {r['source']})")
            updated += 1
            continue
        net = r.get("net_effective") or r.get("price")
        reason = exclusion_reason(prof, r["address"], net, r.get("beds"), borough=r.get("borough"))
        conn.execute(
            """INSERT INTO listings (id, source, address, unit, unit_key, neighborhood, beds, baths,
               price, net_effective, no_fee, available_at, url, src_label, status, exclude_reason, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (lid, r["source"], r["address"], r.get("unit"), uk, r.get("neighborhood"),
             r.get("beds"), r.get("baths"), r.get("price"), net, 1 if r.get("no_fee") else 0,
             r.get("available_at"), r.get("url"), r.get("src_label"),
             "excluded" if reason else "active", reason, now, now))
        if reason:
            continue
        _event(conn, lid, now, "appeared", f"${r.get('price')} {r['address']} {r.get('unit') or ''} via {r['source']}")
        added += 1
    conn.commit()
    print(f"added {added}, already-known {updated}")


def cmd_seed_pipeline(path):
    """Upsert pipeline rows from a JSON array. Keys absent from a row are left untouched;
    pass an explicit null to clear a field. Only address (+unit) and stage are required
    for an update; stage is required for an insert."""
    conn = db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = json.loads(Path(path).read_text())
    cols = ["listing_ref", "address", "unit", "neighborhood", "price", "stage", "agent", "agent_email",
            "agency", "next_action", "next_action_at", "todo", "notes", "last_outreach_at"]
    for r in rows:
        if "stage" in r:
            assert r["stage"] in STAGES, f"bad stage {r['stage']}"
        assert r.get("todo") in (None, "schedule", "answer", "review", "decide"), f"bad todo {r.get('todo')}"
        cur = conn.execute("SELECT * FROM pipeline WHERE address=? AND IFNULL(unit,'')=IFNULL(?, '')",
                           (r["address"], r.get("unit"))).fetchone()
        merged = {c: (cur[c] if cur else None) for c in cols}
        for k, v in r.items():
            if k in cols:
                merged[k] = v
        assert merged["stage"], f"stage required for new row {r['address']} {r.get('unit')}"
        if cur:
            sets = ", ".join(f"{c}=?" for c in cols if c not in ("address", "unit"))
            conn.execute(f"UPDATE pipeline SET {sets}, updated_at=? WHERE id=?",
                         [merged[c] for c in cols if c not in ("address", "unit")] + [now, cur["id"]])
        else:
            conn.execute(f"INSERT INTO pipeline ({', '.join(cols)}, updated_at) VALUES ({','.join('?'*len(cols))}, ?)",
                         [merged[c] for c in cols] + [now])
    conn.commit()
    print(f"pipeline upserted: {len(rows)} rows")


def cmd_decide(lid, verdict):
    """Execute a dashboard tap. pursue: the listing joins the pipeline at 'shortlisted'
    (a row already further along is left alone). pass: the listing is marked 'passed'."""
    assert verdict in ("pursue", "pass"), f"bad verdict {verdict}"
    conn = db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    l = conn.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
    assert l, f"no listing {lid}"
    if verdict == "pass":
        conn.execute("UPDATE listings SET status='passed' WHERE id=?", (lid,))
    else:
        cur = conn.execute("SELECT id FROM pipeline WHERE address=? AND IFNULL(unit,'')=IFNULL(?, '')",
                           (l["address"], l["unit"])).fetchone()
        if not cur:
            conn.execute("""INSERT INTO pipeline (listing_ref, address, unit, neighborhood, price, stage, updated_at)
                            VALUES (?,?,?,?,?,?,?)""",
                         (lid, l["address"], l["unit"], l["neighborhood"], l["net_effective"] or l["price"],
                          "shortlisted", now))
    _event(conn, lid, now, verdict, f"{l['address']} {l['unit'] or ''}".strip())
    conn.commit()
    print(f"{verdict}: {l['address']} {l['unit'] or ''}".strip())


NUDGE_HOURS = 48


def cmd_add_feedback(path):
    """Record viewing feedback. JSON array of {address, unit?, verdict
    (loved|maybe|no), liked?, disliked?, dealbreakers?, raw?, at?}."""
    conn = db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = json.loads(Path(path).read_text())
    for r in rows:
        assert r.get("verdict") in ("loved", "maybe", "no"), f"bad verdict {r.get('verdict')}"
        conn.execute(
            """INSERT OR REPLACE INTO feedback
               (address, unit, at, verdict, liked, disliked, dealbreakers, raw)
               VALUES (?,?,?,?,?,?,?,?)""",
            (r["address"], r.get("unit"), r.get("at") or now, r["verdict"],
             r.get("liked"), r.get("disliked"), r.get("dealbreakers"), r.get("raw")))
    conn.commit()
    print(f"feedback recorded: {len(rows)} rows")


def cmd_feedback():
    conn = db()
    rows = conn.execute("SELECT * FROM feedback ORDER BY at DESC").fetchall()
    print(json.dumps([dict(r) for r in rows], indent=2))


# Neutral starting weights; onboarding and debriefs tune data/weights.json (logged in TASTE.md).
DEFAULT_WEIGHTS = {
    "floor_high": 3.0, "floor_mid": 2.0, "floor_low": 1.0,
    "views_each": 1.0, "views_cap": 3.0, "light_language": 1.0,
    "doorman": 1.0, "wd_inunit": 1.0, "laundry_bldg": 0.5,
    "utilities_included": 1.5, "stab_strong": 3.0, "stab_weak": 1.0,
    "low_floor_no_light_penalty": -1.0, "spacious": 1.0, "cramped_penalty": -1.0, "modern_building": 0.5,
}


def load_weights():
    p = DB.parent / "weights.json"
    if p.exists():
        w = dict(DEFAULT_WEIGHTS)
        w.update({k: v for k, v in json.loads(p.read_text()).items()
                  if not k.startswith("_")})
        return w
    return DEFAULT_WEIGHTS


def unit_floor(unit):
    """'4B' -> 4, '12C' -> 12, '1203' -> 12, 'PH' -> None."""
    m = re.match(r"[A-Za-z]{0,2}(\d+)", unit or "")
    return int(m.group(1)[:-2] or 0) if (m and len(m.group(1)) >= 3) else (int(m.group(1)) if m else None)


def score_listing(d, W):
    """Score a get_rental_details output. Returns (score, stab, notes). Pure: no db."""
    r = d["rentalByListingId"]
    b = d.get("buildingByRentalListingId") or {}
    pd_ = r.get("propertyDetails") or {}
    am = ((pd_.get("amenities") or {}).get("list")) or []
    feats = pd_.get("features") or {}
    views = (feats.get("views") if feats else None) or []
    desc = (r.get("description") or "").lower()

    score, notes = 0.0, []
    floor = unit_floor((pd_.get("address") or {}).get("unit"))
    if floor is not None:
        if floor >= 15:
            score += W["floor_high"]; notes.append(f"floor {floor} (+{W['floor_high']:g})")
        elif floor >= 8:
            score += W["floor_mid"]; notes.append(f"floor {floor} (+{W['floor_mid']:g})")
        elif floor >= 4:
            score += W["floor_low"]; notes.append(f"floor {floor} (+{W['floor_low']:g})")
        else:
            notes.append(f"floor {floor} (low)")
    hits = [v for v in views if v in ("CITY", "SKYLINE", "PARK", "WATER", "GARDEN")]
    if hits:
        pts = min(len(hits) * W["views_each"], W["views_cap"])
        score += pts; notes.append(f"views {'/'.join(hits)} (+{pts:g})")
    light_lang = bool(re.search(r"south(ern)?[- ](facing|exposure)|floor[- ]to[- ]ceiling|sun-?filled|corner unit", desc))
    if light_lang:
        score += W["light_language"]; notes.append(f"light language (+{W['light_language']:g})")
    if floor is not None and floor <= 3 and not hits and not light_lang and W.get("low_floor_no_light_penalty"):
        score += W["low_floor_no_light_penalty"]; notes.append(f"low floor, no light evidence ({W['low_floor_no_light_penalty']:g})")
    if "DOORMAN" in am:
        score += W["doorman"]; notes.append(f"doorman (+{W['doorman']:g})")
    if W.get("modern_building") and ((b.get("yearBuilt") or 0) >= 2005 or r.get("isNewDevelopment")):
        score += W["modern_building"]; notes.append(f"modern building {b.get('yearBuilt') or 'new dev'} (+{W['modern_building']:g})")
    if "WASHER_DRYER" in (feats.get("list") or []) or "washer/dryer" in desc or "in-unit laundry" in desc:
        score += W["wd_inunit"]; notes.append(f"in-unit W/D (+{W['wd_inunit']:g})")
    elif "LAUNDRY" in am:
        score += W["laundry_bldg"]; notes.append(f"bldg laundry (+{W['laundry_bldg']:g})")
    if re.search(r"(all )?(monthly )?utilit(ies|y)[^.]{0,40}included|heat, water and electricity are included", desc):
        score += W["utilities_included"]; notes.append(f"utilities included (+{W['utilities_included']:g})")
    # spaciousness is soft (proportion is judged in person); thresholds scale with bedrooms
    sqft = pd_.get("livingAreaSize")
    beds = pd_.get("bedroomCount") or 0
    big_words = re.search(r"\b(oversized|over-sized|expansive|generous(ly)? (sized|proportioned)|huge|massive|loft-like|sprawling|king-size(d)? bedroom|extra[- ]large|xl studio)\b", desc)  # bare 'spacious' is boilerplate
    small_words = re.search(r"\b(cozy|cosy|efficient(ly)? (laid out|designed)|compact|petite|charming studio|micro)\b", desc)
    big_sqft = sqft and ((beds >= 1 and sqft >= 250 * beds + 400) or (beds == 0 and sqft >= 500))
    small_sqft = sqft and ((beds >= 1 and sqft < 250 * beds + 250) or (beds == 0 and sqft < 450))
    if W.get("spacious") and (big_words or big_sqft):
        score += W["spacious"]; notes.append(f"spacious ({'%d sf' % sqft if big_sqft else big_words.group(0)}) (+{W['spacious']:g})")
    elif W.get("cramped_penalty") and (small_words or small_sqft):
        score += W["cramped_penalty"]; notes.append(f"small/cramped signal ({'%d sf' % sqft if small_sqft else small_words.group(0)}) ({W['cramped_penalty']:g})")

    # stabilization signal
    stab = "unknown"
    hist = r.get("propertyHistory") or []
    events = sorted({e["date"][:4] for h in hist for e in (h.get("rentalEventsOfInterest") or [])})
    if re.search(r"rent[- ]stabilized", desc):
        stab = "stated"
    elif len(hist) >= 4 and events and events[0] <= "2016":
        prices = [e["price"] for h in hist for e in (h.get("rentalEventsOfInterest") or []) if e.get("price")]
        if prices and max(prices) <= (r.get("pricing") or {}).get("price", 0) * 1.05:
            stab = "likely-ladder"
    if stab == "unknown" and (b.get("yearBuilt") or 3000) < 1974 and (b.get("residentialUnitCount") or 0) >= 6:
        stab = "presumptive-prewar"
    if stab in ("stated", "likely-ladder"):
        score += W["stab_strong"]; notes.append(f"stabilization {stab} (+{W['stab_strong']:g})")
    elif stab == "presumptive-prewar":
        score += W["stab_weak"]; notes.append(f"stabilization presumptive-prewar (+{W['stab_weak']:g})")
    return round(score, 1), stab, notes


def cmd_score_details(path, prof):
    """Score a saved get_rental_details output, store score/stab, and apply the walk-up rule
    (no ELEVATOR amenity and a floor above search.exclude.max_walkup_floor -> excluded)."""
    d = json.loads(Path(path).read_text())
    r = d["rentalByListingId"]
    lid = str(r["id"])
    score, stab, notes = score_listing(d, load_weights())
    pd_ = r.get("propertyDetails") or {}
    am = ((pd_.get("amenities") or {}).get("list")) or []
    floor = unit_floor((pd_.get("address") or {}).get("unit"))
    cap = prof["search"]["exclude"].get("max_walkup_floor")
    walkup = f"walk-up floor {floor}" if cap and "ELEVATOR" not in am and floor is not None and floor > cap else None
    conn = db()
    conn.execute("UPDATE listings SET score=?, stab=? WHERE id=?", (score, stab, lid))
    if walkup:
        conn.execute("UPDATE listings SET status='excluded', exclude_reason=? WHERE id=? AND status='active'", (walkup, lid))
        notes.append(f"excluded: {walkup}")
    else:   # the rule changed or the floor was misread before: back in scope
        conn.execute("UPDATE listings SET status='active', exclude_reason=NULL "
                     "WHERE id=? AND status='excluded' AND exclude_reason LIKE 'walk-up%'", (lid,))
    conn.commit()
    print(json.dumps({"id": lid, "score": score, "stab": stab, "notes": notes}))


def cmd_nudges():
    """Threads where the ball is in the agent's court and they've been silent >= 48h.
    The clock runs from last_outreach_at (falling back to updated_at)."""
    conn = db()
    now = datetime.now(timezone.utc)
    out = []
    rows = conn.execute(
        "SELECT * FROM pipeline WHERE stage IN ('inquired','replied') ORDER BY last_outreach_at").fetchall()
    for r in rows:
        ref = r["last_outreach_at"] or r["updated_at"]
        if not ref:
            continue
        ts = datetime.fromisoformat(ref.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        hours = (now - ts).total_seconds() / 3600
        if hours >= NUDGE_HOURS:
            out.append({"address": r["address"], "unit": r["unit"], "stage": r["stage"],
                        "agent": r["agent"], "agent_email": r["agent_email"],
                        "hours_silent": round(hours), "next_action": r["next_action"]})
    print(json.dumps(out, indent=2))


def cmd_digest():
    conn = db()
    sweep = conn.execute("SELECT * FROM sweeps ORDER BY id DESC LIMIT 1").fetchone()
    out = {"latest_sweep": dict(sweep) if sweep else None, "pipeline": {}, "events": []}
    for s in STAGES:
        rows = conn.execute("SELECT * FROM pipeline WHERE stage=? ORDER BY updated_at DESC", (s,)).fetchall()
        if rows:
            out["pipeline"][s] = [dict(r) for r in rows]
    if sweep:
        evs = conn.execute("SELECT * FROM events WHERE at=? ORDER BY kind", (sweep["ran_at"],)).fetchall()
        out["events"] = [dict(e) for e in evs]
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "digest"
    if cmd == "init":
        cmd_init()
    elif cmd == "import-sweep":
        cmd_import_sweep(sys.argv[2], sys.argv[3:], doorman_profile.load())
    elif cmd == "add-listings":
        cmd_add_listings(sys.argv[2], doorman_profile.load())
    elif cmd == "score-details":
        cmd_score_details(sys.argv[2], doorman_profile.load())
    elif cmd == "decide":
        cmd_decide(sys.argv[2], sys.argv[3])
    elif cmd == "seed-pipeline":
        cmd_seed_pipeline(sys.argv[2])
    elif cmd == "nudges":
        cmd_nudges()
    elif cmd == "add-feedback":
        cmd_add_feedback(sys.argv[2])
    elif cmd == "feedback":
        cmd_feedback()
    elif cmd == "digest":
        cmd_digest()
    else:
        sys.exit(f"unknown command {cmd}\n{__doc__}")
