# Debrief — capture a viewing, recalibrate taste

Run on "debrief <address>" or "I saw <address>", or from the digest when the dashboard has unprocessed `feedback`. This is the learning loop: feedback, taste model, weights, ranking.

## 1. Capture
If a dashboard debrief exists (ArtifactData `read_db`, collection `feedback`, `processed == false`), start from its verdict and text. Then ask short, specific questions, not a survey:
- The verdict (loved / maybe / no) and why, in their words.
- Light and exposure compared with the photos.
- Noise, smell, hallway and lobby condition, anything the listing hid.
- Would they take it today at the asking rent? At what rent would they?
- Anything that surprised them.

Record with `python3 scripts/hunt.py add-feedback <json>` (verdict, liked, disliked, dealbreakers, raw). Set the listing's pipeline stage to `viewed` with `python3 scripts/hunt.py seed-pipeline <json>` (a one-row array: address, unit, `"stage": "viewed"`; other fields are left untouched). Mark the dashboard doc `processed: true` (ArtifactData update). Rebuild and republish the dashboard (`python3 scripts/hunt_dashboard.py`, republish with `capabilities` omitted).

## 2. Recalibrate
Compare what they said with the model (`TASTE.md`, `data/weights.json`, the profile). Look for divergence, not confirmation, and read all feedback each time (`python3 scripts/hunt.py feedback`):
- A stated reason no weight captures: propose a new signal or rule.
- A weighted factor they did not care about in person: propose lowering it.
- A hard rule they bent, or a soft one they treated as hard: propose reclassifying.
- Patterns across several debriefs beat any single reaction.

Propose the specific edits (weight deltas with numbers, `TASTE.md` wording, profile changes) and get a yes or no **per change**. Never adjust silently. On a yes: edit `data/weights.json` (or the profile, then `doorman_profile.py check`), and add a dated row to the `TASTE.md` change log with the change, why, and their yes. Weights apply at the next scoring run; offer to re-score the shortlist by re-fetching its details (guard rules as in `digest.md`).

Do not commit `profile.yaml` or `data/hunt.db`. `TASTE.md` and `data/weights.json` may be committed if the user wants their taste history versioned.
