"""Every Tier-1 connector, and the one place that says which are which.

A connector is a function taking a Fetcher and returning the envelope base.py
defines. Registering it here is what makes it run, what makes it appear in
`meta.lastIngest.byChannel`, and what makes its absence countable — so a
channel that exists in the catalog but not here is a gap the ingest reports
rather than one nobody notices.
"""

from . import adzuna, ashby, boards, careerjet, feeds, greenhouse, jooble, lever
from . import reed, themuse, usajobs

# Tier 1a: no key, no limit worth worrying about. These carry Phase 1 on their
# own if every key is missing.
REGISTRY_CONNECTORS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    **boards.CONNECTORS,
}

FEED_CONNECTORS = dict(feeds.CONNECTORS)

# Tier 1b: a key in ~/.job-application/config.env. Missing keys report
# `skipped (no key)` on every run rather than going quiet.
KEYED_CONNECTORS = {
    "adzuna": adzuna.fetch,
    "jooble": jooble.fetch,
    "careerjet": careerjet.fetch,
    "reed": reed.fetch,
    "usajobs": usajobs.fetch,
    "themuse": themuse.fetch,
}

ALL = {**REGISTRY_CONNECTORS, **FEED_CONNECTORS, **KEYED_CONNECTORS}
