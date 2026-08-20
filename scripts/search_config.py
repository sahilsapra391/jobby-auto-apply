#!/usr/bin/env python3
"""The saved search: one versioned object that decides what a scan keeps.

Every scan is scoped by this, and only by this. It is deliberately not "pull
everything into view": a config that matched everything would fill the board
with roles nobody would apply to and make the % match meaningless.

The defaults here are a shape, not a search. They carry no titles, no
countries and no keywords, because a search belongs to the person doing it and
this file is public. Until the user saves one from the board there is no
config at all, and the hourly scheduler stays disarmed — a scan that ran
against nobody's search would be worse than no scan.

  python3 scripts/search_config.py show [--store DIR]
  python3 scripts/search_config.py save --input config.json
  python3 scripts/search_config.py validate --input config.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs_store  # noqa: E402
import store  # noqa: E402

# Country groups the board offers as one pill, so a user picking "Europe" does
# not have to tick thirty boxes. Expanded at ingest, never stored expanded, so
# a pack that gains a country later applies to configs already saved.
REGION_PACKS = {
    "EU": [
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
        "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
        "SI", "ES", "SE", "IS", "LI", "NO", "CH", "GB",
    ],
    "MIDDLE_EAST": ["AE", "SA", "QA", "KW", "BH", "OM", "JO", "EG"],
    "SEA": ["SG", "MY", "PH", "ID", "TH", "VN"],
}

# Two months is a ceiling, not a preference. A posting older than that is
# usually filled or abandoned, and applying to it wastes a slot. `all` means
# "everything the board holds", which is already inside the ceiling because
# nothing older is ever ingested or kept.
MAX_AGE_DAYS = 60
POSTED_WITHIN = {"6h": 0.25, "24h": 1, "7d": 7, "14d": 14, "30d": 30,
                 "60d": 60, "all": None}
# Configs saved before the ceiling existed. Read, never offered.
LEGACY_POSTED_WITHIN = {"90d": "60d"}
SEARCH_SCOPES = ("title", "title+description")
# How the board is ordered and whether it hides aggregator copies. These are
# view state rather than anything ingest reads, but they are still "the search
# Sahil set up", and a saved search that forgets them is a saved search that
# reverts two controls every time the board republishes.
SORTS = ("new", "direct", "score")
WORKPLACES = ("remote", "hybrid", "onsite")
COMPANIES_MODES = ("all", "only", "hide")
# Ordered, because "up to a master's" has to include everything below it.
DEGREE_LADDER = ("none", "associate", "bachelors", "masters", "doctorate")
UNKNOWN_KEYS = ("experience", "degree", "workplace", "postedAt")

# Sweeps rotate through channel packs, so fewer than six a day means some pack
# goes a whole day unswept and the coverage claim stops being true.
MIN_TIER2_SWEEPS_PER_DAY = 6
MIN_TIER1_INTERVAL_MINUTES = 15

DEFAULTS = {
    "version": 0,
    "keywords": [],
    "excludeKeywords": [],
    # Titles. The keyword box is how someone types a job title, and matching
    # it against the description too meant "product manager" hit every posting
    # that merely mentioned working with one. Widening to the description is a
    # deliberate choice in Advanced search, not the default.
    "searchScope": "title",
    "roleCategories": [],
    "locations": [],                 # empty means every country
    "includeRemoteForSelection": True,
    "workplace": list(WORKPLACES),
    "companiesMode": "all",
    "companies": [],
    "maxExperienceYears": None,      # None is "Open to all"
    "degreeMax": None,
    "employmentTypes": [],
    "jobTypes": [],
    "postedWithin": "7d",
    "sort": "new",
    "directOnly": True,
    "includeUnknowns": {key: True for key in UNKNOWN_KEYS},
    "sponsorsVisaOnly": False,
    "minScoreToKeep": 40,
    # Six sweeps a day, Sahil's call: hourly was more polite traffic than the
    # boards change, and a four-hour gap still puts a morning posting on the
    # board the same morning.
    "tier1IntervalMinutes": 4 * 60,
    "tier2SweepsPerDay": MIN_TIER2_SWEEPS_PER_DAY,
    # How many jobs to keep on disk. null means keep everything, which is a
    # real choice with a real cost: the pool is one JSON file, read and
    # rewritten whole on every ingest, so the run gets slower and hungrier as
    # it grows. Measured numbers are in docs/plans/ats-coverage-triage.md.
    "storedCap": jobs_store.STORED_CAP,
}


class ConfigError(Exception):
    pass


def root_dir(override=None):
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get("JOB_APPLICATION_DIR", "~/.job-application")).expanduser()


def config_path(root):
    return Path(root) / "search-config.json"


def _string_list(value, field):
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ConfigError(f"{field} must be a list of strings")
    return [x.strip() for x in value if x.strip()]


def validate(config):
    """Return the config filled out and checked, or raise saying what is wrong.

    Unknown keys are kept rather than rejected: a board that has shipped a new
    filter should not be unable to save until this file catches up. Known keys
    are checked hard, because a silently-wrong `postedWithin` would quietly
    narrow every scan and look like the connectors had gone quiet.
    """
    if not isinstance(config, dict):
        raise ConfigError("the search config must be a JSON object")
    out = {**DEFAULTS, **config}

    for field in ("keywords", "excludeKeywords", "roleCategories", "companies",
                  "employmentTypes", "jobTypes"):
        out[field] = _string_list(out.get(field), field)

    if out["searchScope"] not in SEARCH_SCOPES:
        raise ConfigError(f"searchScope must be one of {list(SEARCH_SCOPES)}")
    out["postedWithin"] = LEGACY_POSTED_WITHIN.get(out["postedWithin"],
                                                   out["postedWithin"])
    if out["postedWithin"] not in POSTED_WITHIN:
        raise ConfigError(f"postedWithin must be one of {sorted(POSTED_WITHIN)}")
    if out["companiesMode"] not in COMPANIES_MODES:
        raise ConfigError(f"companiesMode must be one of {list(COMPANIES_MODES)}")
    if out["sort"] not in SORTS:
        raise ConfigError(f"sort must be one of {list(SORTS)}")
    out["directOnly"] = bool(out.get("directOnly"))

    workplace = _string_list(out.get("workplace"), "workplace")
    bad = [w for w in workplace if w not in WORKPLACES]
    if bad:
        raise ConfigError(f"workplace values must be from {list(WORKPLACES)}; got {bad}")
    out["workplace"] = workplace or list(WORKPLACES)

    locations = out.get("locations") or []
    if not isinstance(locations, list):
        raise ConfigError("locations must be a list")
    cleaned = []
    for entry in locations:
        if not isinstance(entry, dict):
            raise ConfigError("each location must be an object with country or regionPack")
        if entry.get("regionPack"):
            pack = str(entry["regionPack"]).upper()
            if pack not in REGION_PACKS:
                raise ConfigError(f"unknown regionPack {pack}; "
                                  f"known packs are {sorted(REGION_PACKS)}")
            cleaned.append({"regionPack": pack})
        elif entry.get("country"):
            country = str(entry["country"]).upper()
            if len(country) != 2 or not country.isalpha():
                raise ConfigError(f"country must be a two-letter code; got {entry['country']!r}")
            cleaned.append({"country": country})
        else:
            raise ConfigError("each location needs a country or a regionPack")
    out["locations"] = cleaned

    years = out.get("maxExperienceYears")
    if years is not None:
        if not isinstance(years, (int, float)) or isinstance(years, bool) or years < 0:
            raise ConfigError("maxExperienceYears must be a non-negative number, or null for 'Open to all'")
        out["maxExperienceYears"] = int(years)

    degree = out.get("degreeMax")
    if degree is not None and degree not in DEGREE_LADDER:
        raise ConfigError(f"degreeMax must be one of {list(DEGREE_LADDER)}, or null")

    unknowns = out.get("includeUnknowns") or {}
    if not isinstance(unknowns, dict):
        raise ConfigError("includeUnknowns must be an object")
    out["includeUnknowns"] = {key: bool(unknowns.get(key, True)) for key in UNKNOWN_KEYS}

    score = out.get("minScoreToKeep")
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 100:
        raise ConfigError("minScoreToKeep must be a number between 0 and 100")
    out["minScoreToKeep"] = int(score)

    cap = out.get("storedCap")
    if cap is not None:
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < 100:
            raise ConfigError("storedCap must be an integer of at least 100, "
                              "or null to keep everything")

    interval = out.get("tier1IntervalMinutes")
    if not isinstance(interval, int) or isinstance(interval, bool) or interval < MIN_TIER1_INTERVAL_MINUTES:
        raise ConfigError(
            f"tier1IntervalMinutes must be an integer of at least {MIN_TIER1_INTERVAL_MINUTES}")

    sweeps = out.get("tier2SweepsPerDay")
    if not isinstance(sweeps, int) or isinstance(sweeps, bool) or sweeps < MIN_TIER2_SWEEPS_PER_DAY:
        raise ConfigError(
            f"tier2SweepsPerDay must be an integer of at least {MIN_TIER2_SWEEPS_PER_DAY}: "
            "sweeps rotate through channel packs, and fewer leaves a pack unswept for a day")

    out["includeRemoteForSelection"] = bool(out.get("includeRemoteForSelection"))
    out["sponsorsVisaOnly"] = bool(out.get("sponsorsVisaOnly"))
    if not isinstance(out.get("version"), int) or isinstance(out.get("version"), bool):
        raise ConfigError("version must be an integer")
    return out


def expand_locations(config):
    """Every country the config selects, packs expanded. Empty means all."""
    countries = []
    for entry in config.get("locations") or []:
        if entry.get("regionPack"):
            countries.extend(REGION_PACKS.get(entry["regionPack"], []))
        elif entry.get("country"):
            countries.append(entry["country"])
    return sorted(set(countries))


def posted_within_days(config):
    """The date window in days. Never wider than the two-month ceiling.

    There is no "no limit" answer any more. `all` used to return None and every
    caller read that as "keep everything", which is how a nine-month-old
    posting reached the board.
    """
    days = POSTED_WITHIN.get(config.get("postedWithin"), 7)
    return MAX_AGE_DAYS if days is None else min(days, MAX_AGE_DAYS)


def load(root):
    """The saved config, or None when the user has never saved one.

    None is a real answer and callers must handle it: the scheduler stays
    disarmed and manual ingest says it is running against defaults.
    """
    path = config_path(root)
    if not path.exists():
        return None
    try:
        raw = store.read_json(path, None)
    except store.StoreError as exc:
        raise ConfigError(str(exc))
    if raw is None:
        return None
    return validate(raw)


def save(root, config, bump=True):
    """Validate, bump the version, write atomically. Returns the stored config.

    The version is what the board's banner shows and what each job records in
    `configVersion`, so it has to move on every save even when nothing else did.
    """
    current = None
    try:
        current = load(root)
    except ConfigError:
        current = None
    checked = validate(config)
    if bump:
        previous = (current or {}).get("version", 0)
        checked["version"] = max(previous, int(config.get("version") or 0)) + 1
    store.write_private(config_path(root), checked)
    return checked


def main():
    ap = argparse.ArgumentParser(description="The saved search config.")
    ap.add_argument("--store", help="store directory (default ~/.job-application)")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    sub.add_parser("packs")
    for name in ("save", "validate"):
        p = sub.add_parser(name)
        p.add_argument("--input", required=True, help="JSON file, or - for stdin")
    args = ap.parse_args()

    root = root_dir(args.store)
    try:
        if args.command == "show":
            config = load(root)
            result = config if config is not None else {
                "saved": False,
                "why": "no search has been saved yet; the hourly scan stays disarmed",
                "defaults": DEFAULTS,
            }
        elif args.command == "packs":
            result = REGION_PACKS
        else:
            raw = json.load(sys.stdin) if args.input == "-" else json.loads(
                Path(args.input).read_text())
            result = validate(raw) if args.command == "validate" else save(root, raw)
    except ConfigError as exc:
        print(f"search config: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError:
        print("search config: input is not valid JSON", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
