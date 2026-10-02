# Vet — building dossier

Run on "vet <address>" or "check this building". Include the borough if the user did not (e.g. `"12 Example Street, Brooklyn"`).

1. `.venv/bin/python scripts/vet.py "<address>"`. It builds an NYC open-data dossier (HPD violations and complaints, DOB items, and related records) and grades the building clean, caution or red, scaled by building size.
2. Report the grade first, then the specifics behind it: open or recent serious violations, patterns (heat, pests, water), construction or permit activity, anything that changes the decision. Say what the data cannot tell (management responsiveness, noise).
3. If the building is on the radar or pipeline, mention the listing and its score. A RED on a shortlisted building should be flagged plainly with a suggested question to ask in person.
4. Do not contact anyone. If the user wants to proceed, they direct any outreach.

`.venv/bin/python scripts/vet_pipeline.py` vets every building currently on the radar (used by the digest); use it when the user asks to "vet everything".
