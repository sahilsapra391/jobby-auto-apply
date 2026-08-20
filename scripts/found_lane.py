#!/usr/bin/env python3
"""The Found lane: everything a scan turned up, with a % you can argue with.

This is the only lane rendered on the client. The others are a handful of rows
each and server-side HTML is simpler; Found holds hundreds of jobs behind a
filter bar that has to feel instant, and re-rendering that server-side would
mean a round trip per keystroke to a machine that is not even running.

So the jobs travel as JSON and the cards are built in the browser. Two
consequences, both deliberate:

**Nothing job-sourced is ever written as HTML.** Titles and descriptions come
off the open internet. Every card is built with createElement and textContent,
so a posting containing markup renders as the characters it is, and the JSON
blob has `</` escaped so a title cannot close the script element it rides in.

**The payload is capped.** Eight hundred jobs at a trimmed excerpt is around
0.6 MB; the whole pool stays on disk and the footer says how much of it you are
looking at, rather than quietly showing a slice as though it were everything.

Phase 1 extracts none of the deep attributes — required experience, degree,
role category, employment type — so those filters exist and honestly filter the
`unknown` bucket. Pretending they work would be worse than saying they do not.
"""

import html
import json

# Eight hundred is the agreed embedded cap. The stored pool is 3,000; the rest
# stays on disk and the footer says so.
EMBED_CAP = 800
# Full excerpts would be 1.2 MB at the cap for text nobody reads until they
# open Details. This is enough to see what the role is.
EMBED_EXCERPT_CHARS = 400
MAX_TAGS = 6

DATE_WINDOWS = [("6h", "6 hours"), ("24h", "24 hours"), ("7d", "7 days"),
                ("14d", "14 days"), ("30d", "30 days"), ("all", "Any time")]
WORKPLACES = [("remote", "Remote"), ("hybrid", "Hybrid"), ("onsite", "Onsite")]
EXPERIENCE_OPTIONS = [("", "Open to all"), ("0", "No experience"), ("1", "Up to 1 year"),
                      ("2", "Up to 2 years"), ("3", "Up to 3 years"),
                      ("5", "Up to 5 years"), ("7", "Up to 7 years")]
DEGREE_OPTIONS = [("", "Any"), ("none", "No degree needed"), ("associate", "Associate"),
                  ("bachelors", "Bachelor's"), ("masters", "Master's"),
                  ("doctorate", "Doctorate")]
ROLE_CATEGORIES = [("", "Any"), ("product_management", "Product management"),
                   ("business_analysis", "Business analysis"),
                   ("project_management", "Project management"),
                   ("data", "Data"), ("engineering", "Engineering"),
                   ("design", "Design"), ("operations", "Operations")]
EMPLOYMENT_TYPES = [("", "Any"), ("full_time", "Full time"), ("part_time", "Part time"),
                    ("contract", "Contract"), ("internship", "Internship")]
JOB_TYPES = [("", "Any"), ("permanent", "Permanent"), ("temporary", "Temporary"),
             ("volunteer", "Volunteer")]
UNKNOWN_TOGGLES = [("experience", "experience"), ("degree", "degree"),
                   ("workplace", "workplace"), ("postedAt", "posted date")]


def esc(value):
    return html.escape(str(value if value is not None else ""))


def safe_url(url):
    text = str(url or "").strip()
    return text if text.startswith(("https://", "http://")) else ""


def compact(job):
    """One job, trimmed to what a card and its Details panel actually show."""
    score = job.get("score") or {}
    location = job.get("location") or {}
    sightings = []
    for source in (job.get("sources") or []):
        link = safe_url(source.get("url"))
        if link:
            sightings.append({"s": source.get("source") or "", "u": link})
    extracted = job.get("extracted") or {}
    return {
        "id": job.get("id"),
        "co": job.get("company") or "",
        "ti": job.get("title") or "",
        "url": safe_url(job.get("canonicalUrl")),
        "src": job.get("canonicalSource") or "",
        "loc": location.get("raw") or "",
        "cty": location.get("country") or "",
        "wp": job.get("workplace") or "",
        "pa": (job.get("postedAt") or "")[:10],
        "fs": (job.get("firstSeenAt") or "")[:10],
        "sc": score.get("total"),
        "pt": score.get("parts") or {},
        "wt": score.get("weights") or {},
        "wy": score.get("why") or {},
        "un": score.get("unknowns") or [],
        "ga": [g.get("label") for g in (score.get("gates") or []) if g.get("label")],
        "bd": len(job.get("sources") or []),
        "sg": sightings,
        "tg": (job.get("tags") or [])[:MAX_TAGS],
        "ex": (job.get("descriptionExcerpt") or "")[:EMBED_EXCERPT_CHARS],
        "yr": extracted.get("yearsMax"),
        "dg": extracted.get("degree"),
        "rc": extracted.get("roleCategory"),
        "et": extracted.get("employmentType"),
        "sal": job.get("salary"),
    }


def sort_key(job):
    score = (job.get("score") or {}).get("total")
    return (score if isinstance(score, (int, float)) else -1,
            job.get("postedAt") or job.get("firstSeenAt") or "")


def select(jobs):
    """(embedded, total) — the highest-scoring `EMBED_CAP`, newest breaking ties."""
    found = [job for job in (jobs or {}).values() if job.get("state") == "new"]
    found.sort(key=sort_key, reverse=True)
    return found[:EMBED_CAP], len(found)


def payload(jobs):
    """The JSON blob, escaped so no posting can close the script element.

    `</` is the only sequence that can terminate a script element from inside a
    string, and a job title is untrusted text off the open internet.
    """
    blob = json.dumps([compact(job) for job in jobs], separators=(",", ":"),
                      ensure_ascii=False)
    return (blob.replace("</", "<\\/")
                .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _countries(jobs):
    return sorted({job.get("location", {}).get("country") for job in jobs
                   if (job.get("location") or {}).get("country")})


def _options(pairs, selected=""):
    return "".join(
        f'<option value="{esc(v)}"{" selected" if v == selected else ""}>{esc(label)}</option>'
        for v, label in pairs)


def last_ingest_line(meta):
    """What the last scan did, or an honest admission that none has run.

    A scan that found nothing has to read differently from a scan that never
    happened, so this never renders as an empty string.
    """
    report = (meta or {}).get("lastIngest")
    if not report:
        return ('<p class="jb-ingest none">No scan has run yet. '
                'Save a search below, or press Scan now.</p>')
    when = esc((report.get("at") or "")[11:16])
    bits = [f"Last scan {when} UTC",
            f"{report.get('found', 0)} seen",
            f"{report.get('kept', 0)} new",
            f"{report.get('deduped', 0)} merged",
            f"{report.get('gatedOut', 0)} filtered out"]
    line = " &middot; ".join(esc(b) for b in bits)

    extras = []
    skipped = report.get("channelsSkipped") or []
    if skipped:
        names = ", ".join(esc(f"{s.get('channel')} ({s.get('why')})") for s in skipped[:6])
        extras.append(f'<span class="jb-skipped">{len(skipped)} channel'
                      f'{"s" if len(skipped) != 1 else ""} skipped: {names}</span>')
    errors = report.get("errors") or []
    if errors:
        names = ", ".join(esc(f"{e.get('channel')}: {e.get('why')}") for e in errors[:4])
        extras.append(f'<span class="jb-errored">{len(errors)} problem'
                      f'{"s" if len(errors) != 1 else ""}: {names}</span>')
    if report.get("usingDefaults"):
        extras.append('<span class="jb-skipped">no saved search yet, so this ran on defaults'
                      '</span>')
    if report.get("failed"):
        extras.append('<span class="jb-errored">the scan itself failed</span>')
    detail = f'<br>{" &middot; ".join(extras)}' if extras else ""
    return f'<p class="jb-ingest">{line}{detail}</p>'


def banner(config, meta):
    """The saved-search banner: which search runs next, and when.

    `data-version` is the version this page was rendered from. The client keeps
    it so it can tell a save that landed from one the single-key
    read-modify-write quietly lost — a lost click is re-sent by the replay, but
    a lost save would otherwise leave the banner reading "saved" while the next
    scan ran the old search.
    """
    version = (config or {}).get("version", 0)
    if not config:
        return (f'<div class="jb-banner" data-version="{esc(version)}">'
                '<div><strong>No saved search.</strong> '
                '<span>Set the filters below and press Save search. Until then the '
                'hourly scan stays off and nothing runs on its own.</span>'
                '<span class="jb-savestate" id="jb-savestate"></span></div>'
                '<button class="btn scan" id="jb-scan">Scan now</button></div>')
    nxt = (meta or {}).get("nextTier1ScanAt")
    when = f"next run {esc(nxt[11:16])} UTC" if nxt else "next run shortly"
    sweeps = config.get("tier2SweepsPerDay") or 6
    every = config.get("tier1IntervalMinutes") or 60
    return (f'<div class="jb-banner" data-version="{esc(version)}">'
            f'<div><strong>Search v{esc(version)} '
            f'is live.</strong> <span>Every {esc(every)} minutes, plus {esc(sweeps)} '
            f'sweeps a day &middot; {esc(when)}</span>'
            f'<span class="jb-savestate" id="jb-savestate"></span></div>'
            f'<button class="btn scan" id="jb-scan">Scan now</button></div>')


def filter_bar(jobs, config):
    """Every filter the plan asks for, and honest labels on the ones Phase 1
    cannot yet answer."""
    config = config or {}
    countries = _countries(jobs)
    country_options = "".join(
        f'<option value="{esc(c)}">{esc(c)}</option>' for c in countries)
    packs = "".join(f'<option value="pack:{esc(p)}">{esc(label)}</option>'
                    for p, label in (("EU", "Europe (EU/EEA/UK)"),
                                     ("MIDDLE_EAST", "Middle East"),
                                     ("SEA", "South-East Asia")))
    dates = "".join(
        f'<label class="jb-radio"><input type="radio" name="jb-date" value="{esc(v)}"'
        f'{" checked" if v == (config.get("postedWithin") or "7d") else ""}>'
        f'<span>{esc(label)}</span></label>' for v, label in DATE_WINDOWS)
    workplaces = "".join(
        f'<label class="jb-check"><input type="checkbox" data-wp="{esc(v)}" checked>'
        f'<span>{esc(label)}</span></label>' for v, label in WORKPLACES)
    unknowns = "".join(
        f'<label class="jb-check"><input type="checkbox" data-unknown="{esc(key)}" checked>'
        f'<span>unknown {esc(label)}</span></label>' for key, label in UNKNOWN_TOGGLES)

    return f"""
    <div class="jb-filters">
      <div class="jb-row">
        <input type="search" id="jb-kw" placeholder="Add a keyword and press Enter&hellip;"
               aria-label="Add a keyword">
        <div class="jb-pills" id="jb-pills"></div>
      </div>
      <div class="jb-row">
        <input type="search" id="jb-ex" placeholder="Hide jobs mentioning&hellip;"
               aria-label="Hide jobs mentioning">
        <select id="jb-scope" aria-label="Where to search">
          <option value="title+description">Title and description</option>
          <option value="title">Title only</option>
        </select>
        <select id="jb-sort" aria-label="Sort">
          <option value="score">Match %</option>
          <option value="new">Newest</option>
        </select>
      </div>
      <div class="jb-row jb-radios">{dates}</div>
      <div class="jb-row">
        <select id="jb-loc" multiple size="4" aria-label="Locations">
          {packs}{country_options}
        </select>
        <div class="jb-stack">
          <label class="jb-check"><input type="checkbox" id="jb-remote" checked>
            <span>include remote for this selection</span></label>
          <div class="jb-checks">{workplaces}</div>
        </div>
      </div>
      <div class="jb-row">
        <select id="jb-cmode" aria-label="Companies">
          <option value="all">All companies</option>
          <option value="only">Only these companies</option>
          <option value="hide">Hide these companies</option>
        </select>
        <input type="search" id="jb-companies" placeholder="Company names, comma separated"
               aria-label="Company names">
      </div>
      <div class="jb-row">
        <label class="jb-lab">Max experience
          <select id="jb-exp">{_options(EXPERIENCE_OPTIONS)}</select></label>
        <label class="jb-lab">Degree
          <select id="jb-deg">{_options(DEGREE_OPTIONS)}</select></label>
        <label class="jb-lab">Role
          <select id="jb-role">{_options(ROLE_CATEGORIES)}</select></label>
        <label class="jb-lab">Employment
          <select id="jb-emp">{_options(EMPLOYMENT_TYPES)}</select></label>
        <label class="jb-lab">Job type
          <select id="jb-jt">{_options(JOB_TYPES)}</select></label>
      </div>
      <div class="jb-row jb-checks">{unknowns}
        <span class="jb-note">these four are not extracted yet, so they filter the
          unknown bucket honestly</span>
      </div>
      <div class="jb-row jb-actions">
        <button class="btn" id="jb-save">Save search</button>
        <button class="btn ghost" id="jb-clear">Clear</button>
        <span class="jb-count" id="jb-count"></span>
      </div>
    </div>"""


def panel(jobs, total, config, meta):
    """The whole Found tab."""
    if not total:
        return (f'{banner(config, meta)}{last_ingest_line(meta)}'
                '<p class="empty"><strong>Nothing found yet.</strong>'
                '<span>Save a search and the hourly scan will start filling this. '
                'Scan now runs one immediately.</span></p>'
                f'{add_link_card()}')
    shown = len(jobs)
    footer = ""
    if total > shown:
        footer = (f'<p class="jb-footer">Showing {shown} of {total} &mdash; refine the '
                  f'filters or save a narrower search. The rest stay on disk.</p>')
    return f"""{banner(config, meta)}
    {last_ingest_line(meta)}
    {filter_bar(jobs, config)}
    <div class="jb-grid" id="jb-grid"></div>
    <p class="jb-footer" id="jb-none" hidden>Nothing matches these filters.</p>
    {footer}
    {add_link_card()}"""


def add_link_card():
    return """
    <div class="jb-addlink">
      <h4>Found something yourself?</h4>
      <p>Paste a posting and the next sweep starts there.</p>
      <div class="jb-row">
        <input type="url" id="jb-link" placeholder="https://&hellip;" aria-label="Posting link">
        <button class="btn ghost" id="jb-addlink">Add</button>
        <span class="jb-linkmsg" id="jb-linkmsg"></span>
      </div>
    </div>"""


CSS = """
  /* --- Found lane ------------------------------------------------------- */
  .jb-banner { display:flex; align-items:center; justify-content:space-between; gap:1rem;
    flex-wrap:wrap; background:var(--card); border-radius:16px; padding:.85rem 1.15rem;
    box-shadow:var(--sh); margin-bottom:.85rem; font-size:.92rem; }
  .jb-banner strong { font-weight:620; }
  .jb-banner span { color:var(--ink-2); }
  .jb-savestate { display:block; font-size:.82rem; margin-top:.15rem; }
  .jb-savestate.waiting { color:var(--ink-3); }
  .jb-savestate.landed { color:var(--green); }
  .jb-savestate.lost { color:var(--orange); }
  .jb-ingest { margin:0 0 1.1rem; font-size:.84rem; color:var(--ink-2);
    line-height:1.5; }
  .jb-ingest.none { color:var(--ink-3); }
  .jb-skipped { color:var(--orange); }
  .jb-errored { color:var(--red-ink); }
  .jb-filters { background:var(--card); border-radius:16px; padding:1rem 1.15rem;
    box-shadow:var(--sh); margin-bottom:1.25rem; display:flex; flex-direction:column;
    gap:.7rem; }
  .jb-row { display:flex; gap:.55rem; flex-wrap:wrap; align-items:center; }
  .jb-row input[type=search], .jb-row input[type=url] { flex:1 1 200px; min-width:150px;
    font:inherit; font-size:.9rem; padding:.45rem .85rem; border-radius:100px;
    border:1px solid var(--hair); background:var(--bg); color:var(--ink); }
  .jb-row select { font:inherit; font-size:.86rem; padding:.4rem .65rem; border-radius:10px;
    border:1px solid var(--hair); background:var(--bg); color:var(--ink); }
  .jb-row select[multiple] { border-radius:10px; min-width:190px; padding:.3rem; }
  .jb-stack { display:flex; flex-direction:column; gap:.35rem; }
  .jb-checks { display:flex; gap:.7rem; flex-wrap:wrap; }
  .jb-radios { gap:.9rem; }
  .jb-radio, .jb-check { display:inline-flex; align-items:center; gap:.3rem;
    font-size:.83rem; color:var(--ink-2); cursor:pointer; }
  .jb-lab { display:inline-flex; flex-direction:column; gap:.2rem; font-size:.74rem;
    color:var(--ink-3); }
  .jb-note { font-size:.75rem; color:var(--ink-3); }
  .jb-count { margin-left:auto; font-size:.82rem; color:var(--ink-3);
    font-variant-numeric:tabular-nums; }
  .jb-pills { display:flex; gap:.35rem; flex-wrap:wrap; }
  .jb-pill { display:inline-flex; align-items:center; gap:.3rem; font-size:.78rem;
    background:var(--sunk); color:var(--ink); border-radius:100px; padding:.2rem .5rem .2rem .7rem; }
  .jb-pill button { border:none; background:none; color:var(--ink-3); cursor:pointer;
    font:inherit; line-height:1; padding:0 .1rem; }
  .jb-pill button:hover { color:var(--red-ink); }
  .jb-grid { display:grid; gap:.9rem; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); }
  .jb-card { background:var(--card); border-radius:18px; padding:1.1rem 1.2rem 1rem;
    box-shadow:var(--sh); display:flex; flex-direction:column; gap:.7rem; }
  .jb-head { display:flex; gap:.85rem; align-items:flex-start; }
  .jb-ring { width:48px; height:48px; border-radius:50%; flex:none; display:grid;
    place-items:center; background:var(--sunk); }
  .jb-ring i { width:38px; height:38px; border-radius:50%; background:var(--card);
    display:grid; place-items:center; font-style:normal; font-size:.8rem; font-weight:640;
    font-variant-numeric:tabular-nums; color:var(--ink); }
  .jb-co { font-size:.8rem; color:var(--ink-2); margin:0; }
  .jb-ti { font-size:1rem; font-weight:610; letter-spacing:-.02em; margin:.1rem 0 0;
    line-height:1.28; }
  .jb-chips { display:flex; gap:.3rem; flex-wrap:wrap; }
  .jb-chip { font-size:.71rem; font-weight:560; padding:.16rem .5rem; border-radius:100px;
    background:var(--sunk); color:var(--ink-2); white-space:nowrap; }
  .jb-chip.unknown { background:var(--t-o); color:var(--orange); }
  .jb-chip.gate { background:var(--t-red); color:var(--red-ink); }
  .jb-chip.boards { background:var(--t-g); color:var(--green); }
  .jb-acts { display:flex; gap:.45rem; align-items:center; flex-wrap:wrap; margin-top:auto;
    padding-top:.75rem; border-top:1px solid var(--hair); }
  .jb-eye { border:1px solid var(--hair); background:var(--bg); color:var(--ink);
    border-radius:100px; cursor:pointer; font:inherit; font-size:.86rem;
    padding:.35rem .75rem; }
  .jb-eye:hover { border-color:var(--green); color:var(--green); }
  .jb-eye[data-on] { background:var(--t-g); color:var(--green); border-color:var(--green); }
  .jb-small { font:inherit; font-size:.82rem; padding:.35rem .8rem; border-radius:100px;
    border:none; cursor:pointer; }
  .jb-apply { background:var(--btn-g); color:var(--btn-g-ink); }
  .jb-pass { background:var(--bg); color:var(--ink-2); border:1px solid var(--hair); }
  .jb-pass:hover { color:var(--red-ink); border-color:var(--red-ink); }
  .jb-det { background:none; border:none; color:var(--blue); cursor:pointer; font:inherit;
    font-size:.82rem; margin-left:auto; }
  .jb-panel { border-top:1px solid var(--hair); padding-top:.7rem; font-size:.84rem;
    color:var(--ink-2); display:flex; flex-direction:column; gap:.5rem; }
  .jb-bars { display:flex; flex-direction:column; gap:.25rem; }
  .jb-bar { display:grid; grid-template-columns:5.5rem 1fr 2.4rem; align-items:center;
    gap:.5rem; font-size:.76rem; }
  .jb-bar u { text-decoration:none; color:var(--ink-3); }
  .jb-bar span { height:5px; border-radius:100px; background:var(--sunk); display:block; }
  .jb-bar span i { display:block; height:100%; border-radius:100px; background:var(--blue); }
  .jb-bar em { font-style:normal; text-align:right; color:var(--ink-3);
    font-variant-numeric:tabular-nums; }
  .jb-sight { display:flex; gap:.5rem; flex-wrap:wrap; }
  .jb-excerpt { margin:0; line-height:1.45; }
  .jb-footer { text-align:center; color:var(--ink-3); font-size:.85rem; margin:1.2rem 0 0; }
  .jb-addlink { background:var(--card); border-radius:16px; padding:1rem 1.15rem;
    box-shadow:var(--sh); margin-top:1.4rem; }
  .jb-addlink h4 { margin:0 0 .2rem; font-size:.95rem; font-weight:620; }
  .jb-addlink p { margin:0 0 .6rem; font-size:.85rem; color:var(--ink-2); }
  .jb-linkmsg { font-size:.82rem; color:var(--ink-2); }
  .jb-linkmsg.err { color:var(--orange); }
  .jb-linkmsg.ok { color:var(--green); }
  @media (max-width:600px) { .jb-grid { grid-template-columns:1fr; } }
"""


SCRIPT = r"""
// --- Found lane -------------------------------------------------------------
// The jobs arrive as JSON and the cards are built here, because the filter bar
// has to feel instant and the machine that could re-render it is not running.
//
// Every value that came off a job posting goes in through textContent. None of
// it is ever concatenated into HTML: these are titles and descriptions written
// by strangers, and the board's whole CSP posture assumes nobody trusts them.
(function () {
  var holder = document.getElementById('jb-found-data');
  if (!holder) return;
  var JOBS = [];
  try { JOBS = JSON.parse(holder.textContent || '[]'); } catch (e) { JOBS = []; }
  var grid = document.getElementById('jb-grid');
  if (!grid) return;

  var PACKS = {
    EU: ['AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IE','IT','LV',
         'LT','LU','MT','NL','PL','PT','RO','SK','SI','ES','SE','IS','LI','NO','CH','GB'],
    MIDDLE_EAST: ['AE','SA','QA','KW','BH','OM','JO','EG'],
    SEA: ['SG','MY','PH','ID','TH','VN']
  };
  var DAYS = { '6h': 0.25, '24h': 1, '7d': 7, '14d': 14, '30d': 30, all: null };
  var WATCH_KEY = 'jobby.watching', PASS_KEY = 'jobby.passed';
  var keywords = [];

  function readIds(k) {
    try { return JSON.parse(localStorage.getItem(k) || '[]'); } catch (e) { return []; }
  }
  function writeIds(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function remember(k, id) {
    var ids = readIds(k);
    if (ids.indexOf(id) === -1) { ids.push(id); writeIds(k, ids); }
  }
  function post(path, body) {
    return fetch(path, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json().catch(function () { return {}; }); });
  }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function chip(text, cls) { return el('span', 'jb-chip' + (cls ? ' ' + cls : ''), text); }

  function ago(job) {
    var when = job.pa || job.fs;
    if (!when) return null;
    var days = Math.round((Date.now() - Date.parse(when + 'T00:00:00Z')) / 86400000);
    if (isNaN(days)) return null;
    var label = days <= 0 ? 'today' : days === 1 ? 'yesterday' : days + ' days ago';
    return (job.pa ? 'posted ' : 'seen ') + label;
  }
  function ringColour(score) {
    if (score >= 75) return 'var(--green)';
    if (score >= 50) return 'var(--blue)';
    return 'var(--ink-3)';
  }

  function detailPanel(job) {
    var panel = el('div', 'jb-panel');
    if (job.ex) panel.appendChild(el('p', 'jb-excerpt', job.ex));

    var bars = el('div', 'jb-bars');
    ['title', 'experience', 'location', 'keywords', 'freshness'].forEach(function (name) {
      if (!(name in job.pt)) return;
      var row = el('div', 'jb-bar');
      row.appendChild(el('u', null, name));
      var track = el('span');
      var fill = el('i');
      var weight = job.wt[name] || 1;
      fill.style.width = Math.round((job.pt[name] / weight) * 100) + '%';
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el('em', null, job.pt[name] + '/' + weight));
      row.title = job.wy[name] || '';
      bars.appendChild(row);
    });
    panel.appendChild(bars);

    (job.ga || []).forEach(function (label) {
      panel.appendChild(el('p', 'jb-excerpt', 'Capped at 25: ' + label));
    });
    if ((job.un || []).length) {
      panel.appendChild(el('p', 'jb-excerpt',
        'Scored neutral because the posting does not say: ' + job.un.join(', ') + '.'));
    }
    if ((job.sg || []).length) {
      var links = el('div', 'jb-sight');
      links.appendChild(el('span', 'jb-chip', 'seen on'));
      job.sg.forEach(function (s) {
        var a = document.createElement('a');
        a.className = 'go';
        a.href = s.u; a.target = '_blank'; a.rel = 'noopener';
        a.textContent = s.s || 'link';
        links.appendChild(a);
      });
      panel.appendChild(links);
    }
    return panel;
  }

  function card(job) {
    var article = el('article', 'jb-card');
    article.setAttribute('data-id', job.id);

    var head = el('div', 'jb-head');
    var ring = el('div', 'jb-ring');
    var pct = typeof job.sc === 'number' ? job.sc : 0;
    ring.style.background = 'conic-gradient(' + ringColour(pct) + ' ' + pct + '%, var(--sunk) 0)';
    ring.appendChild(el('i', null, pct + '%'));
    head.appendChild(ring);
    var titles = el('div');
    titles.appendChild(el('p', 'jb-co', job.co));
    titles.appendChild(el('h3', 'jb-ti', job.ti));
    head.appendChild(titles);
    article.appendChild(head);

    var chips = el('div', 'jb-chips');
    if (job.loc) chips.appendChild(chip(job.loc));
    if (job.wp) chips.appendChild(chip(job.wp));
    else chips.appendChild(chip('workplace unknown', 'unknown'));
    var when = ago(job);
    if (when) chips.appendChild(chip(when));
    else chips.appendChild(chip('no date', 'unknown'));
    if (job.bd > 1) chips.appendChild(chip('seen on ' + job.bd + ' boards', 'boards'));
    (job.un || []).forEach(function (name) {
      if (name !== 'workplace' && name !== 'postedAt') chip(name, 'unknown');
    });
    (job.un || []).forEach(function (name) {
      if (name === 'experience') chips.appendChild(chip('experience unknown', 'unknown'));
    });
    (job.ga || []).forEach(function (label) { chips.appendChild(chip(label, 'gate')); });
    (job.tg || []).forEach(function (tag) { chips.appendChild(chip(tag)); });
    article.appendChild(chips);

    var acts = el('div', 'jb-acts');
    var eye = el('button', 'jb-eye', '\u{1F441} Watch');
    eye.title = 'Move this to Watching';
    eye.addEventListener('click', function () { doWatch(job, article, eye); });
    acts.appendChild(eye);

    var apply = el('button', 'jb-small jb-apply', 'Apply');
    apply.addEventListener('click', function () { doApply(job, article, apply); });
    acts.appendChild(apply);

    var pass = el('button', 'jb-small jb-pass', 'Pass');
    pass.addEventListener('click', function () { doPass(job, article); });
    acts.appendChild(pass);

    if (job.url) {
      var open = document.createElement('a');
      open.className = 'go'; open.href = job.url; open.target = '_blank';
      open.rel = 'noopener'; open.textContent = 'Posting \u2197';
      acts.appendChild(open);
    }

    var details = el('button', 'jb-det', 'Details');
    var panel = null;
    details.addEventListener('click', function () {
      if (panel) { panel.remove(); panel = null; details.textContent = 'Details'; return; }
      panel = detailPanel(job);
      article.appendChild(panel);
      details.textContent = 'Hide';
    });
    acts.appendChild(details);
    article.appendChild(acts);
    return article;
  }

  function leave(article, message) {
    article.remove();
    var el2 = document.querySelector('.tab[data-tab="found"] .n');
    if (el2) {
      var n = parseInt(el2.textContent, 10);
      if (!isNaN(n)) el2.textContent = Math.max(0, n - 1);
    }
    render();
  }
  function doWatch(job, article, button) {
    button.setAttribute('data-on', '1');
    remember(WATCH_KEY, job.id);
    hidden[job.id] = true;
    post('/api/watch', { applicationId: job.id }).catch(function () {});
    var tab = document.querySelector('.tab[data-tab="watch"] .n');
    if (tab) {
      var n = parseInt(tab.textContent, 10);
      if (!isNaN(n)) tab.textContent = n + 1;
    }
    leave(article);
  }
  function doPass(job, article) {
    remember(PASS_KEY, job.id);
    hidden[job.id] = true;
    post('/api/pass', { applicationId: job.id }).catch(function () {});
    leave(article);
  }
  function doApply(job, article, button) {
    button.disabled = true;
    button.textContent = 'Queueing\u2026';
    remember(WATCH_KEY, job.id);
    hidden[job.id] = true;
    post('/api/watch', { applicationId: job.id })
      .then(function () { return post('/api/apply', { applicationId: job.id }); })
      .catch(function () {});
    leave(article);
  }

  // Anything acted on but not yet reflected by a republish. Re-sent on load,
  // because restoring only the appearance is how fourteen queued requests once
  // vanished while the board still drew them as in flight.
  var hidden = {};
  (function replay() {
    readIds(WATCH_KEY).forEach(function (id) {
      hidden[id] = true;
      post('/api/watch', { applicationId: id }).catch(function () {});
    });
    readIds(PASS_KEY).forEach(function (id) {
      hidden[id] = true;
      post('/api/pass', { applicationId: id }).catch(function () {});
    });
    var live = {};
    JOBS.forEach(function (j) { live[j.id] = true; });
    writeIds(WATCH_KEY, readIds(WATCH_KEY).filter(function (id) { return live[id]; }));
    writeIds(PASS_KEY, readIds(PASS_KEY).filter(function (id) { return live[id]; }));
  })();

  // --- filtering ------------------------------------------------------------
  function value(id) { var n = document.getElementById(id); return n ? n.value : ''; }
  function checked(id) { var n = document.getElementById(id); return n ? n.checked : true; }
  function selectedCountries() {
    var node = document.getElementById('jb-loc');
    if (!node) return [];
    var out = [];
    Array.prototype.forEach.call(node.selectedOptions || [], function (o) {
      if (o.value.indexOf('pack:') === 0) {
        out = out.concat(PACKS[o.value.slice(5)] || []);
      } else if (o.value) { out.push(o.value); }
    });
    return out;
  }
  function activeWorkplaces() {
    return Array.prototype.filter.call(
      document.querySelectorAll('[data-wp]'), function (n) { return n.checked; }
    ).map(function (n) { return n.getAttribute('data-wp'); });
  }
  function unknownAllowed(name) {
    var node = document.querySelector('[data-unknown="' + name + '"]');
    return node ? node.checked : true;
  }
  function dateWindow() {
    var node = document.querySelector('input[name="jb-date"]:checked');
    return DAYS[node ? node.value : '7d'];
  }
  function haystack(job) {
    return (value('jb-scope') === 'title'
      ? job.ti : job.ti + ' ' + job.co + ' ' + job.ex + ' ' + (job.tg || []).join(' ')
    ).toLowerCase();
  }

  function matches(job) {
    if (hidden[job.id]) return false;
    var hay = haystack(job);
    for (var i = 0; i < keywords.length; i++) {
      if (hay.indexOf(keywords[i].toLowerCase()) === -1) return false;
    }
    var exclude = value('jb-ex').trim().toLowerCase();
    if (exclude && hay.indexOf(exclude) !== -1) return false;

    var days = dateWindow();
    if (days !== null && days !== undefined) {
      var when = job.pa || job.fs;
      if (!when) { if (!unknownAllowed('postedAt')) return false; }
      else {
        var age = (Date.now() - Date.parse(when + 'T00:00:00Z')) / 86400000;
        if (age > days) return false;
      }
    }
    var countries = selectedCountries();
    if (countries.length) {
      var remoteCounts = checked('jb-remote') && job.wp === 'remote';
      if (job.cty && countries.indexOf(job.cty) === -1 && !remoteCounts) return false;
    }
    var places = activeWorkplaces();
    if (job.wp) { if (places.indexOf(job.wp) === -1) return false; }
    else if (!unknownAllowed('workplace')) return false;

    var mode = value('jb-cmode');
    if (mode !== 'all') {
      var names = value('jb-companies').split(',').map(function (s) {
        return s.trim().toLowerCase();
      }).filter(Boolean);
      if (names.length) {
        var listed = names.indexOf((job.co || '').toLowerCase()) !== -1;
        if (mode === 'only' && !listed) return false;
        if (mode === 'hide' && listed) return false;
      }
    }
    var maxYears = value('jb-exp');
    if (maxYears !== '') {
      if (job.yr === null || job.yr === undefined) {
        if (!unknownAllowed('experience')) return false;
      } else if (job.yr > parseInt(maxYears, 10)) return false;
    }
    if (value('jb-deg') && job.dg !== value('jb-deg')) {
      if (job.dg || !unknownAllowed('degree')) return false;
    }
    if (value('jb-role') && job.rc !== value('jb-role')) { if (job.rc) return false; }
    if (value('jb-emp') && job.et !== value('jb-emp')) { if (job.et) return false; }
    return true;
  }

  function render() {
    var rows = JOBS.filter(matches);
    if (value('jb-sort') === 'new') {
      rows = rows.slice().sort(function (a, b) {
        return String(b.pa || b.fs || '').localeCompare(String(a.pa || a.fs || ''));
      });
    }
    grid.textContent = '';
    rows.forEach(function (job) { grid.appendChild(card(job)); });
    var count = document.getElementById('jb-count');
    if (count) {
      count.textContent = rows.length === JOBS.length
        ? rows.length + ' shown' : rows.length + ' of ' + JOBS.length;
    }
    var none = document.getElementById('jb-none');
    if (none) none.hidden = rows.length !== 0;
  }

  function drawPills() {
    var box = document.getElementById('jb-pills');
    if (!box) return;
    box.textContent = '';
    keywords.forEach(function (word, i) {
      var pill = el('span', 'jb-pill', word);
      var x = el('button', null, '\u00d7');
      x.setAttribute('aria-label', 'Remove ' + word);
      x.addEventListener('click', function () {
        keywords.splice(i, 1); drawPills(); render();
      });
      pill.appendChild(x);
      box.appendChild(pill);
    });
  }

  var kw = document.getElementById('jb-kw');
  if (kw) {
    kw.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      var word = kw.value.trim();
      if (word && keywords.indexOf(word) === -1) keywords.push(word);
      kw.value = '';
      drawPills(); render();
    });
  }
  ['jb-ex','jb-scope','jb-sort','jb-loc','jb-remote','jb-cmode','jb-companies',
   'jb-exp','jb-deg','jb-role','jb-emp','jb-jt'].forEach(function (id) {
    var node = document.getElementById(id);
    if (node) { node.addEventListener('input', render); node.addEventListener('change', render); }
  });
  document.querySelectorAll('[data-wp],[data-unknown],input[name="jb-date"]').forEach(
    function (n) { n.addEventListener('change', render); });

  var clear = document.getElementById('jb-clear');
  if (clear) {
    clear.addEventListener('click', function () {
      keywords = [];
      ['jb-kw','jb-ex','jb-companies'].forEach(function (id) {
        var n = document.getElementById(id); if (n) n.value = '';
      });
      var loc = document.getElementById('jb-loc');
      if (loc) Array.prototype.forEach.call(loc.options, function (o) { o.selected = false; });
      ['jb-exp','jb-deg','jb-role','jb-emp','jb-jt'].forEach(function (id) {
        var n = document.getElementById(id); if (n) n.value = '';
      });
      document.querySelectorAll('[data-wp],[data-unknown]').forEach(function (n) {
        n.checked = true;
      });
      var week = document.querySelector('input[name="jb-date"][value="7d"]');
      if (week) week.checked = true;
      drawPills(); render();
    });
  }

  var save = document.getElementById('jb-save');
  if (save) {
    save.addEventListener('click', function () {
      var node = document.querySelector('input[name="jb-date"]:checked');
      var locations = [];
      var loc = document.getElementById('jb-loc');
      Array.prototype.forEach.call((loc && loc.selectedOptions) || [], function (o) {
        if (o.value.indexOf('pack:') === 0) locations.push({ regionPack: o.value.slice(5) });
        else if (o.value) locations.push({ country: o.value });
      });
      var years = value('jb-exp');
      var config = {
        keywords: keywords.slice(),
        excludeKeywords: value('jb-ex') ? [value('jb-ex').trim()] : [],
        searchScope: value('jb-scope') || 'title+description',
        roleCategories: value('jb-role') ? [value('jb-role')] : [],
        locations: locations,
        includeRemoteForSelection: checked('jb-remote'),
        workplace: activeWorkplaces(),
        companiesMode: value('jb-cmode') || 'all',
        companies: value('jb-companies').split(',').map(function (s) { return s.trim(); })
          .filter(Boolean),
        maxExperienceYears: years === '' ? null : parseInt(years, 10),
        degreeMax: value('jb-deg') || null,
        employmentTypes: value('jb-emp') ? [value('jb-emp')] : [],
        jobTypes: value('jb-jt') ? [value('jb-jt')] : [],
        postedWithin: node ? node.value : '7d',
        includeUnknowns: {
          experience: unknownAllowed('experience'), degree: unknownAllowed('degree'),
          workplace: unknownAllowed('workplace'), postedAt: unknownAllowed('postedAt')
        }
      };
      save.disabled = true;
      var was = save.textContent;
      save.textContent = 'Saving\u2026';
      post('/api/search-config', config).then(function (res) {
        var ok = res && res.ok;
        save.textContent = ok ? 'Saved \u2713' : 'Could not save';
        // Remember which version was live when this was written. The board is a
        // snapshot, so "the server accepted it" is not the same as "it is now
        // the saved search", and only the version moving proves the latter.
        if (ok) {
          writeIds(SAVE_KEY, [{ at: Date.now(), priorVersion: renderedVersion() }]);
          reconcileSave();
        }
        setTimeout(function () { save.disabled = false; save.textContent = was; }, 3000);
      }).catch(function () {
        save.disabled = false; save.textContent = was;
      });
    });
  }

  var scan = document.getElementById('jb-scan');
  if (scan) {
    scan.addEventListener('click', function () {
      scan.disabled = true;
      var was = scan.textContent;
      scan.textContent = 'Queued\u2026';
      post('/api/scan', {}).then(function () {
        setTimeout(function () { scan.disabled = false; scan.textContent = was; }, 4000);
      }).catch(function () { scan.disabled = false; scan.textContent = was; });
    });
  }

  var addLink = document.getElementById('jb-addlink');
  if (addLink) {
    addLink.addEventListener('click', function () {
      var input = document.getElementById('jb-link');
      var msg = document.getElementById('jb-linkmsg');
      var url = (input && input.value || '').trim();
      function say(text, cls) { if (msg) { msg.textContent = text; msg.className = 'jb-linkmsg ' + (cls || ''); } }
      if (!/^https?:\/\//i.test(url)) { say('That needs to be an http or https link.', 'err'); return; }
      addLink.disabled = true;
      say('Queueing\u2026');
      post('/api/scan', { url: url }).then(function (res) {
        if (res && res.ok) {
          say('Queued. The next sweep starts there.', 'ok');
          if (input) input.value = '';
        } else { say((res && res.error) || 'Could not queue that link.', 'err'); }
        addLink.disabled = false;
      }).catch(function () { say('Could not reach the board.', 'err'); addLink.disabled = false; });
    });
  }

  // --- did the saved search actually land? ---------------------------------
  // The single-key design is read-modify-write with no compare-and-swap, so a
  // save landing in the same instant as a claim can be lost. A lost *click* is
  // re-sent by the replay above and heals itself. A lost *save* would not: the
  // banner would keep reading "saved", and the next scan would quietly run the
  // old search. That asymmetry is the whole reason this exists.
  var SAVE_KEY = 'jobby.searchSaved';
  // The watcher picks a config up within three seconds and the board
  // republishes within a couple of minutes. Ten minutes is generous enough that
  // a slow cycle never reads as a loss.
  var SAVE_GRACE_MS = 10 * 60 * 1000;

  function renderedVersion() {
    var el2 = document.querySelector('.jb-banner');
    var v = el2 && parseInt(el2.getAttribute('data-version'), 10);
    return isNaN(v) ? 0 : v;
  }
  function saySave(text, cls) {
    var node = document.getElementById('jb-savestate');
    if (node) { node.textContent = text; node.className = 'jb-savestate ' + (cls || ''); }
  }
  function reconcileSave() {
    var held = readIds(SAVE_KEY)[0];
    if (!held) return;
    var landed = renderedVersion() > held.priorVersion;
    if (landed) { writeIds(SAVE_KEY, []); saySave('Saved search v' + renderedVersion() + ' is the one running.', 'landed'); return; }
    fetch('/api/search-config').then(function (r) { return r.json(); })
      .then(function (res) {
        if (res && res.pending) {
          saySave('Saved. Waiting for the next check to pick it up.', 'waiting');
        } else if (Date.now() - held.at > SAVE_GRACE_MS) {
          // Nothing waiting, and the version never moved. It was lost.
          writeIds(SAVE_KEY, []);
          saySave('That save did not land. Press Save search again.', 'lost');
        } else {
          saySave('Saved. Waiting for the board to catch up.', 'waiting');
        }
      }).catch(function () {});
  }
  reconcileSave();

  drawPills();
  render();
})();
"""
