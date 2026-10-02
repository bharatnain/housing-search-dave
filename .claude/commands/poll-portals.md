# Poll portals — non-StreetEasy sources

Run on "poll the portals" or "check the other sites". Load the profile; use `search.zips`, `search.boroughs`, `search.bedrooms`, `max_gross_rent` and `move.window` for every source. The StreetEasy guard does not apply here, but be polite: one source at a time, no hammering.

All imports go through `.venv/bin/python scripts/hunt.py add-listings <json>`, which de-duplicates against StreetEasy and reports cross-source overlap. Report the overlap and what is new. Store per-unit listing URLs only, never search-result URLs. Never complete a CAPTCHA: skip that source and say so.

## Scripted
1. `.venv/bin/python scripts/oi_scan.py` (OpenIgloo) writes `data/openigloo-<date>.json`; then `hunt.py add-listings` on it.
2. `.venv/bin/python scripts/lp_scan.py` (Listings Project, lease bucket) writes `data/lp-<date>.json`; then `hunt.py add-listings` on it.

## Browser recipes
Use the in-app browser pane, one site at a time, and wait for results to load before reading. Build the URL from the profile, read the listing cards, and open a card for the per-unit URL when the card lacks one. Skip anything that is a room share (e.g. "Just a Room").
- **Compass:** `https://www.compass.com/for-rent/<borough>-ny/price.max=<gross>/beds=<min>-<max>/` per borough (e.g. `manhattan`, `brooklyn`). Filter to the profile's areas by ZIP or neighborhood.
- **Leasebreak:** browser-only (no scripted access). Search by borough, max price, beds; check lease length against `min_lease_months`.
- **Glenwood:** Manhattan only; skip when no Manhattan area is in the profile. Read the availability list for matching beds and price.
- **StuyTown:** Manhattan only; skip unless the profile covers the East Side below 23rd Street. Read availability for matching beds and price.
- **Craigslist:** last, and screenshot the results first. It has the noisiest data; flag scam signals (no address, wire requests, below-market price) and never reply.

Write each browser find as a row in an add-listings JSON array (`source`, `address`, `unit`, `neighborhood`, `beds`, `price`, `url`, `borough`) in `data/`, import it with `hunt.py add-listings`, then give the user the new matches with price and a line on fit. Offer a dashboard refresh (`.venv/bin/python scripts/hunt_dashboard.py`, republish with `capabilities` omitted).
