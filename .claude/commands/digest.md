# Digest — the daily sweep

Run on "run the digest", "what's new", "status", or a vague greeting. Load the profile first (`python3 scripts/doorman_profile.py check`); if it fails or is missing, run `onboard.md`.

## 1. Dashboard inputs first
Read the dashboard db with the ArtifactData tool (`read_db`, artifact `dashboard.artifact_url`). Skip if the URL is null. Execute each item, then mark it handled (`write_db` update):
- `requests` where `done == false`: do what was asked (a search tweak, a question, a re-check), reply in the digest, set `done: true`.
- `decisions` where `acted == false`: `python3 scripts/hunt.py decide <id> pursue` (adds to the pipeline at `shortlisted`) or `... pass` (sets `passed`); set `acted: true`.
- `feedback` where `processed == false`: run `debrief.md` for each; set `processed: true`.

## 2. Sweep StreetEasy
Check `streeteasy.source` in the profile.

**`api` path:** `python3 scripts/se_api.py sweep` writes the pages; later `details` ids with `python3 scripts/se_api.py details <id..>`.

**`mcp` path:**
1. Get the params: `.venv/bin/python -c "import sys,json;sys.path.insert(0,'scripts');import doorman_profile as p;print(json.dumps(p.sweep_params(p.load())))"`.
2. For each page N (start at 1): run `python3 scripts/se_api.py guard-tick`. **Exit 2 means stop paging and skip the rest of StreetEasy today; say so in the digest.** Otherwise call `mcp__streeteasy__search_rentals` with those params plus `page: N`. Use `perPage` (never `limit`) and never `noFeeOnly` (it post-filters and breaks totals). Save the raw output to `data/sweep-pages/page-N.json`. Continue until the pages cover `totalCount`.
3. On any 403 or 429, run `python3 scripts/se_api.py guard-block <code>` immediately and stop StreetEasy for the day. Never retry, switch to `se_api.py`, or open StreetEasy in a browser to get around it.

Then `python3 scripts/hunt.py import-sweep <YYYY-MM-DD> data/sweep-pages/page-*.json` and capture the JSON diff (new, price changes, gone, excluded). **If it reports `"partial": true`, the sweep was incomplete and nothing was marked gone**; say so.

## 3. Details and scoring
For each new listing within budget (net at or under `max_net_rent`): `python3 scripts/se_api.py guard-tick --kind details` (exit 2 means stop), call `mcp__streeteasy__get_rental_details`, save to `data/details/<id>.json`; 403/429 means `guard-block <code>` and stop. Then `python3 scripts/hunt.py score-details data/details/<id>.json` (walk-up exclusions are applied here).

## 4. Vet and dashboard
- `python3 scripts/vet_pipeline.py` vets every building on the radar. A RED on a shortlisted building leads the report.
- `python3 scripts/hunt_dashboard.py` builds `data/dashboard.html`.
- Republish it to `dashboard.artifact_url` with the Artifact tool, **`capabilities` omitted**. (If the URL is null, first publish with `capabilities: {db: {}}` and store the URL in the profile.)

## 5. Report in chat
Top new matches with scores and one-line reasons; price cuts; departures; pipeline next-actions (`python3 scripts/hunt.py nudges`); dashboard decisions and requests acted on; the dashboard link. Note anything skipped (guard stop, partial sweep, blocked source). No outreach drafts unless the user asks, and never send anything.
