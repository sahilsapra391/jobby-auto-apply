#!/usr/bin/env python3
"""One job description, one record, however many boards carry it.

A single posting is routinely syndicated to Greenhouse, Adzuna and LinkedIn
within an hour of going up, with three different URLs, three different company
spellings and three different truncations of the same body. Treating those as
three jobs would make the Found lane mostly noise, and would make "seen on 3
boards" — the thing that actually tells you a posting is real and being pushed
— impossible to say.

Matching runs in the order §3.2 sets out, and the first hit merges:

  1. Canonical URL. Tracking noise stripped; an aggregator record carrying an
     "apply on the company site" link is resolved to that link first.
  2. Redirect resolution, for shortened and wrapped apply links, capped at a
     few hops so a redirect loop cannot hang an ingest.
  3. Company + normalised title + location bucket, with the company matched
     through the registry's aliases so "Datadog, Inc." meets "Datadog".
  4. Content fingerprint: a 64-bit simhash of the body, Hamming distance <= 6
     within the same company. This is what catches the case the other three
     miss — different URL, cosmetically different title, same job.

Two guards here are not in the spec, both for the same reason: where the
evidence to tell two postings apart already exists, it gets used.

Rule 3 carries a fingerprint veto. When both records have a fingerprint and
those fingerprints are far apart, rule 3 does not merge — two different
requisitions at one company can share a title and a country, and merging those
hides one of them for good.

Rule 4 carries a location guard. A company opening the same role in New York
and in London writes the description once and posts it twice, so the bodies are
identical and rule 4 alone would merge them; they are two applications with two
different work-authorisation answers. An unknown country stays compatible with
everything, because one board reporting a country and another reporting none is
the ordinary case rather than a disagreement.

No network in the matching path by default: redirect resolution takes an
injected fetcher and is skipped entirely when none is given.
"""

import hashlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs_store  # noqa: E402
from seed_companies import LEGAL_SUFFIXES  # noqa: E402

# Query parameters that identify who sent you, never which job it is. A
# denylist rather than an allowlist, because some parameters are load-bearing:
# `gh_jid` on a company careers page IS the job, and dropping it would collapse
# every posting at that company into one record.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_cid", "gclid", "fbclid", "msclkid", "mc_cid",
    "mc_eid", "_ga", "_gl", "igshid", "ref", "refid", "ref_src", "referrer",
    "referer", "trk", "trackingid", "tracking_id", "trackid", "src", "source",
    "campaign", "campaignid", "gh_src", "lever-source", "lever-origin",
    "leversource", "recruiter", "sponsored", "from", "position", "pagenum",
    "seed", "originalsubdomain", "eblastid", "sid", "cid", "aid",
}

# How far a wrapped apply link may be followed. Four hops covers every real
# chain seen; more is a redirect loop or a tracker daisy chain, and an ingest
# that hangs on one is an ingest that reports nothing at all.
REDIRECT_MAX_HOPS = 4
REDIRECT_TIMEOUT_SECONDS = 10
USER_AGENT = "Jobby/1.0 (+https://github.com/sahilsapra391/jobby-auto-apply)"

# 64-bit simhash over 3-word shingles. Six bits of difference is roughly "the
# same text with a paragraph edited"; at eight, unrelated postings in the same
# boilerplate-heavy industry start colliding.
SIMHASH_BITS = 64
SIMHASH_MAX_DISTANCE = 6
SHINGLE_SIZE = 3
# Below this many shingles the fingerprint is mostly boilerplate and any two
# short postings look alike. A truncated aggregator excerpt lands here.
SIMHASH_MIN_SHINGLES = 12

WORD = re.compile(r"[a-z0-9][a-z0-9+#.]*")


# --- URLs -------------------------------------------------------------------

def _strip_tracking(query):
    kept = [(k, v) for k, v in parse_qsl(query, keep_blank_values=False)
            if k.lower() not in TRACKING_PARAMS]
    return urlencode(sorted(kept))


def canonical_url(url):
    """The URL worth storing and linking to: tracking gone, still clickable.

    Deliberately does not touch the host. This value goes into an href, and a
    `www.` stripped off a host that needs it is a dead link on the board.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        return ""
    netloc = parsed.netloc
    if netloc.endswith(":443") and parsed.scheme == "https":
        netloc = netloc[:-4]
    if netloc.endswith(":80") and parsed.scheme == "http":
        netloc = netloc[:-3]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, netloc, path, "", _strip_tracking(parsed.query), ""))


def url_key(url):
    """The comparison key. Never rendered, so it can be as lossy as it likes."""
    canon = canonical_url(url)
    if not canon:
        return ""
    parsed = urlparse(canon)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/").lower() or "/"
    return f"{host}{path}" + (f"?{parsed.query}" if parsed.query else "")


def origin_link(url):
    """The employer's own link hiding inside an aggregator's wrapper, or "".

    Adzuna, Jooble and most redirect trackers carry the real destination in a
    query parameter. Following it turns a record that would only ever match
    other aggregator copies into one that matches the ATS posting itself.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    host = parsed.netloc.lower()
    for _, value in parse_qsl(parsed.query, keep_blank_values=False):
        candidate = unquote(value).strip()
        if not candidate.lower().startswith(("http://", "https://")):
            continue
        other = urlparse(candidate)
        if other.netloc and other.netloc.lower() != host:
            return canonical_url(candidate)
    return ""


def http_redirect(url):
    """One hop: (status, location). The only place this module touches a network.

    HEAD, no redirect following, short timeout, honest User-Agent. Returns
    (0, "") on any failure, because a link that cannot be resolved is simply a
    link that stays as it is.
    """
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=REDIRECT_TIMEOUT_SECONDS) as response:
            return response.status, response.headers.get("location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("location", "") if exc.headers else ""
    except (urllib.error.URLError, OSError, ValueError):
        return 0, ""


def resolve_redirects(url, fetch=None, max_hops=REDIRECT_MAX_HOPS):
    """Follow a wrapped apply link to where it actually lands.

    `fetch` is injected so nothing in the test suite reaches the network. With
    no fetcher this is the identity function, which is also what makes it safe
    to call from the matching path by default.
    """
    current = canonical_url(url)
    if not current or fetch is None:
        return current
    seen = {url_key(current)}
    for _ in range(max_hops):
        status, location = fetch(current)
        if status not in (301, 302, 303, 307, 308) or not location:
            break
        nxt = canonical_url(urllib.parse.urljoin(current, location))
        if not nxt or url_key(nxt) in seen:
            break          # a loop, or a hop that went nowhere new
        seen.add(url_key(nxt))
        current = nxt
    return current


# --- content fingerprint ----------------------------------------------------

def shingles(text, size=SHINGLE_SIZE):
    """Overlapping word triples. Word counts alone match any two job ads."""
    words = WORD.findall(str(text or "").lower())
    if len(words) < size:
        return [" ".join(words)] if words else []
    return [" ".join(words[i:i + size]) for i in range(len(words) - size + 1)]


def simhash(text, bits=SIMHASH_BITS):
    """A 64-bit locality-sensitive hash of a posting body, or None if too thin.

    blake2b rather than the built-in hash(), which is randomised per process:
    a fingerprint that changes on restart would merge nothing and quietly
    duplicate everything.
    """
    grams = shingles(text)
    if len(grams) < SIMHASH_MIN_SHINGLES:
        return None
    vector = [0] * bits
    for gram, weight in Counter(grams).items():
        digest = int.from_bytes(hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest(),
                                "big")
        for bit in range(bits):
            vector[bit] += weight if (digest >> bit) & 1 else -weight
    out = 0
    for bit in range(bits):
        if vector[bit] > 0:
            out |= 1 << bit
    return out


def fingerprint(text):
    """The stored form: "simhash64:<hex>", or None when the body is too thin."""
    value = simhash(text)
    return None if value is None else f"simhash64:{value:016x}"


def parse_fingerprint(value):
    if not value or not str(value).startswith("simhash64:"):
        return None
    try:
        return int(str(value).split(":", 1)[1], 16)
    except ValueError:
        return None


def hamming(a, b):
    return bin(a ^ b).count("1")


def similar(a, b, max_distance=SIMHASH_MAX_DISTANCE):
    """Do two stored fingerprints describe the same body?"""
    left, right = parse_fingerprint(a), parse_fingerprint(b)
    if left is None or right is None:
        return False
    return hamming(left, right) <= max_distance


# --- company and location ---------------------------------------------------

def company_alias_map(companies):
    """alias -> registry key, so "Datadog, Inc." resolves to the same company."""
    out = {}
    for key, entry in (companies or {}).items():
        out[key] = key
        for alias in entry.get("aliases") or []:
            out[str(alias).lower()] = key
    return out


def norm_company(name, alias_map=None):
    """The registry key for a company name, or a normalised form of the name."""
    plain = " ".join("".join(c.lower() if c.isalnum() else " "
                             for c in str(name or "")).split())
    if not plain:
        return ""
    if alias_map:
        for candidate in (plain, plain.replace(" ", "")):
            if candidate in alias_map:
                return alias_map[candidate]
    # Fall back to the same legal-suffix stripping the registry key uses, so an
    # unregistered company still matches itself across boards.
    words = plain.split()
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words) or plain


def buckets_compatible(a, b):
    """Could two location buckets be the same place?

    `unknown` is compatible with everything on purpose: one board reporting a
    country and another reporting none is the ordinary case, and treating that
    as a disagreement would duplicate most syndicated postings.
    """
    return a == b or "unknown" in (a, b)


def location_bucket(job):
    """Country code, or `remote`. Coarse on purpose.

    One posting says "New York, NY", another says "New York, United States"
    and a third says "NYC / Hybrid". City strings do not agree across boards;
    countries do.
    """
    if str(job.get("workplace") or "").lower() == "remote":
        return "remote"
    location = job.get("location") or {}
    country = str(location.get("country") or "").strip().upper()
    if country:
        return country
    raw = str(location.get("raw") or "").lower()
    if "remote" in raw or "anywhere" in raw:
        return "remote"
    return "unknown"


# --- identity ---------------------------------------------------------------

def dedupe_key(job, alias_map=None):
    """The canonical identity a fresh record's id is derived from.

    An ATS or careers URL is the requisition itself and is unique per opening,
    so it is the strongest key available. Everything else falls back to
    company, title and country, which is stable across boards that each invent
    their own URL for the same job.
    """
    for source in job.get("sources") or []:
        if jobs_store.canonical_rank(source.get("sourceClass")) <= 1 and source.get("url"):
            key = url_key(source["url"])
            if key:
                return f"url:{key}"
    canon = url_key(job.get("canonicalUrl"))
    if canon and jobs_store.canonical_rank(_best_class(job)) <= 1:
        return f"url:{canon}"
    return "job:{}|{}|{}".format(norm_company(job.get("company"), alias_map),
                                 jobs_store.norm_title(job.get("title")),
                                 location_bucket(job))


def _best_class(job):
    classes = [s.get("sourceClass") for s in (job.get("sources") or [])
               if isinstance(s, dict)]
    return min(classes, key=jobs_store.canonical_rank, default="other")


def job_id(job, alias_map=None, discriminator=""):
    """The record id, derived from the canonical identity.

    `discriminator` is only ever supplied when the identity alone is not enough
    to tell two records apart — see resolve(), which is the one place that
    decides that. Keeping it a parameter rather than folding it in
    unconditionally means an ordinary record's id stays a pure function of what
    the record *is*, and does not move because an employer fixed a typo.
    """
    key = dedupe_key(job, alias_map)
    if discriminator:
        key = f"{key}#{discriminator}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def sighting_urls(job):
    """Every URL this record can be recognised by, origin links included.

    The origin link matters on both sides of the comparison, not just the
    incoming one: an Adzuna record stored on Monday must still be found by the
    Greenhouse sighting that arrives on Tuesday, and the only thing the two
    have in common is the employer URL buried in Adzuna's query string.
    """
    urls = []
    for source in job.get("sources") or []:
        if source.get("url"):
            urls.append(source["url"])
    if job.get("canonicalUrl"):
        urls.append(job["canonicalUrl"])
    for url in list(urls):
        embedded = origin_link(url)
        if embedded:
            urls.append(embedded)
    seen, out = set(), []
    for url in urls:
        key = url_key(url)
        if key and key not in seen:
            seen.add(key)
            out.append(url)
    return out


class Index:
    """Everything already stored, arranged so matching is a lookup not a scan.

    Built once per ingest. A linear pass per incoming job over a 3,000-job pool
    would be four million comparisons an hour for no reason.
    """

    def __init__(self, jobs, companies=None):
        self.jobs = jobs
        self.alias_map = company_alias_map(companies)
        self.by_url = {}
        self.by_identity = {}
        self.by_company = {}
        for job_key, job in jobs.items():
            self.add(job_key, job)

    def add(self, job_key, job):
        """Index one job. Also used during an ingest, so a record created two
        minutes ago is matchable by the next sighting in the same run."""
        self.jobs[job_key] = job
        for url in sighting_urls(job):
            self.by_url.setdefault(url_key(url), job_key)
        company = norm_company(job.get("company"), self.alias_map)
        identity = (company, job.get("normTitle") or jobs_store.norm_title(job.get("title")),
                    location_bucket(job))
        self.by_identity.setdefault(identity, job_key)
        if job_key not in self.by_company.setdefault(company, []):
            self.by_company[company].append(job_key)


def match(index, incoming, fetch=None):
    """The stored job this sighting belongs to, or (None, None).

    Returns (job_id, how) so the reason a merge happened is reportable rather
    than a mystery — dedupe health is only checkable if the mechanism is named.
    """
    urls = sighting_urls(incoming)

    for url in urls:
        key = url_key(url)
        if key and key in index.by_url:
            return index.by_url[key], "canonical-url"

    if fetch is not None:
        for url in urls:
            key = url_key(resolve_redirects(url, fetch=fetch))
            if key and key in index.by_url:
                return index.by_url[key], "redirect"

    company = norm_company(incoming.get("company"), index.alias_map)
    title = incoming.get("normTitle") or jobs_store.norm_title(incoming.get("title"))
    identity = (company, title, location_bucket(incoming))
    candidate = index.by_identity.get(identity)
    if candidate is not None:
        held = index.jobs[candidate]
        # The veto. Same company, same title, same country is usually the same
        # job — but not when both bodies are known and they are nothing alike.
        # Two open requisitions on one team read exactly like this, and merging
        # them would hide one of them for good.
        both_known = incoming.get("descFingerprint") and held.get("descFingerprint")
        if not both_known or similar(incoming["descFingerprint"], held["descFingerprint"]):
            return candidate, "company-title-location"

    mine = incoming.get("descFingerprint")
    if mine:
        bucket = location_bucket(incoming)
        for candidate in index.by_company.get(company, []):
            held = index.jobs[candidate]
            # A company that opens the same role in New York and London writes
            # one description and posts it twice. Those are two applications
            # with two different work-authorisation answers, and merging them
            # hides one for good. Where both countries are known and they
            # differ, that is enough to say no.
            if not buckets_compatible(bucket, location_bucket(held)):
                continue
            if similar(mine, held.get("descFingerprint")):
                return candidate, "fingerprint"
    return None, None


def resolve(index, incoming, fetch=None):
    """(job_id, how). `how` is None when this is a job nobody has seen before.

    This function owns one guarantee, and the guarantee is the whole reason it
    exists rather than callers doing match() and job_id() themselves:

        **an unmatched job never comes back with an id that is already taken.**

    Without it there is a seam. The fallback dedupe key is exactly the tuple
    rule 3 matches on — company, normalised title, location bucket — so two
    aggregator-only requisitions that rule 3 looked at and *declined to merge*
    still derive the same id, and an upsert keyed on id alone folds them
    together anyway. The matcher's refusal is overruled by the id.

    That is not hypothetical. Two Working Nomads postings for one role at one
    company, one in Canada and one in Japan, bodies nine bits apart, came out of
    a real thirteen-second run as a single record wearing a "seen on 2 boards"
    chip — which is the most convincing possible disguise, because that chip
    means exactly the thing that did not happen.

    A collision here therefore implies rule 3 found this identity and refused
    it, and rule 3 only refuses through the fingerprint veto, which requires
    both fingerprints to be known. So there is always something to discriminate
    with at the moment it is needed. Where there genuinely is not — a body too
    thin to fingerprint — the old conservative merge is left exactly as it was,
    because with no evidence, merging is the better guess.
    """
    found, how = match(index, incoming, fetch=fetch)
    if found is not None:
        return found, how

    candidate = job_id(incoming, index.alias_map)
    if candidate not in index.jobs:
        return candidate, None

    fingerprint_ = incoming.get("descFingerprint")
    if not fingerprint_:
        # Nothing to tell them apart. Conservative, and unchanged.
        return candidate, None
    return job_id(incoming, index.alias_map, discriminator=fingerprint_), None
