#!/usr/bin/env python3
"""Build the job-application dashboard artifact.

Reads the local store and emits a single self-contained HTML file. The purpose
is to let someone who has been away act on a working session in a couple of
minutes: what needs them, what is in flight, and what already went out.

The "Needs you" section leads because it is the only part that is blocking.
Every blocked item carries a direct link to the exact page so acting on it is
one click plus one action, not a hunt through tabs.

Standard library only. No network calls; the page is fully offline.
"""

import argparse
import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Importable whether this is run as a script from the repo root or loaded by
# path (which the tests do), where scripts/ would not be on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import brand  # noqa: E402


def root_dir(override=None):
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get("JOB_APPLICATION_DIR", "~/.job-application")).expanduser()


def read_json(path, default):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def load_state(root):
    sessions = []
    sdir = root / "sessions"
    if sdir.exists():
        for f in sorted(sdir.glob("*.json")):
            data = read_json(f, {})
            if data:
                sessions.append(data)

    history = []
    hpath = root / "applications.jsonl"
    if hpath.exists():
        for line in hpath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    history.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    latest = {}
    for e in history:
        key = e.get("applicationId") or f"{e.get('company')}|{e.get('role')}"
        latest[key] = e

    return {
        "profile": read_json(root / "profile.json", {}),
        "sessions": sessions,
        "history": list(latest.values()),
        "roles": read_json(root / "roles.json", {}),
        "meta": read_json(root / "meta.json", {}),
    }


def esc(v):
    return html.escape(str(v if v is not None else ""))


def safe_url(url):
    """URLs come from scraped pages; only http(s) may become a clickable link."""
    u = str(url or "").strip()
    return u if u.startswith(("https://", "http://")) else ""


def link(url, label="Open"):
    url = safe_url(url)
    if not url:
        return '<span class="muted">no link</span>'
    return f'<a class="go" href="{esc(url)}" target="_blank" rel="noopener">{esc(label)} &nearr;</a>'


def render_pending(fields):
    if not fields:
        return '<li class="done">Nothing outstanding &mdash; ready to submit</li>'
    out = []
    for f in fields:
        q = esc(f.get("question", "Unknown field"))
        reason = f.get("reason") or f.get("state") or ""
        why = f' <span class="why">{esc(reason)}</span>' if reason else ""
        out.append(f"<li>{q}{why}</li>")
    return "\n".join(out)


def build(state, generated):
    sessions = state["sessions"]
    history = state["history"]
    profile = state["profile"]
    roles = state.get("roles", {})

    def posting_url(entry):
        """The posting link, from the entry or the durable role index."""
        if entry.get("url"):
            return entry["url"]
        aid = entry.get("applicationId")
        if aid and aid in roles:
            return roles[aid].get("url", "")
        for r in roles.values():
            if r.get("company") == entry.get("company") and r.get("role") == entry.get("role"):
                return r.get("url", "")
        return ""

    blocked = [s for s in sessions if s.get("status") in ("active", "review")]
    blocked.sort(key=lambda s: len(s.get("pendingFields", [])))

    inflight = [s for s in sessions if s.get("status") == "inflight"]
    watch = [s for s in sessions if s.get("status") == "watch"]
    # Roles the user said no to. Kept rather than deleted, because "we decided
    # against this one" is a fact worth being able to look up later, and because
    # without it a dropped role would simply reappear on the next sweep.
    dropped = sorted((s for s in sessions if s.get("status") == "dropped"),
                     key=lambda x: x.get("droppedAt") or "", reverse=True)
    # An application whose latest event is a post-submission outcome is still
    # submitted; it must not vanish from the board when an outcome is logged.
    OUTCOMES = ("response", "interview", "offer", "rejected")
    submitted = [e for e in history if e.get("event") in ("completed",) + OUTCOMES]
    # "reviewed" never confirmed, plus "submitting" with no completion after it —
    # the latter means a session died mid-submit and the platform must be checked.
    reviewed_only = {
        (e.get("company"), e.get("role"), e.get("event") == "submitting")
        for e in history
        if e.get("event") in ("reviewed", "submitting")
    }
    reviewed_only = {
        (c, r, interrupted) for c, r, interrupted in reviewed_only
        if (c, r) not in {(e.get("company"), e.get("role")) for e in submitted}
    }

    name = profile.get("firstName") or "there"

    cards = []
    for s in blocked:
        pending = s.get("pendingFields", [])
        aid = s.get("applicationId") or f"{s.get('company','')}-{s.get('role','')}"
        badge = "ready" if not pending else f"{len(pending)} to do"
        cls = "ready" if not pending else "hold"
        chip = "ready" if not pending else "hold"
        hay = " ".join(str(x) for x in [
            s.get("company"), s.get("role"), s.get("ats"),
            s.get("location") or s.get("realLocation"),
            " ".join(f.get("question", "") for f in pending)] if x)
        cards.append(f"""
        <article class="card {cls}" data-id="{esc(aid)}" data-hay="{esc(hay)}" data-state="{chip}"
                 data-company="{esc(s.get('company',''))}" data-role="{esc(s.get('role',''))}"
                 data-url="{esc(safe_url(posting_url(s)))}">
          <header>
            <div>
              <h3>{esc(s.get('company', 'Unknown'))}</h3>
              <p class="role">{esc(s.get('role', ''))}</p>
            </div>
            <span class="chip {chip}">{esc(badge)}</span>
          </header>
          <ul class="todo">{render_pending(pending)}</ul>
          <p class="stub">&#10003; Marked submitted <button class="undo" data-id="{esc(aid)}">Undo</button></p>
          <div class="act">
            <button class="btn retry" data-id="{esc(aid)}"
                    data-company="{esc(s.get('company',''))}"
                    data-role="{esc(s.get('role',''))}"
                    title="I have done the blocked step; let the agent finish">Retry</button>
            <a class="btn ghost" href="{esc(safe_url(posting_url(s)))}" target="_blank" rel="noopener">Open &amp; finish</a>
            <button class="btn ghost mark" data-id="{esc(aid)}">Mark submitted</button>
            <button class="btn drop" data-id="{esc(aid)}" title="Do not pursue this one">Drop</button>
            <span class="ats">{esc(s.get('ats', ''))}</span>
          </div>
        </article>""")

    def flight_card(entry):
        phase = entry.get("phase") or "Queued"
        detail = entry.get("phaseDetail") or ""
        at = (entry.get("phaseAt") or "")[11:19]
        steps = ["Queued", "Building documents", "Documents ready",
                 "Filling the form", "Submitting", "Verifying"]
        # Match on prefix so an agent's wording does not have to be exact.
        idx = next((i for i, st in enumerate(steps)
                    if phase.lower().startswith(st.lower()[:8])), 0)
        dots = "".join(
            f'<span class="step{" on" if i <= idx else ""}" title="{esc(st)}"></span>'
            for i, st in enumerate(steps))
        return f"""
        <article class="card flight" data-id="{esc(entry.get('applicationId',''))}">
          <header>
            <div>
              <h3>{esc(entry.get('company', ''))}</h3>
              <p class="role">{esc(entry.get('role', ''))}</p>
            </div>
            <span class="chip warm">in flight</span>
          </header>
          <div class="steps">{dots}</div>
          <p class="phase">{esc(phase)}{f' <span class="why">{esc(detail)}</span>' if detail else ''}</p>
          <div class="act">
            <a class="btn ghost" href="{esc(safe_url(posting_url(entry)))}" target="_blank" rel="noopener">Posting</a>
            <button class="btn drop" data-id="{esc(entry.get('applicationId',''))}"
                    title="Stop the agent and drop this application">Stop</button>
            <span class="ats">{esc(at + ' UTC' if at else '')}</span>
          </div>
        </article>"""

    flight_cards = [flight_card(s) for s in sorted(
        inflight, key=lambda x: x.get("phaseAt") or "", reverse=True)]

    outcome_chip = {"completed": ("", "submitted"), "response": ("warm", "response"),
                    "interview": ("warm", "interview"), "offer": ("ready", "offer"),
                    "rejected": ("", "rejected")}
    def proof_cell(entry):
        """Evidence the submission actually landed, or an honest gap."""
        aid = entry.get("applicationId")
        r = roles.get(aid, {}) if aid else {}
        conf = (r.get("confirmation") or entry.get("confirmation") or "").strip()
        if not conf:
            return '<span class="chip hold" title="No confirmation was recorded for this application">unverified</span>'
        url = r.get("confirmedAt")
        if url:
            return (f'<a class="go" href="{esc(safe_url(url))}" target="_blank" rel="noopener" '
                    f'title="{esc(conf)}">confirmed &nearr;</a>')
        return f'<span class="chip ready" title="{esc(conf)}">confirmed</span>'

    def doc_cell(value):
        """A link when a real file went out, an honest note when none did."""
        v = (value or "").strip()
        if v.endswith(".pdf"):
            from urllib.parse import quote
            return (f'<a class="go" href="/doc/{quote(v)}" target="_blank" '
                    f'rel="noopener">PDF &nearr;</a>')
        if not v or v == "-":
            return '<span class="muted">&mdash;</span>'
        # e.g. "not accepted by this form", "pasted as text into Additional Information"
        short = "sent as text" if "text" in v.lower() else "not requested"
        return f'<span class="muted" title="{esc(v)}">{esc(short)}</span>'

    def sub_row(e):
        cls, label = outcome_chip.get(e.get("event"), ("", e.get("event")))
        hay = " ".join(str(x) for x in [e.get('company'), e.get('role'), label] if x)
        return (f"<tr data-hay=\"{esc(hay)}\" data-state=\"{esc(e.get('event') or '')}\">"
                f"<td>{esc(e.get('company'))}</td><td>{esc(e.get('role'))}</td>"
                f"<td><span class='chip {cls}'>{esc(label)}</span></td>"
                f"<td class='when'>{esc((e.get('at') or '')[:10])}</td>"
                f"<td>{link(posting_url(e), 'Posting')}</td>"
                f"<td>{proof_cell(e)}</td>"
                f"<td>{doc_cell(e.get('resumeVariant'))}</td>"
                f"<td>{doc_cell(e.get('coverLetter'))}</td></tr>")

    sub_rows = "\n".join(
        sub_row(e) for e in sorted(submitted, key=lambda x: x.get("at", ""), reverse=True)
    ) or "<tr><td colspan='8' class='muted'>Nothing submitted yet</td></tr>"

    unconfirmed = "".join(
        f"<li>{esc(c)} &mdash; {esc(r)}"
        f"{' <strong>(submission attempted, interrupted before confirmation)</strong>' if interrupted else ''}</li>"
        for c, r, interrupted in sorted(reviewed_only, key=lambda t: (str(t[0]), str(t[1]))) if c
    )
    unconfirmed_block = f"""
      <div class="note">
        <h4>Not confirmed submitted</h4>
        <p>These reached final review or an interrupted submission, but no confirmation was observed. Check on the platform before reapplying.</p>
        <ul>{unconfirmed}</ul>
      </div>""" if unconfirmed else ""

    def discipline_tag(entry):
        d = entry.get("discipline")
        return f'<br><span class="chip-sm">{esc(d)}</span>' if d else ""

    def skills_cell(entry):
        skills = entry.get("coreSkills") or []
        if not skills:
            return '<span class="muted">not captured</span>'
        return "".join(f'<span class="pill">{esc(k)}</span>' for k in skills[:6])

    def years_cell(entry):
        text = entry.get("yearsText")
        if not text or text == "not stated":
            return '<span class="muted">not stated</span>'
        # Anything above the cap is worth seeing at a glance, not buried.
        over = isinstance(entry.get("yearsMin"), (int, float)) and entry["yearsMin"] > 5
        cls = "yrs over" if over else "yrs"
        return f'<span class="{cls}">{esc(text)}</span>'

    def posted_cell(entry):
        raw = entry.get("postedAgo")
        days = entry.get("postedDays")
        if raw is None and days is None:
            return '<span class="muted">unknown</span>'
        cls = "posted fresh" if isinstance(days, int) and days <= 3 else "posted"
        # Show the absolute date when one could be derived. The relative string
        # ("7 minutes ago") was true at sweep time and goes stale immediately,
        # so it is only shown when there is no date to show instead.
        if entry.get("postedDate"):
            return f'<span class="{cls}">{esc(entry["postedDate"])}</span>'
        return f'<span class="posted muted">{esc(raw)}</span>'

    # Newest first. Unknown dates sort last rather than pretending to be fresh,
    # and fit breaks ties within the same day.
    watch.sort(key=lambda s: (
        s.get("postedDays") if isinstance(s.get("postedDays"), int) else 10**6,
        -(s.get("fitScore") or 0)))
    def dropped_row(entry):
        when = (entry.get("droppedAt") or "")[:10]
        stage = entry.get("droppedStage") or ""
        lane = entry.get("droppedFrom") or "unknown"
        reason = entry.get("droppedReason") or ""
        aid = entry.get("applicationId", "")
        hay = " ".join(str(x) for x in [entry.get("company"), entry.get("role"),
                                        lane, stage, reason] if x)
        return (
            f'<tr data-hay="{esc(hay)}" data-state="{esc(lane)}" data-id="{esc(aid)}">'
            f'<td><strong>{esc(entry.get("company", ""))}</strong><br>'
            f'<span class="muted">{esc(entry.get("role", ""))}</span></td>'
            f'<td><span class="chip lane">{esc(lane)}</span></td>'
            f'<td class="note-cell">{esc(stage)}'
            f'{f"<br><span class=\'muted\'>{esc(reason)}</span>" if reason else ""}</td>'
            f'<td class="when">{esc(when)}</td>'
            f'<td>{link(posting_url(entry), "View")}'
            f'<button class="btn ghost undrop" data-id="{esc(aid)}" '
            f'title="Put this back in Watching">Restore</button></td></tr>'
        )

    dropped_rows = "\n".join(dropped_row(d) for d in dropped)
    dropped_lanes = sorted({d.get("droppedFrom") or "unknown" for d in dropped})

    watch_rows = "\n".join(
        f"<tr data-hay=\"{esc(' '.join(str(x) for x in [s.get('company'), s.get('role'), s.get('location') or s.get('realLocation'), ' '.join(s.get('coreSkills') or []), s.get('alignment') or ''] if x))}\" "
        f"data-fresh=\"{'1' if isinstance(s.get('postedDays'), int) and s.get('postedDays') <= 7 else '0'}\" "
        f"data-region=\"{esc(s.get('region') or '')}\" data-disc=\"{esc(s.get('discipline') or '')}\">"
        f"<td><strong>{esc(s.get('company'))}</strong><br><span class='muted'>{esc(s.get('role'))}</span>"
        f"{discipline_tag(s)}</td>"
        f"<td class='when'>{posted_cell(s)}</td>"
        f"<td>{years_cell(s)}</td>"
        f"<td class='loc'>{esc(s.get('location') or s.get('realLocation') or '')}</td>"
        f"<td class='skills'>{skills_cell(s)}</td>"
        f"<td class='note-cell'>{esc(s.get('alignment') or s.get('step',''))}</td>"
        f"<td>{link(posting_url(s), 'View')}"
        f"<button class='btn apply' data-id=\"{esc(s.get('applicationId',''))}\" "
        f"data-company=\"{esc(s.get('company',''))}\">Apply</button>"
        f"<button class='xbtn drop' data-id=\"{esc(s.get('applicationId',''))}\" "
        f"title='Drop this role' aria-label='Drop {esc(s.get('company',''))}'>&#215;</button>"
        f"</td></tr>" for s in watch
    )


    def filter_bar(key, options):
        """Search box, plus a select only when there is something to select."""
        sel = ""
        if options:
            opts = "".join(f'<option value="{esc(v)}">{esc(label)}</option>'
                           for v, label in options)
            sel = (f'<select data-filter="{esc(key)}">'
                   f'<option value="">All</option>{opts}</select>')
        return (f'<div class="filters" data-scope="{esc(key)}">'
                f'<input type="search" data-search="{esc(key)}" '
                f'placeholder="Search company, role, skill, location...">'
                f'{sel}<span class="fcount" data-count="{esc(key)}"></span></div>')

    sub_states = sorted({e.get("event") for e in submitted if e.get("event")})
    regions = sorted({s.get("region") for s in watch if s.get("region")})
    disciplines = sorted({s.get("discipline") for s in watch if s.get("discipline")})
    watch_filters = ([("fresh", "Posted in last 7 days")]
                     + [(f"region:{r}", r) for r in regions]
                     + [(f"disc:{d}", d) for d in disciplines])

    total_todo = sum(len(s.get("pendingFields", [])) for s in blocked)

    tabs = [
        ("act", "Needs you", len(blocked)),
        ("done", "Submitted", len(submitted)),
        ("watch", "Watching", len(watch)),
        ("flight", "In flight", len(inflight)),
        # Set apart from the four working lanes: nothing here is waiting on
        # anyone, it is a record of decisions already made.
        ("dropped", "Dropped", len(dropped)),
    ]

    stopped = str((state.get("meta") or {}).get("blocked") or "").strip()
    blocked_banner = (
        f'<div class="sysbanner" role="alert"><strong>Automation is stopped.</strong> '
        f'{esc(stopped)}</div>'
    ) if stopped else ""

    tabnav = "".join(
        f'<button class="tab{" on" if i == 0 else ""}{" aside" if k == "dropped" else ""}" data-tab="{k}" role="tab" '
        f'aria-selected="{"true" if i == 0 else "false"}" aria-controls="p-{k}">'
        f'{esc(label)}<span class="n">{n}</span></button>'
        for i, (k, label, n) in enumerate(tabs)
    )

    return f"""<meta charset="utf-8">
<title>Jobby is a free agent!</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#f5f5f7" media="(prefers-color-scheme:light)">
<meta name="theme-color" content="#000000" media="(prefers-color-scheme:dark)">
{brand.favicon_links()}
<!-- Served from deploy/public/ when hosted; harmlessly absent for a local file. -->
<link rel="manifest" href="/site.webmanifest">
<script>
  // Applied before first paint. Without this the page renders in the OS theme
  // for a frame, then snaps to the saved one.
  (function () {{
    try {{
      var t = localStorage.getItem('jobby.theme');
      if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
    }} catch (e) {{}}
  }})();
</script>
<style>
  :root {{
    color-scheme:light dark;
    --bg:#f5f5f7; --card:#fff; --sunk:#f0f0f2; --seg:#e8e8ed;
    --ink:#1d1d1f; --ink-2:#6e6e73; --ink-3:#8e8e93;
    --hair:rgba(0,0,0,.07);
    --blue:#0071e3; --orange:#b45309; --green:#047857; --purple:#6d28d9; --red:#b3261e;
    --t-o:rgba(234,88,12,.08); --t-g:rgba(5,150,105,.08); --t-p:rgba(109,40,217,.07);
    --sh:0 1px 3px rgba(0,0,0,.05), 0 6px 20px rgba(0,0,0,.05);
    /* Logo inverts with the theme. An ink circle on a near-black page has
       almost no edge, so dark mode gets the paper circle instead. */
    --brand-bg:#2B2D2C; --brand-fg:#FFFFFF;
  }}
  @media (prefers-color-scheme:dark) {{
    :root:not([data-theme="light"]) {{
      --bg:#000; --card:#1c1c1e; --sunk:#2c2c2e; --seg:#2c2c2e;
      --ink:#f5f5f7; --ink-2:#aeaeb2; --ink-3:#8e8e93;
      --hair:rgba(255,255,255,.1);
      --blue:#0a84ff; --orange:#ff9f0a; --green:#30d158; --purple:#bf5af2; --red:#ff6961;
      --t-o:rgba(255,159,10,.13); --t-g:rgba(48,209,88,.12); --t-p:rgba(191,90,242,.12);
      --sh:0 1px 3px rgba(0,0,0,.5);
    --brand-bg:#F8F8F8; --brand-fg:#2B2D2C;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg:#000; --card:#1c1c1e; --sunk:#2c2c2e; --seg:#2c2c2e;
    --ink:#f5f5f7; --ink-2:#aeaeb2; --ink-3:#8e8e93;
    --hair:rgba(255,255,255,.1);
    --blue:#0a84ff; --orange:#ff9f0a; --green:#30d158; --purple:#bf5af2; --red:#ff6961;
    --t-o:rgba(255,159,10,.13); --t-g:rgba(48,209,88,.12); --t-p:rgba(191,90,242,.12);
    --sh:0 1px 3px rgba(0,0,0,.5);
    --brand-bg:#F8F8F8; --brand-fg:#2B2D2C;
  }}
  *,*::before,*::after {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",system-ui,sans-serif;
    font-size:17px; line-height:1.47; letter-spacing:-.022em;
    -webkit-font-smoothing:antialiased; }}
  .wrap {{ max-width:860px; margin:0 auto; padding:clamp(2rem,6vw,4rem) 1.25rem 6rem; }}
  /* Tables get the whole screen; prose-width containers keep the rest readable. */
  .panel .sheet {{ max-width:none; width:min(1800px, calc(100vw - 2.5rem));
    margin-left:calc(50% - min(900px, calc(50vw - 1.25rem)));
    margin-right:calc(50% - min(900px, calc(50vw - 1.25rem))); }}
  .panel .filters {{ width:min(1800px, calc(100vw - 2.5rem));
    margin-left:calc(50% - min(900px, calc(50vw - 1.25rem)));
    margin-right:calc(50% - min(900px, calc(50vw - 1.25rem))); }}

  /* Brand. The lockup's circle and mark are painted from --brand-*, which the
     theme blocks invert; the wordmark rides currentColor. */
  .jb-defs {{ position:absolute; width:0; height:0; overflow:hidden; }}
  .appbar {{ max-width:860px; margin:0 auto; padding:1.15rem 1.25rem 0;
    display:flex; align-items:center; justify-content:space-between; gap:1rem; }}
  .brand {{ display:inline-flex; align-items:center; color:var(--ink);
    text-decoration:none; border-radius:8px; }}
  .brand:focus-visible {{ outline:3px solid var(--blue); outline-offset:4px; }}
  .lockup {{ height:1.6rem; width:auto; display:block; }}
  .heromark {{ width:72px; height:72px; display:block; margin:0 auto 1.15rem; }}

  .themeseg {{ display:inline-flex; background:var(--seg); border-radius:100px;
    padding:3px; gap:2px; flex:none; }}
  .themebtn {{ border:none; background:none; color:var(--ink-3); font:inherit;
    cursor:pointer; width:2.1rem; height:1.75rem; padding:0; border-radius:100px;
    display:inline-flex; align-items:center; justify-content:center;
    transition:background .22s cubic-bezier(.4,0,.2,1), color .22s; }}
  .themebtn svg {{ width:1rem; height:1rem; display:block; }}
  .themebtn:hover {{ color:var(--ink-2); }}
  .themebtn.on {{ background:var(--card); color:var(--ink);
    box-shadow:0 1px 3px rgba(0,0,0,.12); }}
  .themebtn:focus-visible {{ outline:3px solid var(--blue); outline-offset:2px; }}
  @media (max-width:600px) {{ .lockup {{ height:1.4rem; }}
    .heromark {{ width:60px; height:60px; }} }}

  .top {{ text-align:center; margin-bottom:2rem; }}
  h1 {{ font-size:clamp(2rem,5.5vw,2.75rem); font-weight:700; letter-spacing:-.035em;
    line-height:1.07; margin:0 0 .5rem; }}
  .lede {{ font-size:1.1rem; color:var(--ink-2); margin:0; letter-spacing:-.018em; }}
  .stamp {{ font-size:.8rem; color:var(--ink-3); margin:.7rem 0 0;
    font-variant-numeric:tabular-nums; }}

  .segwrap {{ display:flex; justify-content:center; margin-bottom:2.25rem;
    position:sticky; top:.75rem; z-index:5; }}
  .seg {{ display:inline-flex; background:var(--seg); border-radius:100px; padding:3px;
    gap:2px; max-width:100%; overflow-x:auto; scrollbar-width:none; }}
  .seg::-webkit-scrollbar {{ display:none; }}
  .tab {{ border:none; background:none; color:var(--ink-2); font:inherit; font-size:.93rem;
    font-weight:510; letter-spacing:-.015em; padding:.5rem 1.05rem; border-radius:100px;
    cursor:pointer; white-space:nowrap; display:inline-flex; align-items:center; gap:.4rem;
    transition:background .22s cubic-bezier(.4,0,.2,1), color .22s; }}
  .tab .n {{ font-size:.78rem; color:var(--ink-3); font-variant-numeric:tabular-nums;
    background:var(--card); padding:.05rem .38rem; border-radius:100px; min-width:1.35em;
    text-align:center; }}
  .tab.on {{ background:var(--card); color:var(--ink); box-shadow:0 1px 3px rgba(0,0,0,.12); }}
  .tab.on .n {{ background:var(--sunk); color:var(--ink-2); }}
  .tab:focus-visible {{ outline:3px solid var(--blue); outline-offset:2px; }}

  .panel {{ display:none; animation:in .3s cubic-bezier(.4,0,.2,1); }}
  .panel.on {{ display:block; }}
  @keyframes in {{ from {{ opacity:0; transform:translateY(6px); }} to {{ opacity:1; transform:none; }} }}
  .hint {{ text-align:center; color:var(--ink-2); font-size:.95rem; margin:0 0 1.75rem;
    letter-spacing:-.015em; }}

  .grid {{ display:grid; gap:.9rem; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); }}
  .card {{ background:var(--card); border-radius:20px; padding:1.35rem 1.45rem 1.15rem;
    box-shadow:var(--sh); display:flex; flex-direction:column; gap:.95rem; }}
  .card.hold {{ background:linear-gradient(var(--t-o),var(--t-o)),var(--card); }}
  .card.ready {{ background:linear-gradient(var(--t-g),var(--t-g)),var(--card); }}
  .card.ref {{ background:linear-gradient(var(--t-p),var(--t-p)),var(--card); }}
  .card header {{ display:flex; justify-content:space-between; gap:.85rem; align-items:flex-start; }}
  .card h3 {{ margin:0; font-size:1.1rem; font-weight:620; letter-spacing:-.022em; line-height:1.25; }}
  .role {{ margin:.2rem 0 0; color:var(--ink-2); font-size:.91rem; letter-spacing:-.014em; }}
  .chip {{ font-size:.73rem; font-weight:590; padding:.26rem .6rem; border-radius:100px;
    white-space:nowrap; font-variant-numeric:tabular-nums; }}
  .chip.hold {{ color:var(--orange); background:var(--t-o); }}
  .chip.ready {{ color:var(--green); background:var(--t-g); }}
  .chip.warm {{ color:var(--purple); background:var(--t-p); }}

  ul.todo {{ margin:0; padding:0; list-style:none; display:flex; flex-direction:column; gap:.45rem; }}
  ul.todo li {{ font-size:.92rem; line-height:1.38; padding-left:1.25rem; position:relative;
    letter-spacing:-.014em; }}
  ul.todo li::before {{ content:""; position:absolute; left:.3rem; top:.52em; width:6px;
    height:6px; border-radius:50%; background:var(--orange); }}
  ul.todo li.done {{ color:var(--green); font-weight:550; }}
  ul.todo li.done::before {{ content:"\\2713"; background:none; width:auto; height:auto;
    left:0; top:0; font-weight:700; }}
  .why {{ color:var(--ink-2); }}
  .why::before {{ content:" \\2014 "; }}

  .act {{ display:flex; gap:.55rem; flex-wrap:wrap; align-items:center; margin-top:auto;
    padding-top:.95rem; border-top:1px solid var(--hair); }}
  .btn {{ display:inline-block; background:var(--blue); color:#fff; text-decoration:none;
    border:none; font:inherit; font-size:.9rem; font-weight:520; letter-spacing:-.014em;
    padding:.52rem 1.15rem; border-radius:100px; cursor:pointer;
    transition:opacity .2s, transform .12s; }}
  .btn:hover {{ opacity:.86; }}
  .btn:active {{ transform:scale(.97); }}
  .btn.ghost {{ background:var(--sunk); color:var(--ink); }}
  .btn[data-done] {{ background:var(--green); }}
  .btn:focus-visible {{ outline:3px solid var(--blue); outline-offset:3px; }}
  .ats {{ color:var(--ink-3); font-size:.78rem; margin-left:auto; }}
  .card.marked {{ opacity:.55; }}
  .card.marked .chip {{ background:var(--t-g); color:var(--green); }}
  .card.marked ul.todo {{ display:none; }}
  .stub {{ display:none; align-items:center; gap:.5rem; font-size:.9rem; color:var(--green);
    font-weight:550; }}
  .card.marked .stub {{ display:flex; }}
  .card.marked .mark {{ display:none; }}
  .undo {{ background:none; border:none; color:var(--ink-3); font:inherit; font-size:.82rem;
    text-decoration:underline; cursor:pointer; padding:0; }}
  .undo:hover {{ color:var(--ink); }}

  .contact {{ margin:0; font-size:.93rem; line-height:1.4; letter-spacing:-.015em; }}
  .contact strong {{ font-weight:600; }}
  .contact span {{ display:block; color:var(--ink-2); font-size:.86rem; margin-top:.1rem; }}
  .msg pre {{ background:var(--bg); border-radius:12px; padding:.85rem .95rem;
    font-family:inherit; font-size:.86rem; line-height:1.5; white-space:pre-wrap; margin:0;
    max-height:140px; overflow:auto; color:var(--ink-2); letter-spacing:-.012em; }}

  .sheet {{ background:var(--card); border-radius:20px; box-shadow:var(--sh); overflow:hidden; }}
  .tablewrap {{ overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; font-size:.93rem; letter-spacing:-.015em;
    table-layout:fixed; }}
  table td {{ overflow-wrap:anywhere; word-break:normal; }}
  th {{ text-align:left; font-size:.78rem; color:var(--ink-3); font-weight:510;
    padding:.9rem 1.4rem; border-bottom:1px solid var(--hair); white-space:nowrap; }}
  td {{ padding:.88rem 1.4rem; border-bottom:1px solid var(--hair); vertical-align:top; }}
  .watch th, .watch td {{ padding:.8rem .8rem; }}
  .watch th:first-child, .watch td:first-child {{ padding-left:1.2rem; }}
  .watch th:last-child, .watch td:last-child {{ padding-right:1.2rem; }}
  tr:last-child td {{ border-bottom:none; }}
  td.when {{ color:var(--ink-3); font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .watch td.when {{ white-space:normal; }}
  a.go {{ color:var(--blue); text-decoration:none; font-weight:510; }}
  td.note-cell {{ color:var(--ink-2); font-size:.86rem; line-height:1.42; }}
  td.loc {{ color:var(--ink-2); font-size:.85rem; }}
  th {{ white-space:normal; }}
  /* Long skill phrases must wrap inside the pill rather than push the column
     wider than its share and reintroduce sideways scroll. */
  .pill {{ display:inline-block; font-size:.73rem; color:var(--ink-2); background:var(--sunk);
    border-radius:10px; padding:.16rem .5rem; margin:0 .25rem .25rem 0;
    max-width:100%; overflow-wrap:anywhere; line-height:1.35; }}
  .posted {{ font-size:.82rem; font-variant-numeric:tabular-nums; }}
  .posted.fresh {{ color:var(--green); font-weight:590; }}
  .ago {{ display:block; font-size:.72rem; color:var(--ink-3); }}
  .scanbar {{ margin:1.1rem 0 0; display:flex; gap:.6rem; align-items:center;
    justify-content:center; flex-wrap:wrap; }}
  .btn.scan {{ background:var(--purple); }}
  .card.flight {{ background:linear-gradient(var(--t-p),var(--t-p)),var(--card); }}
  .steps {{ display:flex; gap:.3rem; align-items:center; }}
  .step {{ width:100%; height:4px; border-radius:100px; background:var(--sunk); }}
  .step.on {{ background:var(--purple); }}
  .phase {{ margin:0; font-size:.92rem; font-weight:550; letter-spacing:-.014em; }}
  .btn.apply {{ background:var(--green); font-size:.8rem; padding:.35rem .8rem;
    margin-top:.4rem; display:block; }}
  .btn.apply[disabled] {{ opacity:.5; cursor:default; }}
  .applymsg {{ display:block; font-size:.74rem; color:var(--ink-3); margin-top:.3rem;
    max-width:16ch; line-height:1.3; }}
  .btn.scan[disabled] {{ opacity:.55; cursor:default; }}
  .scanmsg {{ font-size:.85rem; color:var(--ink-2); }}
  .scanmsg.err {{ color:var(--orange); }}
  .scanmsg.ok {{ color:var(--green); }}
  .filters {{ display:flex; gap:.5rem; flex-wrap:wrap; align-items:center; margin:0 0 1.25rem; }}
  .filters input[type=search] {{ flex:1 1 220px; min-width:180px; font:inherit; font-size:.92rem;
    padding:.5rem .9rem; border-radius:100px; border:1px solid var(--hair); background:var(--card);
    color:var(--ink); letter-spacing:-.014em; }}
  .filters input[type=search]:focus-visible {{ outline:2px solid var(--blue); outline-offset:1px; }}
  .filters select {{ font:inherit; font-size:.88rem; padding:.45rem .7rem; border-radius:100px;
    border:1px solid var(--hair); background:var(--card); color:var(--ink); }}
  .fcount {{ font-size:.82rem; color:var(--ink-3); margin-left:auto; font-variant-numeric:tabular-nums; }}
  .yrs {{ font-size:.82rem; font-weight:550; font-variant-numeric:tabular-nums; }}
  .yrs.over {{ color:var(--orange); }}
  .chip-sm {{ display:inline-block; font-size:.68rem; font-weight:590; color:var(--ink-3);
    border:1px solid var(--hair); border-radius:100px; padding:.05rem .4rem; margin-top:.25rem; }}
  a.go:hover {{ text-decoration:underline; }}

  .note {{ background:linear-gradient(var(--t-o),var(--t-o)),var(--card); border-radius:20px;
    padding:1.3rem 1.45rem; box-shadow:var(--sh); margin-top:1.5rem; }}
  .note h4 {{ margin:0 0 .3rem; font-size:1rem; font-weight:620; letter-spacing:-.02em; }}
  .note p {{ margin:0 0 .7rem; font-size:.9rem; color:var(--ink-2); }}
  .note ul {{ margin:0; padding:0; list-style:none; display:flex; flex-direction:column; gap:.3rem;
    font-size:.9rem; }}
  .empty {{ text-align:center; color:var(--ink-2); font-size:1.05rem; padding:3.5rem 1.5rem;
    background:var(--card); border-radius:20px; box-shadow:var(--sh); letter-spacing:-.018em; }}
  .muted {{ color:var(--ink-3); }}
  @media (prefers-reduced-motion:reduce) {{ *,*::before,*::after {{
    transition:none !important; animation:none !important; }} }}
  @media (max-width:600px) {{ .grid {{ grid-template-columns:1fr; }}
    th,td {{ padding-left:1.1rem; padding-right:1.1rem; }}
    .watch th:nth-child(4), .watch td:nth-child(4),
    .watch th:nth-child(5), .watch td:nth-child(5) {{ display:none; }}
    .watch th, .watch td {{ padding:.65rem .5rem; font-size:.86rem; }} }}
  /* Dropped sits apart from the four working lanes: nothing in it is waiting
     on anyone, so it should not read as a queue. */
  .tab.aside {{ margin-left:auto; opacity:.72; }}
  .tab.aside.on {{ opacity:1; }}
  .xbtn {{ appearance:none; border:1px solid var(--hair); background:transparent;
    color:var(--ink-2); border-radius:6px; cursor:pointer; line-height:1;
    font-size:1rem; padding:.15rem .4rem; margin-left:.35rem; }}
  .xbtn:hover {{ color:var(--red); border-color:var(--red); }}
  .btn.drop {{ color:var(--red); }}
  .btn.drop:hover {{ background:var(--red); color:#fff; border-color:var(--red); }}
  .chip.lane {{ background:var(--sunk); color:var(--ink-2); font-weight:520; }}
  tr.dropping {{ opacity:.4; }}
  .sysbanner {{ background:#8c1d18; color:#fff; padding:.7rem 1rem; font-size:.9rem;
    line-height:1.45; text-align:center; }}
  .sysbanner strong {{ font-weight:650; }}
</style>

{brand.defs()}

{blocked_banner}

<header class="appbar">
  <div class="brand">{brand.lockup()}</div>
  <div class="themeseg" role="radiogroup" aria-label="Colour theme">
      <button class="themebtn" data-theme-set="auto" role="radio" aria-checked="false" title="Match system" aria-label="Match system"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8.6"/><path d="M12 3.4a8.6 8.6 0 0 1 0 17.2Z" fill="currentColor" stroke="none"/></svg></button>
      <button class="themebtn" data-theme-set="light" role="radio" aria-checked="false" title="Light" aria-label="Light"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.6v2M12 19.4v2M4.4 12h-2M21.6 12h-2M6.3 6.3 4.9 4.9M19.1 19.1l-1.4-1.4M17.7 6.3l1.4-1.4M4.9 19.1l1.4-1.4"/></svg></button>
      <button class="themebtn" data-theme-set="dark" role="radio" aria-checked="false" title="Dark" aria-label="Dark"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M20.5 14.6A8.6 8.6 0 0 1 9.4 3.5a8.6 8.6 0 1 0 11.1 11.1Z"/></svg></button>
  </div>
</header>

<div class="wrap">
  <div class="top">
    {brand.icon(cls="heromark", label="")}
    <h1>Jobby is a free agent!</h1>
    <p class="lede">{f"{len(blocked)} waiting on you" if blocked else "Nothing blocked"}{f" &middot; {len(inflight)} in flight" if inflight else ""}</p>
    <p class="stamp">{esc(name)} &middot; {esc(generated)}</p>
    <div class="scanbar">
      <button class="btn scan" id="scanbtn">Scan for new jobs</button>
      <span class="scanmsg" id="scanmsg"></span>
    </div>
  </div>

  <div class="segwrap"><div class="seg" role="tablist">{tabnav}</div></div>

  <div class="panel on" id="p-act" role="tabpanel">
    <p class="hint">Each opens straight to where it is blocked.</p>
    {filter_bar("act", [("ready", "Ready to submit"), ("hold", "Still blocked")])}
    {f'<div class="grid">{"".join(cards)}</div>' if cards else '<p class="empty">Nothing blocked right now.</p>'}
    {unconfirmed_block}
  </div>

  <div class="panel" id="p-flight" role="tabpanel">
    <p class="hint">Applications being worked on right now. Updates as each stage completes.</p>
    {filter_bar("flight", [])}
    {f'<div class="grid">{"".join(flight_cards)}</div>' if flight_cards else '<p class="empty">Nothing in flight. Press Apply on a role in Watching.</p>'}
  </div>

  <div class="panel" id="p-done" role="tabpanel">
    <p class="hint">Already out. Check here before applying again.</p>
    {filter_bar("done", [(o, o.title()) for o in sub_states])}
    <div class="sheet"><div class="tablewrap"><table>
      <thead><tr><th>Company</th><th>Role</th><th>Status</th><th>When</th><th>Posting</th><th>Proof</th><th>Resume</th><th>Cover letter</th></tr></thead>
      <tbody>{sub_rows}</tbody></table></div></div>
  </div>

  <div class="panel watch" id="p-watch" role="tabpanel">
    <p class="hint">Newest first. Search or filter to narrow.</p>
    {filter_bar("watch", watch_filters)}
    <div class="sheet"><div class="tablewrap"><table>
      <colgroup>
        <col style="width:17%"><col style="width:7%"><col style="width:8%">
        <col style="width:12%"><col style="width:19%"><col style="width:31%">
        <col style="width:6%">
      </colgroup>
      <thead><tr><th>Role</th><th>Posted</th><th>Experience</th><th>Location</th><th>Core skills</th><th>How you line up</th><th></th></tr></thead>
      <tbody>{watch_rows}</tbody></table></div></div>
  </div>

  <div class="panel watch" id="p-dropped" role="tabpanel">
    <p class="hint">Roles you decided against. Kept so they are not re-found and offered again.</p>
    {filter_bar("dropped", [(l, l) for l in dropped_lanes])}
    <div class="sheet"><div class="tablewrap"><table>
      <colgroup>
        <col style="width:26%"><col style="width:12%"><col style="width:38%">
        <col style="width:11%"><col style="width:13%">
      </colgroup>
      <thead><tr><th>Role</th><th>Dropped from</th><th>Where it had got to</th><th>When</th><th></th></tr></thead>
      <tbody>{dropped_rows}</tbody></table></div></div>
    {'' if dropped else '<p class="empty">Nothing dropped yet.</p>'}
  </div>
</div>

<script>
  // Theme: auto, light or dark. Only an explicit choice is stored, so auto
  // keeps following the OS if it flips later in the day.
  var themeBtns = document.querySelectorAll('.themebtn');
  var chrome = document.querySelectorAll('meta[name="theme-color"]');
  function paintTheme(mode) {{
    themeBtns.forEach(function (b) {{
      var on = b.dataset.themeSet === mode;
      b.classList.toggle('on', on);
      b.setAttribute('aria-checked', on ? 'true' : 'false');
    }});
    // The two metas are media-scoped for the no-JS case; forcing a theme means
    // pinning one of them on and the other off.
    if (chrome.length === 2) {{
      chrome[0].media = mode === 'auto' ? '(prefers-color-scheme:light)'
        : (mode === 'light' ? 'all' : 'not all');
      chrome[1].media = mode === 'auto' ? '(prefers-color-scheme:dark)'
        : (mode === 'dark' ? 'all' : 'not all');
    }}
  }}
  function setTheme(mode) {{
    try {{
      if (mode === 'auto') localStorage.removeItem('jobby.theme');
      else localStorage.setItem('jobby.theme', mode);
    }} catch (e) {{}}
    if (mode === 'auto') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = mode;
    paintTheme(mode);
  }}
  themeBtns.forEach(function (b) {{
    b.addEventListener('click', function () {{ setTheme(b.dataset.themeSet); }});
  }});
  paintTheme(document.documentElement.dataset.theme || 'auto');

  var tabs = document.querySelectorAll('.tab');
  tabs.forEach(function (t) {{
    t.addEventListener('click', function () {{
      tabs.forEach(function (o) {{ o.classList.remove('on'); o.setAttribute('aria-selected','false'); }});
      document.querySelectorAll('.panel').forEach(function (p) {{ p.classList.remove('on'); }});
      t.classList.add('on'); t.setAttribute('aria-selected','true');
      var p = document.getElementById('p-' + t.dataset.tab);
      if (p) p.classList.add('on');
    }});
  }});
  // "Mark submitted" moves a card out of Needs you and into Submitted. The
  // board cannot write to the store on the agent's machine, so the change is
  // queued and a local poller applies it. The card is moved optimistically so
  // the tab reflects reality immediately, and the queue makes it durable.
  function tabCount(tab, delta) {{
    var el = document.querySelector('.tab[data-tab="' + tab + '"] .n');
    if (!el) return;
    var n = parseInt(el.textContent, 10);
    if (!isNaN(n)) el.textContent = Math.max(0, n + delta);
  }}
  function moveToSubmitted(card) {{
    var company = card.getAttribute('data-company') || '';
    var role = card.getAttribute('data-role') || '';
    var url = card.getAttribute('data-url') || '';
    var tbody = document.querySelector('#p-done tbody');
    if (tbody) {{
      var empty = tbody.querySelector('td[colspan]');
      if (empty) empty.parentNode.remove();
      var tr = document.createElement('tr');
      tr.setAttribute('data-hay', (company + ' ' + role + ' submitted').toLowerCase());
      tr.setAttribute('data-state', 'completed');
      var today = new Date().toISOString().slice(0, 10);
      tr.innerHTML = '<td></td><td></td>'
        + '<td><span class="chip">submitted</span></td>'
        + '<td class="when">' + today + '</td>'
        + '<td>' + (url ? '<a class="go" href="' + url + '" target="_blank" rel="noopener">Posting &nearr;</a>' : '<span class="muted">no link</span>') + '</td>'
        + '<td><span class="muted">&mdash;</span></td><td><span class="muted">&mdash;</span></td>';
      tr.children[0].textContent = company;
      tr.children[1].textContent = role;
      tbody.insertBefore(tr, tbody.firstChild);
    }}
    card.remove();
    tabCount('act', -1);
    tabCount('done', 1);
  }}
  var MARK_KEY = 'jobby.marked';
  function readMarks() {{
    try {{ return JSON.parse(localStorage.getItem(MARK_KEY) || '[]'); }} catch (e) {{ return []; }}
  }}
  function rememberMark(id) {{
    var m = readMarks();
    if (m.indexOf(id) === -1) m.push(id);
    try {{ localStorage.setItem(MARK_KEY, JSON.stringify(m)); }} catch (e) {{}}
  }}
  // On load, re-apply marks the server has not caught up with yet, and forget
  // the ones it has, so the list cannot grow without bound.
  (function replayMarks() {{
    var marks = readMarks();
    if (!marks.length) return;
    var still = [];
    marks.forEach(function (id) {{
      var card = document.querySelector('.card[data-id="' + id.replace(/"/g, '') + '"]');
      if (card) {{ moveToSubmitted(card); still.push(id); }}
    }});
    try {{ localStorage.setItem(MARK_KEY, JSON.stringify(still)); }} catch (e) {{}}
  }})();

  document.querySelectorAll('.mark').forEach(function (b) {{
    b.addEventListener('click', function () {{
      var card = b.closest('.card');
      if (!card) return;
      b.disabled = true;
      b.textContent = 'Saving...';
      fetch('/api/submitted', {{
        method: 'POST',
        headers: {{ 'content-type': 'application/json' }},
        body: JSON.stringify({{
          applicationId: card.dataset.id,
          company: card.dataset.company,
          role: card.dataset.role,
        }}),
      }})
        .then(function (r) {{ return r.json().then(function (x) {{ return {{ s: r.status, b: x }}; }}); }})
        .then(function (res) {{
          if (res.b && res.b.ok) {{ rememberMark(card.dataset.id); moveToSubmitted(card); }}
          else {{
            b.disabled = false;
            b.textContent = 'Mark submitted';
            var msg = (res.b && res.b.error) === 'queue not configured'
              ? 'Queue not set up on this deployment' : 'Could not save';
            b.insertAdjacentHTML('afterend', '<span class="scanmsg err"> ' + msg + '</span>');
          }}
        }})
        .catch(function () {{
          b.disabled = false; b.textContent = 'Mark submitted';
          b.insertAdjacentHTML('afterend', '<span class="scanmsg err"> Could not reach the board</span>');
        }});
    }});
  }});

  // Scan button. The board cannot start anything on the machine running the
  // agent, so this only queues a request; a local poller picks it up and runs
  // the actual scan. The message says so plainly rather than implying it ran.
  var scanBtn = document.getElementById('scanbtn');
  var scanMsg = document.getElementById('scanmsg');
  function say(text, cls) {{ scanMsg.textContent = text; scanMsg.className = 'scanmsg ' + (cls || ''); }}
  if (scanBtn) {{
    scanBtn.addEventListener('click', function () {{
      scanBtn.disabled = true;
      say('Queueing...');
      fetch('/api/scan', {{ method: 'POST' }})
        .then(function (r) {{ return r.json().then(function (b) {{ return {{ s: r.status, b: b }}; }}); }})
        .then(function (res) {{
          if (res.b && res.b.ok) {{
            say('Queued. The agent picks this up on its next check, usually within a few minutes.', 'ok');
          }} else if (res.s === 503) {{
            say('Scan queue is not set up yet on this deployment.', 'err');
            scanBtn.disabled = false;
          }} else {{
            say((res.b && res.b.error) || 'Could not queue the scan.', 'err');
            scanBtn.disabled = false;
          }}
        }})
        .catch(function () {{ say('Could not reach the scan queue.', 'err'); scanBtn.disabled = false; }});
    }});
  }}

  // Apply clicks are remembered locally, because the watcher takes up to a
  // minute to mark the role in flight. Without this a reload in that window
  // puts the row back under Watching, which reads as the click failing.
  var APPLY_KEY = 'jobby.applying';
  function readApplying() {{
    try {{ return JSON.parse(localStorage.getItem(APPLY_KEY) || '[]'); }} catch (e) {{ return []; }}
  }}
  function writeApplying(v) {{
    try {{ localStorage.setItem(APPLY_KEY, JSON.stringify(v)); }} catch (e) {{}}
  }}
  function placeholderCard(company, role) {{
    var grid = document.querySelector('#p-flight .grid');
    if (!grid) {{
      var empty = document.querySelector('#p-flight .empty');
      if (empty) {{
        grid = document.createElement('div');
        grid.className = 'grid';
        empty.parentNode.replaceChild(grid, empty);
      }} else {{ return; }}
    }}
    var art = document.createElement('article');
    art.className = 'card flight';
    art.innerHTML = '<header><div><h3></h3><p class="role"></p></div>'
      + '<span class="chip warm">in flight</span></header>'
      + '<div class="steps"><span class="step on"></span><span class="step"></span>'
      + '<span class="step"></span><span class="step"></span><span class="step"></span>'
      + '<span class="step"></span></div>'
      + '<p class="phase">Queued<span class="why">waiting for the runner to pick it up</span></p>';
    art.querySelector('h3').textContent = company;
    art.querySelector('.role').textContent = role;
    grid.insertBefore(art, grid.firstChild);
  }}
  function leaveWatching(row, company, role) {{
    row.remove();
    tabCount('watch', -1);
    tabCount('flight', 1);
    placeholderCard(company, role);
  }}
  // On load, restore clicks the store has not caught up with, and forget the
  // ones it has (the row is gone from Watching, so the server agrees).
  //
  // It also re-sends the request. Restoring only the appearance was not enough:
  // a queue wipe once destroyed fourteen queued Apply requests, and because the
  // board still drew them as in flight, nothing looked wrong and they were
  // never run. Re-sending is safe because /api/apply dedupes by application id.
  (function replayApplying() {{
    var ids = readApplying();
    if (!ids.length) return;
    var still = [];
    ids.forEach(function (id) {{
      var btn = document.querySelector('.btn.apply[data-id="' + id.replace(/"/g, '') + '"]');
      if (!btn) return;                      // server already moved it
      var row = btn.closest('tr');
      if (!row) return;
      still.push(id);
      leaveWatching(row, btn.dataset.company || '', (row.querySelector('.muted') || {{}}).textContent || '');
      fetch('/api/apply', {{
        method: 'POST',
        headers: {{ 'content-type': 'application/json' }},
        body: JSON.stringify({{ applicationId: id }}),
      }}).catch(function () {{}});
    }});
    writeApplying(still);
  }})();

  // Apply: queue the full pipeline for one role. The board cannot run it, so
  // this only requests it; the local watcher spawns the agent that does the
  // work. The label says "Queued" rather than "Applying" for that reason.
  document.querySelectorAll('.btn.apply').forEach(function (b) {{
    b.addEventListener('click', function () {{
      b.disabled = true;
      var was = b.textContent;
      b.textContent = 'Queueing...';
      fetch('/api/apply', {{
        method: 'POST',
        headers: {{ 'content-type': 'application/json' }},
        body: JSON.stringify({{ applicationId: b.dataset.id }}),
      }})
        .then(function (r) {{ return r.json().then(function (x) {{ return {{ s: r.status, b: x }}; }}); }})
        .then(function (res) {{
          if (res.b && res.b.ok) {{
            var ids = readApplying();
            if (ids.indexOf(b.dataset.id) === -1) ids.push(b.dataset.id);
            writeApplying(ids);
            var row = b.closest('tr');
            var roleCell = row ? row.querySelector('.muted') : null;
            if (row) leaveWatching(row, b.dataset.company || '', roleCell ? roleCell.textContent : '');
          }} else {{
            b.disabled = false; b.textContent = was;
            b.insertAdjacentHTML('afterend',
              '<span class="applymsg">' + ((res.b && res.b.error) || 'Could not queue') + '</span>');
          }}
        }})
        .catch(function () {{
          b.disabled = false; b.textContent = was;
          b.insertAdjacentHTML('afterend', '<span class="applymsg">Could not reach the board</span>');
        }});
    }});
  }});

  // Drop, retry and restore. Each is a queue request the local watcher claims,
  // so the board can only ever ask; it changes the row optimistically and
  // remembers the ask, because the round trip takes a few seconds and a reload
  // inside that window used to make the click look like it had failed.
  var DROP_KEY = 'jobby.dropping';
  function readIds(k) {{
    try {{ return JSON.parse(localStorage.getItem(k) || '[]'); }} catch (e) {{ return []; }}
  }}
  function writeIds(k, v) {{
    try {{ localStorage.setItem(k, JSON.stringify(v)); }} catch (e) {{}}
  }}
  function post(path, id) {{
    return fetch(path, {{
      method: 'POST',
      headers: {{ 'content-type': 'application/json' }},
      body: JSON.stringify({{ applicationId: id }}),
    }}).then(function (r) {{ return r.json().catch(function () {{ return {{}}; }}); }});
  }}
  function tabOf(el) {{
    var panel = el.closest ? el.closest('.panel') : null;
    return panel ? panel.id.replace('p-', '') : '';
  }}
  var LANE_LABEL = {{ watch: 'Watching', act: 'Needs you', flight: 'In flight' }};
  // What the server will record about this drop, worked out from the lane the
  // item is sitting in. Only used to draw the placeholder row; the real values
  // come back on the next republish.
  function dropFacts(el) {{
    var from = tabOf(el);
    var node = el.closest('tr') || el.closest('.card');
    var facts = {{ lane: LANE_LABEL[from] || from, company: '', role: '', stage: '' }};
    if (!node) return facts;
    if (node.tagName === 'TR') {{
      var strong = node.querySelector('strong');
      var muted = node.querySelector('.muted');
      facts.company = strong ? strong.textContent : '';
      facts.role = muted ? muted.textContent : '';
      facts.stage = 'not started';
    }} else {{
      var h3 = node.querySelector('h3');
      var role = node.querySelector('.role');
      facts.company = node.getAttribute('data-company') || (h3 ? h3.textContent : '');
      facts.role = node.getAttribute('data-role') || (role ? role.textContent : '');
      var phase = node.querySelector('.phase');
      var todo = node.querySelector('.todo li');
      facts.stage = phase ? phase.textContent.trim()
                  : (todo ? 'waiting on you: ' + todo.textContent.trim() : '');
    }}
    return facts;
  }}
  // Draw the dropped row straight away. Bumping the tab count without adding the
  // row left the count and the table disagreeing until the next republish, which
  // reads as the click having half worked.
  function placeholderDropped(facts, url) {{
    var tbody = document.querySelector('#p-dropped tbody');
    if (!tbody) return;
    var empty = document.querySelector('#p-dropped .empty');
    if (empty) empty.remove();
    var tr = document.createElement('tr');
    tr.setAttribute('data-hay', (facts.company + ' ' + facts.role + ' ' + facts.lane).toLowerCase());
    tr.setAttribute('data-state', facts.lane);
    var c1 = document.createElement('td');
    var st = document.createElement('strong'); st.textContent = facts.company;
    var sp = document.createElement('span'); sp.className = 'muted'; sp.textContent = facts.role;
    c1.appendChild(st); c1.appendChild(document.createElement('br')); c1.appendChild(sp);
    var c2 = document.createElement('td');
    var chip = document.createElement('span'); chip.className = 'chip lane';
    chip.textContent = facts.lane; c2.appendChild(chip);
    var c3 = document.createElement('td'); c3.className = 'note-cell'; c3.textContent = facts.stage;
    var c4 = document.createElement('td'); c4.className = 'when';
    c4.textContent = new Date().toISOString().slice(0, 10);
    var c5 = document.createElement('td');
    if (url) {{
      var a = document.createElement('a');
      a.className = 'go'; a.href = url; a.target = '_blank'; a.rel = 'noopener';
      a.textContent = 'View';
      c5.appendChild(a);
    }}
    [c1, c2, c3, c4, c5].forEach(function (c) {{ tr.appendChild(c); }});
    tbody.insertBefore(tr, tbody.firstChild);
  }}
  // Remove whatever represents this application in its current lane, wherever
  // it is: a table row in Watching, a card in Needs you or In flight.
  function removeItem(el) {{
    var from = tabOf(el);
    var node = el.closest('tr') || el.closest('.card');
    if (!node) return from;
    node.remove();
    if (from) tabCount(from, -1);
    return from;
  }}

  document.querySelectorAll('.drop').forEach(function (b) {{
    b.addEventListener('click', function () {{
      var id = b.dataset.id;
      if (!id) return;
      b.disabled = true;
      var ids = readIds(DROP_KEY);
      if (ids.indexOf(id) === -1) ids.push(id);
      writeIds(DROP_KEY, ids);
      var facts = dropFacts(b);
      var node = b.closest('tr') || b.closest('.card');
      var link = node ? node.querySelector('a.go, a[href^="http"]') : null;
      removeItem(b);
      tabCount('dropped', 1);
      placeholderDropped(facts, link ? link.href : '');
      post('/api/drop', id).catch(function () {{}});
    }});
  }});

  document.querySelectorAll('.btn.retry').forEach(function (b) {{
    b.addEventListener('click', function () {{
      var id = b.dataset.id;
      if (!id) return;
      b.disabled = true;
      b.textContent = 'Queued...';
      // Retry is the apply queue: the runner reuses the documents already built
      // and picks up from whatever the user has just unblocked.
      var ids = readApplying();
      if (ids.indexOf(id) === -1) ids.push(id);
      writeApplying(ids);
      post('/api/retry', id).then(function (res) {{
        if (res && res.ok) {{
          removeItem(b);
          tabCount('flight', 1);
          placeholderCard(b.dataset.company || '', b.dataset.role || '');
        }} else {{
          b.disabled = false; b.textContent = 'Retry';
        }}
      }}).catch(function () {{ b.disabled = false; b.textContent = 'Retry'; }});
    }});
  }});

  document.querySelectorAll('.undrop').forEach(function (b) {{
    b.addEventListener('click', function () {{
      var id = b.dataset.id;
      if (!id) return;
      b.disabled = true;
      b.textContent = 'Restoring...';
      var row = b.closest('tr');
      if (row) row.classList.add('dropping');
      post('/api/restore', id).catch(function () {{}});
      // Forget the local memory, or the replay below would immediately re-drop it.
      writeIds(DROP_KEY, readIds(DROP_KEY).filter(function (x) {{ return x !== id; }}));
    }});
  }});

  // On load, hide anything dropped that the store has not caught up with, and
  // re-send the request. /api/drop is idempotent per application id.
  (function replayDropping() {{
    var ids = readIds(DROP_KEY);
    if (!ids.length) return;
    var still = [];
    ids.forEach(function (id) {{
      var el = document.querySelector('.drop[data-id="' + id.replace(/"/g, '') + '"]');
      if (!el) return;                       // the server already moved it
      still.push(id);
      var facts = dropFacts(el);
      var node = el.closest('tr') || el.closest('.card');
      var link = node ? node.querySelector('a.go, a[href^="http"]') : null;
      removeItem(el);
      tabCount('dropped', 1);
      placeholderDropped(facts, link ? link.href : '');
      post('/api/drop', id).catch(function () {{}});
    }});
    writeIds(DROP_KEY, still);
  }})();

  // Search and filter, per panel. Rows and cards carry a data-hay haystack and
  // whatever facets their panel offers; everything here is plain substring and
  // attribute matching so it stays fast on a few hundred rows.
  function itemsFor(scope) {{
    var panel = document.getElementById('p-' + scope);
    if (!panel) return [];
    return Array.prototype.slice.call(panel.querySelectorAll('[data-hay]'));
  }}
  function applyFilter(scope) {{
    var search = document.querySelector('[data-search="' + scope + '"]');
    var select = document.querySelector('[data-filter="' + scope + '"]');
    var term = (search && search.value || '').trim().toLowerCase();
    var facet = (select && select.value) || '';
    var shown = 0;
    itemsFor(scope).forEach(function (el) {{
      var hay = (el.getAttribute('data-hay') || '').toLowerCase();
      var ok = !term || hay.indexOf(term) !== -1;
      if (ok && facet) {{
        if (facet === 'fresh') ok = el.getAttribute('data-fresh') === '1';
        else if (facet.indexOf('region:') === 0) ok = el.getAttribute('data-region') === facet.slice(7);
        else if (facet.indexOf('disc:') === 0) ok = el.getAttribute('data-disc') === facet.slice(5);
        else ok = el.getAttribute('data-state') === facet;
      }}
      el.style.display = ok ? '' : 'none';
      if (ok) shown++;
    }});
    var count = document.querySelector('[data-count="' + scope + '"]');
    if (count) {{
      var total = itemsFor(scope).length;
      count.textContent = shown === total ? total + ' shown' : shown + ' of ' + total;
    }}
  }}
  document.querySelectorAll('[data-search]').forEach(function (el) {{
    var scope = el.getAttribute('data-search');
    el.addEventListener('input', function () {{ applyFilter(scope); }});
  }});
  document.querySelectorAll('[data-filter]').forEach(function (el) {{
    var scope = el.getAttribute('data-filter');
    el.addEventListener('change', function () {{ applyFilter(scope); }});
  }});
  ['act', 'flight', 'done', 'watch'].forEach(applyFilter);

  document.querySelectorAll('.copy').forEach(function (b) {{
    b.addEventListener('click', function () {{
      navigator.clipboard.writeText(b.dataset.msg).then(function () {{
        var s = b.textContent; b.textContent = 'Copied'; b.dataset.done = '1';
        setTimeout(function () {{ b.textContent = s; delete b.dataset.done; }}, 1600);
      }});
    }});
  }});
</script>
"""


def main():
    ap = argparse.ArgumentParser(description="Build the job-application dashboard.")
    ap.add_argument("--out", required=True, help="output HTML path")
    ap.add_argument("--root", help="store directory")
    args = ap.parse_args()

    root = root_dir(args.root)
    if not root.exists():
        print("job-application dashboard: no store found; run store.py init first", file=sys.stderr)
        return 2

    state = load_state(root)
    generated = datetime.now(timezone.utc).astimezone().strftime("%d %b %Y, %H:%M")
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(state, generated), encoding="utf-8")

    print(json.dumps({
        "written": str(out),
        "blocked": len([s for s in state["sessions"] if s.get("status") in ("active", "review")]),
        "inflight": len([s for s in state["sessions"] if s.get("status") == "inflight"]),
        "submitted": len([e for e in state["history"]
                          if e.get("event") in ("completed", "response", "interview", "offer", "rejected")]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
