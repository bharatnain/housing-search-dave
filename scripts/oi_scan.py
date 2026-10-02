#!/usr/bin/env python3
"""OpenIgloo listings scan (server-rendered pages; filters are app-only, so we
crawl pages and filter locally against profile.yaml).

Usage: python3 scripts/oi_scan.py [pages=40] [out.json]
Writes data/openigloo-<date>.json, an array for: python3 scripts/hunt.py add-listings <file>
"""
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

import doorman_profile

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


def fetch(page):
    url = "https://www.openigloo.com/listings" + (f"?page={page}" if page > 1 else "")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


def parse(html):
    out = []
    for chunk in html.split('href="/unit/nyc/')[1:]:
        slug = chunk.split('"', 1)[0]
        m = re.search(r'alt="([^"]+?) #([^,"]+), (Manhattan|Brooklyn|Queens|Bronx|Staten Island), NY (\d{5})', chunk)
        if not m:
            continue
        addr, unit, boro, zipc = m.groups()
        pm = re.search(r"\$([\d,]{4,6})", chunk)
        price = int(pm.group(1).replace(",", "")) if pm else None
        text = re.sub(r"<[^>]+>", " ", chunk[:6000])
        beds = 0 if re.search(r"\bStudio\b", text) else (int(re.search(r"(\d) beds?", text).group(1))
                                                           if re.search(r"(\d) beds?", text) else None)
        hood = re.search(r">\s*([A-Za-z' .-]{3,40})\s*·\s*(?:Studio|\d bed)", text)
        out.append({"address": addr, "unit": unit, "borough": boro, "zip": zipc, "price": price,
                    "beds": beds, "neighborhood": hood.group(1).strip() if hood else None,
                    "stabilized": "Rent-stabilized" in chunk[:3000], "verified": "Verified" in chunk[:3000],
                    "url": "https://www.openigloo.com/unit/nyc/" + slug})
    return out


def fits(row, prof):
    s = prof["search"]
    b = s["bedrooms"]
    return (row["zip"] in s["zips"] and bool(row["price"]) and row["price"] <= s["max_gross_rent"]
            and (row["beds"] is None or b["min"] <= row["beds"] <= b["max"]))


def listing(r):
    out = {"source": "openigloo", "address": r["address"], "unit": r["unit"],
           "neighborhood": r["neighborhood"], "beds": r["beds"], "price": r["price"],
           "url": r["url"], "borough": r["borough"]}
    if r["stabilized"]:
        out["src_label"] = "rent-stabilized"
    return out


def main():
    prof = doorman_profile.load()
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    allrows, seen = [], set()
    for p in range(1, pages + 1):
        try:
            rows = parse(fetch(p))
        except Exception as e:
            print(f"page {p} failed: {e}", file=sys.stderr)
            break
        fresh = [r for r in rows if r["url"] not in seen]
        if not fresh:
            break
        seen.update(r["url"] for r in fresh)
        allrows.extend(fresh)
    keep = [r for r in allrows if fits(r, prof)]
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else \
        Path(__file__).resolve().parent.parent / "data" / f"openigloo-{date.today()}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps([listing(r) for r in keep], indent=2))
    print(f"scanned {len(allrows)} listings over <= {pages} pages -> {dest}")
    print(f"fits your profile: {len(keep)}  (stabilized: {sum(1 for r in keep if r['stabilized'])})")


if __name__ == "__main__":
    main()
