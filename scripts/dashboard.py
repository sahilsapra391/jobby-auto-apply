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
import re
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

# Importable whether this is run as a script from the repo root or loaded by
# path (which the tests do), where scripts/ would not be on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import brand  # noqa: E402
import clock  # noqa: E402
import answer_record  # noqa: E402
import company_logos  # noqa: E402
import found_lane  # noqa: E402
import premium_ui  # noqa: E402
import profile_panel  # noqa: E402
import settings_panel  # noqa: E402
import state_slim  # noqa: E402
import store  # noqa: E402


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


def load_state(root, apps_root=None):
    """The store, plus which applications have an answer record.

    `apps_root` is the raw --root override rather than the resolved store path,
    because that is exactly what publish.py hands r2_artifacts. Passing the
    resolved path instead would send this to <store>/applications on a machine
    whose documents are in REPO/applications, and every Submitted row would read
    "not captured" while the records sat there.
    """
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

    meta = read_json(root / "meta.json", {})
    # Which applications have an answer record, decided by a directory scan here
    # and carried in the state as ids. The records themselves are fetched when a
    # modal opens; the deployed renderer has no applications/ folder to scan, so
    # without this every Submitted row would read "not captured" on the board
    # while reading correctly on the laptop.
    if isinstance(meta, dict):
        meta["answersCaptured"] = sorted(answer_record.captured_ids(apps_root))

    return {
        "profile": read_json(root / "profile.json", {}),
        "sessions": sessions,
        "history": list(latest.values()),
        "roles": read_json(root / "roles.json", {}),
        "meta": meta,
        # The discovery pool. A corrupt file here must not take the whole board
        # down, but it must not silently render as an empty Found lane either:
        # the last-ingest line says when the last scan ran regardless.
        "jobs": read_json(root / "jobs.json", {}),
        "searchConfig": read_json(root / "search-config.json", None),
        # Connection metadata only. Provider CLIs retain all account tokens in
        # their own local stores, so nothing credential-shaped reaches HTML.
        "providers": read_json(root / "providers.json", {}),
        # The Memory Bank and the base resume wording. Neither is rendered into
        # the page: they travel in the published state and the Profile panel
        # fetches them. See publishable_state for why that is not optional.
        "answers": read_json(root / "answers.json", {}),
        "resumeContent": store.resume_content(root),
    }


SESSION_PUBLIC_FIELDS = {
    "alignment", "applicationId", "applyUrl", "ats", "blockerNote", "company",
    "comp", "coreSkills", "coverLetter", "datePosted", "discipline",
    "droppedAt", "droppedFrom", "droppedReason", "droppedStage", "fitScore",
    "foundAt", "jobNumber", "linkedinUrl", "location", "note", "pendingFields",
    "parkClass", "pendingNote", "phase", "phaseAt", "phaseDetail",
    "postedAgo", "postedDate",
    "postedDays", "postingUrl", "previousStatus", "promotedFrom", "realLocation",
    "region", "requisition", "resumeVariant", "role", "seenOnBoards", "source",
    "sponsorshipStance", "status", "step", "sweptAt", "updatedAt", "url",
    "verifiedLive", "workSite", "yearsMin", "yearsText",
}
HISTORY_PUBLIC_FIELDS = {
    "applicationId", "at", "company", "confirmation", "coverLetter",
    "event", "resumeVariant", "role", "url",
}
ROLE_PUBLIC_FIELDS = {
    "applicationId", "ats", "canonicalUrl", "company", "companyWebsite",
    "confirmation", "confirmedAt", "coverLetter", "logoUrl", "resumeVariant",
    "region", "role", "source", "url",
}


def _public_records(records, fields):
    """Keep only values that the password-gated board renderer consumes."""
    return [
        {key: value for key, value in record.items() if key in fields}
        for record in records
        if isinstance(record, dict)
    ]


# The account this board belongs to. One today, and the payload says which so
# the API can refuse to serve a board published by a different account rather
# than trusting that there is only ever one. §8 of the multi-tenant plan puts a
# user_id on every per-user row; this is that column, arriving early where it
# costs nothing.
USER_ID = os.environ.get("JOBBY_USER_ID", "account-1")

# What an identity is, per §8: one right to work, its own contact block, its own
# base document, exactly one marked default. `profile.resumes[]` already holds
# this, so the published shape mirrors the eventual table rather than inventing
# a second vocabulary for the same thing.
def _identities(profile):
    out = []
    for entry in (profile.get("resumes") or []):
        if not isinstance(entry, dict):
            continue
        identity = entry.get("identity") or {}
        address = identity.get("fullAddress") or {}
        out.append({
            "key": entry.get("key"),
            "countryScope": [str(a).upper() for a in (entry.get("appliesTo") or [])],
            "isDefault": bool(entry.get("default")),
            "contact": {
                "location": identity.get("location"),
                "phone": identity.get("phone"),
                "oneLine": address.get("oneLine"),
                "country": address.get("country"),
            },
            # The name only. The path is a directory layout on one person's
            # machine and says nothing a reader of the board needs.
            "resumeRef": PurePosixPath(str(entry.get("path") or "")).name or None,
        })
    return out


def _memory_bank(state):
    """The Profile page's data, as the API will hand it out.

    Not page material, and that is measured rather than assumed: the board at
    its embed cap is 3.65 MB against a 3.7 MB budget, and the bank is 74 KB. It
    would not fit even if nothing else were true.

    **Every answer travels, including the sensitive ones.** This reverses the
    withholding shipped in #73, on the owner's instruction after seeing it live:
    he does not want a hidden category on his own board, and it is his data and
    his board. So all 156 answers are published and rendered in plain text.

    Three things this does not change, because they are different mechanisms
    that a display decision has no business touching:

    - The `sensitive` flag stays on every record it was on. It is what the
      multi-tenant envelope-encryption path selects on, and issue #82's gate
      stands: that work closes before a second account exists.
    - Secrets are still never captured at all. One-time codes, passwords and
      identifiers are refused at the door in `store.py` and never written, so
      there is nothing here to publish in the first place. Non-capture is not
      the same mechanism as non-display and is untouched.
    - **This reverses on multi-tenancy.** A single user reading their own
      compensation on their own password-gated board is a different question
      from a store holding other people's. When account two exists this goes
      back, which is what #82 is for. Recorded here so the next reader knows it
      was a decision rather than an oversight.
    """
    answers = state.get("answers") or {}
    entries = []
    for key, entry in answers.items():
        if not isinstance(entry, dict):
            continue
        record = dict(entry)
        record["key"] = record.get("key") or key
        # `answer` is where this field started and four real entries still use
        # it. Normalise once, here, on the machine that owns the store.
        #
        # It was normalised in the API route instead, and that put "is this
        # answered" in three places in two languages: store.answered(), the
        # route, and the browser. Measured on 2026-08-25 against the real
        # store, the browser then counted four answered entries as open and
        # badged the nav with a 4 that nothing could ever clear. One
        # normalisation, before anything reads it.
        if not record.get("value") and record.get("answer"):
            record["value"] = record.pop("answer")
        # `sensitive` and `dataClass` still travel. They are the classification
        # the encryption path selects on; they simply no longer change what the
        # page draws.
        store.classify(record)
        entries.append(record)
    entries.sort(key=lambda e: str(e.get("question") or "").lower())
    return {
        "userId": USER_ID,
        "answers": entries,
        "identities": _identities(state.get("profile") or {}),
        "resumeContent": state.get("resumeContent") or {},
    }


def bank_counts(bank):
    """The three numbers the board is allowed to render: counts, never text."""
    entries = bank.get("answers") or []
    return {
        "total": len(entries),
        "unanswered": sum(1 for e in entries
                          if not store.answered(e)
                          and e.get("remembered") is not False),
        "sensitive": sum(1 for e in entries if e.get("sensitive")),
        "identities": len(bank.get("identities") or []),
    }


def publishable_state(state):
    """Return the minimum renderer input safe to persist as board state.

    The deployed UI renders this payload with the renderer from its own Git
    commit. KV therefore carries changing application facts, never executable
    HTML or provider credentials. Job discovery records are already a public
    posting projection and retain their full shape so filter and scoring logic
    stays identical between local and deployed renders.
    """
    roles = state.get("roles") or {}
    meta = dict(state.get("meta") or {})
    jobs, pool_total, pool_census = publishable_jobs(
        state.get("jobs") or {}, state.get("searchConfig"),
        cap=state.get("_embedCap"))
    meta["poolTotal"] = pool_total
    meta["poolCensus"] = pool_census
    bank = _memory_bank(state)
    meta["memoryBank"] = bank_counts(bank)
    return {
        "userId": USER_ID,
        "profile": {"firstName": (state.get("profile") or {}).get("firstName")},
        # Served by /api/profile, never rendered. Kept a sibling of the render
        # input rather than a field inside it, so "the renderer must not touch
        # this" is visible in the shape and not only in a comment.
        "memoryBank": bank,
        "sessions": _public_records(state.get("sessions") or [], SESSION_PUBLIC_FIELDS),
        "history": _public_records(state.get("history") or [], HISTORY_PUBLIC_FIELDS),
        "roles": {
            str(key): {field: value for field, value in record.items()
                       if field in ROLE_PUBLIC_FIELDS}
            for key, record in roles.items()
            if isinstance(record, dict)
        },
        "meta": meta,
        # Slimmed, not whole. The records keep every field the deployed
        # renderer reads, so filter and scoring stay identical, and lose the
        # ones nothing draws. That was 20.66 MB of jobs against a 20 MB
        # ceiling, and the ceiling won for 22 hours. See scripts/state_slim.py.
        "jobs": state_slim.slim_jobs(jobs),
        "searchConfig": state.get("searchConfig"),
        "providers": settings_panel.public_payload(state.get("providers") or {}),
    }


# What an expanded card actually draws. found_lane renders five points a column
# and nothing reads past them, so publishing more is bytes the reader discards.
PUBLISHED_SECTION_POINTS = 5
# 200 keeps 79.5% of real bullets whole (measured over 94,438 of them: mean 157,
# p90 279). It is chosen against the synthetic worst case rather than the mean,
# because that is what the ceiling has to survive: 3,400 records of five full
# length bullets a column is 17.5 MB, against 20.7 at 300 characters.
PUBLISHED_SECTION_CHARS = 200


def _trim_sections(job):
    """One record with its sections cut to what the board will show.

    The store keeps the whole parse, because a later reader may want it. The
    published state cannot: sections reached 9.38 MB of a 21.03 MB state after
    the full-body backfill, and the deployed reader stops at 20 MB, so every
    publish failed and the board froze for hours. Trimmed to what the panel
    draws, the same state is 16.2 MB.
    """
    sections = job.get("sections")
    if not isinstance(sections, dict):
        return job
    cut = {}
    for name in ("responsibilities", "requirements"):
        lines = sections.get(name)
        if isinstance(lines, list) and lines:
            cut[name] = [str(line)[:PUBLISHED_SECTION_CHARS]
                         for line in lines[:PUBLISHED_SECTION_POINTS]]
    if not cut:
        return job
    trimmed = dict(job)
    trimmed["sections"] = cut
    return trimmed


def publishable_jobs(jobs, config, cap=None):
    """(jobs, total, census) — the slice the board can draw, and the pool behind it.

    The store holds tens of thousands of postings and KV will not: the envelope
    has to decompress under MAX_STATE_BYTES, and Cloudflare refuses a value
    over 25 MB. found_lane embeds at most EMBED_CAP of them in the page in any
    case, so publishing the whole pool meant shipping tens of megabytes to be
    discarded at render. At 2.8 KB a record a fifty-thousand-job store is about
    134 MB of state, which is not a tuning problem, it is four times the
    ceiling.

    So the selection the renderer would have made runs here instead, and the
    true total and per-country census travel beside it. panel() and payload()
    already take those as separate arguments, because the footer has always
    been meant to say how much of the pool you are looking at rather than
    quoting the size of the slice back at you.

    Records the user acted on ride along whatever their state. There are a
    couple of hundred, they never age out, and they are where Watching and
    Submitted find their company marks, so dropping them would blank a logo.
    """
    selected, total = found_lane.select(jobs, config, cap=cap)
    census = found_lane.pool_census(jobs)
    keep = {str(job.get("id") or "") for job in selected}
    # Filtered in the pool's own order rather than rebuilt selection-first.
    # build() reads company marks by walking jobs and taking the first record
    # it sees for each employer, so the iteration order decides which posting
    # supplies each logo. Reordering here silently moved Datadog's mark from
    # careers.datadoghq.com to learn.datadoghq.com and Duolingo's to a blog
    # subdomain: the same board, drawn with different marks, for no reason a
    # reader could see.
    out = {}
    for job_id, job in (jobs or {}).items():
        key = str(job_id)
        if (job.get("state") or "new") != "new" or key in keep:
            out[key] = _trim_sections(job)
    return out, total, census


def esc(v):
    return html.escape(str(v if v is not None else ""))


def safe_url(url):
    """URLs come from scraped pages; only http(s) may become a clickable link."""
    u = str(url or "").strip()
    if (
        not u
        or any(character.isspace() or ord(character) < 32 for character in u)
        or any(character in u for character in "\\<>\"'")
    ):
        return ""
    try:
        parsed = urllib.parse.urlsplit(u)
        _ = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return urllib.parse.urlunsplit(parsed._replace(scheme=parsed.scheme.lower()))


def link(url, label="Open"):
    url = safe_url(url)
    if not url:
        return '<span class="muted">no link</span>'
    return f'<a class="go" href="{esc(url)}" target="_blank" rel="noopener">{esc(label)} &nearr;</a>'


def render_pending(fields):
    if not fields:
        return '<li class="done">Nothing outstanding. Ready to submit.</li>'
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
    jobs = state.get("jobs") or {}
    logo_jobs = {}
    for job in jobs.values():
        company = str(job.get("company") or "").strip().casefold()
        if company and company not in logo_jobs:
            logo_jobs[company] = job

    def logo_entry(entry):
        """Combine durable role and discovery metadata for one company mark."""
        combined = {}
        company = str(entry.get("company") or "").strip().casefold()
        if company in logo_jobs:
            combined.update(logo_jobs[company])
        aid = entry.get("applicationId")
        if aid and isinstance(roles.get(aid), dict):
            combined.update(roles[aid])
        combined.update(entry)
        return combined

    def company_mark(entry):
        return company_logos.mark(
            entry.get("company") or "",
            logo_entry(entry),
            posting_url(entry),
        )

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

    search_config_saved = state.get("searchConfig")
    found, found_total = found_lane.select(state.get("jobs") or {}, search_config_saved)
    found_census = found_lane.pool_census(state.get("jobs") or {})
    # A published state carries the slice the board can draw plus the size of
    # the pool behind it, because the whole pool no longer fits in KV. Prefer
    # the carried numbers: the Found count and the location picker are counting
    # the pool, and recomputing them from the slice would quote the slice back.
    # Larger-only, so a stale or truncated field can never shrink a real count.
    carried_total = (state.get("meta") or {}).get("poolTotal")
    if isinstance(carried_total, int) and carried_total > found_total:
        found_total = carried_total
    carried_census = (state.get("meta") or {}).get("poolCensus")
    if isinstance(carried_census, dict) and carried_census:
        found_census = carried_census

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
    # Written by load_state on the laptop, because the deployed renderer has no
    # applications/ folder to scan. Ids only; the records are fetched on open.
    captured = {
        str(aid) for aid in ((state.get("meta") or {}).get("answersCaptured") or [])
        if isinstance(aid, str) and aid
    }
    uncaptured = any(str(e.get("applicationId") or "") not in captured
                     for e in submitted)
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

    # Counts, and only counts. The Profile page's data is fetched, not embedded;
    # a number is the one thing about the Memory Bank that is cheap enough to
    # render and safe enough to render. An outstanding question is the only
    # thing in there that is costing the user anything, so it gets the badge.
    bank_counts_public = (state.get("meta") or {}).get("memoryBank") or {}
    open_questions = int(bank_counts_public.get("unanswered") or 0)
    profile_badge = (
        f'<span class="nav-n" data-profile-open>{open_questions}</span>'
        if open_questions else '<span class="nav-n" data-profile-open hidden>0</span>'
    )

    cards = []
    for s in blocked:
        pending = s.get("pendingFields", [])
        aid = s.get("applicationId") or f"{s.get('company','')}-{s.get('role','')}"
        # A park because the agent had no browser and a park on a real blocker
        # both land here looking like work for the user. One of them is not.
        capability = s.get("parkClass") == "capability"
        badge = "ready" if not pending else f"{len(pending)} to do"
        cls = "ready" if not pending else "hold"
        chip = "ready" if not pending else "hold"
        if capability:
            badge, cls, chip = "system fault", "fault", "fault"
        hay = " ".join(str(x) for x in [
            s.get("company"), s.get("role"), s.get("ats"),
            s.get("location") or s.get("realLocation"),
            " ".join(f.get("question", "") for f in pending)] if x)
        cards.append(f"""
        <article class="card {cls}" data-id="{esc(aid)}" data-hay="{esc(hay)}" data-state="{chip}"
                 data-company="{esc(s.get('company',''))}" data-role="{esc(s.get('role',''))}"
                 data-url="{esc(safe_url(posting_url(s)))}">
          <header>
            <div class="company-title">{company_mark(s)}<div>
              <h3>{esc(s.get('company', 'Unknown'))}</h3>
              <p class="role">{esc(s.get('role', ''))}</p></div>
            </div>
            <span class="chip {chip}">{esc(badge)}</span>
          </header>
          {'<p class="fault-note">Nothing was filled: the agent started without '
           'browser tools. This is a fault in Jobby, not a step for you. '
           'Retry once the browser server is healthy.</p>' if capability else ''}
          <ul class="todo">{render_pending(pending)}</ul>
          <p class="stub">&#10003; Marked submitted <button class="undo" data-id="{esc(aid)}">Undo</button></p>
          <div class="act">
            <button class="btn retry" data-id="{esc(aid)}"
                    data-company="{esc(s.get('company',''))}"
                    data-role="{esc(s.get('role',''))}"
                    title="I have done the blocked step; let the agent finish">Retry</button>
            <button class="btn ghost finish" data-id="{esc(aid)}"
                    data-url="{esc(safe_url(posting_url(s)))}"
                    title="Bring up the tab the agent left, with the form still filled">Open &amp; finish</button>
            <button class="btn mark" data-id="{esc(aid)}"
                    title="I submitted this myself. Move it to Submitted.">Confirm submitted</button>
            <button class="btn drop" data-id="{esc(aid)}" title="Do not pursue this one">Drop</button>
            <span class="ats">{esc(s.get('ats', ''))}</span>
          </div>
        </article>""")

    def flight_card(entry):
        phase = entry.get("phase") or "Queued"
        detail = entry.get("phaseDetail") or ""
        at = (entry.get("phaseAt") or "")[11:19]
        # Five, not six. "Documents ready" was a dot that no run reports any
        # more: the phases a run writes are set by brief.py, the two calls in
        # the prompt, and finish.py, and that is not one of them. A dot that
        # only ever lights up on its way past is a step the reader cannot use.
        steps = ["Queued", "Building documents",
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
            <div class="company-title">{company_mark(entry)}<div>
              <h3>{esc(entry.get('company', ''))}</h3>
              <p class="role">{esc(entry.get('role', ''))}</p></div>
            </div>
            <span class="chip warm">in flight</span>
          </header>
          <div class="steps">{dots}</div>
          <p class="phase">{esc(phase)}{' <span class="why">' + esc(detail) + '</span>' if detail else ''}</p>
          <div class="act">
            <a class="btn ghost" href="{esc(safe_url(posting_url(entry)))}" target="_blank"
               rel="noopener">Posting &nearr;</a>
            <button class="btn retry" data-id="{esc(entry.get('applicationId',''))}"
                    data-company="{esc(entry.get('company',''))}"
                    data-role="{esc(entry.get('role',''))}"
                    title="Start this one again">Retry</button>
            <button class="btn drop" data-id="{esc(entry.get('applicationId',''))}"
                    title="Stop the agent and drop this application">Stop</button>
            <span class="ats">{esc(clock.clock_label(at))}</span>
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
        """A link when a real file went out, an honest note when none did.

        Link by basename. Runs record this either way, some as
        "Sahil_Sapra_Cohere_Resume.pdf" and some as
        "applications/Cohere/Sahil_Sapra_Cohere_Resume.pdf". Documents are
        published under the basename and the route rejects a name containing
        slashes, so a path-recorded document produced a link that resolved to
        "No such document" while the file itself was published and fine.
        """
        v = (value or "").strip()
        if v.endswith(".pdf"):
            from urllib.parse import quote
            return (f'<a class="go" href="/doc/{quote(PurePosixPath(v).name)}" '
                    f'target="_blank" rel="noopener">PDF &nearr;</a>')
        if not v or v == "-":
            return '<span class="muted">Not available</span>'
        # e.g. "not accepted by this form", "pasted as text into Additional Information"
        short = "sent as text" if "text" in v.lower() else "not requested"
        return f'<span class="muted" title="{esc(v)}">{esc(short)}</span>'

    def answers_cell(e):
        """The form's own questions, or an honest note that nobody wrote them down.

        Capture began on this branch, so every application submitted before it
        has no record and never will: reconstructing what a form asked months
        ago would be inventing an audit trail. "not captured" says exactly that
        rather than opening an empty modal, which reads as a form with no
        questions and is the one wrong answer available here.
        """
        aid = str(e.get("applicationId") or "")
        if not aid or aid not in captured:
            # Bare, with the explanation in the panel hint above rather than in
            # a title attribute. The same sentence repeated on a hundred rows
            # was 13 KB of page for one fact.
            return '<span class="muted">not captured</span>'
        return (f'<button class="btn ghost answers-open" type="button" '
                f'data-id="{esc(aid)}" '
                f'data-company="{esc(e.get("company") or "")}" '
                f'data-role="{esc(e.get("role") or "")}" '
                f'aria-haspopup="dialog" aria-controls="p-answers">Answers</button>')

    def sub_row(e):
        cls, label = outcome_chip.get(e.get("event"), ("", e.get("event")))
        hay = " ".join(str(x) for x in [e.get('company'), e.get('role'), label] if x)
        return (f"<tr data-hay=\"{esc(hay)}\" data-state=\"{esc(e.get('event') or '')}\">"
                f"<td><div class='company-cell'>{company_mark(e)}<span>{esc(e.get('company'))}</span></div></td>"
                f"<td>{esc(e.get('role'))}</td>"
                f"<td><span class='chip {cls}'>{esc(label)}</span></td>"
                f"<td class='when'>{esc(clock.date_label(e.get('at')))}</td>"
                f"<td>{link(posting_url(e), 'Posting')}</td>"
                f"<td>{proof_cell(e)}</td>"
                f"<td>{doc_cell(e.get('resumeVariant'))}</td>"
                f"<td>{doc_cell(e.get('coverLetter'))}</td>"
                f"<td>{answers_cell(e)}</td></tr>")

    sub_rows = "\n".join(
        sub_row(e) for e in sorted(submitted, key=lambda x: x.get("at", ""), reverse=True)
    ) or "<tr><td colspan='9' class='muted'>Nothing submitted yet</td></tr>"

    unconfirmed = "".join(
        f"<li><strong>{esc(c)}</strong><span class=\"ui-separator\">{esc(r)}</span>"
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

    # Sweeps record this as free text, and every sweep words it differently:
    # "~23 hours", "~20-21 hours", "LinkedIn feed said 3 hours", "22 days
    # (released)", "~2-3 hours (2026-08-19)", "not shown". Requiring the word
    # "ago" matched almost none of it, so the freshest roles had no sortable
    # value and sank to the bottom of a list ordered newest first.
    ISO_DATE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
    RELATIVE = re.compile(
        r"(\d+)\s*(?:[-\u2013]\s*\d+\s*)?\+?\s*"
        r"(minute|min|hour|hr|day|week|month|year)s?", re.I)
    PER_DAY = {"minute": 1 / 1440, "min": 1 / 1440, "hour": 1 / 24, "hr": 1 / 24,
               "day": 1.0, "week": 7.0, "month": 30.0, "year": 365.0}

    def _scan_time(entry):
        """When the sweep saw this. A relative note is relative to THAT moment,
        not to now: "2 hours" recorded yesterday means yesterday, and measuring
        it from now would make a day-old posting look brand new."""
        stamp = entry.get("foundAt") or entry.get("updatedAt") or ""
        try:
            return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.now(timezone.utc)

    def posted_on(entry):
        """(date, detail, age_in_days) for a posting, from whatever it recorded."""
        raw = str(entry.get("postedAgo") or "").strip()
        days = entry.get("postedDays")
        date = entry.get("postedDate")
        if not isinstance(days, (int, float)):
            days = None

        # An explicit date beats everything, including one written into the note.
        if not date:
            found = ISO_DATE.search(raw)
            if found:
                date = found.group(0)

        anchor = _scan_time(entry)
        if date is None:
            if days is None:
                m = RELATIVE.search(raw)
                if m:
                    days = int(m.group(1)) * PER_DAY[m.group(2).lower()]
            if days is not None:
                date = (anchor - timedelta(days=days)).date().isoformat()

        if days is None and date:
            try:
                # Today in Eastern, not in UTC. After 20:00 Eastern the UTC
                # date has already rolled over, so a posting from this morning
                # reads as a day old.
                days = (datetime.now(clock.ZONE).date()
                        - datetime.strptime(date[:10], "%Y-%m-%d").date()).days
            except ValueError:
                days = None
        return date, raw, days

    def posted_cell(entry):
        date, raw, days = posted_on(entry)
        if date is None and not raw:
            return '<span class="muted">unknown</span>'
        cls = "posted fresh" if isinstance(days, (int, float)) and days <= 3 else "posted"
        # The date leads, because it is the fact and it is what the column is
        # sorted by. The relative string was true when the sweep ran and goes
        # stale immediately, so it sits underneath as detail.
        head = esc(date) if date else esc(raw)
        detail = f'<br><span class="ago muted">{esc(raw)}</span>' if raw and date else ""
        return f'<span class="{cls}">{head}</span>{detail}'

    # Newest first. Unknown dates sort last rather than pretending to be fresh,
    # and fit breaks ties within the same day.
    # Newest first, on the derived date rather than on whichever field the sweep
    # happened to record. Anything with no date at all sorts last rather than
    # pretending to be fresh, and fit breaks ties within the same day.
    watch.sort(key=lambda s: (
        posted_on(s)[2] if posted_on(s)[2] is not None else 10**6,
        -(s.get("fitScore") or 0)))
    def dropped_row(entry):
        when = clock.date_label(entry.get("droppedAt"))
        stage = entry.get("droppedStage") or ""
        lane = entry.get("droppedFrom") or "unknown"
        reason = entry.get("droppedReason") or ""
        aid = entry.get("applicationId", "")
        hay = " ".join(str(x) for x in [entry.get("company"), entry.get("role"),
                                        lane, stage, reason] if x)
        reason_line = f'<br><span class="muted">{esc(reason)}</span>' if reason else ""
        return (
            f'<tr data-hay="{esc(hay)}" data-state="{esc(lane)}" data-id="{esc(aid)}">'
            f'<td><div class="company-cell">{company_mark(entry)}<span><strong>{esc(entry.get("company", ""))}</strong><br>'
            f'<span class="muted">{esc(entry.get("role", ""))}</span></span></div></td>'
            f'<td><span class="chip lane">{esc(lane)}</span></td>'
            f'<td class="note-cell">{esc(stage)}{reason_line}</td>'
            f'<td class="when">{esc(when)}</td>'
            f'<td><div class="rowacts-inline">{link(posting_url(entry), "Posting")}'
            f'<button class="btn ghost undrop" data-id="{esc(aid)}" '
            f'title="Put this back in Watching">Restore</button></div></td></tr>'
        )

    dropped_rows = "\n".join(dropped_row(d) for d in dropped)
    dropped_lanes = sorted({d.get("droppedFrom") or "unknown" for d in dropped})

    watch_rows = "\n".join(
        f"<tr data-hay=\"{esc(' '.join(str(x) for x in [s.get('company'), s.get('role'), s.get('location') or s.get('realLocation'), ' '.join(s.get('coreSkills') or []), s.get('alignment') or ''] if x))}\" "
        f"data-fresh=\"{1 if isinstance(s.get('postedDays'), int) and s.get('postedDays') <= 7 else 0}\" "
        f"data-region=\"{esc(s.get('region') or '')}\" data-disc=\"{esc(s.get('discipline') or '')}\">"
        f"<td><div class='company-cell'>{company_mark(s)}<span><strong>{esc(s.get('company'))}</strong><br>"
        f"<span class='muted'>{esc(s.get('role'))}</span>{discipline_tag(s)}</span></div></td>"
        f"<td class='when'>{posted_cell(s)}</td>"
        f"<td>{years_cell(s)}</td>"
        f"<td class='loc'>{esc(s.get('location') or s.get('realLocation') or '')}</td>"
        f"<td class='skills'>{skills_cell(s)}</td>"
        f"<td class='note-cell'>{esc(s.get('alignment') or s.get('step',''))}</td>"
        f"<td><div class='rowacts'>{link(posting_url(s), 'View posting')}"
        f"<div class='rowacts-btns'>"
        f"<button class='btn apply' data-id=\"{esc(s.get('applicationId',''))}\" "
        f"data-company=\"{esc(s.get('company',''))}\">Apply</button>"
        f"<button class='xbtn drop' data-id=\"{esc(s.get('applicationId',''))}\" "
        f"title='Drop this role' aria-label='Drop {esc(s.get('company',''))}'>&#215;</button>"
        f"</div></div></td></tr>" for s in watch
    )


    def filter_bar(key, options, count=1, wide=False):
        """Search box, plus a select only when there is something to select.

        Nothing at all when the lane is empty: a search box over no rows is an
        invitation to search an empty set. `wide` breaks the bar out to the full
        page width, which suits the table lanes; the card lanes leave it in the
        wrap so it lines up with the cards rather than spanning past them.
        """
        if not count:
            return ""
        sel = ""
        if options:
            opts = "".join(f'<option value="{esc(v)}">{esc(label)}</option>'
                           for v, label in options)
            sel = (f'<select data-filter="{esc(key)}">'
                   f'<option value="">All</option>{opts}</select>')
        return (f'<div class="filters{" wide" if wide else ""}" data-scope="{esc(key)}">'
                f'<input type="search" data-search="{esc(key)}" '
                f'placeholder="Search company, role, skill, location...">'
                f'{sel}<span class="fcount" data-count="{esc(key)}"></span></div>')

    # What each lane says when it holds nothing. A blank panel reads as broken;
    # saying which button fills it is more use than "no results".
    EMPTY = {
        "act": ("Nothing needs you.",
                "Every application is either done or running. Press Apply on "
                "something in Watching if you want more here."),
        "flight": ("Nothing in flight.",
                   "What are you doing? Go apply for more jobs."),
        "watch": ("Nothing on the watch list.",
                  "Press Scan now and it will go looking."),
        "done": ("Nothing submitted yet.",
                 "The first one has to start somewhere."),
        "dropped": ("Nothing dropped.",
                    "Roles you say no to end up here, with how far they had got."),
    }

    def empty_state(key):
        head, sub = EMPTY[key]
        return (f'<p class="empty"><strong>{esc(head)}</strong>'
                f'<span>{esc(sub)}</span></p>')

    sub_states = sorted({e.get("event") for e in submitted if e.get("event")})
    regions = sorted({s.get("region") for s in watch if s.get("region")})
    disciplines = sorted({s.get("discipline") for s in watch if s.get("discipline")})
    watch_filters = ([("fresh", "Posted in last 7 days")]
                     + [(f"region:{r}", r) for r in regions]
                     + [(f"disc:{d}", d) for d in disciplines])

    total_todo = sum(len(s.get("pendingFields", [])) for s in blocked)

    # Ordered the way the work actually flows: what was found, what is being
    # watched, what is running, what is stuck, what went out. Dropped sits apart
    # at the end because nothing in it is waiting on anyone.
    tabs = [
        ("found", "Found", found_total),
        ("watch", "Watching", len(watch)),
        ("flight", "In flight", len(inflight)),
        ("act", "Needs you", len(blocked)),
        ("done", "Submitted", len(submitted)),
    ]

    # Capacity gone is not the same as automation stopped, so it gets its own
    # line rather than the alarm banner. It appears where the question is asked:
    # the last time the fleet filled up, the way it surfaced was the user asking
    # how many applications were in flight.
    parked_note = str((state.get("meta") or {}).get("slotsParked") or "").strip()
    parked_banner = (
        f'<p class="capacity" role="status">{esc(parked_note)}</p>'
    ) if parked_note else ""

    stopped = str((state.get("meta") or {}).get("blocked") or "").strip()
    blocked_banner = (
        f'<div class="sysbanner" role="alert"><strong>Automation is stopped.</strong> '
        f'{esc(stopped)}</div>'
    ) if stopped else ""

    # Found is the default tab when it holds anything; otherwise Needs you
    # leads, exactly as it did before there was a Found lane.
    default_tab = "found" if found_total else "act"
    tabnav = "".join(
        f'<button class="tab{" on" if k == default_tab else ""}" data-tab="{k}" role="tab" '
        f'aria-selected="{"true" if k == default_tab else "false"}" aria-controls="p-{k}">'
        f'{esc(label)}<span class="n">{n}</span></button>'
        for k, label, n in tabs
    )

    # Without a doctype the page renders in quirks mode, where phones ignore
    # width=device-width and lay out at 980px, then zoom the whole board down
    # to fit. Everything is legible on a laptop and about a third size on a
    # phone, which is where this actually gets read.
    return f"""<!doctype html>
<meta charset="utf-8">
<title>Jobby is a free agent!</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#f7f3ed" media="(prefers-color-scheme:light)">
<meta name="theme-color" content="#141519" media="(prefers-color-scheme:dark)">
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
    document.documentElement.classList.add('jb-intro');
  }})();
</script>
<!-- Vercel Web Analytics -->
<script>
  window.va = window.va || function () {{ (window.vaq = window.vaq || []).push(arguments); }};
</script>
<script defer src="/_vercel/insights/script.js"></script>
<style>
  :root {{
    color-scheme:light dark;
    --bg:#f5f5f7; --card:#fff; --sunk:#f0f0f2; --seg:#e8e8ed;
    --ink:#1d1d1f; --ink-2:#6e6e73; --ink-3:#8e8e93;
    --hair:rgba(0,0,0,.07);
    --blue:#0071e3; --orange:#b45309; --green:#047857; --purple:#6d28d9; --red:#b3261e; --btn-g:#047857; --btn-g-ink:#fff;
    --t-o:rgba(234,88,12,.08); --t-g:rgba(5,150,105,.08); --t-p:rgba(109,40,217,.07);
    --t-red:rgba(179,38,30,.08); --red-ink:#b3261e;
    --sh:0 1px 3px rgba(0,0,0,.05), 0 6px 20px rgba(0,0,0,.05);
    /* One logo in every theme: the pack's primary, white mark knocked out of
       an ink circle. Deliberately not themed, so the mark reads the same on
       the board as it does in the tab strip and on a home screen. */
    --brand-bg:#2B2D2C; --brand-fg:#FFFFFF;
  }}
  @media (prefers-color-scheme:dark) {{
    :root:not([data-theme="light"]) {{
      --bg:#000; --card:#1c1c1e; --sunk:#2c2c2e; --seg:#2c2c2e;
      --ink:#f5f5f7; --ink-2:#aeaeb2; --ink-3:#8e8e93;
      --hair:rgba(255,255,255,.1);
      --blue:#0a84ff; --orange:#ff9f0a; --green:#30d158; --purple:#bf5af2; --red:#c9372c; --btn-g:#30d158; --btn-g-ink:#04240f;
      --t-o:rgba(255,159,10,.13); --t-g:rgba(48,209,88,.12); --t-p:rgba(191,90,242,.12);
      --t-red:rgba(201,55,44,.16); --red-ink:#ff6961;
      --sh:0 1px 3px rgba(0,0,0,.5);
    }}
  }}
  :root[data-theme="dark"] {{
    --bg:#000; --card:#1c1c1e; --sunk:#2c2c2e; --seg:#2c2c2e;
    --ink:#f5f5f7; --ink-2:#aeaeb2; --ink-3:#8e8e93;
    --hair:rgba(255,255,255,.1);
    --blue:#0a84ff; --orange:#ff9f0a; --green:#30d158; --purple:#bf5af2; --red:#c9372c; --btn-g:#30d158; --btn-g-ink:#04240f;
    --t-o:rgba(255,159,10,.13); --t-g:rgba(48,209,88,.12); --t-p:rgba(191,90,242,.12);
    --t-red:rgba(201,55,44,.16); --red-ink:#ff6961;
    --sh:0 1px 3px rgba(0,0,0,.5);
  }}
  *,*::before,*::after {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",system-ui,sans-serif;
    font-size:17px; line-height:1.47; letter-spacing:-.022em;
    -webkit-font-smoothing:antialiased; }}
  /* Home and Settings sit in the centre of the app bar, between the brand and
     theme control, using the same control language as the lane tabs. */
  .primary-nav {{ width:max-content; max-width:100%; margin:0; padding:.24rem;
    display:flex; align-items:center; justify-content:center; gap:.18rem;
    background:var(--seg); border:1px solid var(--hair); border-radius:999px;
    justify-self:center; }}
  .primary-nav a {{ display:flex; align-items:center; justify-content:center; min-height:2.15rem;
    padding:.42rem .95rem; border-radius:999px; color:var(--ink-2); text-decoration:none;
    font-size:.88rem; font-weight:650; }}
  .primary-nav a:hover {{ color:var(--ink); background:var(--sunk); }}
  .primary-nav a[aria-current="page"] {{ color:var(--ink); background:var(--card);
    box-shadow:var(--sh); }}
  .wrap {{ max-width:860px; margin:0 auto; padding:clamp(2rem,6vw,4rem) 1.25rem 6rem; }}
  /* Tables get the whole screen; prose-width containers keep the rest readable. */
  .panel .sheet {{ max-width:none; width:min(1800px, calc(100vw - 2.5rem));
    margin-left:calc(50% - min(900px, calc(50vw - 1.25rem)));
    margin-right:calc(50% - min(900px, calc(50vw - 1.25rem))); }}
  /* Only the table lanes break the filter bar out to the full page. The card
     lanes keep it in the wrap, so it lines up with the cards instead of
     spanning half a screen past them. */
  .panel .filters.wide {{ width:min(1800px, calc(100vw - 2.5rem));
    margin-left:calc(50% - min(900px, calc(50vw - 1.25rem)));
    margin-right:calc(50% - min(900px, calc(50vw - 1.25rem))); }}

  /* Brand. The lockup's circle and mark are painted from --brand-*, which the
     theme blocks invert; the wordmark rides currentColor. */
  .jb-defs {{ position:absolute; width:0; height:0; overflow:hidden; }}
  .appbar {{ max-width:860px; margin:0 auto; padding:1.15rem 1.25rem 0;
    display:grid; grid-template-columns:1fr auto 1fr; align-items:center; gap:1rem; }}
  .brand {{ display:inline-flex; align-items:center; color:var(--ink);
    text-decoration:none; border-radius:8px; justify-self:start; }}
  .brand:focus-visible {{ outline:3px solid var(--blue); outline-offset:4px; }}
  /* The circle is tangent to its viewBox on all four sides, so the default
     SVG overflow:hidden shaves the antialiased edge and the curve reads flat
     top and bottom. The nudge is optical: the wordmark has no descender past
     the circle, which makes the pair look low against the theme control. */
  .lockup {{ height:1.6rem; width:auto; display:block; overflow:visible;
    position:relative; top:-1px; }}
  /* The mascot is the hero mark, at rest. It used to be a hover reveal over the
     circular badge, but the badge already sits in the app bar lockup, so the
     drawing is the thing worth showing and hover is no longer a prerequisite.
     The linework is one colour, so on a near-black page it rendered as cream on
     ink and read as a ghost. Dark themes therefore set it on a cream oval and
     ink it dark: --mark-plate and --mark-ink carry that pair, and in light
     themes the plate is transparent because the page is already cream. */
  .jb-morph {{ position:relative; top:-10px; width:139px; height:153px;
    margin:0 auto 1.15rem; display:grid; place-items:center; cursor:pointer;
    -webkit-tap-highlight-color:transparent; }}
  .jb-morph:focus-visible {{ outline:3px solid var(--blue); outline-offset:6px;
    border-radius:50%; }}
  .jb-plate {{ position:absolute; left:50%; top:50%; width:100%; height:100%;
    transform:translate(-50%,-50%); border-radius:50%; background:var(--mark-plate);
    transition:background-color .38s cubic-bezier(.22,1,.36,1); }}
  .jb-mascot2 {{ position:relative; height:111px; width:auto; display:block;
    color:var(--mark-ink); pointer-events:none;
    transition:color .38s cubic-bezier(.22,1,.36,1); }}
  /* The draw-on beats play once on first paint, and again whenever the mark is
     clicked: the contour runs its dashoffset to zero, then the interior ink
     wipes down. Resting state is the finished drawing, so anything that
     re-renders the hero gets the mark whole rather than replaying the reveal.

     Both beats animate a property the compositor cannot take: stroke-dashoffset
     on the contour, and the height of the clip rect inside the SVG. The wipe
     used to be a CSS clip-path with an opacity fade, and both of those promote
     the pack to its own layer, which Chrome rasterizes coarse for the length of
     the animation and re-rasterizes sharp at the end. On linework this fine
     that reads as half a second of blur. */
  .jb-mascot2 .jm2-contour {{ stroke-dasharray:1; stroke-dashoffset:0; }}
  .jb-mascot2 .jm2-wipe-rect {{ height:1628px; }}
  .jb-intro .jm2-contour {{ animation:jm2-draw .75s ease .1s backwards; }}
  .jb-intro .jm2-wipe-rect {{ animation:jm2-wipe .8s ease .5s backwards; }}
  @keyframes jm2-draw {{ from {{ stroke-dashoffset:1; }} to {{ stroke-dashoffset:0; }} }}
  @keyframes jm2-wipe {{ from {{ height:0px; }} to {{ height:1628px; }} }}
  @media (prefers-reduced-motion:reduce) {{
    .jb-intro .jm2-contour, .jb-intro .jm2-wipe-rect {{ animation:none; }} }}

  .themeseg {{ display:inline-flex; background:var(--seg); border-radius:100px;
    padding:3px; gap:2px; flex:none; justify-self:end; }}
  .themebtn {{ border:none; background:none; color:var(--ink-3); font:inherit;
    cursor:pointer; width:2.1rem; height:1.75rem; padding:0; border-radius:100px;
    display:inline-flex; align-items:center; justify-content:center;
    transition:background .22s cubic-bezier(.4,0,.2,1), color .22s; }}
  .themebtn svg {{ width:1rem; height:1rem; display:block; }}
  .themebtn:hover {{ color:var(--ink-2); }}
  .themebtn.on {{ background:var(--card); color:var(--ink);
    box-shadow:0 1px 3px rgba(0,0,0,.12); }}
  .themebtn:focus-visible {{ outline:3px solid var(--blue); outline-offset:2px; }}
  @media (max-width:600px) {{ .appbar {{ padding:.9rem .75rem 0; gap:.4rem;
      grid-template-columns:auto 1fr auto; }}
    .lockup {{ height:1.4rem; }}
    .primary-nav {{ padding:.18rem; gap:.1rem; }}
    .primary-nav a {{ min-height:1.9rem; padding:.34rem .62rem; font-size:.8rem; }}
    .themebtn {{ width:1.75rem; }}
    .jb-morph {{ width:109px; height:121px; top:-6px; }}
    .jb-mascot2 {{ height:88px; }} }}

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
  .chip.fault {{ color:var(--red-ink); background:var(--t-red); }}
  .capacity {{ margin:0 0 .85rem; padding:.6rem .85rem; border-radius:var(--radius);
    background:var(--t-o); color:var(--orange); font-size:.9rem; line-height:1.45; }}

  ul.todo {{ margin:0; padding:0; list-style:none; display:flex; flex-direction:column; gap:.45rem; }}
  ul.todo li {{ font-size:.92rem; line-height:1.38; padding-left:1.25rem; position:relative;
    letter-spacing:-.014em; }}
  ul.todo li::before {{ content:""; position:absolute; left:.3rem; top:.52em; width:6px;
    height:6px; border-radius:50%; background:var(--orange); }}
  ul.todo li.done {{ color:var(--green); font-weight:550; }}
  ul.todo li.done::before {{ content:"\\2713"; background:none; width:auto; height:auto;
    left:0; top:0; font-weight:700; }}
  .why {{ color:var(--ink-2); }}
  .why::before {{ content:": "; }}

  .act {{ display:flex; gap:.55rem; flex-wrap:wrap; align-items:center; margin-top:auto;
    padding-top:.95rem; border-top:1px solid var(--hair); }}
  .btn {{ display:inline-block; background:var(--blue); color:#fff; text-decoration:none;
    border:none; font:inherit; font-size:.9rem; font-weight:520; letter-spacing:-.014em;
    padding:.52rem 1.15rem; border-radius:100px; cursor:pointer;
    transition:opacity .2s, transform .12s; }}
  .btn:hover {{ opacity:.86; }}
  .btn:active {{ transform:scale(.97); }}
  .btn.ghost {{ background:var(--sunk); color:var(--ink); }}
  .btn[data-done] {{ background:var(--btn-g); color:var(--btn-g-ink); }}
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
  .tablewrap table {{ min-width:900px; }}
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
  .btn.apply {{ background:var(--btn-g); color:var(--btn-g-ink);
    font-size:.8rem; padding:.35rem .8rem; }}
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
    background:var(--card); border-radius:20px; box-shadow:var(--sh); letter-spacing:-.018em;
    display:flex; flex-direction:column; gap:.45rem; }}
  .empty strong {{ color:var(--ink); font-size:1.15rem; font-weight:620; }}
  .empty span {{ font-size:.95rem; }}
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
  /* Destructive controls are filled, not outlined. .btn already sets a blue
     background, so colouring only the text left red-on-blue. */
  .btn.mark {{ background:var(--btn-g); color:var(--btn-g-ink); }}
  .btn.drop {{ background:var(--red); color:#fff; border-color:var(--red); }}
  .xbtn {{ appearance:none; border:1px solid var(--red); background:var(--red);
    color:#fff; border-radius:6px; cursor:pointer; line-height:1; font-weight:600;
    font-size:.95rem; padding:.3rem .55rem; }}
  .xbtn:hover, .btn.drop:hover {{ filter:brightness(1.1); }}
  .xbtn:active {{ transform:scale(.97); }}
  .xbtn:focus-visible, .btn.drop:focus-visible {{ outline:3px solid var(--red); outline-offset:3px; }}
  /* The three row controls need room between them; the cell stays a table-cell
     so the colgroup widths still apply. */
  /* The posting link on its own line, then Apply and the drop control pushed to
     opposite ends of exactly that width, so the block reads as one unit.
     inline-flex matters: a stretch column made the link's box span the whole
     table cell while its text ended well short of it, which left the drop
     control floating past the arrow instead of sitting under it. Shrink-wrapped
     to the link, the row is exactly as wide as "View posting" and its arrow. */
  .rowacts {{ display:inline-flex; flex-direction:column; align-items:stretch;
    gap:.85rem; white-space:nowrap; margin-left:1.4rem; }}
  .rowacts-btns {{ display:flex; align-items:center; justify-content:space-between; gap:.5rem; }}
  /* Posting and Restore sat flush against each other. */
  .rowacts-inline {{ display:flex; align-items:center; gap:.85rem; }}
  .chip.lane {{ background:var(--sunk); color:var(--ink-2); font-weight:520; }}
  tr.dropping {{ opacity:.4; }}
  .sysbanner {{ background:#8c1d18; color:#fff; padding:.7rem 1rem; font-size:.9rem;
    line-height:1.45; text-align:center; }}
  .sysbanner strong {{ font-weight:650; }}
{found_lane.CSS}
{settings_panel.CSS}
{profile_panel.CSS}
{premium_ui.CSS}
</style>

{brand.defs()}

{blocked_banner}

<header class="appbar">
  <a class="brand" href="/" data-home-found aria-label="Jobby home">{brand.lockup()}</a>
  <nav class="primary-nav" aria-label="Primary">
    <a href="/" data-primary-view="home">Home</a>
    <a href="/profile" data-primary-view="profile">Profile{profile_badge}</a>
    <a href="/settings" data-primary-view="settings">Settings</a>
  </nav>
  <div class="themeseg" role="radiogroup" aria-label="Colour theme">
      <button class="themebtn" data-theme-set="auto" role="radio" aria-checked="false" title="Use system theme" aria-label="System theme"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg></button>
      <button class="themebtn" data-theme-set="light" role="radio" aria-checked="false" title="Use light theme" aria-label="Light theme"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.6v2M12 19.4v2M4.4 12h-2M21.6 12h-2M6.3 6.3 4.9 4.9M19.1 19.1l-1.4-1.4M17.7 6.3l1.4-1.4M4.9 19.1l1.4-1.4"/></svg></button>
      <button class="themebtn" data-theme-set="dark" role="radio" aria-checked="false" title="Use dark theme" aria-label="Dark theme"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M20.5 14.6A8.6 8.6 0 0 1 9.4 3.5a8.6 8.6 0 1 0 11.1 11.1Z"/></svg></button>
  </div>
</header>

<div class="wrap">
  <div class="top">
    <div class="hero-copy">
      <h1>Jobby is a free agent.</h1>
      <p class="lede">{f"{len(blocked)} waiting on you" if blocked else "Nothing blocked"}{f" &middot; {len(inflight)} in flight" if inflight else ""}</p>
      <p class="stamp">{esc(name)} &middot; Updated {esc(generated)}</p>
    </div>
    <div class="jb-morph" role="button" tabindex="0" aria-label="Replay the Jobby mark">
      <span class="jb-plate" aria-hidden="true"></span>
      {brand.mascot()}
    </div>
  </div>

  <div class="segwrap"><div class="seg" role="tablist">{tabnav}</div></div>

  <div class="panel{" on" if default_tab == "found" else ""}" id="p-found" role="tabpanel">
    {found_lane.panel(found, found_total, search_config_saved, state.get("meta") or {})}
  </div>

  <div class="panel{" on" if default_tab == "act" else ""}" id="p-act" role="tabpanel">
    {'<p class="hint">Each opens straight to where it is blocked.</p>' if cards else ''}
    {filter_bar("act", [("ready", "Ready to submit"), ("hold", "Still blocked")], len(cards))}
    {f'<div class="grid">{"".join(cards)}</div>' if cards else empty_state("act")}
    {unconfirmed_block}
  </div>

  <div class="panel" id="p-flight" role="tabpanel">
    {parked_banner}
    {'<p class="hint">Applications being worked on right now. Updates as each stage completes.</p>' if flight_cards else ''}
    {filter_bar("flight", [], len(flight_cards))}
    {f'<div class="grid">{"".join(flight_cards)}</div>' if flight_cards else empty_state("flight")}
  </div>

  <div class="panel" id="p-done" role="tabpanel">
    {'<p class="hint">Already out. Check here before applying again.</p>' if submitted else ''}
    {'<p class="hint">Answers opens the questions that form asked and what went into each one. Rows reading <em>not captured</em> were submitted before Jobby started writing that record down; nothing is reconstructed for them.</p>' if uncaptured else ''}
    {filter_bar("done", [(o, o.title()) for o in sub_states], len(submitted), wide=True)}
    {f'''<div class="sheet"><div class="tablewrap"><table>
      <thead><tr><th>Company</th><th>Role</th><th>Status</th><th>When</th><th>Posting</th><th>Proof</th><th>Resume</th><th>Cover letter</th><th>Answers</th></tr></thead>
      <tbody>{sub_rows}</tbody></table></div></div>''' if submitted else empty_state("done")}
  </div>

  <div class="panel watch" id="p-watch" role="tabpanel">
    {'<p class="hint">Newest first. Search or filter to narrow.</p>' if watch else ''}
    {filter_bar("watch", watch_filters, len(watch), wide=True)}
    {f'''<div class="sheet"><div class="tablewrap"><table>
      <colgroup>
        <col style="width:17%"><col style="width:7%"><col style="width:8%">
        <col style="width:12%"><col style="width:18%"><col style="width:26%">
        <col style="width:12%">
      </colgroup>
      <thead><tr><th>Role</th><th>Posted</th><th>Experience</th><th>Location</th><th>Core skills</th><th>How you line up</th><th></th></tr></thead>
      <tbody>{watch_rows}</tbody></table></div></div>''' if watch else empty_state("watch")}
  </div>

  {profile_panel.panel(bank_counts_public)}

  {settings_panel.panel(state.get("providers") or {})}
</div>

<button class="dropped-dock" id="dropped-open" type="button" aria-haspopup="dialog" aria-controls="p-dropped">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13M10 11v5M14 11v5"/></svg>
  <span class="dropped-label">Dropped</span><span class="dropped-count">{len(dropped)}</span>
</button>
<dialog class="dropped-dialog" id="p-dropped" aria-labelledby="dropped-title">
  <div class="dialog-head">
    <div><p class="eyebrow">Archive</p><h2 id="dropped-title">Dropped applications</h2></div>
    <button class="dialog-close" id="dropped-close" type="button" aria-label="Close dropped applications">&times;</button>
  </div>
  {'<p class="hint">Roles you decided against. They stay here so Jobby does not offer them again.</p>' if dropped else ''}
  {filter_bar("dropped", [(l, l) for l in dropped_lanes], len(dropped), wide=False)}
  {f'''<div class="sheet"><div class="tablewrap"><table>
    <colgroup><col style="width:25%"><col style="width:13%"><col style="width:31%"><col style="width:11%"><col style="width:20%"></colgroup>
    <thead><tr><th>Role</th><th>Dropped from</th><th>Last stage</th><th>When</th><th></th></tr></thead>
    <tbody>{dropped_rows}</tbody></table></div></div>''' if dropped else empty_state("dropped")}
</dialog>

<!-- What one employer's form asked, and what went into it. The rows are fetched
     when this opens and built through textContent, never markup: every question
     was written by a stranger and every answer passed through their page. -->
<dialog class="answers-dialog" id="p-answers" aria-labelledby="answers-title">
  <div class="dialog-head">
    <div><p class="eyebrow" id="answers-where">Submitted</p><h2 id="answers-title">Application answers</h2></div>
    <button class="dialog-close" id="answers-close" type="button" aria-label="Close application answers">&times;</button>
  </div>
  <p class="hint answers-note">What the form asked, what went in, and where each value came from. Codes, passwords and government or bank identifiers were never recorded, so those rows say so.</p>
  <div id="answers-body"><p class="answers-loading">Reading the record...</p></div>
</dialog>

<script type="application/json" id="jb-found-data">{found_lane.payload(found, found_census, found_total)}</script>
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
  function prepareCompanyLogo(img) {{
    function loaded() {{
      if (img.naturalWidth > 1 && img.naturalHeight > 1) img.classList.add('loaded');
      else img.remove();
    }}
    img.addEventListener('load', loaded);
    img.addEventListener('error', function () {{ img.remove(); }});
    if (img.complete) loaded();
  }}
  document.querySelectorAll('[data-company-logo]').forEach(prepareCompanyLogo);
  window.jobbyPrepareCompanyLogo = prepareCompanyLogo;

  var heroMark = document.querySelector('.jb-morph');
  var heroWipe = document.querySelector('.jb-mascot2 .jm2-wipe-rect');
  var introTimer = null;
  function endIntro() {{ document.documentElement.classList.remove('jb-intro'); }}
  function playIntro() {{
    var root = document.documentElement;
    root.classList.remove('jb-intro');
    void root.offsetWidth;
    root.classList.add('jb-intro');
    clearTimeout(introTimer);
    introTimer = setTimeout(endIntro, 2600);
  }}
  if (heroWipe) heroWipe.addEventListener('animationend', endIntro);
  introTimer = setTimeout(endIntro, 2600);
  if (heroMark) {{
    heroMark.addEventListener('click', playIntro);
    heroMark.addEventListener('keydown', function (event) {{
      if (event.key === 'Enter' || event.key === ' ') {{ event.preventDefault(); playIntro(); }}
    }});
  }}

  // The logo is the way back to the board from anywhere, and "the board" means
  // Found, not whichever tab was last open.
  var brandLink = document.querySelector('[data-home-found]');
  if (brandLink) brandLink.addEventListener('click', function (event) {{
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    var found = document.querySelector('.tab[data-tab="found"]');
    if (window.jobbyGoHome) window.jobbyGoHome();
    if (found) activateTab(found);
  }});

  // Live lanes. The board is a static render of a published snapshot, and the
  // watcher republishes at most every two minutes, so a card moved by clicking
  // Apply used to sit in the wrong lane until the page was reloaded, and a
  // reload inside that window put it back where it started. Two things fix
  // that: this poll, which moves cards without a reload, and a record of the
  // moves this browser made, which outranks the snapshot until the snapshot is
  // newer than the move.
  var LANE_FOR_STATUS = {{
    active: 'act', review: 'act', inflight: 'flight', watch: 'watch'
  }};
  var PENDING_KEY = 'jobby.pendingLanes';
  var LANE_POLL_MS = 5000;

  function readPending() {{
    try {{
      var raw = JSON.parse(localStorage.getItem(PENDING_KEY) || '{{}}');
      return (raw && typeof raw === 'object') ? raw : {{}};
    }} catch (e) {{ return {{}}; }}
  }}
  function writePending(value) {{
    try {{ localStorage.setItem(PENDING_KEY, JSON.stringify(value)); }} catch (e) {{}}
  }}
  // The status the last poll saw for each application, so a move can record
  // what it is superseding.
  var lastSeenStatus = {{}};
  // How long an unanswered move may hold a card. A retry the runner never
  // picks up should not pin a card in In flight forever.
  var PENDING_CAP_MS = 30 * 60 * 1000;

  // Called the moment the user acts, before anything has reached the server.
  function rememberMove(id, lane) {{
    if (!id || !lane) return;
    var pending = readPending();
    // `was` is the point. Retry leaves an application on its old status until
    // the runner picks it up, so "the snapshot is newer than the move" fired on
    // the very next publish and dragged the card back out of In flight. What
    // supersedes the move is the status CHANGING, not the clock moving.
    pending[id] = {{ lane: lane, at: new Date().toISOString(),
                    was: lastSeenStatus[id] || '' }};
    writePending(pending);
    // Ask the server straight away rather than waiting out the interval.
    setTimeout(function () {{ if (window.jobbyPollLanes) window.jobbyPollLanes(); }}, 800);
  }}
  window.jobbyRememberMove = rememberMove;
  // Found lives in its own script and has no card to move: an Apply there
  // creates the application rather than moving one. It calls this so the job
  // shows up in In flight on the click, the same way Retry already does.
  window.jobbyPlaceholder = function (company, role, id) {{
    if (!id || document.querySelector('.card[data-id="' + CSS.escape(id) + '"]')) return;
    placeholderCard(company || '', role || '', id);
  }};

  function cardFor(id) {{
    return document.querySelector('.card[data-id="' + CSS.escape(id) + '"]');
  }}
  function laneOf(card) {{
    var panel = card.closest ? card.closest('.panel') : null;
    return panel ? panel.id.replace('p-', '') : '';
  }}
  function gridFor(lane) {{
    var panel = document.getElementById('p-' + lane);
    if (!panel) return null;
    var grid = panel.querySelector('.grid');
    if (!grid) {{
      grid = document.createElement('div');
      grid.className = 'grid';
      var empty = panel.querySelector('.empty');
      if (empty) panel.insertBefore(grid, empty);
      else panel.appendChild(grid);
    }}
    return grid;
  }}
  function paintEmptyState(lane) {{
    var panel = document.getElementById('p-' + lane);
    if (!panel) return;
    var grid = panel.querySelector('.grid');
    var empty = panel.querySelector('.empty');
    if (empty) empty.hidden = !!(grid && grid.children.length);
  }}
  function moveCard(card, lane) {{
    var target = gridFor(lane);
    if (!target || card.parentNode === target) return false;
    var from = laneOf(card);
    target.appendChild(card);
    if (from) {{ tabCount(from, -1); paintEmptyState(from); }}
    tabCount(lane, 1);
    paintEmptyState(lane);
    return true;
  }}

  function stillMine(mine, serverStatus) {{
    if (!mine) return false;
    if (Date.now() - Date.parse(mine.at) > PENDING_CAP_MS) return false;
    // The server has said something new about this application, so it knows
    // more than the click did.
    return (serverStatus || '') === (mine.was || '');
  }}

  function applyLanes(data) {{
    if (!data || !data.ok) return;
    var pending = readPending();
    var sessions = data.sessions || {{}};
    var keep = {{}};
    var moved = 0;
    Object.keys(sessions).forEach(function (id) {{
      var lane = LANE_FOR_STATUS[sessions[id]];
      var mine = pending[id];
      if (stillMine(mine, sessions[id])) {{
        keep[id] = mine;
        lane = mine.lane;
      }}
      if (!lane) return;
      var card = cardFor(id);
      if (card && laneOf(card) !== lane && moveCard(card, lane)) moved += 1;
    }});
    // A move for an application the snapshot does not carry yet, which is what
    // an Apply out of Found looks like until the session exists.
    Object.keys(pending).forEach(function (id) {{
      if (keep[id] || Object.prototype.hasOwnProperty.call(sessions, id)) return;
      if (stillMine(pending[id], '')) keep[id] = pending[id];
    }});
    lastSeenStatus = sessions;
    writePending(keep);
    return moved;
  }}

  function pollLanes() {{
    if (document.hidden) return Promise.resolve();
    return fetch('/api/lanes', {{ credentials: 'same-origin', cache: 'no-store' }})
      .then(function (r) {{ return r.ok ? r.json() : null; }})
      .then(applyLanes)
      // A saved copy of the board has no route to ask, and a dropped request is
      // not worth a message; the next tick tries again.
      .catch(function () {{}});
  }}
  window.jobbyPollLanes = pollLanes;
  pollLanes();
  setInterval(pollLanes, LANE_POLL_MS);
  // Coming back to the tab should not wait out the interval.
  document.addEventListener('visibilitychange', function () {{
    if (!document.hidden) pollLanes();
  }});

  var tabs = document.querySelectorAll('.tab');
  function activateTab(t) {{
    tabs.forEach(function (o) {{ o.classList.remove('on'); o.setAttribute('aria-selected','false'); }});
    document.querySelectorAll('.panel').forEach(function (p) {{ p.classList.remove('on'); }});
    t.classList.add('on'); t.setAttribute('aria-selected','true');
    var p = document.getElementById('p-' + t.dataset.tab);
    if (p) p.classList.add('on');
  }}
  tabs.forEach(function (t) {{
    t.addEventListener('click', function () {{ activateTab(t); }});
  }});
  var droppedDialog = document.getElementById('p-dropped');
  var droppedOpen = document.getElementById('dropped-open');
  var droppedClose = document.getElementById('dropped-close');
  if (droppedOpen && droppedDialog) droppedOpen.addEventListener('click', function () {{
    if (droppedDialog.showModal) droppedDialog.showModal();
    else droppedDialog.setAttribute('open', '');
  }});
  if (droppedClose && droppedDialog) droppedClose.addEventListener('click', function () {{
    if (droppedDialog.close) droppedDialog.close();
    else droppedDialog.removeAttribute('open');
  }});
  if (droppedDialog) droppedDialog.addEventListener('click', function (event) {{
    if (event.target === droppedDialog && droppedDialog.close) droppedDialog.close();
  }});

  // The application answers modal. Records are fetched rather than embedded:
  // the board state is near its ceiling and a hundred manifests would be a
  // couple of hundred kilobytes of page that almost nobody opens. Nothing here
  // touches innerHTML. Every question came off somebody else's form and every
  // answer went through their page, so both are text and only ever text.
  var answersDialog = document.getElementById('p-answers');
  var answersBody = document.getElementById('answers-body');
  var answersWhere = document.getElementById('answers-where');
  var answersTitle = document.getElementById('answers-title');
  var answersClose = document.getElementById('answers-close');
  var answersCache = {{}};
  var SOURCE_LABEL = {{ stored: 'answer store', profile: 'profile',
                       generated: 'generated', provided: 'you' }};
  var SOURCE_WHY = {{
    stored: 'A confirmed answer you had already given to this question.',
    profile: 'Taken from your profile: name, contact details, work history.',
    generated: 'Built for this application from the tailored resume or the posting.',
    provided: 'You supplied this one yourself while the application was in Needs you.'
  }};

  function ansEl(tag, cls, text) {{
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }}

  function answerRow(field) {{
    var row = ansEl('div', 'ans-row');
    row.appendChild(ansEl('p', 'ans-q', field.question || 'Unlabelled field'));

    // Two categories, and only one of them is a display decision.
    //
    // `withheld` is non-capture: the value was never written, so there is
    // nothing here to show and nothing a click could uncover. Say which class
    // of thing it was rather than leaving a blank row that reads as a field
    // the form never asked about.
    //
    // `sensitive` is a property of the data, not an instruction to this
    // function. Pay and demographics render like every other row, because this
    // is one person's own board and he knows his own salary; covering it was
    // friction with no reader it protected against. The flag stays on the
    // record and is what a second account's board will key its own masking on.
    var value = ansEl('div', 'ans-a');
    if (field.unrecorded) {{
      // A salvaged question. The form asked it; nothing wrote down the answer,
      // and nothing ever will. Saying so per row matters as much as the banner,
      // because a blank next to a question reads as a field left empty.
      value.appendChild(ansEl('span', 'ans-unrecorded', 'Answer not recorded'));
    }} else if (field.withheld) {{
      value.appendChild(ansEl('span', 'ans-withheld',
        'Not recorded \\u2014 ' + field.withheld));
    }} else {{
      value.appendChild(ansEl('span', 'ans-value', field.answer || '(left blank)'));
    }}
    row.appendChild(value);

    if (field.unrecorded) {{
      // No source badge and no read-back badge: both would be invented.
      row.appendChild(ansEl('div', 'ans-meta'));
      return row;
    }}
    var meta = ansEl('div', 'ans-meta');
    var source = field.source || 'generated';
    var badge = ansEl('span', 'ans-badge ans-src-' + source,
                      SOURCE_LABEL[source] || source);
    badge.title = SOURCE_WHY[source] || '';
    meta.appendChild(badge);
    if (field.readBack === 'differs') {{
      // The value on the page disagreed with the value that was meant to go in.
      // That is the failure this whole read-back pass exists to catch, so it is
      // never quietly folded into the answer column.
      var diff = ansEl('span', 'ans-badge ans-differs', 'read back differently');
      diff.title = field.readBackValue
        ? 'The page showed: ' + field.readBackValue
        : 'The value read back off the page did not match what was entered.';
      meta.appendChild(diff);
    }} else if (field.readBack === 'unverified') {{
      var un = ansEl('span', 'ans-badge ans-unverified', 'not read back');
      un.title = 'This field was filled but its value was not read back off the page.';
      meta.appendChild(un);
    }}
    if (field.note) meta.appendChild(ansEl('span', 'ans-note', field.note));
    row.appendChild(meta);
    return row;
  }}

  function renderAnswers(record) {{
    answersBody.textContent = '';
    var fields = (record && record.fields) || [];
    if (record && record.capture === 'questions-only') {{
      // Said before the rows rather than after, because someone scanning a
      // column of "Answer not recorded" should already know why.
      var salvage = ansEl('p', 'ans-salvage',
        'These are the questions this form asked, kept by the session that '
        + 'filled it. The answers were never written down, and nothing here '
        + 'guesses at them.');
      answersBody.appendChild(salvage);
    }}
    if (!fields.length) {{
      answersBody.appendChild(ansEl('p', 'answers-loading',
        'The record was written but lists no fields.'));
      return;
    }}
    var counted = ansEl('p', 'answers-count',
      fields.length + (fields.length === 1 ? ' field' : ' fields')
      + (record.capturedAt ? ' \\u00b7 captured ' + record.capturedAt.slice(0, 10) : '')
      + (record.ats ? ' \\u00b7 ' + record.ats : ''));
    answersBody.appendChild(counted);
    var list = ansEl('div', 'ans-list');
    fields.forEach(function (f) {{ list.appendChild(answerRow(f)); }});
    answersBody.appendChild(list);
  }}

  function openAnswers(button) {{
    if (!answersDialog || !answersBody) return;
    var id = button.dataset.id || '';
    answersWhere.textContent = button.dataset.company || 'Submitted';
    answersTitle.textContent = button.dataset.role || 'Application answers';
    if (answersDialog.showModal) answersDialog.showModal();
    else answersDialog.setAttribute('open', '');
    if (answersCache[id]) {{ renderAnswers(answersCache[id]); return; }}
    answersBody.textContent = '';
    answersBody.appendChild(ansEl('p', 'answers-loading', 'Reading the record\\u2026'));
    if (typeof fetch !== 'function') {{
      answersBody.textContent = '';
      answersBody.appendChild(ansEl('p', 'answers-loading',
        'This copy of the board cannot reach the record.'));
      return;
    }}
    // Through /doc/, the same authenticated proxy the resume and the cover
    // letter come down. A record is the same class of per-application artifact
    // they are, so it is the same R2 key layout, the same index, the same
    // password gate and the same dual-read. A second route would have been a
    // second gate to keep honest.
    fetch('/doc/answers-' + encodeURIComponent(id) + '.json',
          {{ credentials: 'same-origin' }})
      .then(function (r) {{
        // A miss answers text/plain, so the type is checked rather than
        // trusting that 404 is the only way to not get a record.
        var kind = r.headers && r.headers.get ? (r.headers.get('content-type') || '') : '';
        return r.ok && kind.indexOf('json') >= 0 ? r.json() : null;
      }})
      .then(function (record) {{
        if (!record || !record.fields) {{
          answersBody.textContent = '';
          answersBody.appendChild(ansEl('p', 'answers-loading',
            'The record is filed on your Mac but has not been published yet.'));
          return;
        }}
        answersCache[id] = record;
        renderAnswers(record);
      }})
      // A saved copy of the board has no route to ask. That is not an error
      // worth dressing up, and the rest of the row still stands.
      .catch(function () {{
        answersBody.textContent = '';
        answersBody.appendChild(ansEl('p', 'answers-loading',
          'This copy of the board cannot reach the record.'));
      }});
  }}

  document.querySelectorAll('.answers-open').forEach(function (b) {{
    b.addEventListener('click', function () {{ openAnswers(b); }});
  }});
  if (answersClose && answersDialog) answersClose.addEventListener('click', function () {{
    if (answersDialog.close) answersDialog.close();
    else answersDialog.removeAttribute('open');
  }});
  if (answersDialog) answersDialog.addEventListener('click', function (event) {{
    if (event.target === answersDialog && answersDialog.close) answersDialog.close();
  }});
  // "Confirm submitted" moves a card out of Needs you and into Submitted. The
  // board cannot write to the store on the agent's machine, so the change is
  // queued and a local poller applies it. The card is moved optimistically so
  // the tab reflects reality immediately, and the queue makes it durable.
  function tabCount(tab, delta) {{
    var el = tab === 'dropped'
      ? document.querySelector('.dropped-count')
      : document.querySelector('.tab[data-tab="' + tab + '"] .n');
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
      function addCell(text, className) {{
        var td = document.createElement('td');
        if (className) td.className = className;
        td.textContent = text;
        tr.appendChild(td);
        return td;
      }}
      addCell(company);
      addCell(role);
      var stateCell = addCell('');
      var chip = document.createElement('span');
      chip.className = 'chip';
      chip.textContent = 'submitted';
      stateCell.appendChild(chip);
      addCell(today, 'when');
      var postingCell = addCell('');
      var safePosting = '';
      try {{
        var parsedPosting = new URL(url);
        if ((parsedPosting.protocol === 'http:' || parsedPosting.protocol === 'https:')
            && !parsedPosting.username && !parsedPosting.password) {{
          safePosting = parsedPosting.href;
        }}
      }} catch (e) {{}}
      if (safePosting) {{
        var postingLink = document.createElement('a');
        postingLink.className = 'go';
        postingLink.setAttribute('href', safePosting);
        postingLink.setAttribute('target', '_blank');
        postingLink.setAttribute('rel', 'noopener');
        postingLink.textContent = 'Posting ↗';
        postingCell.appendChild(postingLink);
      }} else {{
        var noLink = document.createElement('span');
        noLink.className = 'muted';
        noLink.textContent = 'no link';
        postingCell.appendChild(noLink);
      }}
      for (var missing = 0; missing < 2; missing += 1) {{
        var emptyCell = addCell('');
        var empty = document.createElement('span');
        empty.className = 'muted';
        empty.textContent = 'Not available';
        emptyCell.appendChild(empty);
      }}
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
        headers: {{ 'content-type': 'application/json', 'x-jobby-request': '1' }},
        body: JSON.stringify({{
          applicationId: card.dataset.id,
          company: card.dataset.company,
          role: card.dataset.role,
        }}),
      }})
        .then(function (r) {{ return r.json().then(function (x) {{ return {{ s: r.status, b: x }}; }}); }})
        .then(function (res) {{
          if (res.b && res.b.ok) {{
            rememberMark(card.dataset.id);
            rememberMove(card.dataset.id, 'done');
            moveToSubmitted(card);
          }}
          else {{
            b.disabled = false;
            b.textContent = 'Confirm submitted';
            var msg = (res.b && res.b.error) === 'queue not configured'
              ? 'Queue not set up on this deployment' : 'Could not save';
            b.insertAdjacentHTML('afterend', '<span class="scanmsg err"> ' + msg + '</span>');
          }}
        }})
        .catch(function () {{
          b.disabled = false; b.textContent = 'Confirm submitted';
          b.insertAdjacentHTML('afterend', '<span class="scanmsg err"> Could not reach the board</span>');
        }});
    }});
  }});

  // Apply clicks are remembered locally, because the watcher takes up to a
  // minute to mark the role in flight. Without this a reload in that window
  // puts the row back under Watching, which reads as the click failing.
  //
  // The memory is stamped and expires. It used to be a bare list of ids, and
  // the replay re-sent /api/apply for any id that still had an Apply OR Retry
  // button on the page. A card parked in Needs you has a Retry button, so a
  // parked application was re-queued on every single page load: the watcher
  // claimed it, spawned a runner, the runner opened a browser, found the same
  // blocker, parked again. That is the browser reopening over and over, and it
  // never stopped on its own because nothing ever cleared the id.
  //
  // The gap this bridges is one watcher tick, seconds. Ten minutes is already
  // far past the point where the server not reflecting a click means something
  // else is wrong, and re-sending forever cannot fix that.
  var APPLY_KEY = 'jobby.applying';
  var APPLY_TTL_MS = 10 * 60 * 1000;
  function readApplying() {{
    var raw;
    try {{ raw = JSON.parse(localStorage.getItem(APPLY_KEY) || '[]'); }} catch (e) {{ return []; }}
    if (!Array.isArray(raw)) return [];
    var now = Date.now();
    return raw
      .map(function (x) {{ return typeof x === 'string' ? {{ id: x, at: now }} : x; }})
      .filter(function (x) {{ return x && x.id && (now - (x.at || 0)) < APPLY_TTL_MS; }});
  }}
  function writeApplying(v) {{
    var now = Date.now();
    var out = (v || []).map(function (x) {{
      return typeof x === 'string' ? {{ id: x, at: now }} : x;
    }});
    try {{ localStorage.setItem(APPLY_KEY, JSON.stringify(out)); }} catch (e) {{}}
  }}
  function forgetApplying(id) {{
    writeApplying(readApplying().filter(function (x) {{ return x.id !== id; }}));
  }}
  // A queued card is drawn here, client side, before the watcher has marked
  // anything. It used to carry no controls at all: the phase said "Queued,
  // waiting for the runner to pick it up" and there was no way to take it back.
  // If the queue entry was one that could not succeed, that left the user
  // watching a browser reopen with nothing to press.
  function placeholderCard(company, role, id) {{
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
      + '<span class="step"></span><span class="step"></span><span class="step"></span></div>'
      + '<p class="phase">Queued<span class="why">waiting for the runner to pick it up</span></p>'
      + '<div class="act"></div>';
    art.querySelector('h3').textContent = company;
    art.querySelector('.role').textContent = role;
    if (id) {{
      art.setAttribute('data-id', id);
      var stop = document.createElement('button');
      stop.className = 'btn drop';
      stop.textContent = 'Stop';
      stop.title = 'Take this off the queue';
      stop.addEventListener('click', function () {{
        stop.disabled = true;
        stop.textContent = 'Stopping...';
        // Forget the queued apply first. Dropping without this leaves the
        // board's own memory asking for it again on the next reload.
        forgetApplying(id);
        var ids = readIds(DROP_KEY);
        if (ids.indexOf(id) === -1) ids.push(id);
        writeIds(DROP_KEY, ids);
        post('/api/drop', id).catch(function () {{}});
        art.remove();
        tabCount('flight', -1);
        tabCount('dropped', 1);
      }});
      art.querySelector('.act').appendChild(stop);
    }}
    grid.insertBefore(art, grid.firstChild);
  }}
  function leaveWatching(row, company, role, id) {{
    row.remove();
    tabCount('watch', -1);
    tabCount('flight', 1);
    placeholderCard(company, role, id);
  }}
  // On load, restore clicks the store has not caught up with, and forget the
  // ones it has (the row is gone from Watching, so the server agrees).
  //
  // It also re-sends the request. Restoring only the appearance was not enough:
  // a queue wipe once destroyed fourteen queued Apply requests, and because the
  // board still drew them as in flight, nothing looked wrong and they were
  // never run. Re-sending is safe because /api/apply dedupes by application id.
  (function replayApplying() {{
    var entries = readApplying();
    if (!entries.length) return;
    var still = [];
    entries.forEach(function (entry) {{
      var id = entry.id;
      var safe = id.replace(/"/g, '');
      // Apply comes from a Watching row, Retry from a Needs you card. Matching
      // only the Apply button meant a retried card was never restored, so it
      // reappeared under Needs you on every reload while the agent was queued.
      var btn = document.querySelector('.btn.apply[data-id="' + safe + '"], '
                                     + '.btn.retry[data-id="' + safe + '"]');
      if (!btn) return;                      // server already moved it
      var node = btn.closest('tr') || btn.closest('.card');
      if (!node) return;
      // A Needs you card carries a Retry button, and the server putting it
      // there is the server saying this one is blocked on the user. Re-sending
      // apply for it is how a parked application got picked up again on every
      // reload. Forget it instead.
      if (node.classList && (node.classList.contains('ready')
                             || node.classList.contains('hold'))) return;
      still.push(entry);
      var company = btn.dataset.company || '';
      var role = btn.dataset.role || '';
      if (!company) {{
        var h3 = node.querySelector('h3');
        var muted = node.querySelector('.muted') || node.querySelector('.role');
        company = h3 ? h3.textContent : '';
        role = muted ? muted.textContent : '';
      }}
      var from = tabOf(btn);
      node.remove();
      if (from) tabCount(from, -1);
      tabCount('flight', 1);
      placeholderCard(company, role, id);
      fetch('/api/apply', {{
        method: 'POST',
        headers: {{ 'content-type': 'application/json', 'x-jobby-request': '1' }},
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
        headers: {{ 'content-type': 'application/json', 'x-jobby-request': '1' }},
        body: JSON.stringify({{ applicationId: b.dataset.id }}),
      }})
        .then(function (r) {{ return r.json().then(function (x) {{ return {{ s: r.status, b: x }}; }}); }})
        .then(function (res) {{
          if (res.b && res.b.ok) {{
            var ids = readApplying();
            if (!ids.some(function (x) {{ return x.id === b.dataset.id; }})) {{
              ids.push({{ id: b.dataset.id, at: Date.now() }});
            }}
            writeApplying(ids);
            var row = b.closest('tr');
            var roleCell = row ? row.querySelector('.muted') : null;
            if (row) leaveWatching(row, b.dataset.company || '',
                                   roleCell ? roleCell.textContent : '', b.dataset.id);
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
      headers: {{ 'content-type': 'application/json', 'x-jobby-request': '1' }},
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
      // Cancel any apply this board is still remembering for it. Without
      // this, Stop moved the card to Dropped and the next reload put the same
      // application straight back on the queue.
      forgetApplying(id);
      var facts = dropFacts(b);
      var node = b.closest('tr') || b.closest('.card');
      var link = node ? node.querySelector('a.go, a[href^="http"]') : null;
      removeItem(b);
      tabCount('dropped', 1);
      placeholderDropped(facts, link ? link.href : '');
      post('/api/drop', id).catch(function () {{}});
    }});
  }});

  // Open & finish asks the watcher to raise the tab the agent left behind, on
  // the machine doing the work. Opening the posting from here instead would put
  // the user in a different browser, in front of a blank application, with none
  // of the filling the agent had already done.
  document.querySelectorAll('.btn.finish').forEach(function (b) {{
    b.addEventListener('click', function () {{
      var id = b.dataset.id;
      if (!id) return;
      var was = b.textContent;
      b.disabled = true;
      b.textContent = 'Bringing it up...';
      post('/api/focus', id)
        .then(function (res) {{
          b.textContent = (res && res.ok) ? 'Check your browser' : 'Could not reach it';
          setTimeout(function () {{ b.disabled = false; b.textContent = was; }}, 4000);
        }})
        .catch(function () {{
          b.disabled = false; b.textContent = was;
        }});
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
          rememberMove(id, 'flight');
          placeholderCard(b.dataset.company || '', b.dataset.role || '', b.dataset.id);
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

{found_lane.SCRIPT}
{profile_panel.SCRIPT}
{settings_panel.SCRIPT}
{premium_ui.SCRIPT}
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
    ap.add_argument("--embed-cap", type=int,
                    help="how many Found records to embed; publish lowers this "
                         "when the state would not fit KV's ceiling")
    ap.add_argument("--state-out", help="optional sanitized renderer-state JSON path")
    args = ap.parse_args()

    root = root_dir(args.root)
    if not root.exists():
        print("job-application dashboard: no store found; run store.py init first", file=sys.stderr)
        return 2

    # Both sides of this: main's embed cap, and this branch's raw --root
    # override. The override is the raw argument rather than the resolved store
    # path because that is what publish.py hands r2_artifacts, and the two have
    # to resolve applications/ to the same directory.
    state = load_state(root, args.root)
    if args.embed_cap:
        # Only the published state honours this. The local HTML is a file on
        # disk with no ceiling over it, and shrinking it would hide records for
        # no reason.
        state["_embedCap"] = args.embed_cap
    generated = clock.now_label()
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(state, generated), encoding="utf-8")
    if args.state_out:
        state_out = Path(args.state_out).expanduser()
        state_out.parent.mkdir(parents=True, exist_ok=True)
        state_out.write_text(
            json.dumps(publishable_state(state), separators=(",", ":")),
            encoding="utf-8",
        )

    print(json.dumps({
        "written": str(out),
        "greeting": state["profile"].get("firstName") or "there",
        "blocked": len([s for s in state["sessions"] if s.get("status") in ("active", "review")]),
        "inflight": len([s for s in state["sessions"] if s.get("status") == "inflight"]),
        "submitted": len([e for e in state["history"]
                          if e.get("event") in ("completed", "response", "interview", "offer", "rejected")]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
