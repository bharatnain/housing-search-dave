# Doorman

An apartment-hunt assistant for NYC renters that runs in Claude Code on your Mac. You tell it about your household and budget in a guided interview; it sweeps StreetEasy, scores listings against your taste, vets buildings with NYC open data, and keeps a dashboard where you tap pursue or pass and leave notes after viewings.

Based on the original Doorman. NYC only.

## Setup (Mac)

1. Install [Claude Code](https://claude.com/claude-code) and have a claude.ai account (the dashboard is published as an artifact there).
2. Clone this repo and `cd` into it.
3. Create the Python environment:
   ```
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```
4. Add the StreetEasy MCP server to Claude Code (see its own install instructions) so `search_rentals`, `get_rental_details`, `list_areas` and `list_amenities` are available. Alternatively set `streeteasy.source: api` in your profile to use the bundled direct client.
5. Open the folder in Claude Code and say: **set me up**.

Onboarding is a real interview (household, areas, budget, bedrooms, must-haves, timing), calibrated against a few listings you like or dislike. It writes `profile.yaml` only after you say yes, runs a first sweep, vets buildings and publishes your dashboard.

## Phrases that work

| Say | What happens |
|---|---|
| "set me up" / "change my budget" | `onboard`: interview, or a targeted profile edit |
| "run the digest" / "what's new" | `digest`: act on dashboard taps, sweep StreetEasy, score, vet, republish, report |
| "debrief 12 Example St 4A" | `debrief`: capture how a viewing went and propose taste changes for your yes |
| "vet 12 Example St" | `vet`: HPD/DOB building dossier graded clean, caution or red |
| "poll the portals" | `poll-portals`: OpenIgloo, Listings Project and browser-checked sites |

## Layout

```
CLAUDE.md               routing and standing rules Claude follows
TASTE.md                your taste model and dated change log
profile.example.yaml    documented profile template
profile.yaml            your profile (gitignored)
.claude/commands/       onboard, digest, debrief, vet, poll-portals runbooks
.claude/settings.json   script allowlist and deny rules
scripts/                profile loader, store, StreetEasy client, vetting, dashboard, portal scanners
data/weights.json       scoring weights (tuned at onboarding)
data/                   hunt.db, sweep pages, details, dashboard.html (gitignored)
tests/                  pytest suite and fixture profiles
```

## Privacy

- `profile.yaml`, `data/hunt.db`, raw sweep pages, logs and the generated dashboard are gitignored and stay on your Mac.
- Doorman never asks for or stores an SSN, income figures, bank details or passwords. Qualification claims for agents are yes/no only.
- It sends no email, drafts no outreach and touches no calendar. You contact agents yourself.
- StreetEasy calls go through a shared rate guard (daily cap, spacing, 24 h cooldown after a block). Doorman stops rather than work around a block.
- The dashboard artifact is private to your claude.ai account unless you share it; it contains the addresses you are tracking and your notes.
- Do not push this repo to a public remote with your profile or database committed; both are gitignored by default.
