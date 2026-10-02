#!/usr/bin/env python3
"""Load and validate profile.yaml: the only source of personal values in Doorman.

Every script calls load(); nothing else hardcodes a search criterion.

Usage: python3 scripts/doorman_profile.py check [path]   # prints problems, exit 1 if any
"""
import datetime as dt
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BOROUGHS = {"manhattan", "brooklyn", "queens", "bronx", "staten island"}


class ProfileError(Exception):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("profile.yaml has problems:\n" + "\n".join(f"- {p}" for p in self.problems))


def path():
    return Path(os.environ.get("DOORMAN_PROFILE") or ROOT / "profile.yaml")


def load(p=None):
    p = Path(p) if p else path()
    if not p.exists():
        raise ProfileError([f"no profile.yaml yet at {p} — say 'set me up' to create one"])
    try:
        d = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        raise ProfileError([f"profile.yaml is not valid YAML: {e}"]) from e
    problems = validate(d)
    if problems:
        raise ProfileError(problems)
    return d


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _pos_int_or_null(v):
    return v is None or (_is_int(v) and v > 0)


def _iso(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10]
    return v


def validate(d):
    """Normalize hand-edited values in place and return one plain line per problem,
    each starting with the field name."""
    if not isinstance(d, dict):
        return ["profile: the file is empty or not a mapping"]
    out = []
    sections = ("household", "search", "move", "realtor_facts", "streeteasy", "dashboard")
    for k in sections:
        if not isinstance(d.get(k), dict):
            out.append(f"{k}: missing section")
    if out:
        return out
    h, s, m, rf, se, db = (d[k] for k in sections)

    if not isinstance(h.get("name"), str) or not h["name"].strip():
        out.append("household.name: give a name (used in the dashboard title)")
    if not _is_int(h.get("people")) or h["people"] < 1:
        out.append("household.people: a whole number, at least 1")
    pets = h.get("pets")
    if not (pets in ("none", "cat", "dog") or (isinstance(pets, str) and pets.startswith("other:"))):
        out.append("household.pets: none, cat, dog or 'other: <text>'")

    bad = [b for b in s.get("boroughs") or [] if str(b).lower() not in BOROUGHS]
    if not s.get("boroughs") or bad:
        out.append(f"search.boroughs: pick from {sorted(BOROUGHS)}" + (f" (not {bad})" if bad else ""))
    else:
        s["boroughs"] = [str(b).lower() for b in s["boroughs"]]
    areas = s.get("areas")
    if not isinstance(areas, list) or not areas or not all(
            isinstance(a, dict) and isinstance(a.get("name"), str) and _is_int(a.get("code")) for a in areas):
        out.append("search.areas: a non-empty list of {name: <StreetEasy area>, code: <number>} (resolved with list_areas)")
    zips = []
    for z in s.get("zips") or []:
        z = f"{z:05d}" if _is_int(z) else str(z)
        zips.append(z)
        if not re.fullmatch(r"\d{5}", z):
            out.append(f"search.zips: '{z}' is not a 5-digit ZIP")
    s["zips"] = zips
    ex = s.get("exclude") or {}
    s["exclude"] = ex
    n = ex.get("north_of_street")
    if not (n is None or (_is_int(n) and 1 <= n <= 220)):
        out.append("search.exclude.north_of_street: null or a street number 1-220")
    if not isinstance(ex.get("coops", False), bool):
        out.append("search.exclude.coops: true or false")
    ex.setdefault("coops", False)
    if not _pos_int_or_null(ex.get("max_walkup_floor")):
        out.append("search.exclude.max_walkup_floor: null or a floor number, 1 or more")
    br = s.get("bedrooms") or {}
    lo, hi = br.get("min"), br.get("max")
    if not (_is_int(lo) and _is_int(hi) and 0 <= lo <= hi <= 6):
        out.append("search.bedrooms: min and max between 0 (studio) and 6, min <= max")
    gross, net = s.get("max_gross_rent"), s.get("max_net_rent")
    if not (_is_int(gross) and gross > 0):
        out.append("search.max_gross_rent: a whole dollar amount")
    if not (_is_int(net) and net > 0):
        out.append("search.max_net_rent: a whole dollar amount")
    elif _is_int(gross) and net > gross:
        out.append("search.max_net_rent: can't be above max_gross_rent (net is after concessions)")
    for k in ("min_sqft", "min_lease_months"):
        if not _pos_int_or_null(s.get(k)):
            out.append(f"search.{k}: null or a positive whole number")
    am = s.get("required_amenities") or []
    if not isinstance(am, list) or not all(isinstance(a, str) for a in am):
        out.append("search.required_amenities: a list of StreetEasy amenity names, e.g. [ELEVATOR]")
    else:
        s["required_amenities"] = [a.strip().upper() for a in am]

    w = m.get("window")
    if not (isinstance(w, list) and len(w) == 2):
        out.append("move.window: two dates, [earliest, latest]")
    else:
        w = [_iso(x) for x in w]
        m["window"] = w
        try:
            a, b = (dt.date.fromisoformat(str(x)) for x in w)
            if a > b:
                out.append("move.window: the first date must not be after the second")
        except ValueError:
            out.append("move.window: dates as YYYY-MM-DD")

    for k in ("meets_income_40x", "credit_700_plus", "guarantor"):
        if not isinstance(rf.get(k, False), bool):
            out.append(f"realtor_facts.{k}: true or false")
    if rf.get("phone") is not None and not isinstance(rf["phone"], str):
        out.append("realtor_facts.phone: null or a quoted phone number")

    if se.get("source") not in ("mcp", "api"):
        out.append("streeteasy.source: mcp or api")
    if not (_is_int(se.get("daily_cap")) and 1 <= se["daily_cap"] <= 500):
        out.append("streeteasy.daily_cap: a number from 1 to 500")
    url = db.get("artifact_url")
    if url is not None and not (isinstance(url, str) and url.startswith("https://claude.ai/")):
        out.append("dashboard.artifact_url: null or a https://claude.ai/... link")
    if not isinstance(d.get("backup_to_git", False), bool):
        out.append("backup_to_git: true or false")
    return out


def sweep_params(p):
    """search_rentals parameters for the StreetEasy MCP. Never sets noFeeOnly."""
    s = p["search"]
    out = {"areas": [a["name"] for a in s["areas"]], "maxPrice": s["max_gross_rent"],
           "minBedrooms": s["bedrooms"]["min"], "maxBedrooms": s["bedrooms"]["max"],
           "sortBy": "PRICE", "sortDirection": "ASCENDING", "perPage": 100}
    if s.get("required_amenities"):
        out["amenities"] = list(s["required_amenities"])
    if p["household"]["pets"] != "none":
        out["petsAllowed"] = True
    return out


def area_codes(p):
    return [a["code"] for a in p["search"]["areas"]]


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "check":
        print(__doc__); sys.exit(1)
    try:
        load(sys.argv[2] if len(sys.argv) > 2 else None)
        print("profile ok")
    except ProfileError as e:
        print(e); sys.exit(1)
