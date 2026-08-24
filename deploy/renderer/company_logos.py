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
    domain = _employer_domain(entry, posting_url)
    if domain:
        return "/api/company-logo?domain=" + urllib.parse.quote(domain, safe="")
    return ""


def mark(company, entry=None, posting_url=""):
    """A decorative logo with a monogram that remains when an image fails."""
    fallback = html.escape(initials(company))
    source = proxy_url(entry, posting_url)
    image = (
        f'<img src="{html.escape(source)}" alt="" loading="lazy" data-company-logo>'
        if source else ""
    )
    return f'<span class="company-mark" aria-hidden="true"><span>{fallback}</span>{image}</span>'
