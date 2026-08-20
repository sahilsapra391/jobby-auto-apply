#!/usr/bin/env python3
"""Fill the company registry, which is what makes hourly scanning cheap.

Tier 1 costs nothing per company: an ATS board is one JSON request with no key
and no rate limit worth worrying about. The whole game is therefore knowing
which companies to ask, so `companies.json` is the flywheel — every company
that enters it is covered for free from then on, forever.

Two sources feed it.

Roles already applied to. `roles.json` holds every posting this system has
ever seen, with the URL it was applied through, so the ATS and its board token
can be read straight off the URL rather than guessed. These are the highest
confidence entries in the registry: an application was actually filed there.

A curated per-region pack. Every token in SEED_PACK was confirmed live against
the platform's public board API on 2026-08-19 (evidence in
references/sources-registry.md); anything that did not resolve was dropped
rather than shipped hopefully. A wrong token is not harmless — it is a 404 on
every ingest forever, and the point of the error accounting is that nobody has
to notice it by hand.

Companies whose URL yields no derivable token are still recorded, with the
careers page and `ats: unknown`. That is a real state and Phase 3's resolver
is what clears it; inventing a token to fill the field would be worse.

  python3 scripts/seed_companies.py --dry-run
  python3 scripts/seed_companies.py [--store DIR] [--no-pack] [--no-roles]
"""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs_store  # noqa: E402

# How a posting URL names its ATS and that employer's board. Ordered: the more
# specific host patterns first, since several platforms share a suffix.
ATS_URL_PATTERNS = [
    ("greenhouse", re.compile(
        r"^(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_.-]+)")),
    ("greenhouse", re.compile(r"^boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_.-]+)")),
    ("lever", re.compile(r"^jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"^jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)")),
    ("smartrecruiters", re.compile(r"^jobs\.smartrecruiters\.com/([A-Za-z0-9_.-]+)")),
    ("workable", re.compile(r"^apply\.workable\.com/([A-Za-z0-9_.-]+)")),
    ("recruitee", re.compile(r"^([A-Za-z0-9-]+)\.recruitee\.com")),
    ("zoho", re.compile(r"^([A-Za-z0-9-]+)\.zohorecruit\.(?:com|in|eu)")),
    ("personio", re.compile(r"^([A-Za-z0-9-]+)\.jobs\.personio\.(?:de|com)")),
    ("breezy", re.compile(r"^([A-Za-z0-9-]+)\.breezy\.hr")),
    ("teamtailor", re.compile(r"^([A-Za-z0-9-]+)\.teamtailor\.com")),
    ("bamboohr", re.compile(r"^([A-Za-z0-9-]+)\.bamboohr\.com")),
    ("jazzhr", re.compile(r"^([A-Za-z0-9-]+)\.applytojob\.com")),
    ("rippling", re.compile(r"^api\.rippling\.com/platform/api/ats/v1/board/([A-Za-z0-9-]+)")),
    ("smartrecruiters", re.compile(r"^careers\.smartrecruiters\.com/([A-Za-z0-9_.-]+)")),
    ("workday", re.compile(r"^([A-Za-z0-9-]+)\.wd\d+\.myworkdayjobs\.com")),
    ("darwinbox", re.compile(r"^([A-Za-z0-9-]+)\.darwinbox\.in")),
]

# Platforms with a connector that can fetch a board directly. Everything else
# in the registry is recorded but not yet reachable without a resolver.
TIER1_PLATFORMS = ("greenhouse", "lever", "ashby", "smartrecruiters", "workable",
                   "recruitee", "personio", "breezy", "rippling")

# Aggregator and search hosts. A posting reached through one of these says
# nothing about the employer's ATS, so it must not become a board token.
NOT_A_BOARD = {
    "www.linkedin.com", "in.linkedin.com", "linkedin.com", "www.indeed.com",
    "www.glassdoor.com", "www.google.com", "www.instahyre.com", "www.naukri.com",
    "wellfound.com", "angel.co", "www.monster.com", "www.ziprecruiter.com",
}

# Suffixes that are legal furniture rather than the name anyone uses. Stripping
# them is what lets an aggregator's "Datadog, Inc." match the ATS's "Datadog".
LEGAL_SUFFIXES = (
    "incorporated", "inc", "llc", "l.l.c", "ltd", "limited", "plc", "corp",
    "corporation", "co", "company", "gmbh", "ag", "bv", "b.v", "nv", "n.v",
    "sa", "s.a", "sas", "srl", "spa", "ab", "as", "oy", "aps", "pty", "pte",
    "pvt", "private", "holdings", "group", "technologies", "technology", "labs",
)

# Curated per-region coverage, so a fresh install is not limited to whatever
# the user happened to apply to. Region is where the company is headquartered,
# not where the roles are; the config's location filter does that job.
SEED_PACK = [
    ("Careem", "greenhouse", "careem", "AE"),
    ("Airtasker", "lever", "airtasker", "AU"),
    ("Culture Amp", "greenhouse", "cultureamp", "AU"),
    ("Deputy", "lever", "deputy", "AU"),
    ("Eucalyptus", "greenhouse", "eucalyptus", "AU"),
    ("Immutable", "lever", "immutable", "AU"),
    ("Faire", "greenhouse", "faire", "CA"),
    ("Hootsuite", "greenhouse", "hootsuite", "CA"),
    ("Thinkific", "greenhouse", "thinkific", "CA"),
    ("Wealthsimple", "lever", "wealthsimple", "CA"),
    ("Babbel", "ashby", "babbel", "DE"),
    ("Choco", "ashby", "choco", "DE"),
    ("Contentful", "greenhouse", "contentful", "DE"),
    ("Forto", "ashby", "forto", "DE"),
    ("GetYourGuide", "greenhouse", "getyourguide", "DE"),
    ("HelloFresh", "greenhouse", "hellofresh", "DE"),
    ("N26", "greenhouse", "n26", "DE"),
    ("Raisin", "greenhouse", "raisin", "DE"),
    ("SumUp", "greenhouse", "sumup", "DE"),
    ("Trade Republic", "greenhouse", "traderepublic", "DE"),
    ("Trustpilot", "greenhouse", "trustpilot", "DK"),
    ("Bolt", "ashby", "bolt", "EE"),
    ("Pipedrive", "lever", "pipedrive", "EE"),
    ("Typeform", "greenhouse", "typeform", "ES"),
    ("Algolia", "greenhouse", "algolia", "FR"),
    ("Back Market", "ashby", "backmarket", "FR"),
    ("Dataiku", "greenhouse", "dataiku", "FR"),
    ("Doctolib", "greenhouse", "doctolib", "FR"),
    ("Ledger", "lever", "ledger", "FR"),
    ("Mistral AI", "lever", "mistral", "FR"),
    ("Sorare", "ashby", "sorare", "FR"),
    ("Spendesk", "ashby", "spendesk", "FR"),
    ("Cleo", "greenhouse", "cleo", "GB"),
    ("Freetrade", "ashby", "freetrade", "GB"),
    ("GoCardless", "greenhouse", "gocardless", "GB"),
    ("Grafana Labs", "greenhouse", "grafanalabs", "GB"),
    ("Improbable", "ashby", "improbable", "GB"),
    ("Monzo", "greenhouse", "monzo", "GB"),
    ("Snyk", "ashby", "snyk", "GB"),
    ("Wise", "greenhouse", "wise", "GB"),
    ("Zego", "ashby", "zego", "GB"),
    ("Xendit", "greenhouse", "xendit", "ID"),
    ("Atlan", "ashby", "atlan", "IN"),
    ("CRED", "lever", "cred", "IN"),
    ("Groww", "greenhouse", "groww", "IN"),
    ("HighRadius", "greenhouse", "highradius", "IN"),
    ("InMobi", "greenhouse", "inmobi", "IN"),
    ("Nutanix", "ashby", "nutanix", "IN"),
    ("Postman", "greenhouse", "postman", "IN"),
    ("Slice", "greenhouse", "slice", "IN"),
    ("Turing", "greenhouse", "turing", "IN"),
    ("Satispay", "lever", "satispay", "IT"),
    ("Mercari", "greenhouse", "mercari", "JP"),
    ("PayPay", "greenhouse", "paypay", "JP"),
    ("Woven by Toyota", "lever", "woven-planet", "JP"),
    ("Sendbird", "greenhouse", "sendbird", "KR"),
    ("Adyen", "greenhouse", "adyen", "NL"),
    ("Cognite", "greenhouse", "cognite", "NO"),
    ("Brainly", "ashby", "brainly", "PL"),
    ("Feedzai", "greenhouse", "feedzai", "PT"),
    ("Remote", "greenhouse", "remotecom", "PT"),
    ("Sword Health", "greenhouse", "swordhealth", "PT"),
    ("Truecaller", "greenhouse", "truecaller", "SE"),
    ("Thunes", "greenhouse", "thunes", "SG"),
    ("Affirm", "greenhouse", "affirm", "US"),
    ("Amplitude", "greenhouse", "amplitude", "US"),
    ("Anthropic", "greenhouse", "anthropic", "US"),
    ("Asana", "greenhouse", "asana", "US"),
    ("Attentive", "greenhouse", "attentive", "US"),
    ("Brex", "greenhouse", "brex", "US"),
    ("Chime", "greenhouse", "chime", "US"),
    ("Cloudflare", "greenhouse", "cloudflare", "US"),
    ("Coinbase", "greenhouse", "coinbase", "US"),
    ("Confluent", "ashby", "confluent", "US"),
    ("Coursera", "greenhouse", "coursera", "US"),
    ("Discord", "greenhouse", "discord", "US"),
    ("Dropbox", "greenhouse", "dropbox", "US"),
    ("Duolingo", "greenhouse", "duolingo", "US"),
    ("Elastic", "greenhouse", "elastic", "US"),
    ("Figma", "greenhouse", "figma", "US"),
    ("GitLab", "greenhouse", "gitlab", "US"),
    ("Gusto", "greenhouse", "gusto", "US"),
    ("Instacart", "greenhouse", "instacart", "US"),
    ("Klaviyo", "greenhouse", "klaviyo", "US"),
    ("Lyft", "greenhouse", "lyft", "US"),
    ("Pinterest", "greenhouse", "pinterest", "US"),
    ("Plaid", "lever", "plaid", "US"),
    ("Reddit", "greenhouse", "reddit", "US"),
    ("Robinhood", "greenhouse", "robinhood", "US"),
    ("Samsara", "greenhouse", "samsara", "US"),
    ("Scale AI", "greenhouse", "scaleai", "US"),
    ("Stripe", "greenhouse", "stripe", "US"),
    ("Twilio", "greenhouse", "twilio", "US"),
    ("Vercel", "greenhouse", "vercel", "US"),
    ("Voleon", "lever", "voleon", "US"),
    ("Zapier", "ashby", "zapier", "US"),
]

# roles.json records region as free text, because a human wrote it. Map what is
# recognisable and leave the rest unknown rather than guessing a country.
REGION_ALIASES = {
    "us": "US", "usa": "US", "united states": "US",
    "in": "IN", "india": "IN",
    "canada": "CA", "ca": "CA",
    "uk": "GB", "united kingdom": "GB", "gb": "GB",
    "au": "AU", "australia": "AU",
    "eu": "EU", "europe": "EU",
    "remote": "REMOTE",
}


def normalise_region(value):
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    if text in REGION_ALIASES:
        return REGION_ALIASES[text]
    for key, code in REGION_ALIASES.items():
        if text.startswith(key):
            return code
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return "unknown"


def company_key(name):
    """A stable registry key: lowercase, punctuation-free, legal suffix stripped."""
    words = "".join(c.lower() if c.isalnum() else " " for c in str(name or "")).split()
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def aliases_for(name, token=None):
    """Every spelling this company is likely to appear under, deduped."""
    out = {company_key(name)}
    plain = " ".join("".join(c.lower() if c.isalnum() else " " for c in str(name or "")).split())
    out.add(plain)
    out.add(plain.replace(" ", ""))
    out.add(company_key(name).replace(" ", ""))
    if token:
        out.add(str(token).lower())
    return sorted(x for x in out if x)


def ats_from_url(url):
    """(ats, token) read off a posting URL, or (None, None).

    Never guesses. An aggregator URL says nothing about the employer's ATS, and
    a token invented from one would send every future fetch to a stranger's
    board.
    """
    parsed = urlparse(str(url or ""))
    host = (parsed.netloc or "").lower()
    if not host or host in NOT_A_BOARD:
        return None, None
    target = host + parsed.path + (f"?{parsed.query}" if parsed.query else "")
    for ats, pattern in ATS_URL_PATTERNS:
        found = pattern.match(target)
        if found and found.group(1):
            return ats, found.group(1)
    return None, None


def careers_url(url):
    """The employer's own careers host, for a posting with no derivable token."""
    parsed = urlparse(str(url or ""))
    if not parsed.netloc or parsed.netloc.lower() in NOT_A_BOARD:
        return ""
    return f"{parsed.scheme or 'https'}://{parsed.netloc}"


def blank_company(name, region="unknown", added_from="seed"):
    return {
        "name": name,
        "aliases": aliases_for(name),
        "ats": "unknown",
        "token": None,
        "careersUrl": "",
        "industry": None,
        "region": region,
        "addedFrom": added_from,
        "lastFetchAt": None,
        "lastStatus": "unfetched",
    }


def merge_company(existing, incoming):
    """Later knowledge fills gaps; a real ATS token is never downgraded."""
    out = dict(existing)
    for field in ("careersUrl", "industry"):
        if incoming.get(field) and not out.get(field):
            out[field] = incoming[field]
    if incoming.get("ats") and incoming["ats"] != "unknown":
        if out.get("ats") in (None, "", "unknown") or incoming.get("token"):
            out["ats"] = incoming["ats"]
            out["token"] = incoming.get("token") or out.get("token")
    if out.get("region") in (None, "", "unknown") and incoming.get("region") not in (None, "", "unknown"):
        out["region"] = incoming["region"]
    out["aliases"] = sorted(set(out.get("aliases") or []) | set(incoming.get("aliases") or []))
    sources = set(str(out.get("addedFrom") or "").split("+")) | {incoming.get("addedFrom")}
    out["addedFrom"] = "+".join(sorted(x for x in sources if x))
    return out


def from_roles(roles):
    """Registry entries derived from postings already applied to."""
    out = []
    for entry in (roles or {}).values():
        name = (entry.get("company") or "").strip()
        if not name:
            continue
        ats, token = ats_from_url(entry.get("url"))
        record = blank_company(name, normalise_region(entry.get("region")), "roles")
        if ats:
            record["ats"] = ats
            record["token"] = token
            record["aliases"] = aliases_for(name, token)
        else:
            record["careersUrl"] = careers_url(entry.get("url"))
        out.append(record)
    return out


def from_pack():
    out = []
    for name, ats, token, region in SEED_PACK:
        record = blank_company(name, region, "pack")
        record["ats"] = ats
        record["token"] = token
        record["aliases"] = aliases_for(name, token)
        out.append(record)
    return out


def seed(existing, incoming):
    """Fold candidate entries into the registry. Returns (registry, summary)."""
    registry = dict(existing or {})
    added, updated = 0, 0
    for record in incoming:
        key = company_key(record["name"])
        if not key:
            continue
        if key in registry:
            merged = merge_company(registry[key], record)
            if merged != registry[key]:
                updated += 1
            registry[key] = merged
        else:
            registry[key] = record
            added += 1
    fetchable = sum(1 for c in registry.values()
                    if c.get("ats") in TIER1_PLATFORMS and c.get("token"))
    return registry, {
        "companies": len(registry), "added": added, "updated": updated,
        "tier1Fetchable": fetchable,
        "awaitingAtsResolution": sum(1 for c in registry.values() if c.get("ats") == "unknown"),
    }


def main():
    ap = argparse.ArgumentParser(description="Seed the company registry.")
    ap.add_argument("--store", help="store directory (default ~/.job-application)")
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    ap.add_argument("--no-pack", action="store_true", help="skip the curated region packs")
    ap.add_argument("--no-roles", action="store_true", help="skip roles.json")
    args = ap.parse_args()

    root = jobs_store.root_dir(args.store)
    candidates = []
    if not args.no_roles:
        roles_path = Path(root) / "roles.json"
        try:
            roles = jobs_store._read(roles_path, {}) if roles_path.exists() else {}
        except jobs_store.JobsStoreError as exc:
            print(f"seed companies: {exc}", file=sys.stderr)
            return 2
        candidates += from_roles(roles)
    if not args.no_pack:
        candidates += from_pack()

    try:
        existing = jobs_store.load_companies(root)
    except jobs_store.JobsStoreError as exc:
        print(f"seed companies: {exc}", file=sys.stderr)
        return 2
    registry, summary = seed(existing, candidates)
    summary["byAts"] = {}
    for company in registry.values():
        ats = company.get("ats") or "unknown"
        summary["byAts"][ats] = summary["byAts"].get(ats, 0) + 1
    summary["dryRun"] = bool(args.dry_run)
    if not args.dry_run:
        jobs_store.save_companies(root, registry)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
