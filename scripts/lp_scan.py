#!/usr/bin/env python3
"""Listings Project weekly scan: fetch all NYC real-estate listings, keep the lease fits.

Usage: python3 scripts/lp_scan.py [out.json]
Keeps Apartments for Rent / Lease Takeover within profile.yaml bedrooms and gross rent.
Writes data/lp-<date>.json, an array for: python3 scripts/hunt.py add-listings <file>
"""
import json
import re
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

import doorman_profile

BASE = "https://www.listingsproject.com/real-estate/new-york-city?page={}"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
MAX_PAGES = 35


def fetch(page):
    req = urllib.request.Request(BASE.format(page), headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def parse_page(html):
    out = []
    for chunk in html.split('<hr class="listing-index-hr">'):
        m_title = re.search(r'<h4[^>]*>\s*<a[^>]*href="(/listings/[^"]+)"[^>]*>\s*(.*?)\s*</a>',
                            chunk, re.S)
        if not m_title:
            continue
        l = {"url": "https://www.listingsproject.com" + m_title.group(1),
             "title": strip_tags(m_title.group(2))}
        m = re.search(r"\$([\d,]+(?:\.\d+)?)\s*(/month|/week|/day)?", chunk)
        if m:
            l["price"] = float(m.group(1).replace(",", ""))
            l["per"] = (m.group(2) or "").lstrip("/") or "flat"
        m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})\s*-\s*([A-Z][a-z]+ \d{1,2}, \d{4})", chunk)
        if m:
            l["start"], l["end"] = m.group(1), m.group(2)
        m = re.search(r'class="text-grey-dark mb-2 text-smish"[^>]*>\s*(.*?)</div>', chunk, re.S)
        if m:
            loccat = strip_tags(m.group(1))
            parts = [p.strip() for p in loccat.split("|")]
            l["location"] = parts[0]
            l["category"] = parts[1] if len(parts) > 1 else ""
        m = re.search(r"<p[^>]*>(.*?)<a[^>]*>See more", chunk, re.S)
        l["desc"] = strip_tags(m.group(1))[:400] if m else ""
        out.append(l)
    return out


def monthly(l):
    p, per = l.get("price"), l.get("per")
    if p is None:
        return None
    return {"month": p, "week": p * 4.33, "day": p * 30, "flat": None}.get(per)


def beds(l):
    t = (l["title"] + " " + l.get("desc", "")).lower()
    if re.search(r"\bstudio\b", t):
        return 0
    m = re.search(r"\b(\d)[\s-]*(?:br|bed|bedroom)", t)
    return int(m.group(1)) if m else None


def pdate(s):
    try:
        return datetime.strptime(s, "%B %d, %Y").date()
    except (ValueError, TypeError):
        return None


def cut_buckets(listings, prof):
    s = prof["search"]
    lo, hi, gross = s["bedrooms"]["min"], s["bedrooms"]["max"], s["max_gross_rent"]
    lease = []
    for l in listings:
        cat = l.get("category", "")
        mo = monthly(l)
        b = beds(l)
        l["monthly_equiv"] = round(mo) if mo else None
        l["beds_guess"] = b
        if cat.startswith("Apartments") and ("Rent" in cat or "Lease Takeover" in cat):
            if (mo is None or mo <= gross) and (b is None or lo <= b <= hi):
                lease.append(l)
    return lease


def listing(l):
    return {"source": "listingsproject", "address": l["title"], "neighborhood": l.get("location"),
            "price": l["monthly_equiv"], "url": l["url"]}


def main():
    prof = doorman_profile.load()
    all_listings, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        try:
            batch = parse_page(fetch(page))
        except Exception as e:
            print(f"page {page} failed: {e}", file=sys.stderr)
            break
        new = [l for l in batch if l["url"] not in seen]
        if not new:
            break
        seen.update(l["url"] for l in new)
        all_listings.extend(new)
        if len(batch) < 10:
            break
    lease = cut_buckets(all_listings, prof)
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).resolve().parent.parent / "data" / f"lp-{date.today()}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps([listing(l) for l in lease], indent=2))
    print(f"scanned {len(all_listings)} listings -> {dest}")
    print(f"lease/takeover fits your profile: {len(lease)}")


if __name__ == "__main__":
    main()
