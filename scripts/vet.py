#!/usr/bin/env python3
"""Doorman vetting engine: address -> building dossier from NYC open data.

Usage: .venv/bin/python scripts/vet.py "100 Example Street, Brooklyn"
Prints a JSON dossier + human-readable summary with red/yellow flags.
"""
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta

UA = {"User-Agent": "doorman/1.0 (apartment hunt)"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def soda(dataset, **params):
    qs = urllib.parse.urlencode({f"${k}": v for k, v in params.items()})
    return get(f"https://data.cityofnewyork.us/resource/{dataset}.json?{qs}")


def grade(open_c, open_b, units, flags, open_c_recent=None):
    """Contextualize by building size and age of the violations: 2 recent open C in a 300-unit
    tower is caution, in a 19-unit walk-up it's red; decade-old paperwork violations don't count."""
    c = open_c if open_c_recent is None else open_c_recent
    if c:
        rate = c / units if units else 1.0
        if rate >= 0.04 or not units or units <= 30:
            return "red"
        return "caution"
    if any(f.startswith("YELLOW") for f in flags):
        return "caution"
    return "clean"


def pick_feature(features, boroughs=None, near=None):
    """A bare street address can match in several boroughs: keep the profile's boroughs,
    then take the match nearest the listing's lat/lon when known."""
    if boroughs:
        inb = [f for f in features if str(f["properties"].get("borough", "")).lower() in boroughs]
        features = inb or features
    if near:
        lat, lon = near
        features = sorted(features, key=lambda f: (f["geometry"]["coordinates"][1] - lat) ** 2
                          + (f["geometry"]["coordinates"][0] - lon) ** 2)
    return features[0]


def vet(address, boroughs=None, near=None):
    d = {"address_query": address}
    geo = get("https://geosearch.planninglabs.nyc/v2/search?text=" + urllib.parse.quote(address))
    if not geo.get("features"):
        return {"error": f"GeoSearch found nothing for {address!r}"}
    f = pick_feature(geo["features"], boroughs, near)
    props = f["properties"]
    pad = (props.get("addendum") or {}).get("pad") or {}
    bbl, bin_ = pad.get("bbl"), pad.get("bin")
    d.update({"matched": props.get("label"), "bbl": bbl, "bin": bin_})
    # normalized street address as city datasets write it (e.g. "100 WEST 10 STREET")
    norm = f"{props.get('housenumber', '')} {props.get('street', '')}".upper().strip()
    d["normalized"] = norm
    soql = norm.replace("'", "''")   # SoQL string literal ("ST MARK'S PL")

    two_years_ago = (date.today() - timedelta(days=730)).isoformat()

    if bbl:
        d["hpd_violations"] = soda(
            "wvxf-dwi5", select="class,violationstatus,count(*) as n",
            group="class,violationstatus", where=f"bbl='{bbl}'")
        three_years_ago = (date.today() - timedelta(days=3 * 365)).isoformat()
        rc = soda("wvxf-dwi5", select="count(*) as n",
                  where=f"bbl='{bbl}' AND class='C' AND violationstatus='Open' AND inspectiondate>'{three_years_ago}' AND novdescription NOT LIKE '%POST NOTICE%'")
        d["open_c_recent"] = int(rc[0]["n"]) if rc else 0
        d["open_c_detail"] = soda(
            "wvxf-dwi5", select="apartment,inspectiondate,novdescription",
            where=f"bbl='{bbl}' AND class='C' AND violationstatus='Open'",
            order="inspectiondate DESC", limit=8)
        d["hpd_litigations"] = soda(
            "59kj-x8nc", select="casetype,casestatus,count(*) as n",
            group="casetype,casestatus", where=f"bbl='{bbl}'")
    if norm:
        d["complaints_311_2y"] = soda(
            "erm2-nwe9", select="complaint_type,count(*) as n", group="complaint_type",
            where=f"incident_address='{soql}' AND created_date>'{two_years_ago}'",
            order="n DESC", limit=10)
        d["evictions"] = soda(
            "6z8x-wfk4", select="executed_date,eviction_address,residential_commercial_ind",
            where=f"eviction_address like '{soql}%'", limit=10)
    if bin_:
        d["dob_complaints"] = soda(
            "eabe-havv", select="complaint_category,count(*) as n",
            group="complaint_category", where=f"bin='{bin_}'", limit=15)
    if bbl:
        try:
            pl = soda("64uk-42ks", select="yearbuilt,unitsres,unitstotal,numfloors,ownername,bldgclass,yearalter1",
                      where=f"bbl='{bbl}'", limit=1)
            d["pluto"] = pl[0] if pl else {}
        except Exception as e:  # PLUTO is a nice-to-have
            d["pluto"] = {"error": str(e)}

    # flags
    flags = []
    open_c = sum(int(v["n"]) for v in d.get("hpd_violations", [])
                 if v["class"] == "C" and v["violationstatus"] == "Open")
    open_b = sum(int(v["n"]) for v in d.get("hpd_violations", [])
                 if v["class"] == "B" and v["violationstatus"] == "Open")
    if open_c:
        rec = d.get("open_c_recent")
        flags.append(f"RED: {open_c} OPEN class-C (immediately hazardous) HPD violation(s)"
                     + (f", {rec} issued in the last 3 years" if rec is not None else ""))
    if open_b >= 3:
        flags.append(f"YELLOW: {open_b} open class-B HPD violations")
    heat = sum(int(c["n"]) for c in d.get("complaints_311_2y", [])
               if "HEAT" in c["complaint_type"].upper())
    if heat >= 3:
        flags.append(f"YELLOW: {heat} heat/hot-water 311 complaints in 2y")
    elev = sum(int(c["n"]) for c in d.get("complaints_311_2y", [])
               if "ELEVATOR" in c["complaint_type"].upper())
    if elev >= 3:
        flags.append(f"YELLOW: {elev} elevator 311 complaints in 2y")
    lit_open = sum(int(x["n"]) for x in d.get("hpd_litigations", [])
                   if str(x.get("casestatus", "")).upper() not in ("CLOSED", ""))
    if lit_open:
        flags.append(f"YELLOW: {lit_open} HPD litigation case(s) not closed")
    res_evict = [e for e in d.get("evictions", [])
                 if e.get("residential_commercial_ind", "").startswith("R")]
    if res_evict:
        flags.append(f"NOTE: {len(res_evict)} residential marshal eviction(s) on record at this address")
    d["flags"] = flags or ["CLEAN: no automatic flags"]
    # one-line human summary, scaled to building size
    pl = d.get("pluto") or {}
    units = int(float(pl.get("unitsres") or 0)) or None
    d["grade"] = grade(open_c, open_b, units, flags, d.get("open_c_recent"))
    bits = []
    if pl.get("yearbuilt") and pl["yearbuilt"] not in ("0",):
        bits.append(f"built {pl['yearbuilt']}")
    if units:
        bits.append(f"{units} units")
    if pl.get("numfloors"):
        bits.append(f"{int(float(pl['numfloors']))} floors")
    if pl.get("ownername"):
        bits.append(f"owner {pl['ownername'].title()}")
    rec = d.get("open_c_recent")
    stale = f", {open_c - rec} of the C are 3y+ old paperwork" if rec is not None and open_c and open_c > rec else ""
    bits.append(f"HPD open: {open_c} C / {open_b} B" + (f" (per {units} units)" if units and (open_c or open_b) else "") + stale)
    top311 = ", ".join(f"{c['complaint_type'].lower()} {c['n']}" for c in d.get("complaints_311_2y", [])[:3])
    bits.append(f"311 2y: {top311}" if top311 else "311 2y: none")
    if lit_open:
        bits.append(f"{lit_open} open HPD litigation")
    if res_evict:
        bits.append(f"{len(res_evict)} evictions on record")
    d["summary"] = " · ".join(bits)
    det = d.get("open_c_detail") or []
    if det:
        d["open_c_text"] = "; ".join(
            f"{(x.get('apartment') or 'bldg').strip()} {str(x.get('inspectiondate') or '')[:10]}: "
            + " ".join((x.get("novdescription") or "").split())[:110]
            for x in det[:6])

    d["manual_links"] = {
        "openigloo": "https://www.openigloo.com/search?query=" + urllib.parse.quote(address),
        "hpd_building": f"https://hpdonline.nyc.gov/hpdonline/building/search-results?bbl={bbl}" if bbl else None,
        "dob_bis": f"https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin={bin_}" if bin_ else None,
        "acris_deeds": "https://a836-acris.nyc.gov/CP/",
    }
    return d


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('usage: vet.py "<address, borough>"')
    d = vet(sys.argv[1])
    print(json.dumps(d, indent=2))
    print("\n=== FLAGS ===", file=sys.stderr)
    for fl in d.get("flags", []):
        print(" -", fl, file=sys.stderr)
