#!/usr/bin/env python3
"""Build the Doorman dashboard page from data/hunt.db and the profile -> data/dashboard.html.

Usage: python3 scripts/hunt_dashboard.py   # then publish data/dashboard.html as an artifact
The page's script writes to the artifact db: requests, decisions/<listing id> and feedback/<key>.
"""
import html
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import doorman_profile  # noqa: E402

DB = ROOT / "data" / "hunt.db"
OUT = ROOT / "data" / "dashboard.html"
NUDGE_HOURS = 48
COLS = 6
VET_LABEL = {"clean": "bldg clean", "caution": "bldg caution", "red": "bldg RED"}
VERDICT = {"loved": ("&#9829; loved", "v-loved"), "maybe": ("~ maybe", "v-maybe"), "no": ("&#10005; no", "v-no")}


def esc(v):
    return html.escape(str(v), quote=True) if v is not None else ""


def href(u):
    return esc(u) if str(u or "").startswith(("http://", "https://")) else "#"


CSS = r'''
  :root { --paper:#FBF9F5; --card:#FFFFFF; --ink:#21262B; --muted:#68716C; --line:#E5E0D4;
    --accent:#2E6B5C; --accent-soft:#E8F0EC; --brass:#96762E; --brass-soft:#F5EEDD;
    --alert:#A04B3A; --alert-soft:#F6E9E5; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
    --paper:#16191B; --card:#1D2124; --ink:#E6E3DA; --muted:#9AA39D; --line:#2D3335;
    --accent:#6BB09C; --accent-soft:#22322D; --brass:#CDA85F; --brass-soft:#322B1C;
    --alert:#D98A77; --alert-soft:#35241F; } }
  :root[data-theme="dark"] {
    --paper:#16191B; --card:#1D2124; --ink:#E6E3DA; --muted:#9AA39D; --line:#2D3335;
    --accent:#6BB09C; --accent-soft:#22322D; --brass:#CDA85F; --brass-soft:#322B1C;
    --alert:#D98A77; --alert-soft:#35241F; }
  * { box-sizing:border-box; }
  body { background:var(--paper); color:var(--ink); margin:0; padding:2.5rem 1.25rem 4rem;
    font-family:"Public Sans","Helvetica Neue",Arial,sans-serif; font-size:15px; line-height:1.5; }
  .wrap { max-width:78rem; margin:0 auto; }
  a { color:var(--accent); }
  .eyebrow { font-size:.7rem; font-weight:700; letter-spacing:.12em; text-transform:uppercase; color:var(--accent); margin:0 0 .5rem; }
  h1 { font-family:"Fraunces",Georgia,serif; font-weight:600; font-size:2.2rem; margin:0 0 .3rem; }
  .dateline { font-size:.8rem; color:var(--muted); margin:0 0 1.75rem; }
  .stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:.7rem; margin:0 0 2rem; }
  .stat { background:var(--card); border:1px solid var(--line); border-radius:6px; padding:.75rem .9rem; }
  .stat b { display:block; font-family:"IBM Plex Mono",monospace; font-size:1.3rem; font-weight:600; font-variant-numeric:tabular-nums; }
  .stat span { font-size:.74rem; color:var(--muted); }
  h2 { font-family:"Fraunces",Georgia,serif; font-weight:600; font-size:1.25rem; margin:2rem 0 .6rem; }
  .count { font-family:"IBM Plex Mono",monospace; font-size:.8rem; color:var(--muted); margin-left:.5rem; }
  .tablewrap { overflow-x:auto; border:1px solid var(--line); border-radius:8px; background:var(--card); margin:.5rem 0 1.5rem; }
  table { border-collapse:collapse; width:100%; font-size:.86rem; }
  th,td { text-align:left; padding:.55rem .8rem; border-top:1px solid var(--line); vertical-align:top; }
  th { white-space:nowrap; }
  thead th { border-top:0; font-size:.7rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
  td.num { font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums; white-space:nowrap; }
  td .chip { display:inline-block; margin:.2rem .2rem 0 0; }
  .cut { color:var(--accent); font-weight:700; }
  .empty { color:var(--muted); }
  .hint { font-size:.78rem; color:var(--muted); margin:-.3rem 0 .6rem; }
  .day { margin:0 0 1rem; }
  .day h3 { font-size:.74rem; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--brass);
    margin:.6rem 0 .3rem; }
  .row { display:grid; grid-template-columns:5.2rem 1fr; gap:.6rem; background:var(--card); border:1px solid var(--line);
    border-radius:8px; padding:.6rem .8rem; margin:.35rem 0; }
  .row.slim { grid-template-columns:1fr; padding:.45rem .8rem; }
  .when { font-family:"IBM Plex Mono",monospace; font-size:.86rem; font-weight:600; font-variant-numeric:tabular-nums;
    color:var(--ink); padding-top:.1rem; }
  .when.muted { color:var(--muted); font-size:.78rem; }
  .v-loved { color:var(--accent); } .v-maybe { color:var(--brass); } .v-no { color:var(--alert); }
  .body { min-width:0; }
  .head { display:flex; flex-wrap:wrap; align-items:baseline; gap:.15rem .5rem; }
  .addr { font-weight:700; font-size:.95rem; }
  .unit { font-family:"IBM Plex Mono",monospace; font-weight:600; }
  .hood { font-size:.7rem; color:var(--muted); font-weight:600; text-transform:uppercase; letter-spacing:.06em; }
  .chips { display:flex; flex-wrap:wrap; gap:.35rem; margin-top:.25rem; }
  .chip { font-size:.72rem; font-weight:600; padding:.14rem .5rem; border-radius:99px; background:var(--accent-soft); color:var(--accent); }
  .chip.stale, .chip.due-hot { background:var(--alert-soft); color:var(--alert); }
  .chip.due-warm, .chip.draft { background:var(--brass-soft); color:var(--brass); }
  .chip.vet-clean { background:var(--accent-soft); color:var(--accent); }
  .chip.vet-caution { background:var(--brass-soft); color:var(--brass); }
  .chip.vet-red { background:var(--alert-soft); color:var(--alert); }
  .chip.vet-none { background:transparent; border:1px dashed var(--line); color:var(--muted); font-weight:500; }
  .more ul { margin:.2rem 0 0; padding-left:1.1rem; }
  .na { margin:.35rem 0 0; font-size:.86rem; color:var(--ink); }
  .na b { color:var(--brass); }
  .more, .fbd { margin-top:.35rem; font-size:.8rem; }
  .more summary, .fbd summary { cursor:pointer; color:var(--muted); font-weight:600; }
  .more summary:hover, .fbd summary:hover { color:var(--accent); }
  .fold { margin-top:1.5rem; }
  .fold summary { cursor:pointer; font-family:"Fraunces",Georgia,serif; font-weight:600; font-size:1.1rem; color:var(--muted); }
  @media (max-width:520px) { .row { grid-template-columns:1fr; gap:.2rem; } }
  .notes { margin:.3rem 0 0; font-size:.82rem; color:var(--muted); }
  footer { margin-top:2.5rem; border-top:1px solid var(--line); padding-top:1rem; font-size:.78rem; color:var(--muted); }
  .decide { display:flex; gap:.35rem; }
  .decide button { font:inherit; font-size:.74rem; font-weight:700; padding:.25rem .6rem; border-radius:6px;
    border:1px solid var(--line); background:var(--card); color:var(--ink); cursor:pointer; }
  .decide button.pursue:hover { border-color:var(--accent); color:var(--accent); }
  .decide button.pass:hover { border-color:var(--alert); color:var(--alert); }
  .decide button:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
  .decided { font-size:.74rem; font-weight:700; }
  .decided.p { color:var(--accent); }
  .decided.x { color:var(--alert); }
  .fb { margin-top:.7rem; border-top:1px dashed var(--line); padding-top:.6rem; display:flex; flex-direction:column; gap:.45rem; }
  .fbrow { display:flex; align-items:center; gap:.4rem; flex-wrap:wrap; }
  .fblabel { font-size:.74rem; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; }
  .fb button { font:inherit; font-size:.76rem; font-weight:700; padding:.28rem .65rem; border-radius:6px;
    border:1px solid var(--line); background:var(--card); color:var(--ink); cursor:pointer; }
  .fb button[aria-pressed="true"] { border-color:var(--accent); background:var(--accent-soft); color:var(--accent); }
  .fb button:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
  .fb textarea { font:inherit; font-size:.84rem; border:1px solid var(--line); border-radius:6px;
    background:var(--paper); color:var(--ink); padding:.45rem .6rem; resize:vertical; }
  .fb .fbsave { align-self:flex-start; }
  .fbstate { font-size:.76rem; color:var(--accent); font-weight:600; }
  .askbox { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:.9rem 1.1rem; margin:0 0 1.75rem; }
  .askbox label { display:block; font-size:.74rem; font-weight:700; color:var(--accent); text-transform:uppercase; letter-spacing:.08em; margin-bottom:.45rem; }
  .askrow { display:flex; gap:.5rem; }
  .askrow input { flex:1; font:inherit; font-size:.9rem; border:1px solid var(--line); border-radius:6px; background:var(--paper); color:var(--ink); padding:.5rem .7rem; min-width:0; }
  .askrow button { font:inherit; font-size:.84rem; font-weight:700; padding:.5rem 1rem; border-radius:6px; border:1px solid var(--accent); background:var(--accent-soft); color:var(--accent); cursor:pointer; }
  .askrow button:focus-visible, .askrow input:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
  .askstate { margin:.5rem 0 0; font-size:.76rem; color:var(--muted); }
'''

JS = r'''
  (async () => {
    if (typeof claude === "undefined" || !claude.use) return;
    const db = await claude.use("db");
    if (!db) return;
    // free-text requests -> requests/<autoid>
    const askbox = document.querySelector(".askbox");
    if (askbox) {
      askbox.hidden = false;
      const input = askbox.querySelector("input");
      const state = askbox.querySelector(".askstate");
      const send = async () => {
        const text = input.value.trim();
        if (!text) return;
        try {
          await db.collection("requests").add({
            text, at: new Date().toISOString(), done: false
          });
          input.value = "";
          state.textContent = "Sent — handled by the next session or the next digest.";
        } catch (e) { state.textContent = "Couldn't send, try again."; }
      };
      askbox.querySelector("#asksend").addEventListener("click", send);
      input.addEventListener("keydown", ev => { if (ev.key === "Enter") send(); });
    }

    const cells = document.querySelectorAll(".decide");
    const byId = {};
    // a listing can sit in more than one table (e.g. Top matches and New matches) — track every cell per id
    cells.forEach(c => { (byId[c.dataset.id] ||= []).push(c); c.hidden = false; });
    function markAll(id, choice) {
      (byId[id] || []).forEach(c => { if (c.isConnected) markDecided(c, choice); });
    }
    function markDecided(cell, choice) {
      const el = document.createElement("span");
      el.className = "decided " + (choice === "pursue" ? "p" : "x");
      el.textContent = choice === "pursue" ? "\u2665 pursuing" : "\u2715 passed";
      cell.replaceWith(el);
    }
    cells.forEach(cell => {
      cell.addEventListener("click", async (ev) => {
        const btn = ev.target.closest("button");
        if (!btn) return;
        const choice = btn.classList.contains("pursue") ? "pursue" : "pass";
        btn.disabled = true;
        try {
          await db.doc("decisions/" + cell.dataset.id).set({
            choice, address: cell.dataset.addr,
            at: new Date().toISOString(), acted: false
          });
          markAll(cell.dataset.id, choice);
        } catch (e) { btn.disabled = false; }
      });
    });
    db.collection("decisions").onSnapshot(snap => {
      snap.docs.forEach(d => {
        const body = d.data();
        if (body && body.choice) markAll(d.id, body.choice);
      });
    }, () => {});

    // viewing debriefs -> feedback/<key>
    document.querySelectorAll(".fb").forEach(async box => {
      box.closest(".fbd").hidden = false;
      const ref = db.doc("feedback/" + box.dataset.key);
      const state = box.querySelector(".fbstate");
      const ta = box.querySelector("textarea");
      let verdict = null;
      const vbtns = box.querySelectorAll(".fbrow button");
      vbtns.forEach(b => b.addEventListener("click", () => {
        verdict = b.dataset.v;
        vbtns.forEach(x => x.setAttribute("aria-pressed", String(x === b)));
      }));
      try {
        const snap = await ref.get();
        if (snap.exists) {
          const d = snap.data();
          verdict = d.verdict || null;
          ta.value = d.raw || "";
          vbtns.forEach(x => x.setAttribute("aria-pressed", String(x.dataset.v === verdict)));
          state.textContent = "saved " + (d.at || "").slice(0, 16).replace("T", " ");
          box.closest(".fbd").querySelector("summary").textContent = "Debrief saved (" + verdict + ") — edit";
        }
      } catch (e) {}
      box.querySelector(".fbsave").addEventListener("click", async () => {
        if (!verdict) { state.textContent = "pick loved / maybe / no first"; return; }
        try {
          await ref.set({
            address: box.dataset.addr, unit: box.dataset.unit,
            verdict, raw: ta.value.trim(),
            at: new Date().toISOString(), processed: false
          });
          state.textContent = "saved — the next digest will process it";
        } catch (e) { state.textContent = "save failed, try again"; }
      });
    });
  })();
'''


def _dt(v):
    if not v:
        return None
    try:
        ts = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _money(n):
    return f"${int(n):,}"


def build(conn, prof):
    conn.row_factory = sqlite3.Row
    s = prof["search"]
    cap = s["max_net_rent"]
    bmin, bmax = s["bedrooms"]["min"], s["bedrooms"]["max"]
    sizes = "studio" if bmax == 0 else f"{'studio' if bmin == 0 else str(bmin) + 'BR'} to {bmax}BR"
    areas = ", ".join(a["name"].replace("_", " ").title() for a in s.get("areas") or [])
    ex = s["exclude"]
    rules = []
    if ex.get("coops"):
        rules.append("no co-ops")
    if ex.get("north_of_street"):
        rules.append(f"nothing north of {ex['north_of_street']}th St")
    if ex.get("max_walkup_floor"):
        rules.append(f"walk-ups only to floor {ex['max_walkup_floor']}")
    criteria = f"Up to {_money(cap)} net · {sizes}" + (f" · {areas}" if areas else "") + \
        "".join(f" · {r}" for r in rules)

    q = lambda sql, *a: conn.execute(sql, a).fetchall()
    counts = {r["status"]: r["c"] for r in q("SELECT status, COUNT(*) c FROM listings GROUP BY status")}
    sweep = (q("SELECT * FROM sweeps ORDER BY id DESC LIMIT 1") or [None])[0]

    new_rows, cut_rows, gone_ct = [], [], 0
    if sweep:
        for e in q("SELECT * FROM events WHERE at=?", sweep["ran_at"]):
            if e["kind"] == "disappeared":
                gone_ct += 1
                continue
            l = (q("SELECT * FROM listings WHERE id=?", e["listing_id"]) or [None])[0]
            if not l or l["status"] != "active":
                continue
            if e["kind"] == "appeared":
                new_rows.append(l)
            elif e["kind"] == "price_change":
                try:
                    old, new = (int(x) for x in e["detail"].split(" -> "))
                except ValueError:
                    continue
                if new < old:
                    cut_rows.append((l, old, new))
        new_rows.sort(key=lambda r: r["net_effective"] or 0)
        cut_rows.sort(key=lambda t: t[2])

    vets = {v["address"]: v for v in q("SELECT * FROM vets")}

    def vet_chip(address):
        v = vets.get(address)
        if not v or not v["grade"]:
            return '<span class="chip vet-none" title="not vetted yet">not vetted</span>'
        g = v["grade"]
        return (f'<span class="chip vet-{esc(g)}" title="{esc(v["summary"])}">'
                f'{esc(VET_LABEL.get(g, g))}</span>')

    def vet_fold(address):
        v = vets.get(address)
        if not v or not v["grade"]:
            return ""
        try:
            flags = json.loads(v["flags"] or "[]")
        except ValueError:
            flags = []
        try:
            links = json.loads(v["dossier"] or "{}").get("manual_links") or {}
        except (ValueError, AttributeError):
            links = {}
        lk = " · ".join(f'<a href="{href(u)}">{esc(k.replace("_", " "))}</a>' for k, u in links.items() if u)
        fl = "".join(f"<li>{esc(f)}</li>" for f in flags)
        return (f'<details class="more"><summary>building check ({esc(VET_LABEL.get(v["grade"], v["grade"]))})</summary>'
                f'<p class="notes">{esc(v["summary"])}</p><ul class="notes">{fl}</ul>'
                f'<p class="notes">{lk} · checked {esc((v["ran_at"] or "")[:10])}</p></details>')

    def decide_cell(lid, addr, unit):
        return (f'<div class="decide" data-id="{esc(lid)}" data-addr="{esc(f"{addr} {unit or ''}".strip())}" hidden>'
                '<button class="pursue">&#9829; pursue</button>'
                '<button class="pass">&#10005; pass</button></div>')

    def listing_row(l, extra=""):
        b = l["beds"]
        beds = "?" if b is None else ("Studio" if b == 0 else f"{b:g}BR")
        net = _money(l["net_effective"] or l["price"] or 0)
        gross = f" ({_money(l['price'])} gross)" if l["price"] and l["net_effective"] != l["price"] else ""
        chips = ""
        if l["score"]:
            chips += f'<span class="chip">score {l["score"]:g}</span>'
        if l["stab"] and l["stab"] != "unknown":
            chips += f'<span class="chip">stab: {esc(l["stab"])}</span>'
        return (f'<tr><td class="num">{net}{gross}</td><td>{beds}</td><td>{esc(l["neighborhood"])}</td>'
                f'<td><a href="{href(l["url"])}">{esc(l["address"])} {esc(l["unit"])}</a> '
                f'{chips}{vet_chip(l["address"])}{vet_fold(l["address"])}</td>'
                f'<td>{extra}</td><td>{decide_cell(l["id"], l["address"], l["unit"])}</td></tr>')

    def table(head, rows, empty):
        body = "".join(rows) or f'<tr><td colspan="{COLS}" class="empty">{esc(empty)}</td></tr>'
        th = "".join(f"<th>{esc(h)}</th>" for h in head)
        return (f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead>'
                f'<tbody>{body}</tbody></table></div>')

    H = ["Net rent", "Size", "Neighborhood", "Listing", "", "Your call"]
    ranked = q("SELECT * FROM listings WHERE status='active' AND source='streeteasy' AND score IS NOT NULL "
               "ORDER BY score DESC, net_effective ASC")
    top_html = table(["Net rent", "Size", "Neighborhood", "Listing", "Terms", "Your call"],
                     [listing_row(l, f'avail {esc(l["available_at"] or "?")}') for l in ranked],
                     "No scored matches yet")
    new_html = table(H, [listing_row(l, f'avail {esc(l["available_at"] or "?")}') for l in new_rows],
                     "No new matches this sweep")
    cut_html = table(["Net rent", "Size", "Neighborhood", "Listing", "Change", "Your call"],
                     [listing_row(l, f'<span class="cut">{_money(o)} &rarr; {_money(n)}</span>')
                      for l, o, n in cut_rows], "No price cuts this sweep")
    others = q("SELECT * FROM listings WHERE status='active' AND source!='streeteasy' "
               "ORDER BY net_effective ASC")
    other_html = table(["Net rent", "Size", "Neighborhood", "Listing", "Source", "Your call"],
                       [listing_row(l, f'<span class="chip">{esc(l["source"])}</span> {esc(l["src_label"])}')
                        for l in others], "Nothing from the other portals right now")

    # --- pipeline ---------------------------------------------------------------
    now = datetime.now(timezone.utc)

    def rows_for(*stages):
        return q(f"SELECT * FROM pipeline WHERE stage IN ({','.join('?' * len(stages))}) "
                 "ORDER BY next_action_at IS NULL, next_action_at, updated_at DESC", *stages)

    def head_(r):
        unit = f' <span class="unit">{esc(r["unit"])}</span>' if r["unit"] else ""
        return (f'<span class="addr">{esc(r["address"])}{unit}</span>'
                f'<span class="hood">{esc(r["neighborhood"])}</span>')

    def chips_(r, extra=()):
        bits = []
        if r["price"]:
            bits.append(f'<span class="chip">{_money(r["price"])}</span>')
        if r["agent"]:
            bits.append(f'<span class="chip">{esc(r["agent"])} · {esc(r["agency"])}</span>')
        return "".join(bits) + vet_chip(r["address"]) + "".join(extra)

    def notes_fold(r, label="notes"):
        if not r["notes"]:
            return ""
        return f'<details class="more"><summary>{label}</summary><p class="notes">{esc(r["notes"])}</p></details>'

    def debrief(r):
        key = f'{r["address"]} {r["unit"] or ""}'.strip().replace(" ", "_").replace("/", "-")
        return ('<details class="fbd" hidden><summary>Log debrief</summary>'
                f'<div class="fb" data-key="{esc(key)}" data-addr="{esc(r["address"])}" '
                f'data-unit="{esc(r["unit"])}">'
                '<div class="fbrow"><button data-v="loved">&#9829; loved it</button>'
                '<button data-v="maybe">~ maybe</button>'
                '<button data-v="no">&#10005; no</button></div>'
                '<textarea rows="2" placeholder="What worked, what didn\'t: light, noise, vibe, anything"></textarea>'
                '<button class="fbsave">Save debrief</button>'
                '<span class="fbstate" role="status"></span></div></details>')

    def age_of(r):
        ref = _dt(r["last_outreach_at"] or r["updated_at"])
        if not ref:
            return "", 0
        hrs = (now - ref).total_seconds() / 3600
        return (f"{int(hrs // 24)}d" if hrs >= 24 else f"{int(hrs)}h"), hrs

    def linkify(text):
        return re.sub(r"(https?://[^\s&<]+)", lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', esc(text))

    def card(r, when, when_cls="", extra_chips=(), tail=""):
        na = f'<p class="na">{linkify(r["next_action"])}</p>' if r["next_action"] else ""
        return (f'<div class="row"><div class="when {when_cls}">{when}</div><div class="body">'
                f'<div class="head">{head_(r)}</div><div class="chips">{chips_(r, extra_chips)}</div>'
                f'{na}{vet_fold(r["address"])}{notes_fold(r)}{tail}</div></div>')

    viewing = rows_for("viewing")
    viewing_html = "".join(card(r, esc(r["next_action_at"] or "tbd")[:16].replace("T", " "), tail=debrief(r))
                           for r in viewing) or '<p class="empty">No viewings booked.</p>'

    fbk = {}
    for f in q("SELECT address, unit, verdict FROM feedback ORDER BY at"):
        fbk[(f["address"], f["unit"] or "")] = f["verdict"]
    viewed = rows_for("viewed")
    viewed.sort(key=lambda r: {"loved": 0, "maybe": 1, "no": 2}.get(fbk.get((r["address"], r["unit"] or "")), 1))
    seen_html = ""
    for r in viewed:
        v = fbk.get((r["address"], r["unit"] or ""))
        label, cls = VERDICT.get(v, ("?", ""))
        seen_html += card(r, label, cls, tail="" if v else debrief(r))
    seen_html = seen_html or '<p class="empty">Nothing viewed yet.</p>'

    owed = q("SELECT * FROM pipeline WHERE todo IS NOT NULL AND stage!='dropped' "
             "ORDER BY next_action_at IS NULL, next_action_at, updated_at DESC")
    KINDS = [("schedule", "Reply to lock a viewing"), ("answer", "Answer them"),
             ("review", "Look at something"), ("decide", "Keep or drop")]
    you_html = ""
    for kind, title in KINDS:
        grp = [r for r in owed if r["todo"] == kind]
        if grp:
            you_html += (f'<div class="day"><h3>{title} <span class="count">{len(grp)}</span></h3>'
                         + "".join(card(r, age_of(r)[0], "muted") for r in grp) + "</div>")
    you_html = you_html or '<p class="empty">Nothing on you.</p>'

    them = [r for r in rows_for("replied", "inquired") if not r["todo"]]
    them_html = ""
    for r in them:
        age, hrs = age_of(r)
        extra = ['<span class="chip">no reply yet</span>'] if r["stage"] == "inquired" else []
        if hrs >= NUDGE_HOURS:
            extra.append('<span class="chip stale">nudge</span>')
        them_html += card(r, age, "muted", extra)
    them_html = them_html or '<p class="empty">Nobody outstanding.</p>'

    prog = rows_for("shortlisted", "discovered", "applying", "applied", "decision")
    prog_html = "".join(card(r, esc(r["stage"]), "muted") for r in prog)
    dropped = rows_for("dropped")
    dropped_html = "".join(f'<div class="row slim"><div class="body"><div class="head">{head_(r)}</div>'
                           f'<p class="notes">{esc(r["notes"])}</p></div></div>' for r in dropped)

    pipe_html = (
        f'<h2>Viewings <span class="count">{len(viewing)}</span></h2>'
        '<p class="hint">Tap <i>building check</i> for the vet read, <i>Log debrief</i> after you walk out.</p>'
        f'{viewing_html}<h2>Seen <span class="count">{len(viewed)}</span></h2>{seen_html}'
        + (f'<h2>In progress <span class="count">{len(prog)}</span></h2>{prog_html}' if prog else "")
        + f'<h2>On you <span class="count">{len(owed)}</span></h2>{you_html}'
        f'<h2>Waiting on them <span class="count">{len(them)}</span></h2>'
        f'<p class="hint"><i>nudge</i> = silent {NUDGE_HOURS}h or more.</p>{them_html}'
        f'<details class="fold"><summary>Dropped ({len(dropped)})</summary>{dropped_html}</details>')

    swept = sweep["ran_at"][:16].replace("T", " ") + " UTC" if sweep else "never"
    gen = now.strftime("%Y-%m-%d %H:%M UTC")
    name = prof["household"]["name"]
    stats = [(counts.get("active", 0), "active listings tracked"),
             (len(ranked), f"scored matches (up to {_money(cap)} net)"),
             (len(new_rows), "new this sweep"), (len(cut_rows), "price cuts this sweep"),
             (gone_ct, "left the market"), (counts.get("excluded", 0), "excluded by your rules")]
    stats_html = "".join(f'<div class="stat"><b>{n}</b><span>{esc(t)}</span></div>' for n, t in stats)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(name)}'s Apartment Hunt</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:wght@500;600&family=Public+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500;600&display=swap">
<style>
{CSS}
</style></head><body>
<div class="wrap">
  <p class="eyebrow">Doorman · live hunt tracker</p>
  <h1>{esc(name)}'s Apartment Hunt</h1>
  <p class="dateline">Last sweep {esc(swept)} · dashboard generated {gen}</p>
  <div class="stats">{stats_html}</div>
  <div class="askbox" hidden>
    <label for="ask">Tell Doorman anything</label>
    <div class="askrow">
      <input id="ask" type="text" placeholder="e.g. vet 1 Example St, or re-check the price cuts">
      <button id="asksend">Send</button>
    </div>
    <p class="askstate" role="status">Handled at the next digest.</p>
  </div>
  <h2>Top matches <span class="count">ranked by score, then net rent</span></h2>
  {top_html}
  <h2>New matches <span class="count">latest sweep</span></h2>
  {new_html}
  <h2>Price cuts <span class="count">latest sweep</span></h2>
  {cut_html}
  {pipe_html}
  <details class="fold"><summary>Other portals ({len(others)})</summary>
  <p class="hint">Sublets and lease takeovers are short-term; check the term.</p>
  {other_html}</details>
  <script>
{JS}
  </script>
  <footer>Criteria: {esc(criteria)}. {counts.get("excluded", 0)} listings excluded by those rules and not shown. Regenerate: <code>python3 scripts/hunt_dashboard.py</code>, then republish.</footer>
</div></body></html>"""


def main():
    prof = doorman_profile.load()
    conn = sqlite3.connect(DB)
    page = build(conn, prof)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(page)
    print(f"wrote {OUT} ({len(page)} bytes)")


if __name__ == "__main__":
    main()
