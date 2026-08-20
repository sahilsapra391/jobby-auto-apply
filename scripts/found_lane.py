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
        "place": place(job),
        # 0 = the company's own ATS, 1 = its careers page, 2+ = somebody
        # else's copy. Applying through the employer's own system beats
        # applying through a redirect, so the lane leads with these.
        "rank": direct_rank(job),
        "id": job.get("id"),
        "co": job.get("company") or "",
        "ti": tidy_title(job.get("title")),
        "url": safe_url(job.get("canonicalUrl")),
        "src": SOURCE_LABELS.get(job.get("canonicalSource") or "",
                                 (job.get("canonicalSource") or "").title()),
        "loc": location.get("raw") or "",
        "cty": location.get("country") or "",
        "wp": WORKPLACE_LABELS.get(job.get("workplace") or "", ""),
        "pa": (job.get("postedAt") or "")[:10],
        "fs": (job.get("firstSeenAt") or "")[:10],
        "sc": score.get("total"),
        "pt": score.get("parts") or {},
        "wt": score.get("weights") or {},
        "wy": score.get("why") or {},
        "un": score.get("unknowns") or [],
        "ga": [g.get("label") for g in (score.get("gates") or []) if g.get("label")],
        # Boards, not sightings. One aggregator listing the same job twice is
        # one board, and Adzuna does exactly that — two adzuna.it URLs for one
        # posting came out of the supervised run. "Seen on 2 boards" has to
        # mean two boards or it means nothing.
        "bd": len({s.get("source") for s in (job.get("sources") or []) if s.get("source")}),
        "sg": [dict(s, s=SOURCE_LABELS.get(s["s"], s["s"].title())) for s in sightings],
        "tg": [tidy_tag(t) for t in (job.get("tags") or [])[:MAX_TAGS] if tidy_tag(t)],
        "ex": (job.get("descriptionExcerpt") or "")[:EMBED_EXCERPT_CHARS],
        # The floor the posting demands: that is what a screen rejects on, and
        # what "up to N years" in the filter bar has to compare against.
        "yr": extracted.get("yearsMin") if extracted.get("yearsMin") is not None
              else extracted.get("yearsMax"),
        "yrt": extracted.get("yearsText"),
        "dg": extracted.get("degree"),
        "rc": extracted.get("roleCategory"),
        "et": extracted.get("employmentType"),
        "sal": job.get("salary"),
        # One line of the posting itself, so a card says something about the
        # job rather than only about its metadata.
        "sum": " ".join((job.get("descriptionExcerpt") or "").split())[:150],
    }


# Codes are for filtering; a card says the name. Only the countries the region
# packs and the connectors can actually produce.
COUNTRY_NAMES = {
    "US": "United States", "GB": "United Kingdom", "IN": "India", "CA": "Canada",
    "AU": "Australia", "NZ": "New Zealand", "IE": "Ireland", "DE": "Germany",
    "FR": "France", "NL": "Netherlands", "ES": "Spain", "IT": "Italy",
    "PT": "Portugal", "PL": "Poland", "AT": "Austria", "BE": "Belgium",
    "CH": "Switzerland", "SE": "Sweden", "NO": "Norway", "DK": "Denmark",
    "FI": "Finland", "IS": "Iceland", "CZ": "Czechia", "SK": "Slovakia",
    "HU": "Hungary", "RO": "Romania", "BG": "Bulgaria", "GR": "Greece",
    "HR": "Croatia", "SI": "Slovenia", "EE": "Estonia", "LV": "Latvia",
    "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta", "CY": "Cyprus",
    "AE": "UAE", "SA": "Saudi Arabia", "QA": "Qatar", "KW": "Kuwait",
    "BH": "Bahrain", "OM": "Oman", "JO": "Jordan", "EG": "Egypt",
    "IL": "Israel", "TR": "Turkey", "JP": "Japan", "KR": "South Korea",
    "CN": "China", "HK": "Hong Kong", "TW": "Taiwan", "SG": "Singapore",
    "MY": "Malaysia", "PH": "Philippines", "ID": "Indonesia", "TH": "Thailand",
    "VN": "Vietnam", "BR": "Brazil", "MX": "Mexico", "AR": "Argentina",
    "CL": "Chile", "CO": "Colombia", "ZA": "South Africa", "UA": "Ukraine",
}

WORLDWIDE = ("anywhere in the world", "worldwide", "anywhere", "global")

# Strings that arrive in the city slot and are not cities. An aggregator
# queried for the US that returns a posting located "Europe" yields city
# "Europe", country "US", and the chip then reads "Europe, United States",
# which is worse than saying nothing.
NOT_A_CITY = {
    "europe", "emea", "apac", "latam", "americas", "asia", "africa",
    "north america", "south america", "middle east", "worldwide", "global",
    "remote", "anywhere", "anywhere in the world", "eu", "usa", "us",
    "united states", "uk", "united kingdom", "italia", "espana", "españa",
    "deutschland", "polska", "nationwide", "various", "multiple locations",
}


# Acronyms that .title() would mangle into "Emea".
REGION_LABELS = {"emea": "EMEA", "apac": "APAC", "latam": "LATAM", "eu": "EU",
                 "us": "US", "uk": "UK", "usa": "USA"}


def place(job):
    """One line of the best real location information the posting gave.

    Never a guess. A city with no country stays a city, because inferring the
    country from a city name is how a London, Ontario role gets filtered as a
    British one.
    """
    location = job.get("location") or {}
    country = (location.get("country") or "").upper()
    city = (location.get("city") or "").strip().rstrip(",")
    raw = (location.get("raw") or "").strip()
    if country:
        name = COUNTRY_NAMES.get(country, country)
        usable = (city and city.lower() not in NOT_A_CITY
                  and city.lower() != name.lower())
        return f"{city}, {name}" if usable else name
    if raw and raw.lower() in WORLDWIDE:
        return "Worldwide"
    if city and city.lower() in NOT_A_CITY:
        return REGION_LABELS.get(city.lower(), city.title())
    return city or raw or ""


DIRECT_RANKS = {"ats": 0, "careers": 1}


def direct_rank(job):
    """How close this record is to the employer's own application form."""
    import jobs_store
    classes = [s.get("sourceClass") for s in (job.get("sources") or [])
               if isinstance(s, dict)]
    best = min(classes, key=jobs_store.canonical_rank, default="other")
    return jobs_store.canonical_rank(best)


# What a board calls itself, versus what it is called. Chips read as names.
SOURCE_LABELS = {
    "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby",
    "smartrecruiters": "SmartRecruiters", "workable": "Workable",
    "recruitee": "Recruitee", "personio": "Personio", "breezy": "Breezy",
    "rippling": "Rippling", "adzuna": "Adzuna", "jooble": "Jooble",
    "careerjet": "Careerjet", "reed": "Reed", "usajobs": "USAJobs",
    "themuse": "The Muse", "remoteok": "RemoteOK", "remotive": "Remotive",
    "himalayas": "Himalayas", "workingnomads": "Working Nomads",
    "weworkremotely": "We Work Remotely", "linkedin": "LinkedIn",
}
WORKPLACE_LABELS = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}

# Words a title-caser must not touch. Employers write these in caps on purpose.
KEEP_UPPER = {
    "AI", "ML", "API", "APIs", "SDK", "UX", "UI", "QA", "SRE", "IT", "HR",
    "B2B", "B2C", "SaaS", "CRM", "ERP", "SQL", "AWS", "GCP", "iOS", "PM",
    "GTM", "KYC", "AML", "LLM", "NLP", "CX", "VP", "CTO", "CEO", "COO", "CFO",
    "US", "USA", "UK", "EU", "UAE", "APAC", "EMEA", "R&D", "3D", "AR", "VR",
    "RX", "SEO", "SEM", "PPC", "P&L", "M&A", "IoT", "DevOps", "SaaS", "K", "M", "F",
}
SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on",
               "or", "the", "to", "with", "vs"}
# Looked up case-insensitively, but restored in the spelling the world uses:
# "DEVOPS" comes back as "DevOps", "SAAS" as "SaaS".
CANONICAL = {word.upper(): word for word in KEEP_UPPER}


def _titlecase_part(part, first):
    bare = part.strip("()[],.-")
    if not bare:
        return part
    if bare.upper() in CANONICAL:
        return part.replace(bare, CANONICAL[bare.upper()])
    if not first and bare.lower() in SMALL_WORDS:
        return part.lower()
    if any(c.islower() for c in bare):
        return part                      # already mixed case; leave it alone
    return part[:1].upper() + part[1:].lower()


def _titlecase_word(word, first):
    # "AI/ML" and "M/F" are two acronyms wearing one word, so each side of a
    # slash is decided on its own.
    if "/" in word:
        return "/".join(_titlecase_part(p, first and i == 0)
                        for i, p in enumerate(word.split("/")))
    return _titlecase_part(word, first)


def tidy_title(title):
    """Fix a title an employer typed in block capitals, and leave the rest.

    "JUNIOR PRODUCT MANAGER- OBSZAR RX (K/M)" is a real posting title and it
    shouts across a card. Only titles that are overwhelmingly uppercase are
    touched, and known acronyms survive, so "Senior PM, AI Platform" is
    returned exactly as written.
    """
    text = str(title or "").strip()
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8:
        return text
    if sum(1 for c in letters if c.isupper()) / len(letters) < 0.7:
        return text
    words = text.split()
    return " ".join(_titlecase_word(w, i == 0) for i, w in enumerate(words))


def tidy_tag(tag):
    """A chip reads as a label, not as a database value."""
    text = str(tag or "").strip()
    if not text:
        return ""
    if text.upper() in CANONICAL:
        return CANONICAL[text.upper()]
    if any(c.isupper() for c in text):
        return text                      # the board already styled it
    # Tags arrive lowercased by normalisation, so unlike a title they always
    # want capitalising rather than leaving alone.
    out = []
    for i, word in enumerate(text.split()):
        if word.upper() in CANONICAL:
            out.append(CANONICAL[word.upper()])
        elif i and word in SMALL_WORDS:
            out.append(word)
        else:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out)


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
                '<button class="jb-btn primary" id="jb-scan">Scan now</button></div>')
    nxt = (meta or {}).get("nextTier1ScanAt")
    when = f"next run {esc(nxt[11:16])} UTC" if nxt else "next run shortly"
    sweeps = config.get("tier2SweepsPerDay") or 6
    every = config.get("tier1IntervalMinutes") or 60
    return (f'<div class="jb-banner" data-version="{esc(version)}">'
            f'<div><strong>Search v{esc(version)} '
            f'is live.</strong> <span>Every {esc(every)} minutes, plus {esc(sweeps)} '
            f'sweeps a day &middot; {esc(when)}</span>'
            f'<span class="jb-savestate" id="jb-savestate"></span></div>'
            f'<button class="jb-btn primary" id="jb-scan">Scan now</button></div>')


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
      <div class="jb-row jb-basic">
        <input type="search" id="jb-kw" placeholder="Add a keyword and press Enter&hellip;"
               aria-label="Add a keyword">
        <div class="jb-radios">{dates}</div>
        <label class="jb-check"><input type="checkbox" id="jb-direct">
          <span>direct listings only</span></label>
        <select id="jb-sort" aria-label="Sort">
          <option value="direct">Direct first</option>
          <option value="score">Match %</option>
          <option value="new">Newest</option>
        </select>
        <span class="jb-count" id="jb-count"></span>
      </div>
      <div class="jb-pills" id="jb-pills"></div>
      <details class="jb-adv">
        <summary>Advanced search</summary>
        <div class="jb-advbody">
          <div class="jb-row">
            <input type="search" id="jb-ex" placeholder="Hide jobs mentioning&hellip;"
                   aria-label="Hide jobs mentioning">
            <select id="jb-scope" aria-label="Where to search">
              <option value="title+description">Title and description</option>
              <option value="title">Title only</option>
            </select>
            <select id="jb-cmode" aria-label="Companies">
              <option value="all">All companies</option>
              <option value="only">Only these companies</option>
              <option value="hide">Hide these companies</option>
            </select>
            <input type="search" id="jb-companies" placeholder="Company names, comma separated"
                   aria-label="Company names">
          </div>
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
            <span class="jb-note">degree, role and job type are not extracted yet,
              so they filter the unknown bucket honestly</span>
          </div>
        </div>
      </details>
      <div class="jb-row jb-actions">
        <button class="jb-btn primary" id="jb-save">Save search</button>
        <button class="jb-btn" id="jb-clear">Clear</button>
      </div>
    </div>"""


def panel(jobs, total, config, meta):
    """The whole Found tab."""
    if not total:
        return (f'<div class="jb-wide">{banner(config, meta)}{last_ingest_line(meta)}'
                '<p class="empty"><strong>Nothing found yet.</strong>'
                '<span>Save a search and the hourly scan will start filling this. '
                'Scan now runs one immediately.</span></p>'
                f'{add_link_card()}</div>')
    shown = len(jobs)
    footer = ""
    if total > shown:
        footer = (f'<p class="jb-footer">Showing {shown} of {total} &mdash; refine the '
                  f'filters or save a narrower search. The rest stay on disk.</p>')
    return f"""<div class="jb-wide">
    {banner(config, meta)}
    {last_ingest_line(meta)}
    {filter_bar(jobs, config)}
    <div class="jb-grid" id="jb-grid"></div>
    <p class="jb-footer" id="jb-none" hidden>Nothing matches these filters.</p>
    {footer}
    {add_link_card()}
    </div>"""


def add_link_card():
    return """
    <div class="jb-addlink">
      <h4>Found something yourself?</h4>
      <p>Paste a posting and the next sweep starts there.</p>
      <div class="jb-row">
        <input type="url" id="jb-link" placeholder="https://&hellip;" aria-label="Posting link">
        <button class="jb-btn" id="jb-addlink">Add</button>
        <span class="jb-linkmsg" id="jb-linkmsg"></span>
      </div>
    </div>"""


CSS = """
  /* --- Found lane ------------------------------------------------------- */
  /* The lane breaks out of the prose column the way the table lanes do. A
     grid of cards in an 860px column is two per row and a lot of scrolling. */
  .jb-wide { width:min(1800px, calc(100vw - 2.5rem));
    margin-left:calc(50% - min(900px, calc(50vw - 1.25rem)));
    margin-right:calc(50% - min(900px, calc(50vw - 1.25rem))); }

  .jb-banner { display:flex; align-items:center; justify-content:space-between; gap:1rem;
    flex-wrap:wrap; background:var(--card); border-radius:14px; padding:.7rem 1.1rem;
    box-shadow:var(--sh); margin-bottom:.6rem; font-size:.9rem; }
  .jb-banner strong { font-weight:620; }
  .jb-banner span { color:var(--ink-2); }
  .jb-savestate { display:block; font-size:.82rem; margin-top:.15rem; }
  .jb-savestate.waiting { color:var(--ink-3); }
  .jb-savestate.landed { color:var(--green); }
  .jb-savestate.lost { color:var(--orange); }
  .jb-ingest { margin:0 0 .75rem; font-size:.82rem; color:var(--ink-2); line-height:1.5; }
  .jb-ingest.none { color:var(--ink-3); }
  .jb-skipped { color:var(--orange); }
  .jb-errored { color:var(--red-ink); }

  /* Filter bar: one line of the two things anyone actually changes, and the
     rest folded away. It was a wall. */
  .jb-filters { background:var(--card); border-radius:14px; padding:.7rem .9rem;
    box-shadow:var(--sh); margin-bottom:1rem; display:flex; flex-direction:column; gap:.5rem; }
  .jb-row { display:flex; gap:.5rem; flex-wrap:wrap; align-items:center; }
  .jb-basic { gap:.75rem; }
  .jb-row input[type=search], .jb-row input[type=url] { flex:1 1 220px; min-width:170px;
    font:inherit; font-size:.9rem; padding:.42rem .85rem; border-radius:100px;
    border:1px solid var(--hair); background:var(--bg); color:var(--ink); }
  .jb-row select { font:inherit; font-size:.85rem; padding:.38rem .6rem; border-radius:9px;
    border:1px solid var(--hair); background:var(--bg); color:var(--ink); }
  .jb-row select[multiple] { border-radius:9px; min-width:200px; padding:.3rem; }
  .jb-stack { display:flex; flex-direction:column; gap:.35rem; }
  .jb-checks { display:flex; gap:.7rem; flex-wrap:wrap; }
  .jb-radios { display:flex; gap:.1rem; background:var(--sunk); border-radius:100px; padding:2px; }
  .jb-radio { display:inline-flex; align-items:center; cursor:pointer; }
  .jb-radio input { position:absolute; opacity:0; width:0; height:0; }
  .jb-radio span { display:inline-block; font-size:.8rem; color:var(--ink-2);
    padding:.28rem .65rem; border-radius:100px; white-space:nowrap; }
  .jb-radio input:checked + span { background:var(--card); color:var(--ink); font-weight:560;
    box-shadow:0 1px 2px rgba(0,0,0,.12); }
  .jb-radio input:focus-visible + span { outline:2px solid var(--blue); outline-offset:1px; }
  .jb-check { display:inline-flex; align-items:center; gap:.3rem; font-size:.82rem;
    color:var(--ink-2); cursor:pointer; }
  .jb-lab { display:inline-flex; flex-direction:column; gap:.15rem; font-size:.72rem;
    color:var(--ink-3); }
  .jb-note { font-size:.75rem; color:var(--ink-3); }
  .jb-count { margin-left:auto; font-size:.82rem; color:var(--ink-3);
    font-variant-numeric:tabular-nums; white-space:nowrap; }
  .jb-adv > summary { cursor:pointer; font-size:.82rem; color:var(--blue);
    list-style:none; padding:.15rem 0; width:max-content; }
  .jb-adv > summary::-webkit-details-marker { display:none; }
  .jb-adv > summary::before { content:"\25B8  "; }
  .jb-adv[open] > summary::before { content:"\25BE  "; }
  .jb-adv > summary:focus-visible { outline:2px solid var(--blue); outline-offset:3px; }
  .jb-advbody { display:flex; flex-direction:column; gap:.5rem; padding-top:.5rem;
    margin-top:.35rem; border-top:1px solid var(--hair); }
  .jb-pills { display:flex; gap:.35rem; flex-wrap:wrap; }
  .jb-pills:empty { display:none; }
  .jb-pill { display:inline-flex; align-items:center; gap:.3rem; font-size:.78rem;
    background:var(--sunk); color:var(--ink); border-radius:100px;
    padding:.2rem .5rem .2rem .7rem; }
  .jb-pill button { border:none; background:none; color:var(--ink-3); cursor:pointer;
    font:inherit; line-height:1; padding:0 .1rem; }
  .jb-pill button:hover { color:var(--red-ink); }

  /* One button shape everywhere on this lane. */
  .jb-btn { font:inherit; font-size:.845rem; font-weight:530; line-height:1;
    padding:.5rem .9rem; border-radius:100px; cursor:pointer;
    border:1px solid var(--hair); background:var(--bg); color:var(--ink);
    display:inline-flex; align-items:center; gap:.32rem; text-decoration:none;
    transition:border-color .18s, color .18s, background .18s; }
  .jb-btn:hover { border-color:var(--ink-3); }
  .jb-btn:active { transform:scale(.97); }
  .jb-btn:focus-visible { outline:2px solid var(--blue); outline-offset:2px; }
  .jb-btn[disabled] { opacity:.5; cursor:default; }
  .jb-btn.primary { background:var(--btn-g); border-color:var(--btn-g);
    color:var(--btn-g-ink); }
  .jb-btn.on { background:var(--t-g); border-color:var(--green); color:var(--green); }
  .jb-btn.danger:hover { border-color:var(--red-ink); color:var(--red-ink); }
  .jb-btn svg { width:.95rem; height:.95rem; display:block; }

  /* Four to a row on a wide screen, and only four. Five made the cards narrow
     enough that every title wrapped to three lines. */
  .jb-grid { display:grid; gap:1.15rem;
    grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); }
  @media (min-width:1180px) { .jb-grid { grid-template-columns:repeat(4, 1fr); } }
  .jb-card { background:var(--card); border-radius:20px; padding:1.3rem 1.4rem 1.1rem;
    box-shadow:var(--sh); display:flex; flex-direction:column; gap:.8rem;
    border:1px solid transparent; transition:border-color .18s, transform .18s; }
  .jb-card:hover { border-color:var(--hair); transform:translateY(-1px); }
  .jb-head { display:flex; gap:1rem; align-items:flex-start; }
  .jb-ring { width:56px; height:56px; border-radius:50%; flex:none; display:grid;
    place-items:center; background:var(--sunk); }
  .jb-ring i { width:44px; height:44px; border-radius:50%; background:var(--card);
    display:grid; place-items:center; font-style:normal; font-size:.9rem; font-weight:660;
    font-variant-numeric:tabular-nums; color:var(--ink); letter-spacing:-.03em; }
  .jb-co { font-size:.82rem; color:var(--ink-2); margin:0; font-weight:540;
    letter-spacing:-.01em; }
  .jb-ti { font-size:1.06rem; font-weight:620; letter-spacing:-.025em; margin:.15rem 0 0;
    line-height:1.28; }
  .jb-sum { margin:0; font-size:.845rem; line-height:1.5; color:var(--ink-2);
    display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
    overflow:hidden; }
  .jb-chips { display:flex; gap:.35rem; flex-wrap:wrap; }
  .jb-chip { font-size:.735rem; font-weight:550; padding:.22rem .6rem; border-radius:100px;
    background:var(--sunk); color:var(--ink-2); white-space:nowrap;
    letter-spacing:-.005em; }
  .jb-chip.place { background:var(--t-p); color:var(--purple); }
  .jb-chip.pay { background:var(--t-g); color:var(--green); }
  .jb-chip.unknown { background:transparent; color:var(--ink-3);
    border:1px dashed var(--hair); }
  .jb-chip.gate { background:var(--t-red); color:var(--red-ink); }
  .jb-chip.boards { background:var(--t-g); color:var(--green); }
  .jb-chip.years { background:var(--t-o); color:var(--orange); }
  .jb-chip.direct { background:var(--t-p); color:var(--purple); font-weight:590; }
  .jb-acts { display:flex; gap:.45rem; align-items:center; flex-wrap:wrap; margin-top:auto;
    padding-top:.9rem; border-top:1px solid var(--hair); }
  .jb-acts .jb-det { margin-left:auto; }
  .jb-panel { border-top:1px solid var(--hair); padding-top:.6rem; font-size:.83rem;
    color:var(--ink-2); display:flex; flex-direction:column; gap:.45rem; }
  .jb-bars { display:flex; flex-direction:column; gap:.22rem; }
  .jb-bar { display:grid; grid-template-columns:5.2rem 1fr 2.4rem; align-items:center;
    gap:.5rem; font-size:.75rem; }
  .jb-bar u { text-decoration:none; color:var(--ink-3); }
  .jb-bar span { height:5px; border-radius:100px; background:var(--sunk); display:block; }
  .jb-bar span i { display:block; height:100%; border-radius:100px; background:var(--blue); }
  .jb-bar em { font-style:normal; text-align:right; color:var(--ink-3);
    font-variant-numeric:tabular-nums; }
  .jb-sight { display:flex; gap:.5rem; flex-wrap:wrap; align-items:center; }
  .jb-excerpt { margin:0; line-height:1.45; }
  .jb-footer { text-align:center; color:var(--ink-3); font-size:.85rem; margin:1.1rem 0 0; }
  .jb-addlink { background:var(--card); border-radius:14px; padding:.85rem 1.1rem;
    box-shadow:var(--sh); margin-top:1.2rem; }
  .jb-addlink h4 { margin:0 0 .15rem; font-size:.92rem; font-weight:620; }
  .jb-addlink p { margin:0 0 .5rem; font-size:.84rem; color:var(--ink-2); }
  .jb-linkmsg { font-size:.82rem; color:var(--ink-2); }
  .jb-linkmsg.err { color:var(--orange); }
  .jb-linkmsg.ok { color:var(--green); }
  @media (max-width:900px) {
    .jb-wide { width:auto; margin-left:0; margin-right:0; }
    .jb-grid { grid-template-columns:1fr; }
    .jb-count { margin-left:0; }
  }
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

  // One drawn icon per action. Five identical pills reading Watch / Apply /
  // Pass / Posting / Details were hard to tell apart at a glance, and Details
  // in particular read as an afterthought.
  function svg(paths, extra) {
    var node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    node.setAttribute('viewBox', '0 0 24 24');
    node.setAttribute('fill', 'none');
    node.setAttribute('stroke', 'currentColor');
    node.setAttribute('stroke-width', '1.8');
    node.setAttribute('stroke-linecap', 'round');
    node.setAttribute('stroke-linejoin', 'round');
    node.setAttribute('aria-hidden', 'true');
    paths.forEach(function (d) {
      var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', d);
      node.appendChild(path);
    });
    (extra || []).forEach(function (c) {
      var circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      circle.setAttribute('cx', c[0]); circle.setAttribute('cy', c[1]);
      circle.setAttribute('r', c[2]);
      node.appendChild(circle);
    });
    return node;
  }
  var ICONS = {
    // An eye, drawn. The emoji renders as a photoreal eyeball on macOS.
    watch: function () {
      return svg(['M1.8 12S5.4 5.2 12 5.2 22.2 12 22.2 12 18.6 18.8 12 18.8 1.8 12 1.8 12Z'],
                 [[12, 12, 3.1]]);
    },
    // A paper plane: this one sends something.
    apply: function () { return svg(['M21.5 2.5 11 13', 'M21.5 2.5 15 21.5l-4-8.5-8.5-4Z']); },
    // A struck-through circle: no, and not deleted.
    pass: function () { return svg(['M5.6 5.6l12.8 12.8'], [[12, 12, 8.4]]); },
    // Arrow out of a box: this leaves the board.
    posting: function () {
      return svg(['M14 4h6v6', 'M20 4 11 13', 'M18 14.5V19a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 19V8a1.5 1.5 0 0 1 1.5-1.5H10']);
    },
    // A chevron that turns over, so Details reads as "there is more below".
    details: function () { return svg(['M6 9.5 12 15.5 18 9.5']); },
  };
  function button(cls, label, icon) {
    var b = el('button', 'jb-btn' + (cls ? ' ' + cls : ''));
    if (icon) b.appendChild(icon);
    b.appendChild(el('span', null, label));
    return b;
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

    if (job.sum) article.appendChild(el('p', 'jb-sum', job.sum));

    // Chips carry what the posting actually said, and say plainly when it said
    // nothing. A row of "unknown" is honest; a row of invented detail is not.
    var chips = el('div', 'jb-chips');
    if (job.place) chips.appendChild(chip(job.place, 'place'));
    if (job.wp) chips.appendChild(chip(job.wp));
    if (job.yrt) chips.appendChild(chip(job.yrt, 'years'));
    else chips.appendChild(chip('Experience not stated', 'unknown'));
    var when = ago(job);
    if (when) chips.appendChild(chip(when));
    else chips.appendChild(chip('No date', 'unknown'));
    if (job.sal) chips.appendChild(chip(job.sal, 'pay'));
    if (job.bd > 1) chips.appendChild(chip('On ' + job.bd + ' boards', 'boards'));
    chips.appendChild(chip(job.src || 'Unknown source',
                           job.rank <= 1 ? 'direct' : ''));
    (job.ga || []).forEach(function (label) { chips.appendChild(chip(label, 'gate')); });
    (job.tg || []).slice(0, 3).forEach(function (tag) { chips.appendChild(chip(tag)); });
    article.appendChild(chips);

    var acts = el('div', 'jb-acts');
    var eye = button('', 'Watch', ICONS.watch());
    eye.title = 'Move this to Watching';
    eye.addEventListener('click', function () { doWatch(job, article, eye); });
    acts.appendChild(eye);

    var apply = button('primary', 'Apply', ICONS.apply());
    apply.title = 'Queue the full application';
    apply.addEventListener('click', function () { doApply(job, article, apply); });
    acts.appendChild(apply);

    var pass = button('danger', 'Pass', ICONS.pass());
    pass.title = 'Never offer this one again';
    pass.addEventListener('click', function () { doPass(job, article); });
    acts.appendChild(pass);

    if (job.url) {
      var open = el('a', 'jb-btn');
      open.href = job.url; open.target = '_blank'; open.rel = 'noopener';
      open.title = 'Open the posting on ' + (job.src || 'its board');
      open.appendChild(ICONS.posting());
      open.appendChild(el('span', null, 'Posting'));
      acts.appendChild(open);
    }

    var details = button('jb-det', 'Details', ICONS.details());
    details.title = 'Why this percentage, and where else it was seen';
    var panel = null;
    details.addEventListener('click', function () {
      if (panel) {
        panel.remove(); panel = null;
        details.querySelector('span').textContent = 'Details';
        return;
      }
      panel = detailPanel(job);
      article.appendChild(panel);
      details.querySelector('span').textContent = 'Hide';
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
  function doWatch(job, article, btn) {
    btn.classList.add('on');
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
  function doApply(job, article, btn) {
    btn.disabled = true;
    var label = btn.querySelector('span');
    if (label) label.textContent = 'Queueing\u2026';
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
    if (checked('jb-direct') && job.rank > 1) return false;
    if (value('jb-role') && job.rc !== value('jb-role')) { if (job.rc) return false; }
    if (value('jb-emp') && job.et !== value('jb-emp')) { if (job.et) return false; }
    return true;
  }

  function render() {
    var rows = JOBS.filter(matches);
    var how = value('jb-sort') || 'direct';
    if (how === 'new') {
      rows = rows.slice().sort(function (a, b) {
        return String(b.pa || b.fs || '').localeCompare(String(a.pa || a.fs || ''));
      });
    } else if (how === 'direct') {
      // Direct listings first, best match within each group. Applying through
      // the employer's own ATS beats applying through somebody's redirect, so
      // a 70% Greenhouse posting outranks a 70% aggregator copy of it.
      rows = rows.slice().sort(function (a, b) {
        var grouped = (a.rank > 1 ? 1 : 0) - (b.rank > 1 ? 1 : 0);
        return grouped || (b.sc || 0) - (a.sc || 0);
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
   'jb-exp','jb-deg','jb-role','jb-emp','jb-jt','jb-direct'].forEach(function (id) {
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
      var direct = document.getElementById('jb-direct');
      if (direct) direct.checked = false;
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
