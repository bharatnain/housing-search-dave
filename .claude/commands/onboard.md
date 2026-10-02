# Onboard — set up or change the profile

Run on "set me up", "change my ...", or automatically when `profile.yaml` is missing. This is a real interview, one topic per turn. Reflect each answer back before moving on. Plain language, no jargon.

Never ask for or store an SSN, income figures, bank details or passwords. If the user volunteers one, do not repeat or save it.

## Re-entry (profile already exists)
A targeted change ("raise budget to 5,400", "add Astoria") edits that section of `profile.yaml` only: show the before/after, get a yes, write, then `python3 scripts/doorman_profile.py check`. Weight or taste changes need an explicit yes and a `TASTE.md` entry with the date and their words.

## 1. Preflight
Stop on any blocker and give a plain fix.
- `python3 --version`; `ls .venv/bin/python` (if missing: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`).
- The StreetEasy MCP answers: call `mcp__streeteasy__list_areas`. If `streeteasy.source: api` is wanted instead, skip this and note it.
- The Artifact tool is available (needed for the dashboard).

## 2. Interview
- **Household:** who is moving, how many adults, pets (none / cat / dog / other), room needs (e.g. an office). A name for the dashboard title.
- **Money:** max gross rent (asking). Explain net-effective (rent after free months spread over the lease) and ask for the net cap too; net must not exceed gross. Broker fees and concessions tolerance. Then the yes/no claims only: meets the landlord income multiple? credit above 700? guarantor available? Never ask for figures.
- **Where:** their words become areas. Call `mcp__streeteasy__list_areas`, resolve each to `{name, code}`, show the resolved list and get a confirmation. Derive the ZIPs for those areas (used by the portal scanners) and the boroughs. Then hard boundaries (e.g. nothing north of a given street, set `exclude.north_of_street`) and commute anchors (record as notes for `TASTE.md`, not profile fields).
- **Unit:** bedrooms min/max (0 = studio), minimum square feet (optional), walk-up tolerance (`exclude.max_walkup_floor`, null for any), co-ops yes/no (`exclude.coops`), outdoor space, laundry.
- **Building:** call `mcp__streeteasy__list_amenities`. Must-haves become `required_amenities` (sweep filters, so keep the list short); nice-to-haves become weights in `data/weights.json`. Explain the neutral defaults (floor, light, views, spacious/cramped, doorman, laundry, utilities included, rent stabilization, modern building) and let them raise, lower or zero any.
- **Timing:** move window (two dates) and minimum lease months.
- **StreetEasy source:** `mcp` (default) or `api`, and the daily call cap (default 120; explain it protects their account).
- **Calibration:** ask for 3 to 5 StreetEasy listings they like or dislike (links or addresses). Fetch each with `mcp__streeteasy__get_rental_details` (run `python3 scripts/se_api.py guard-tick --kind details` before each; stop on exit 2; on any 403/429 run `python3 scripts/se_api.py guard-block <code>`). Save each to `data/details/<id>.json`, score with the draft weights, explain each score, and adjust the draft weights until the ranking matches their gut. Show the weight changes.

## 3. Confirm
Show a readable summary of the proposed `profile.yaml`, `data/weights.json` and the `TASTE.md` seed (boundaries, commute anchors, deal-breakers, nice-to-haves). Write the files **only after the user says yes**. Then run `python3 scripts/doorman_profile.py check` and fix anything it reports (it names the field).

## 4. First run
1. `python3 scripts/hunt.py init`.
2. Sweep and import exactly as in `digest.md` section 2 (MCP path) or `python3 scripts/se_api.py sweep` (api path); then `hunt.py import-sweep`, details for in-budget listings, `hunt.py score-details`.
3. `python3 scripts/vet_pipeline.py`.
4. Portal poll: `python3 scripts/oi_scan.py` and `python3 scripts/lp_scan.py`, then `hunt.py add-listings` on each output (scripted portals only; browser portals are on request via "poll the portals").
5. `python3 scripts/hunt_dashboard.py`, then publish `data/dashboard.html` as an artifact with `capabilities: {db: {}}`. Save the returned URL to `dashboard.artifact_url` in `profile.yaml` and re-run `doorman_profile.py check`.
6. Show a five-line "how to use Doorman" card: the five phrases (set me up, run the digest, debrief <address>, vet <address>, poll the portals), plus: tap pursue/pass and leave debriefs on the dashboard, Doorman acts on them at the next digest.
