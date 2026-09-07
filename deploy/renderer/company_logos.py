#!/usr/bin/env python3
"""Safe company-logo metadata for the self-contained dashboard.

Real feed logos and direct employer domains are used when the stored record
actually knows them. ATS and aggregator hosts are never shown as the employer.
When no trustworthy source exists, the UI renders a deterministic monogram.
"""

import html
import re
import urllib.parse


LOGO_KEYS = (
    "logoUrl", "companyLogo", "companyLogoUrl", "company_logo",
    "company_logo_url", "logo_url", "logo",
)
DOMAIN_KEYS = ("companyDomain", "companyWebsite", "website")
NAME_STOPWORDS = {
    "the", "and", "inc", "llc", "ltd", "corp", "corporation", "company",
    "group", "holdings", "international", "technologies", "technology",
}
GENERIC_LINK_HOSTS = (
    "facebook.com", "instagram.com", "linkedin.com", "youtube.com", "youtu.be",
    "twitter.com", "x.com", "glassdoor.com", "github.com", "wikipedia.org",
    "google.com", "googleusercontent.com", "microsoft.com", "apple.com",
)
TRUSTED_SOURCE_HOSTS = {
    "cdn-images.himalayas.app", "remotive.com", "remoteok.com",
    "wwr-pro.s3.amazonaws.com", "we-work-remotely.imgix.net",
}
INFRA_HOST_PARTS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com",
    "workday.com", "smartrecruiters.com", "workable.com", "jobvite.com",
    "icims.com", "oraclecloud.com", "successfactors.com", "bamboohr.com",
    "breezy.hr", "recruitee.com", "zohorecruit.com", "taleo.net",
    "dayforcehcm.com", "paylocity.com", "paycomonline.net", "paycor.com",
    "ultipro.com", "ukg.com", "applytojob.com", "applicantpro.com",
    "hireology.com", "jazz.co", "teamtailor.com", "pinpointhq.com",
    "eightfold.ai", "phenompeople.com", "trinethire.com", "jobtrain.co.uk",
    "jobviewtrack.com", "careerpuck.com",
    "linkedin.com", "indeed.com", "adzuna.", "jooble.", "careerjet.",
    "remotive.com", "remoteok.com", "himalayas.app", "weworkremotely.com",
    # **reed.co.uk was missing while every other aggregator was here**, and it
    # is this project's largest single source of postings that name a host at
    # all: 2,577 of 23,633, every one of which took `PLAN_POSTING_HOST` and
    # drew reed's own favicon as the employer's mark. Measured 2026-09-06,
    # after 47 postings were repointed off a feed publisher onto their real
    # employers and every one of them still showed a job board's logo.
    #
    # `rippling.com` (521 postings via ats.rippling.com) and `usajobs.gov`
    # (343) are the next two of this shape and are deliberately not added
    # here: Rippling is an employer as well as an ATS, so the trade is not the
    # same one, and it should be measured rather than assumed.
    "reed.co.uk",
)


def _is_infrastructure_host(host):
    return any(
        part.endswith(".") and part in host
        or host == part
        or host.endswith("." + part)
        for part in INFRA_HOST_PARTS
    )


def safe_http_url(value):
    text = str(value or "").strip()
    if not text or any(c.isspace() or ord(c) < 32 for c in text):
        return ""
    try:
        parsed = urllib.parse.urlsplit(text)
        _ = parsed.port
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    return urllib.parse.urlunsplit(parsed._replace(scheme=parsed.scheme.lower()))


def _hostname(value):
    url = safe_http_url(value)
    if not url:
        text = str(value or "").strip().lower().strip(".")
        if re.fullmatch(r"[a-z0-9.-]+", text) and "." in text:
            return text
        return ""
    return (urllib.parse.urlsplit(url).hostname or "").lower().strip(".")


# Where each applicant tracking system keeps the employer's own account name.
# PATH means the first path segment (boards.greenhouse.io/twilio/jobs/1), HOST
# means the leftmost label (davies.pinpointhq.com). The slug is the employer's
# own choice of name, which is what makes it worth more than the host.
ATS_SLUG_IN_PATH = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com",
    "workable.com", "jobvite.com", "breezy.hr", "teamtailor.com",
)
ATS_SLUG_IN_HOST = (
    "pinpointhq.com", "recruitee.com", "myworkdayjobs.com", "bamboohr.com",
    "applytojob.com", "zohorecruit.com", "hireology.com",
)
LEGAL_SUFFIXES = {
    "inc", "llc", "ltd", "limited", "corp", "corporation", "co", "company",
    "gmbh", "bv", "nv", "ag", "sa", "plc", "pvt", "private", "pty", "srl",
    "spa", "oy", "ab", "as", "aps", "holdings", "group",
}
# Short slugs collide with unrelated businesses far too easily, and a wrong
# employer mark is worse than no mark at all.
MIN_CANDIDATE_LENGTH = 4


def _compact(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _name_core(company):
    """The company name reduced to the part a domain would plausibly use."""
    words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", str(company or ""))]
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return "".join(words)


def ats_slug(posting_url):
    """The employer's own account name inside an applicant tracking URL."""
    host = _hostname(posting_url)
    if not host:
        return ""
    for part in ATS_SLUG_IN_HOST:
        if host.endswith("." + part):
            label = host.split(".")[0]
            return label if label not in {"www", "jobs", "apply", "careers"} else ""
    for part in ATS_SLUG_IN_PATH:
        if host == part or host.endswith("." + part):
            path = urllib.parse.urlsplit(safe_http_url(posting_url) or "").path
            segments = [seg for seg in path.split("/") if seg]
            return segments[0].lower() if segments else ""
    return ""


def _slug_corroborates(slug, company):
    """Does the slug look like it belongs to the company the record names?

    The slug alone is not enough. `davies` is a real Greenhouse account and also
    a domain owned by someone else entirely, so a slug is only followed when the
    company name agrees with it.
    """
    slug, core = _compact(slug), _name_core(company)
    if not slug or not core or len(slug) < MIN_CANDIDATE_LENGTH:
        return False
    return slug == core or core.startswith(slug) or slug.startswith(core)


CANDIDATE_TLDS = (".com", ".io", ".ai", ".co")
MAX_CANDIDATES = 6


def candidate_bases(entry, posting_url=""):
    """The second-level labels worth trying, best first."""
    company = (entry or {}).get("company") or ""
    bases = []
    slug = ats_slug(posting_url)
    if _slug_corroborates(slug, company):
        bases.append(_compact(slug))
    core = _name_core(company)
    if len(core) >= MIN_CANDIDATE_LENGTH and core not in bases:
        bases.append(core)
    return bases


def candidate_domains(entry, posting_url=""):
    """Domains to try, best first, when nothing trustworthy is recorded.

    Best effort by construction, and safe because of how it fails: an
    unregistered or unknown domain makes the logo endpoint answer 404 and the
    monogram stays. The guard is against the other failure, a domain that
    resolves to a real but different company, which is why every base has to
    agree with the recorded company name.

    Measured over 200 randomly sampled companies from a 10,990-job store on
    2026-08-25: a known employer domain or one of these candidates returned an
    image for 168, or 84%. An earlier version also tried the first word of the
    name on its own, which reached 89% and was discarded, because what it added
    was Federal Deposit Insurance Corporation resolving to federal.ai and Bureau
    of Prisons to bureau.ai. A stranger's logo is worse than no logo.
    """
    company = (entry or {}).get("company") or ""
    bases = []
    slug = ats_slug(posting_url)
    if _slug_corroborates(slug, company):
        bases.append(_compact(slug))
    core = _name_core(company)
    if len(core) >= MIN_CANDIDATE_LENGTH:
        bases.append(core)
    seen, out = set(), []
    for base in bases:
        for tld in CANDIDATE_TLDS:
            domain = base + tld
            if domain not in seen:
                seen.add(domain)
                out.append(domain)
    return out[:MAX_CANDIDATES]


def _employer_domain(entry, posting_url=""):
    for key in DOMAIN_KEYS:
        host = _hostname((entry or {}).get(key))
        if host and not _is_infrastructure_host(host):
            return host
    host = _hostname(posting_url)
    if host and not _is_infrastructure_host(host):
        return host
    return ""


def website_from_description(company, description):
    """Find an employer-owned site that the posting itself links to."""
    body = html.unescape(str(description or ""))
    candidates = re.findall(r'''(?:href\s*=\s*["']|\b)(https?://[^\s"'<>]+)''', body, re.I)
    tokens = [
        token.lower() for token in re.findall(r"[A-Za-z0-9]+", str(company or ""))
        if len(token) >= 3 and token.lower() not in NAME_STOPWORDS
    ]
    for candidate in candidates:
        host = _hostname(candidate.rstrip(".,);"))
        compact_host = re.sub(r"[^a-z0-9]", "", host)
        if not host or _is_infrastructure_host(host):
            continue
        if any(host == item or host.endswith("." + item) for item in GENERIC_LINK_HOSTS):
            continue
        if any(re.sub(r"[^a-z0-9]", "", token) in compact_host for token in tokens):
            return "https://" + host
    return ""


def initials(company):
    words = re.findall(r"[A-Za-z0-9]+", str(company or ""))
    useful = [w for w in words if w.lower() not in NAME_STOPWORDS]
    words = useful or words
    if not words:
        return "?"
    return (words[0][0] + (words[1][0] if len(words) > 1 else "")).upper()


def proxy_url(entry=None, posting_url=""):
    entry = entry or {}
    for key in LOGO_KEYS:
        source = safe_http_url(entry.get(key))
        host = _hostname(source)
        if source and host in TRUSTED_SOURCE_HOSTS:
            return "/api/company-logo?src=" + urllib.parse.quote(source, safe="")
    known = _employer_domain(entry, posting_url)
    if known:
        return "/api/company-logo?domain=" + urllib.parse.quote(known, safe="")
    guesses = candidate_domains(entry, posting_url)
    if not guesses:
        return ""
    return ("/api/company-logo?domain=" + urllib.parse.quote(guesses[0], safe="")
            + "&alt=1")


# How the Found payload names a logo without spending bytes on it. The page
# weight budget leaves about 21 bytes a job at the embed cap, and a domain
# string is longer than that, so what travels is a single digit saying which
# mechanical derivation the client should run over data it already holds. The
# policy, which host counts as infrastructure and whether a slug corroborates
# the company name, stays here.
PLAN_NONE = 0
PLAN_POSTING_HOST = 1   # the posting URL's own host is the employer
PLAN_PATH_SLUG = 2      # first path segment of the posting URL
PLAN_HOST_SLUG = 3      # leftmost label of the posting URL's host
PLAN_NAME_CORE = 4      # the company name, compacted


def logo_plan(entry, posting_url=""):
    """Which derivation the client should run, as one digit."""
    entry = entry or {}
    for key in LOGO_KEYS:
        source = safe_http_url(entry.get(key))
        if source and _hostname(source) in TRUSTED_SOURCE_HOSTS:
            return PLAN_NONE  # a real feed logo travels as a URL instead
    for key in DOMAIN_KEYS:
        if _hostname(entry.get(key)) and not _is_infrastructure_host(_hostname(entry.get(key))):
            return PLAN_NONE  # rare, and travels as a URL
    host = _hostname(posting_url)
    if host and not _is_infrastructure_host(host):
        return PLAN_POSTING_HOST
    company = entry.get("company") or ""
    slug = ats_slug(posting_url)
    if _slug_corroborates(slug, company):
        for part in ATS_SLUG_IN_HOST:
            if host.endswith("." + part):
                return PLAN_HOST_SLUG
        return PLAN_PATH_SLUG
    if len(_name_core(company)) >= MIN_CANDIDATE_LENGTH:
        return PLAN_NAME_CORE
    return PLAN_NONE


def mark(company, entry=None, posting_url=""):
    """A decorative logo with a monogram that remains when an image fails."""
    fallback = html.escape(initials(company))
    source = proxy_url(entry, posting_url)
    image = (
        f'<img src="{html.escape(source)}" alt="" loading="lazy" data-company-logo>'
        if source else ""
    )
    return f'<span class="company-mark" aria-hidden="true"><span>{fallback}</span>{image}</span>'
