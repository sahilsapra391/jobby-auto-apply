#!/usr/bin/env python3
"""The posting body for one application, fetched when it is needed.

The pool keeps a 1,500-character `descriptionExcerpt` and nothing more, because
6,389 records with full bodies is tens of megabytes read and rewritten on every
sweep. Promotion copies only what the job record holds, so an application that
arrived by pressing the eye in Found has no job description at all.

That was invisible while postings were hand-fetched. Once Found started feeding
Apply on its own, every document build failed with `invalid_request`, which is
the code `document_job` records when the bundle cannot be assembled. The reason
underneath was always the same sentence: the application has no job description.

So the body is fetched here, once per application, at the moment the brief is
built. One request, against the ATS's own JSON endpoint where it has one and
the posting's HTML where it does not.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "sources"))

import base  # noqa: E402

# Long enough for a real posting, short enough that a mis-parsed page cannot
# push megabytes of navigation into a provider prompt.
MAX_CHARS = 40_000
MIN_CHARS = 400


class Unfetchable(RuntimeError):
    """The posting body could not be read. Carries a user-readable reason."""


def _greenhouse(url):
    parts = [p for p in url.split("?")[0].split("/") if p]
    if len(parts) < 4 or parts[-2] != "jobs":
        return None
    token, job_id = parts[-3], parts[-1]
    if not job_id.isdigit():
        return None
    return (f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}",
            lambda data: data.get("content") or "")


def _lever(url):
    parts = [p for p in url.split("?")[0].split("/") if p]
    if len(parts) < 3:
        return None
    token, job_id = parts[-2], parts[-1]
    return (f"https://api.lever.co/v0/postings/{token}/{job_id}",
            lambda data: " ".join(filter(None, [
                data.get("descriptionPlain") or data.get("description") or "",
                *[f"{s.get('text', '')} {s.get('content', '')}"
                  for s in (data.get("lists") or []) if isinstance(s, dict)],
                data.get("additionalPlain") or data.get("additional") or "",
            ])))


def _ashby(url):
    parts = [p for p in url.split("?")[0].split("/") if p]
    if len(parts) < 3:
        return None
    token, job_id = parts[-2], parts[-1]
    return (
        "https://api.ashbyhq.com/posting-api/job-board/"
        f"{token}?includeCompensation=true&jobPostingId={job_id}",
        lambda data: next(
            (job.get("descriptionHtml") or job.get("descriptionPlain") or ""
             for job in (data.get("jobs") or []) if job.get("id") == job_id),
            "",
        ),
    )


def _smartrecruiters(url):
    # The public route is per company, not global: /v1/jobs/{id} answers 404 for
    # a posting that /v1/companies/{company}/postings/{id} returns in full.
    parts = [p for p in url.split("?")[0].split("/") if p]
    if len(parts) < 2 or not parts[-1].isdigit():
        return None
    company, posting = parts[-2], parts[-1]
    return (f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting}",
            lambda data: " ".join(
                (section or {}).get("text", "")
                for group in ((data.get("jobAd") or {}).get("sections") or {}).values()
                for section in ([group] if isinstance(group, dict) else group or [])
            ))


def _workday(url):
    # Every Workday tenant serves the same posting through its CXS API, which
    # is the only readable form: the page itself is an empty single-page app
    # shell, so fetching it returns navigation and no job.
    match = re.match(
        r"https://([^.]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)/job/(.+)$",
        url.split("?")[0],
    )
    if not match:
        return None
    tenant, pod, site, path = match.groups()
    return (f"https://{tenant}.{pod}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/job/{path}",
            lambda data: (data.get("jobPostingInfo") or {}).get("jobDescription") or "")


def _oracle(url):
    # Oracle Recruiting Cloud serves a single-page shell that answers a direct
    # fetch with a redirect to its own index, so the page route reports the
    # posting as delisted when it is live. The REST resource behind it returns
    # the description, and the requisition id and site are both in the URL.
    match = re.match(
        r"https://([^/]+)/hcmUI/CandidateExperience/(?:[^/]+/)?sites/([^/]+)/"
        r"(?:job|requisitions/preview)/(\d+)",
        url.split("?")[0],
    )
    if not match:
        return None
    host, site, requisition = match.groups()
    api = (f"https://{host}/hcmRestApi/resources/latest/"
           f"recruitingCEJobRequisitionDetails?onlyData=true&expand=all"
           f'&finder=ById;Id="{requisition}",siteNumber={site}')

    def read(data):
        items = data.get("items") or []
        if not items:
            return ""
        item = items[0]
        # The body is split across two fields on most tenants, and either can
        # be the one carrying the requirements.
        return " ".join(part for part in (item.get("ExternalDescriptionStr"),
                                          item.get("ExternalQualificationsStr"))
                        if part)

    return api, read


# Ordered by how much of the queue each one carries, so the common case is
# matched first. A host this does not know falls through to the page itself.
ENDPOINTS = (
    ("greenhouse.io", _greenhouse),
    ("lever.co", _lever),
    ("ashbyhq.com", _ashby),
    ("smartrecruiters.com", _smartrecruiters),
    ("myworkdayjobs.com", _workday),
    ("oraclecloud.com", _oracle),
)


def _from_api(fetcher, url):
    for host, build in ENDPOINTS:
        if host not in url:
            continue
        route = build(url)
        if route is None:
            return ""
        api_url, extract = route
        data, _response = fetcher.get_json(api_url, conditional=False)
        if data is None:
            # A closed posting 404s on the API while its page still renders, and
            # a tenant can move. Neither is worth failing the application over
            # before the page has been tried, so this returns empty rather than
            # raising and the caller falls through.
            return ""
        try:
            return base.strip_html(extract(data))
        except (AttributeError, KeyError, TypeError):
            return ""
    return ""


def _from_page(fetcher, url):
    try:
        response = fetcher.get(url, conditional=False)
    except OSError as exc:
        raise Unfetchable(f"the posting page could not be reached: {exc}"[:200]) from exc
    if not response.ok:
        # A board answers a withdrawn posting by redirecting to its own index,
        # and the API 404s the same posting. Reporting "HTTP 302" sends the user
        # to look for a network fault that is not there.
        if response.status in (301, 302, 303, 307, 308) or response.status == 404:
            raise Unfetchable("the posting is no longer listed on its job board")
        raise Unfetchable(base.describe(response, "the posting page"))
    return base.strip_html(response.body.decode("utf-8", "replace"))


def fetch(url, root=None, fetcher=None):
    """The posting body as plain text. Raises Unfetchable with the reason."""
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise Unfetchable("the application has no posting address")
    fetcher = fetcher or base.Fetcher(root=root)
    text = _from_api(fetcher, url)
    if len(text) < MIN_CHARS:
        # An ATS route that returned a stub is worth one more request against
        # the page a person would read, rather than failing the application.
        page = _from_page(fetcher, url)
        if len(page) > len(text):
            text = page
    if len(text) < MIN_CHARS:
        raise Unfetchable(
            f"the posting body was only {len(text)} characters, too short to tailor against"
        )
    return text[:MAX_CHARS]


def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Fetch one posting body.")
    parser.add_argument("url")
    parser.add_argument("--chars", action="store_true", help="print the length only")
    args = parser.parse_args()
    try:
        text = fetch(args.url)
    except Unfetchable as exc:
        print(json.dumps({"ok": False, "why": str(exc)}, indent=2))
        return 1
    print(json.dumps({"ok": True, "characters": len(text)}, indent=2)
          if args.chars else text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
