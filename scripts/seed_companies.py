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

A curated per-region pack. Every token was confirmed live against the
platform's public board API before being committed — 96 on 2026-08-19, then 285 and
193 more on 2026-08-20, out of 1,345 candidates tried. Anything that did not resolve
was dropped rather than shipped hopefully, which is why the pack is smaller
than any list of companies a person would name. A wrong token is not harmless — it is a 404 on
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
    # A direct posting is /j/<job-id> and does not carry the tenant. Treating
    # "j" as a tenant poisons the registry and makes every Workable fetch fail.
    ("workable", re.compile(r"^apply\.workable\.com/(?!j(?:/|$))([A-Za-z0-9_.-]+)")),
    ("recruitee", re.compile(r"^([A-Za-z0-9-]+)\.recruitee\.com")),
    ("zoho", re.compile(r"^([A-Za-z0-9-]+)\.zohorecruit\.(?:com|in|eu)")),
    ("personio", re.compile(r"^([A-Za-z0-9-]+)\.jobs\.personio\.(?:de|com)")),
    ("breezy", re.compile(r"^([A-Za-z0-9-]+)\.breezy\.hr")),
    ("teamtailor", re.compile(r"^([A-Za-z0-9-]+)\.teamtailor\.com")),
    ("pinpoint", re.compile(r"^([A-Za-z0-9-]+)\.pinpointhq\.com")),
    ("bamboohr", re.compile(r"^([A-Za-z0-9-]+)\.bamboohr\.com")),
    ("jazzhr", re.compile(r"^([A-Za-z0-9-]+)\.applytojob\.com")),
    ("gem", re.compile(r"^jobs\.gem\.com/([A-Za-z0-9-]+)")),
    ("oracle_orc", re.compile(
        r"^[A-Za-z0-9.-]+\.oraclecloud\.com/hcmUI/CandidateExperience/[^/]+/sites/([A-Za-z0-9_-]+)")),
    ("rippling", re.compile(r"^api\.rippling\.com/platform/api/ats/v1/board/([A-Za-z0-9-]+)")),
    ("smartrecruiters", re.compile(r"^careers\.smartrecruiters\.com/([A-Za-z0-9_.-]+)")),
    ("workday", re.compile(r"^([A-Za-z0-9-]+)\.wd\d+\.myworkdayjobs\.com")),
    ("darwinbox", re.compile(r"^([A-Za-z0-9-]+)\.darwinbox\.in")),
]

# Platforms with a connector that can fetch a board directly. Everything else
# in the registry is recorded but not yet reachable without a resolver.
TIER1_PLATFORMS = ("greenhouse", "lever", "ashby", "smartrecruiters", "workable",
                   "recruitee", "personio", "breezy", "rippling", "workday",
                   "pinpoint", "teamtailor", "jazzhr", "oracle_orc", "gem")

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
# Teamtailor tenants verified against https://{token}.teamtailor.com/jobs.json
# on 2026-08-25, each answering 200 with a non-empty `items` array. The registry
# previously held one entry — name "Teamtailor", token "career" — which is the
# vendor's own marketing subdomain and not a board. career.teamtailor.com does
# not resolve, so the connector reported "every board failed (1)" on every run
# and the platform contributed nothing.
TEAMTAILOR_SEEDS = [
    ("Oatly", "teamtailor", "oatly", "SE"),
    ("Instabee", "teamtailor", "instabee", "SE"),
    ("Anyfin", "teamtailor", "anyfin", "SE"),
    ("Quinyx", "teamtailor", "quinyx", "SE"),
    ("Tibber", "teamtailor", "tibber", "NO"),
    ("Hemnet", "teamtailor", "hemnet", "SE"),
    ("Funnel", "teamtailor", "funnel", "SE"),
]

SEED_PACK = [
    ("Acronis", "workday", "acronis", "CH"),
    ("GEICO", "workday", "geico", "US"),
    ("Gem", "gem", "gem", "US"),
    ("FocusKPI", "jazzhr", "focuskpi", "US"),
    ("Oracle", "oracle_orc", "CX_1", "US"),
    ("Pinpoint", "pinpoint", "workwithus", "GB"),
    ("Careem", "greenhouse", "careem", "AE"),
    ("Tamara", "greenhouse", "tamara", "AE"),
    ("Storyblok", "greenhouse", "storyblok", "AT"),
    ("Airtasker", "ashby", "airtasker", "AU"),
    # SafetyCulture is deliberately absent. It left Ashby —
    # api.ashbyhq.com/posting-api/job-board/safetyculture returns 404 — and it
    # was the one dead token of 137, reported as an error on every single run.
    # Not re-pointed by guess: smartrecruiters answers for the name but with
    # zero postings, which is not evidence of a move. The pack is the *verified*
    # list, so an unresolved company does not belong in it; from_roles and
    # from_postings will re-add it with a real token the next time it is seen.
    ("Culture Amp", "greenhouse", "cultureamp", "AU"),
    ("Eucalyptus", "greenhouse", "eucalyptus", "AU"),
    ("Airtasker", "lever", "airtasker", "AU"),
    ("Deputy", "lever", "deputy", "AU"),
    ("Immutable", "lever", "immutable", "AU"),
    ("Payhawk", "personio", "payhawk", "BG"),
    ("Thinkific", "ashby", "thinkific", "CA"),
    ("Faire", "greenhouse", "faire", "CA"),
    ("Hootsuite", "greenhouse", "hootsuite", "CA"),
    ("Thinkific", "greenhouse", "thinkific", "CA"),
    ("Wealthsimple", "lever", "wealthsimple", "CA"),
    ("Babbel", "ashby", "babbel", "DE"),
    ("Choco", "ashby", "choco", "DE"),
    ("Forto", "ashby", "forto", "DE"),
    ("GetYourGuide", "ashby", "getyourguide", "DE"),
    ("Kombo", "ashby", "kombo", "DE"),
    ("Moss", "ashby", "moss", "DE"),
    ("Celonis", "greenhouse", "celonis", "DE"),
    ("Contentful", "greenhouse", "contentful", "DE"),
    ("GetYourGuide", "greenhouse", "getyourguide", "DE"),
    ("HelloFresh", "greenhouse", "hellofresh", "DE"),
    ("N26", "greenhouse", "n26", "DE"),
    ("Raisin", "greenhouse", "raisin", "DE"),
    ("SumUp", "greenhouse", "sumup", "DE"),
    ("Trade Republic", "greenhouse", "traderepublic", "DE"),
    ("Kombo", "smartrecruiters", "kombo", "DE"),
    ("Kombo", "workable", "kombo", "DE"),
    ("Trustpilot", "greenhouse", "trustpilot", "DK"),
    ("Bolt", "ashby", "bolt", "EE"),
    ("Pipedrive", "lever", "pipedrive", "EE"),
    ("Cabify", "greenhouse", "cabify", "ES"),
    ("Typeform", "greenhouse", "typeform", "ES"),
    ("Relex", "greenhouse", "relex", "FI"),
    ("Back Market", "ashby", "backmarket", "FR"),
    ("Ledger", "ashby", "ledger", "FR"),
    ("Sorare", "ashby", "sorare", "FR"),
    ("Spendesk", "ashby", "spendesk", "FR"),
    ("Algolia", "greenhouse", "algolia", "FR"),
    ("Dataiku", "greenhouse", "dataiku", "FR"),
    ("Doctolib", "greenhouse", "doctolib", "FR"),
    ("Mirakl", "greenhouse", "mirakl", "FR"),
    ("Contentsquare", "lever", "contentsquare", "FR"),
    ("Ledger", "lever", "ledger", "FR"),
    ("Mistral AI", "lever", "mistral", "FR"),
    ("Freetrade", "ashby", "freetrade", "GB"),
    ("Humaans", "ashby", "humaans", "GB"),
    ("Improbable", "ashby", "improbable", "GB"),
    ("Marshmallow", "ashby", "marshmallow", "GB"),
    ("Snyk", "ashby", "snyk", "GB"),
    ("Zego", "ashby", "zego", "GB"),
    ("Cleo", "greenhouse", "cleo", "GB"),
    ("Ebury", "greenhouse", "ebury", "GB"),
    ("GoCardless", "greenhouse", "gocardless", "GB"),
    ("Grafana Labs", "greenhouse", "grafanalabs", "GB"),
    ("Monzo", "greenhouse", "monzo", "GB"),
    ("Wise", "greenhouse", "wise", "GB"),
    ("Humaans", "lever", "humaans", "GB"),
    ("Xendit", "greenhouse", "xendit", "ID"),
    ("Andela", "ashby", "andela", "IN"),
    ("Aspora", "ashby", "aspora", "IN"),
    ("Atlan", "ashby", "atlan", "IN"),
    ("Bounce", "ashby", "bounce", "IN"),
    ("Loadshare", "ashby", "loadshare", "IN"),
    ("Navi", "ashby", "navi", "IN"),
    ("Nutanix", "ashby", "nutanix", "IN"),
    ("Scaler", "ashby", "scaler", "IN"),
    ("Turing", "ashby", "turing", "IN"),
    ("Clear", "greenhouse", "clear", "IN"),
    ("Glance", "greenhouse", "glance", "IN"),
    ("Groww", "greenhouse", "groww", "IN"),
    ("HighRadius", "greenhouse", "highradius", "IN"),
    ("InMobi", "greenhouse", "inmobi", "IN"),
    ("Karbon", "greenhouse", "karbon", "IN"),
    ("Kite", "greenhouse", "kite", "IN"),
    ("Mediaocean", "greenhouse", "mediaocean", "IN"),
    ("PhonePe", "greenhouse", "phonepe", "IN"),
    ("Pine", "greenhouse", "pine", "IN"),
    ("Postman", "greenhouse", "postman", "IN"),
    ("Slice", "greenhouse", "slice", "IN"),
    ("Turing", "greenhouse", "turing", "IN"),
    ("CRED", "lever", "cred", "IN"),
    ("Cactus", "personio", "cactus", "IN"),
    ("Entropik", "smartrecruiters", "entropik", "IN"),
    ("InterviewBit", "smartrecruiters", "interviewbit", "IN"),
    ("Kodo", "smartrecruiters", "kodo", "IN"),
    ("Quicko", "smartrecruiters", "quicko", "IN"),
    ("Swiggy", "smartrecruiters", "swiggy", "IN"),
    ("Unacademy", "smartrecruiters", "unacademy", "IN"),
    ("Upstox", "smartrecruiters", "upstox", "IN"),
    ("Satispay", "lever", "satispay", "IT"),
    ("Mercari", "greenhouse", "mercari", "JP"),
    ("PayPay", "greenhouse", "paypay", "JP"),
    ("Woven by Toyota", "lever", "woven-planet", "JP"),
    ("Sendbird", "greenhouse", "sendbird", "KR"),
    ("Adyen", "greenhouse", "adyen", "NL"),
    ("Sanity", "ashby", "sanity", "NO"),
    ("Cognite", "greenhouse", "cognite", "NO"),
    ("Kahoot", "workable", "kahoot", "NO"),
    ("Brainly", "ashby", "brainly", "PL"),
    ("Talkdesk", "ashby", "talkdesk", "PT"),
    ("Feedzai", "greenhouse", "feedzai", "PT"),
    ("Remote", "greenhouse", "remotecom", "PT"),
    ("Sword Health", "greenhouse", "swordhealth", "PT"),
    ("Truecaller", "greenhouse", "truecaller", "SE"),
    ("Thunes", "greenhouse", "thunes", "SG"),
    ("Abound", "ashby", "abound", "US"),
    ("Airbyte", "ashby", "airbyte", "US"),
    ("Airtable", "ashby", "airtable", "US"),
    ("Amplitude", "ashby", "amplitude", "US"),
    ("Ankorstore", "ashby", "ankorstore", "US"),
    ("Anomalo", "ashby", "anomalo", "US"),
    ("Anyscale", "ashby", "anyscale", "US"),
    ("Astra", "ashby", "astra", "US"),
    ("Astronomer", "ashby", "astronomer", "US"),
    ("Bold", "ashby", "bold", "US"),
    ("Boom", "ashby", "boom", "US"),
    ("Boomi", "ashby", "boomi", "US"),
    ("Braintrust", "ashby", "braintrust", "US"),
    ("Bunny", "ashby", "bunny", "US"),
    ("Clerk", "ashby", "clerk", "US"),
    ("Clever", "ashby", "clever", "US"),
    ("Coalesce", "ashby", "coalesce", "US"),
    ("Column", "ashby", "column", "US"),
    ("Conductor", "ashby", "conductor", "US"),
    ("Confluent", "ashby", "confluent", "US"),
    ("Corti", "ashby", "corti", "US"),
    ("Cribl", "ashby", "cribl", "US"),
    ("Datafold", "ashby", "datafold", "US"),
    ("Deel", "ashby", "deel", "US"),
    ("Depot", "ashby", "depot", "US"),
    ("Eagle", "ashby", "eagle", "US"),
    ("Encord", "ashby", "encord", "US"),
    ("Expensify", "ashby", "expensify", "US"),
    ("Extend", "ashby", "extend", "US"),
    ("Fathom", "ashby", "fathom", "US"),
    ("Fig", "ashby", "fig", "US"),
    ("Figure", "ashby", "figure", "US"),
    ("Finch", "ashby", "finch", "US"),
    ("Flock", "ashby", "flock", "US"),
    ("Footprint", "ashby", "footprint", "US"),
    ("Freed", "ashby", "freed", "US"),
    ("Freshpaint", "ashby", "freshpaint", "US"),
    ("Guild", "ashby", "guild", "US"),
    ("Hex", "ashby", "hex", "US"),
    ("Hightouch", "ashby", "hightouch", "US"),
    ("Inngest", "ashby", "inngest", "US"),
    ("Iterable", "ashby", "iterable", "US"),
    ("Jane", "ashby", "jane", "US"),
    ("Juicebox", "ashby", "juicebox", "US"),
    ("Knock", "ashby", "knock", "US"),
    ("Kraken", "ashby", "kraken", "US"),
    ("Kustomer", "ashby", "kustomer", "US"),
    ("LanceDB", "ashby", "lancedb", "US"),
    ("Langfuse", "ashby", "langfuse", "US"),
    ("LaunchDarkly", "ashby", "launchdarkly", "US"),
    ("Linear", "ashby", "linear", "US"),
    ("LiveKit", "ashby", "livekit", "US"),
    ("LlamaIndex", "ashby", "llamaindex", "US"),
    ("Loom", "ashby", "loom", "US"),
    ("Marqeta", "ashby", "marqeta", "US"),
    ("Materialize", "ashby", "materialize", "US"),
    ("Meow", "ashby", "meow", "US"),
    ("Mercury", "ashby", "mercury", "US"),
    ("Merge", "ashby", "merge", "US"),
    ("Mesh", "ashby", "mesh", "US"),
    ("Metaview", "ashby", "metaview", "US"),
    ("Method", "ashby", "method", "US"),
    ("Middesk", "ashby", "middesk", "US"),
    ("Miro", "ashby", "miro", "US"),
    ("Modal", "ashby", "modal", "US"),
    ("MotherDuck", "ashby", "motherduck", "US"),
    ("Mux", "ashby", "mux", "US"),
    ("Nabla", "ashby", "nabla", "US"),
    ("Nango", "ashby", "nango", "US"),
    ("Neon", "ashby", "neon", "US"),
    ("Neptune", "ashby", "neptune", "US"),
    ("NetBird", "ashby", "netbird", "US"),
    ("Orca", "ashby", "orca", "US"),
    ("Oyster", "ashby", "oyster", "US"),
    ("Paragon", "ashby", "paragon", "US"),
    ("Persona", "ashby", "persona", "US"),
    ("Pika", "ashby", "pika", "US"),
    ("Pinecone", "ashby", "pinecone", "US"),
    ("Poolside", "ashby", "poolside", "US"),
    ("Poshmark", "ashby", "poshmark", "US"),
    ("PostHog", "ashby", "posthog", "US"),
    ("Prefect", "ashby", "prefect", "US"),
    ("Railway", "ashby", "railway", "US"),
    ("Raycast", "ashby", "raycast", "US"),
    ("Recharge", "ashby", "recharge", "US"),
    ("Reddit", "ashby", "reddit", "US"),
    ("Regard", "ashby", "regard", "US"),
    ("Resend", "ashby", "resend", "US"),
    ("Restate", "ashby", "restate", "US"),
    ("Rev", "ashby", "rev", "US"),
    ("Rho", "ashby", "rho", "US"),
    ("Runway", "ashby", "runway", "US"),
    ("Sardine", "ashby", "sardine", "US"),
    ("Secureframe", "ashby", "secureframe", "US"),
    ("Semgrep", "ashby", "semgrep", "US"),
    ("Sentry", "ashby", "sentry", "US"),
    ("Shiftsmart", "ashby", "shiftsmart", "US"),
    ("Sift", "ashby", "sift", "US"),
    ("Slab", "ashby", "slab", "US"),
    ("Slope", "ashby", "slope", "US"),
    ("Socket", "ashby", "socket", "US"),
    ("Strava", "ashby", "strava", "US"),
    ("Stream", "ashby", "stream", "US"),
    ("Stytch", "ashby", "stytch", "US"),
    ("Svix", "ashby", "svix", "US"),
    ("Traba", "ashby", "traba", "US"),
    ("Turbopuffer", "ashby", "turbopuffer", "US"),
    ("Twelve", "ashby", "twelve", "US"),
    ("Unit", "ashby", "unit", "US"),
    ("Vector", "ashby", "vector", "US"),
    ("Velocity", "ashby", "velocity", "US"),
    ("Vercel", "ashby", "vercel", "US"),
    ("Verkada", "ashby", "verkada", "US"),
    ("Warp", "ashby", "warp", "US"),
    ("Weaviate", "ashby", "weaviate", "US"),
    ("Wistia", "ashby", "wistia", "US"),
    ("Wiz", "ashby", "wiz", "US"),
    ("WorkOS", "ashby", "workos", "US"),
    ("Zapier", "ashby", "zapier", "US"),
    ("incident", "ashby", "incident", "US"),
    ("project44", "ashby", "project44", "US"),
    ("Fathom", "breezy", "fathom", "US"),
    ("Surge", "breezy", "surge", "US"),
    ("Affirm", "greenhouse", "affirm", "US"),
    ("Airbnb", "greenhouse", "airbnb", "US"),
    ("Airtable", "greenhouse", "airtable", "US"),
    ("Alloy", "greenhouse", "alloy", "US"),
    ("Amplitude", "greenhouse", "amplitude", "US"),
    ("Anthropic", "greenhouse", "anthropic", "US"),
    ("Apollo", "greenhouse", "apollo", "US"),
    ("Archer", "greenhouse", "archer", "US"),
    ("Asana", "greenhouse", "asana", "US"),
    ("Attentive", "greenhouse", "attentive", "US"),
    ("Axon", "greenhouse", "axon", "US"),
    ("Bandwidth", "greenhouse", "bandwidth", "US"),
    ("Betterment", "greenhouse", "betterment", "US"),
    ("Bold", "greenhouse", "bold", "US"),
    ("Braze", "greenhouse", "braze", "US"),
    ("Brex", "greenhouse", "brex", "US"),
    ("BrightHire", "greenhouse", "brighthire", "US"),
    ("Calm", "greenhouse", "calm", "US"),
    ("Carta", "greenhouse", "carta", "US"),
    ("Celigo", "greenhouse", "celigo", "US"),
    ("Chainguard", "greenhouse", "chainguard", "US"),
    ("Checkr", "greenhouse", "checkr", "US"),
    ("Chime", "greenhouse", "chime", "US"),
    ("Clever", "greenhouse", "clever", "US"),
    ("ClickHouse", "greenhouse", "clickhouse", "US"),
    ("Cloudflare", "greenhouse", "cloudflare", "US"),
    ("Coinbase", "greenhouse", "coinbase", "US"),
    ("Comet", "greenhouse", "comet", "US"),
    ("CoreWeave", "greenhouse", "coreweave", "US"),
    ("Coursera", "greenhouse", "coursera", "US"),
    ("Cribl", "greenhouse", "cribl", "US"),
    ("DataGrail", "greenhouse", "datagrail", "US"),
    ("Datadog", "greenhouse", "datadog", "US"),
    ("Descope", "greenhouse", "descope", "US"),
    ("Descript", "greenhouse", "descript", "US"),
    ("Disco", "greenhouse", "disco", "US"),
    ("Discord", "greenhouse", "discord", "US"),
    ("Dremio", "greenhouse", "dremio", "US"),
    ("Dropbox", "greenhouse", "dropbox", "US"),
    ("Duolingo", "greenhouse", "duolingo", "US"),
    ("Elastic", "greenhouse", "elastic", "US"),
    ("Eve", "greenhouse", "eve", "US"),
    ("Everlaw", "greenhouse", "everlaw", "US"),
    ("Extend", "greenhouse", "extend", "US"),
    ("Fastly", "greenhouse", "fastly", "US"),
    ("Figma", "greenhouse", "figma", "US"),
    ("Figure", "greenhouse", "figure", "US"),
    ("Fivetran", "greenhouse", "fivetran", "US"),
    ("Flexport", "greenhouse", "flexport", "US"),
    ("Formic", "greenhouse", "formic", "US"),
    ("Forter", "greenhouse", "forter", "US"),
    ("FourKites", "greenhouse", "fourkites", "US"),
    ("Galileo", "greenhouse", "galileo", "US"),
    ("Gemini", "greenhouse", "gemini", "US"),
    ("GitLab", "greenhouse", "gitlab", "US"),
    ("Grailed", "greenhouse", "grailed", "US"),
    ("Guild", "greenhouse", "guild", "US"),
    ("Gusto", "greenhouse", "gusto", "US"),
    ("Help", "greenhouse", "help", "US"),
    ("Highnote", "greenhouse", "highnote", "US"),
    ("Hightouch", "greenhouse", "hightouch", "US"),
    ("Honeycomb", "greenhouse", "honeycomb", "US"),
    ("Imbue", "greenhouse", "imbue", "US"),
    ("Instacart", "greenhouse", "instacart", "US"),
    ("Instawork", "greenhouse", "instawork", "US"),
    ("Intercom", "greenhouse", "intercom", "US"),
    ("Iterable", "greenhouse", "iterable", "US"),
    ("Justworks", "greenhouse", "justworks", "US"),
    ("Klaviyo", "greenhouse", "klaviyo", "US"),
    ("Knock", "greenhouse", "knock", "US"),
    ("Kodiak", "greenhouse", "kodiak", "US"),
    ("Labelbox", "greenhouse", "labelbox", "US"),
    ("Laika", "greenhouse", "laika", "US"),
    ("LaunchDarkly", "greenhouse", "launchdarkly", "US"),
    ("Ledgy", "greenhouse", "ledgy", "US"),
    ("Lithic", "greenhouse", "lithic", "US"),
    ("Lyft", "greenhouse", "lyft", "US"),
    ("Magic", "greenhouse", "magic", "US"),
    ("Make", "greenhouse", "make", "US"),
    ("Marqeta", "greenhouse", "marqeta", "US"),
    ("MasterClass", "greenhouse", "masterclass", "US"),
    ("Mercury", "greenhouse", "mercury", "US"),
    ("Merge", "greenhouse", "merge", "US"),
    ("Mesh", "greenhouse", "mesh", "US"),
    ("Method", "greenhouse", "method", "US"),
    ("Mixpanel", "greenhouse", "mixpanel", "US"),
    ("Motive", "greenhouse", "motive", "US"),
    ("Netlify", "greenhouse", "netlify", "US"),
    ("Netradyne", "greenhouse", "netradyne", "US"),
    ("NewRelic", "greenhouse", "newrelic", "US"),
    ("Newsela", "greenhouse", "newsela", "US"),
    ("Nextdoor", "greenhouse", "nextdoor", "US"),
    ("Nuro", "greenhouse", "nuro", "US"),
    ("Okta", "greenhouse", "okta", "US"),
    ("Orca", "greenhouse", "orca", "US"),
    ("Orkes", "greenhouse", "orkes", "US"),
    ("Osano", "greenhouse", "osano", "US"),
    ("Oscar", "greenhouse", "oscar", "US"),
    ("Otter", "greenhouse", "otter", "US"),
    ("PagerDuty", "greenhouse", "pagerduty", "US"),
    ("Papaya", "greenhouse", "papaya", "US"),
    ("Peloton", "greenhouse", "peloton", "US"),
    ("Pinterest", "greenhouse", "pinterest", "US"),
    ("PlanetScale", "greenhouse", "planetscale", "US"),
    ("Portable", "greenhouse", "portable", "US"),
    ("Poshmark", "greenhouse", "poshmark", "US"),
    ("Postscript", "greenhouse", "postscript", "US"),
    ("Prisma", "greenhouse", "prisma", "US"),
    ("Prismatic", "greenhouse", "prismatic", "US"),
    ("Prove", "greenhouse", "prove", "US"),
    ("Pulley", "greenhouse", "pulley", "US"),
    ("Reddit", "greenhouse", "reddit", "US"),
    ("Relativity", "greenhouse", "relativity", "US"),
    ("Remote", "greenhouse", "remote", "US"),
    ("Robinhood", "greenhouse", "robinhood", "US"),
    ("Roblox", "greenhouse", "roblox", "US"),
    ("Salesloft", "greenhouse", "salesloft", "US"),
    ("Samsara", "greenhouse", "samsara", "US"),
    ("Scale AI", "greenhouse", "scaleai", "US"),
    ("SeekOut", "greenhouse", "seekout", "US"),
    ("Shield", "greenhouse", "shield", "US"),
    ("Showpad", "greenhouse", "showpad", "US"),
    ("SingleStore", "greenhouse", "singlestore", "US"),
    ("Smartsheet", "greenhouse", "smartsheet", "US"),
    ("Socket", "greenhouse", "socket", "US"),
    ("Squarespace", "greenhouse", "squarespace", "US"),
    ("Starburst", "greenhouse", "starburst", "US"),
    ("Stitch", "greenhouse", "stitch", "US"),
    ("StockX", "greenhouse", "stockx", "US"),
    ("StreamNative", "greenhouse", "streamnative", "US"),
    ("Stripe", "greenhouse", "stripe", "US"),
    ("Suki", "greenhouse", "suki", "US"),
    ("Tailscale", "greenhouse", "tailscale", "US"),
    ("Talkspace", "greenhouse", "talkspace", "US"),
    ("Thoropass", "greenhouse", "thoropass", "US"),
    ("Tigera", "greenhouse", "tigera", "US"),
    ("Toast", "greenhouse", "toast", "US"),
    ("Twilio", "greenhouse", "twilio", "US"),
    ("Twitch", "greenhouse", "twitch", "US"),
    ("Udemy", "greenhouse", "udemy", "US"),
    ("Vercel", "greenhouse", "vercel", "US"),
    ("Verkada", "greenhouse", "verkada", "US"),
    ("Vonage", "greenhouse", "vonage", "US"),
    ("Voxel51", "greenhouse", "voxel51", "US"),
    ("Warp", "greenhouse", "warp", "US"),
    ("Wing", "greenhouse", "wing", "US"),
    ("Workato", "greenhouse", "workato", "US"),
    ("Yotpo", "greenhouse", "yotpo", "US"),
    ("ZoomInfo", "greenhouse", "zoominfo", "US"),
    ("hireEZ", "greenhouse", "hireez", "US"),
    ("project44", "greenhouse", "project44", "US"),
    ("Alloy", "lever", "alloy", "US"),
    ("AngelList", "lever", "angellist", "US"),
    ("Anyscale", "lever", "anyscale", "US"),
    ("Beta", "lever", "beta", "US"),
    ("BrightHire", "lever", "brighthire", "US"),
    ("Cin7", "lever", "cin7", "US"),
    ("Clari", "lever", "clari", "US"),
    ("DevCycle", "lever", "devcycle", "US"),
    ("Finch", "lever", "finch", "US"),
    ("Findem", "lever", "findem", "US"),
    ("Freshworks", "lever", "freshworks", "US"),
    ("Hevo", "lever", "hevo", "US"),
    ("Highspot", "lever", "highspot", "US"),
    ("Imbue", "lever", "imbue", "US"),
    ("Increase", "lever", "increase", "US"),
    ("Kraken", "lever", "kraken", "US"),
    ("Labelbox", "lever", "labelbox", "US"),
    ("Mable", "lever", "mable", "US"),
    ("Matillion", "lever", "matillion", "US"),
    ("Metaview", "lever", "metaview", "US"),
    ("Mindtickle", "lever", "mindtickle", "US"),
    ("Nango", "lever", "nango", "US"),
    ("Neon", "lever", "neon", "US"),
    ("Olo", "lever", "olo", "US"),
    ("Plaid", "lever", "plaid", "US"),
    ("Plivo", "lever", "plivo", "US"),
    ("Porter", "lever", "porter", "US"),
    ("Saronic", "lever", "saronic", "US"),
    ("Secureframe", "lever", "secureframe", "US"),
    ("StartEngine", "lever", "startengine", "US"),
    ("Synthego", "lever", "synthego", "US"),
    ("Sysdig", "lever", "sysdig", "US"),
    ("Tinybird", "lever", "tinybird", "US"),
    ("TriNet", "lever", "trinet", "US"),
    ("Twingate", "lever", "twingate", "US"),
    ("Voleon", "lever", "voleon", "US"),
    ("Wealthfront", "lever", "wealthfront", "US"),
    ("Wonolo", "lever", "wonolo", "US"),
    ("ZeroTier", "lever", "zerotier", "US"),
    ("Zilliz", "lever", "zilliz", "US"),
    ("Bright", "personio", "bright", "US"),
    ("Center", "personio", "center", "US"),
    ("Cyclr", "personio", "cyclr", "US"),
    ("Neptune", "personio", "neptune", "US"),
    ("Ory", "personio", "ory", "US"),
    ("Resolve", "personio", "resolve", "US"),
    ("Twelve", "personio", "twelve", "US"),
    ("Bright", "recruitee", "bright", "US"),
    ("Make", "recruitee", "make", "US"),
    ("Multiplier", "recruitee", "multiplier", "US"),
    ("Neptune", "recruitee", "neptune", "US"),
    ("Norm", "recruitee", "norm", "US"),
    ("Trustero", "recruitee", "trustero", "US"),
    ("Agora", "rippling", "agora", "US"),
    ("Arc", "rippling", "arc", "US"),
    ("Astra", "rippling", "astra", "US"),
    ("Conductor", "rippling", "conductor", "US"),
    ("Eagle", "rippling", "eagle", "US"),
    ("Internal", "rippling", "internal", "US"),
    ("Jitsu", "rippling", "jitsu", "US"),
    ("Moov", "rippling", "moov", "US"),
    ("Multiplier", "rippling", "multiplier", "US"),
    ("BrightHire", "smartrecruiters", "brighthire", "US"),
    ("Databand", "smartrecruiters", "databand", "US"),
    ("Gcore", "smartrecruiters", "gcore", "US"),
    ("Juicebox", "smartrecruiters", "juicebox", "US"),
    ("NewRelic", "smartrecruiters", "newrelic", "US"),
    ("SeekOut", "smartrecruiters", "seekout", "US"),
    ("Socure", "smartrecruiters", "socure", "US"),
    ("Unleash", "smartrecruiters", "unleash", "US"),
    ("Footprint", "workable", "footprint", "US"),
    ("Alpha", "ashby", "alpha", "unknown"),
    ("Bob", "ashby", "bob", "unknown"),
    ("Dune", "ashby", "dune", "unknown"),
    ("Flink", "ashby", "flink", "unknown"),
    ("Flip", "ashby", "flip", "unknown"),
    ("Gelato", "ashby", "gelato", "unknown"),
    ("Ghost", "ashby", "ghost", "unknown"),
    ("Kiwi", "ashby", "kiwi", "unknown"),
    ("Paddle", "ashby", "paddle", "unknown"),
    ("Primer", "ashby", "primer", "unknown"),
    ("Quantexa", "ashby", "quantexa", "unknown"),
    ("Surfshark", "ashby", "surfshark", "unknown"),
    ("TrueLayer", "ashby", "truelayer", "unknown"),
    ("Trustly", "ashby", "trustly", "unknown"),
    ("Athena", "greenhouse", "athena", "unknown"),
    ("Bob", "greenhouse", "bob", "unknown"),
    ("Flip", "greenhouse", "flip", "unknown"),
    ("Ghost", "greenhouse", "ghost", "unknown"),
    ("Later", "greenhouse", "later", "unknown"),
    ("Nintex", "greenhouse", "nintex", "unknown"),
    ("Prospa", "greenhouse", "prospa", "unknown"),
    ("TrueLayer", "greenhouse", "truelayer", "unknown"),
    ("Blue", "lever", "blue", "unknown"),
    ("Enable", "lever", "enable", "unknown"),
    ("Jobandtalent", "lever", "jobandtalent", "unknown"),
    ("Lemon", "lever", "lemon", "unknown"),
    ("Omnisend", "lever", "omnisend", "unknown"),
    ("Prismic", "lever", "prismic", "unknown"),
    ("Surfshark", "lever", "surfshark", "unknown"),
    ("Swile", "lever", "swile", "unknown"),
    ("Trustly", "lever", "trustly", "unknown"),
    ("Whereby", "lever", "whereby", "unknown"),
    ("Younited", "lever", "younited", "unknown"),
    ("Fever", "personio", "fever", "unknown"),
    ("Kiwi", "personio", "kiwi", "unknown"),
    ("Tandem", "personio", "tandem", "unknown"),
    ("Alpha", "recruitee", "alpha", "unknown"),
    ("Automation", "recruitee", "automation", "unknown"),
    ("Hygraph", "recruitee", "hygraph", "unknown"),
    ("Memo", "recruitee", "memo", "unknown"),
    ("HiBob", "smartrecruiters", "hibob", "unknown"),
    ("Weezy", "smartrecruiters", "weezy", "unknown"),
]

# Boards whose public API requires more than a tenant token. These origins and
# site paths were verified with live responses before entering the seed pack.
VERIFIED_CAREERS_URLS = {
    ("workday", "acronis"): "https://acronis.wd502.myworkdayjobs.com/acronis_careers",
    ("workday", "geico"): "https://geico.wd1.myworkdayjobs.com/External",
    ("oracle_orc", "CX_1"): "https://eeho.fa.us2.oraclecloud.com",
}

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


def valid_board_token(ats, token):
    """Whether a discovered token can identify a tenant on that platform."""
    if not token:
        return False
    return not (ats == "workable" and str(token).lower() == "j")


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
    incoming_is_verified = incoming.get("addedFrom") == "pack"
    for field in ("careersUrl", "industry"):
        # A verified pack URL must replace stale learned ATS infrastructure.
        # Vendors migrate tenants and site names, as Acronis did in 2026-08.
        if incoming.get(field) and (not out.get(field)
                                    or (field == "careersUrl" and incoming_is_verified)):
            out[field] = incoming[field]
    if incoming.get("ats") and incoming["ats"] != "unknown":
        existing_sources = set(str(out.get("addedFrom") or "").split("+"))
        existing_is_verified = "pack" in existing_sources
        if (out.get("ats") in (None, "", "unknown")
                or (incoming_is_verified and not existing_is_verified)):
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
            if ats in ("workday", "oracle_orc"):
                parsed = urlparse(str(entry.get("url") or ""))
                parts = [p for p in parsed.path.split("/") if p]
                if ats == "workday" and parts and parts[0].lower().startswith("en-"):
                    parts.pop(0)
                if ats == "workday" and parts:
                    record["careersUrl"] = f"https://{parsed.netloc}/{parts[0]}"
                elif ats == "oracle_orc":
                    record["careersUrl"] = f"https://{parsed.netloc}"
        else:
            record["careersUrl"] = careers_url(entry.get("url"))
        out.append(record)
    return out


def from_postings(postings):
    """Learn only direct ATS tenants carried by normalized sightings."""
    out = []
    for posting in postings or []:
        urls = [posting.get("canonicalUrl")]
        urls.extend(s.get("url") for s in (posting.get("sources") or [])
                    if isinstance(s, dict))
        for url in urls:
            ats, _token = ats_from_url(url)
            if not ats:
                continue
            role = {"company": posting.get("company"),
                    "region": (posting.get("location") or {}).get("country"),
                    "url": url}
            records = from_roles({"posting": role})
            if records:
                out.append(records[0])
            break
    return out


def from_pack():
    out = []
    for name, ats, token, region in SEED_PACK + TEAMTAILOR_SEEDS:
        record = blank_company(name, region, "pack")
        record["ats"] = ats
        record["token"] = token
        record["careersUrl"] = VERIFIED_CAREERS_URLS.get((ats, token), "")
        record["aliases"] = aliases_for(name, token)
        out.append(record)
    return out


def seed(existing, incoming):
    """Fold candidate entries into the registry. Returns (registry, summary)."""
    registry = {}
    invalid_tokens_dropped = 0
    for key, company in (existing or {}).items():
        record = dict(company)
        if record.get("ats") not in (None, "", "unknown") and not valid_board_token(
                record.get("ats"), record.get("token")):
            record["ats"] = "unknown"
            record["token"] = None
            record["lastStatus"] = "unresolved (invalid tenant token discarded)"
            invalid_tokens_dropped += 1
        registry[key] = record
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
        "invalidTokensDropped": invalid_tokens_dropped,
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
