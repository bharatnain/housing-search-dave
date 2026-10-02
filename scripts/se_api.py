#!/usr/bin/env python3
"""Direct StreetEasy GraphQL client, the optional alternative to the StreetEasy MCP
(streeteasy.source: api). Output shapes match the MCP tool outputs that hunt.py expects.

Both paths share one guard (logs/se_guard.json): a daily call cap (streeteasy.daily_cap),
spacing between calls, and a 24 h cooldown after any 403/429. The MCP runbook calls
guard-tick before each MCP call and guard-block after a block, so neither path routes
around the other.

Usage:
  .venv/bin/python scripts/se_api.py sweep [--out-dir data/sweep-pages]      # all pages -> page files, prints paths
  .venv/bin/python scripts/se_api.py search --page N [--per-page 100] [--out FILE]
  .venv/bin/python scripts/se_api.py details <id> [<id> ...]                  # -> data/details/<id>.json (skips existing)
  .venv/bin/python scripts/se_api.py status                                   # calls today, daily cap, cooldown
  .venv/bin/python scripts/se_api.py guard-tick [--kind search|details]       # before each MCP call; exit 2 = stop
  .venv/bin/python scripts/se_api.py guard-block <code>                       # after a 403/429 on the MCP path
"""
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
import uuid
import urllib.error
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import doorman_profile

ENDPOINT = "https://api-v6.streeteasy.com/"
HEADERS = {
    "Host": "api-v6.streeteasy.com", "Connection": "keep-alive", "Sec-Ch-Ua-Platform": '"macOS"',
    "X-Forwarded-Proto": "https", "Sec-Ch-Ua": '"Chromium";v="133", "Not(A:Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0", "App-Version": "1.0.0",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Apollographql-Client-Version": "version  50bef71ef923e981bdcb7c781851c3bfdb12a0c1",
    "Apollographql-Client-Name": "srp-frontend-service", "Os": "web", "Dnt": "1",
    "Origin": "https://streeteasy.com", "Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty", "Referer": "https://streeteasy.com/", "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
}
ROOT = Path(__file__).resolve().parent.parent
SE_BASE = "https://streeteasy.com"
PHOTO_CDN = "https://photos.zillowstatic.com/fp"

FRAGMENTS = """
fragment LeadMediaForSRP on LeadMedia { __typename photo { __typename key } floorPlan { __typename key } }
fragment OpenHouseForSRP on OpenHouseDigest { __typename startTime endTime appointmentOnly }
fragment RentalListingDigestForSearchResults on SearchRentalListing {
  __typename id areaName availableAt bedroomCount buildingType fullBathroomCount furnished
  geoPoint { __typename latitude longitude } halfBathroomCount hasTour3d hasVideos isNewDevelopment
  leadMedia { __typename ...LeadMediaForSRP } leaseTermMonths livingAreaSize mediaAssetCount monthsFree
  noFee netEffectivePrice offMarketAt photos { __typename key } price priceChangedAt priceDelta slug
  sourceGroupLabel sourceType status street unit upcomingOpenHouse { __typename ...OpenHouseForSRP } urlPath
}
"""

DETAILS_QUERY = """
query RentalListingDetailsFederated($listingID: ID!) {
  rentalByListingId(id: $listingID) {
    id offMarketAt availableAt buildingId status statusChanges { status changedAt }
    createdAt updatedAt interestingChangeAt description
    media { photos { key } floorPlans { key } videos { imageUrl id provider } tour3dUrl assetCount }
    propertyDetails { address { street houseNumber streetName city state zipCode unit } roomCount bedroomCount
      fullBathroomCount halfBathroomCount livingAreaSize
      amenities { list doormanTypes parkingTypes sharedOutdoorSpaceTypes storageSpaceTypes }
      features { list fireplaceTypes privateOutdoorSpaceTypes views } }
    mlsNumber backOffice { brokerageListingId }
    pricing { leaseTermMonths monthsFree noFee price priceDelta priceChanges { changedAt } }
    recentListingsPriceStats { rentalPriceStats { medianPrice } salePriceStats { medianPrice } }
    upcomingOpenHouses { id startTime endTime appointmentOnly }
    listingSource { sourceType }
    propertyHistory { listingId sourceGroupLabel photos { key } offMarketAt
      rentalEventsOfInterest { date price ... on PriceChangeOfInterest { pricePercentChange } ... on RentalStatusChangeOfInterest { status } } }
  }
  buildingByRentalListingId(id: $listingID) {
    id name type residentialUnitCount yearBuilt status additionalDetails { leasingStartDate salesStartDate }
    address { street city state zipCode } heroImage { key } media { photos { key } } complex { id name } area { name }
    saleInventorySummary { availableListingDigests { id } } rentalInventorySummary { availableListingDigests { id } }
    isLandLease policies { list petPolicy { catsAllowed dogsAllowed maxDogWeight restrictedDogBreeds } }
    nearby { transitStations { name distance routes geo { latitude longitude } } }
  }
  getBuildingExpressByRentalListingId(id: $listingID) { nearbySchools { name district grades id idstr geoCenter { latitude longitude } } }
  getRentalListingExpressById(id: $listingID) { hasActiveBuildingShowcase }
}
"""


# The bot wall blocks bursts from one address, not single daily sweeps: never retry a 403
# (each retry deepens the block), pace every call, and cap calls per run and per day.
# Detail reads trip it far more than search pages, so they get much slower spacing.
MIN_GAP_S = 2.0          # seconds between any two calls
JITTER_S = 1.5           # plus a random 0..JITTER_S so the cadence is not machine-regular
CALL_BUDGET = int(os.environ.get("SE_BUDGET", "80"))   # calls per run; details loops must stay under it
DAILY_BUDGET = 120       # calls per day, all scripts and the MCP together; main() reads streeteasy.daily_cap
COOLDOWN_H = 24          # hours to stay off StreetEasy after a block
DETAILS_GAP_S = 10
DETAILS_JITTER_S = 10    # 10–20 s between detail reads, random each time
STATE = ROOT / "logs" / "se_guard.json"
_last_call = [0.0]
_calls = [0]


class Blocked(RuntimeError):
    """StreetEasy refused, or the guard says stop: do not retry, do not route around it."""


def _state():
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        s = json.loads(STATE.read_text())
    except (OSError, ValueError):
        s = {}
    if s.get("day") != today:
        s["day"], s["calls"] = today, 0
    if s.get("blocked_until") and datetime.fromisoformat(s["blocked_until"]) <= datetime.now(timezone.utc):
        s.pop("blocked_until")
    return s


def _save(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s))


def guard_status():
    s = _state()
    return {"calls_today": s["calls"], "daily_budget": DAILY_BUDGET, "blocked_until": s.get("blocked_until")}


def guard_tick(kind="search"):
    """Check the shared guard, wait out the spacing, and count one call. Raises Blocked."""
    s = _state()
    if s.get("blocked_until"):
        raise Blocked(f"StreetEasy cooling down until {s['blocked_until']} after a block. Not calling.")
    if s["calls"] >= DAILY_BUDGET:
        raise Blocked(f"daily budget {DAILY_BUDGET} reached ({s['calls']} calls today, all scripts). Not calling.")
    gap = DETAILS_GAP_S + random.uniform(0, DETAILS_JITTER_S) if kind == "details" \
        else MIN_GAP_S + random.uniform(0, JITTER_S)
    wait = gap - (time.time() - max(_last_call[0], s.get("last_call_at", 0)))
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = s["last_call_at"] = time.time()
    s["calls"] += 1
    _save(s)


def guard_block(code):
    s = _state()
    s["blocked_until"] = (datetime.now(timezone.utc) + timedelta(hours=COOLDOWN_H)).isoformat(timespec="seconds")
    _save(s)
    return s["blocked_until"]


def gql(query, variables=None, retries=2, kind="search"):
    body = {"query": query}
    if variables:
        body["variables"] = variables
    if _calls[0] >= CALL_BUDGET:
        raise Blocked(f"call budget {CALL_BUDGET} reached for this run (set SE_BUDGET to raise it deliberately)")
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), headers=HEADERS, method="POST")
    last = None
    for i in range(retries):
        guard_tick(kind)
        _calls[0] += 1
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                d = json.loads(r.read().decode())
            if d.get("errors") and not d.get("data"):
                raise RuntimeError("StreetEasy GraphQL Error: " + d["errors"][0].get("message", "?"))
            return d["data"]
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                until = guard_block(e.code)
                raise Blocked(f"StreetEasy {e.code} (bot wall). Not retrying; cooling down until {until}.") from e
            last = e; time.sleep(3 * (i + 1))
        except Exception as e:  # noqa
            last = e; time.sleep(3 * (i + 1))
    raise last


def search_query(areas, max_price, min_beds, max_beds, amenities, page, per_page):
    filters = (f"{{ rentalStatus: ACTIVE, areas: [{', '.join(str(a) for a in areas)}], "
               f"price: {{ lowerBound: null, upperBound: {max_price} }}, "
               f"bedrooms: {{ lowerBound: {min_beds}, upperBound: {max_beds} }}, "
               f"amenities: [{', '.join(amenities)}] }}")
    inp = (f"{{ sorting: {{ attribute: PRICE, direction: ASCENDING }}, filters: {filters}, "
           f"adStrategy: NONE, userSearchToken: \"{uuid.uuid4()}\", perPage: {per_page}, page: {page} }}")
    return f"""
query SearchRentalsFederated {{
  searchRentals(input: {inp}) {{
    __typename
    edges {{
      __typename
      ... on OrganicRentalEdge {{ node {{ __typename ...RentalListingDigestForSearchResults }} amenitiesMatch matchedAmenities missingAmenities }}
      ... on FeaturedRentalEdge {{ node {{ __typename ...RentalListingDigestForSearchResults }} amenitiesMatch matchedAmenities missingAmenities }}
      ... on SponsoredRentalEdge {{ node {{ __typename ...RentalListingDigestForSearchResults }} sponsoredSimilarityLabel }}
    }}
    totalCount
  }}
}}
{FRAGMENTS}"""


def normalize(edge):
    n = edge["node"]
    kind = {"FeaturedRentalEdge": "featured", "SponsoredRentalEdge": "sponsored"}.get(edge.get("__typename"), "organic")
    bath = (n.get("fullBathroomCount") or 0) + ((n.get("halfBathroomCount") or 0) * 0.5)
    geo = n.get("geoPoint")
    lead = ((n.get("leadMedia") or {}).get("photo") or {}).get("key")
    url = f"{SE_BASE}/{n['urlPath'].lstrip('/')}" if n.get("urlPath") else f"{SE_BASE}/rental/{n['id']}"
    out = {
        "id": n["id"], "kind": kind,
        "address": " ".join(x for x in [n.get("street"), n.get("unit")] if x).strip(),
        "street": n.get("street"), "unit": n.get("unit") or None, "neighborhood": n.get("areaName"),
        "price": n.get("price"), "netEffectivePrice": n.get("netEffectivePrice"), "noFee": n.get("noFee"),
        "monthsFree": n.get("monthsFree"), "priceDelta": n.get("priceDelta"),
        "bedrooms": n.get("bedroomCount"), "bathrooms": bath,
        "fullBathrooms": n.get("fullBathroomCount"), "halfBathrooms": n.get("halfBathroomCount"),
        "livingAreaSize": n.get("livingAreaSize"), "buildingType": n.get("buildingType"),
        "furnished": n.get("furnished"), "status": n.get("status"), "availableAt": n.get("availableAt"),
        "leaseTermMonths": n.get("leaseTermMonths"), "isNewDevelopment": n.get("isNewDevelopment"),
        "hasVideos": n.get("hasVideos"), "hasTour3d": n.get("hasTour3d"), "mediaAssetCount": n.get("mediaAssetCount"),
        "sourceGroupLabel": n.get("sourceGroupLabel"),
        "geo": {"latitude": geo["latitude"], "longitude": geo["longitude"]} if geo else None,
        "url": url, "slug": n.get("slug"),
        "leadPhotoUrl": f"{PHOTO_CDN}/{lead}-se_large_800_400.jpg" if lead else None,
        "photoUrls": [f"{PHOTO_CDN}/{p['key']}-se_large_800_400.jpg" for p in (n.get("photos") or []) if p.get("key")],
    }
    if "amenitiesMatch" in edge:
        out["amenitiesMatch"] = edge.get("amenitiesMatch")
        out["matchedAmenities"] = edge.get("matchedAmenities") or []
        out["missingAmenities"] = edge.get("missingAmenities") or []
    return out


def search(page, per_page, prof):
    s = prof["search"]
    d = gql(search_query(doorman_profile.area_codes(prof), s["max_gross_rent"], s["bedrooms"]["min"],
                         s["bedrooms"]["max"], s.get("required_amenities") or [], page, per_page))
    edges = [e for e in d["searchRentals"]["edges"] if e and e.get("node")]
    listings = [normalize(e) for e in edges]
    return {"totalCount": d["searchRentals"]["totalCount"], "page": page, "perPage": per_page,
            "returned": len(listings), "listings": listings}


def strip_typename(v):
    if isinstance(v, list):
        return [strip_typename(x) for x in v]
    if isinstance(v, dict):
        return {k: strip_typename(x) for k, x in v.items() if k != "__typename"}
    return v



def details(listing_id):
    d = strip_typename(gql(DETAILS_QUERY, {"listingID": str(listing_id)}, kind="details"))
    r = d.get("rentalByListingId")
    if r:
        r["url"] = f"{SE_BASE}/rental/{listing_id}"
        m = r.get("media") or {}
        r.setdefault("media", m)
        m["photoUrls"] = [f"{PHOTO_CDN}/{p['key']}-se_large_800_400.jpg" for p in (m.get("photos") or []) if p.get("key")]
        m["floorPlanUrls"] = [f"{PHOTO_CDN}/{p['key']}-se_large_800_400.jpg" for p in (m.get("floorPlans") or []) if p.get("key")]
        m["videoLinks"] = []
    return d



def _write_pages(prof, out_dir):
    """Page through the profile's search. Returns (paths, totalCount); on Blocked the pages
    already written are kept and the error is printed, so a partial sweep can still import."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("page-*.json"):
        f.unlink()
    paths, total, pages, p = [], 0, 1, 1
    try:
        while p <= pages:
            res = search(p, 100, prof)
            total = res["totalCount"]
            pages = max(1, -(-total // 100))
            f = out_dir / f"page-{p}.json"
            f.write_text(json.dumps(res)); paths.append(str(f))
            p += 1
    except Blocked as e:
        print(f"stopped after {len(paths)} pages: {e}", file=sys.stderr)
    return paths, total, pages


def main(argv):
    global DAILY_BUDGET
    if not argv:
        print(__doc__); return 1
    cmd, args = argv[0], argv[1:]
    try:
        prof = doorman_profile.load()
        DAILY_BUDGET = prof["streeteasy"]["daily_cap"]
    except doorman_profile.ProfileError as e:
        if cmd in ("search", "sweep"):
            print(e, file=sys.stderr); return 1
        prof = None   # guard commands still work before onboarding, at the default cap
    if cmd == "search":
        page = int(args[args.index("--page") + 1]) if "--page" in args else 1
        per = int(args[args.index("--per-page") + 1]) if "--per-page" in args else 100
        res = search(page, per, prof)
        if "--out" in args:
            Path(args[args.index("--out") + 1]).write_text(json.dumps(res))
            print(args[args.index("--out") + 1], res["totalCount"], res["returned"])
        else:
            print(json.dumps(res)[:2000])
    elif cmd == "sweep":
        out_dir = Path(args[args.index("--out-dir") + 1]) if "--out-dir" in args else ROOT / "data" / "sweep-pages"
        paths, total, pages = _write_pages(prof, out_dir)
        print(f"sweep: totalCount={total} pages={pages}", file=sys.stderr)
        print(" ".join(paths))
    elif cmd == "details":
        dest = ROOT / "data" / "details"; dest.mkdir(parents=True, exist_ok=True)
        ok = fail = skip = 0
        for lid in args:
            f = dest / f"{lid}.json"
            if f.exists():
                skip += 1; continue
            try:
                f.write_text(json.dumps(details(lid))); ok += 1
            except Blocked as e:
                print(f"blocked after {ok} details: {e}", file=sys.stderr); break
            except Exception as e:
                fail += 1; print("fail", lid, str(e)[:120], file=sys.stderr)
        print(f"details: ok={ok} fail={fail} skipped={skip}")
    elif cmd == "status":
        print(json.dumps(guard_status()))
    elif cmd == "guard-tick":
        kind = args[args.index("--kind") + 1] if "--kind" in args else "search"
        try:
            guard_tick(kind)
        except Blocked as e:
            print(f"stop: {e}"); return 2
        print(json.dumps(guard_status()))
    elif cmd == "guard-block":
        if not args:
            print(__doc__); return 1
        print(f"StreetEasy {args[0]}: cooling down until {guard_block(args[0])}. No StreetEasy calls until then.")
    else:
        print(__doc__); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
