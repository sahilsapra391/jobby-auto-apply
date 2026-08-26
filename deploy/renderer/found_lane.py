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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import company_logos  # noqa: E402
import match_score  # noqa: E402
import clock
import search_config  # noqa: E402

# How many jobs travel to the browser.
#
# Chosen from a measurement, not a preference. At the real record shape the
# payload costs 857 bytes a job once the weights are hoisted out, the rubric
# explanations are interned and the duplicated excerpt is dropped — down from
# 1,576. Measured on a real 12,855-job pool, the whole rendered board comes to
# 3.24 MB at 3,400 including the pool census, and 3.47 MB at 3,800 without it.
# 3,400 was the largest that left real margin under the 4 MB target rather than
# grazing it. Do not raise this past what has been measured.
#
# Lowered to 3,350 on 2026-08-25, because that measurement was taken against a
# page carrying one feature and the page now carries two. The Profile page's
# shell costs 35,713 bytes of shared document whichever lane a reader opens,
# and at 3,400 that put the whole board at 3,879,904 bytes: past the 3.7 MB
# margin line, grazing the ceiling this cap exists to keep clear of. Fifty
# records buy it back and then some -- measured at 3,825,578 bytes, which is
# 14,879 *below* where the board sat before any of this. The alternative was
# moving the margin line, which is the same thing as not having one.
EMBED_CAP = 3350
# One slice of the description, used by the card and by Details. It used to be
# two overlapping slices of the same text.
EMBED_EXCERPT_CHARS = 320
MAX_TAGS = 6

# The share of the budget reserved for per-region floors. The rest is spent
# globally, so dense regions still dominate — they just cannot take everything.
RESERVED_SHARE = 0.55

# Fixed order, so a score breakdown can travel as an array instead of five
# repeated key names on every job.
COMPONENTS = ("title", "experience", "location", "keywords", "freshness")

# Identical on every job, so it travels once instead of 3,400 times.
#
# **This depends on an invariant that Phase 4 is explicitly about breaking.**
# Calibration makes the weights vary — per job, or per cohort — and the moment
# they do, hoisting one copy means every card's "why this %" panel explains
# itself with somebody else's numbers. It will not error and it will not look
# wrong; the bars will simply be against the wrong denominators.
#
# If you are implementing calibration: move the weights back onto the row, or
# emit a small table of distinct weight sets and reference them the way `wy`
# references the interned explanations. The second costs almost nothing,
# because there will be far fewer weight sets than jobs.
MATCH_WEIGHTS = dict(match_score.WEIGHTS)

# The same vocabulary search_config offers, so a saved value always has a chip.
# It used not to: a search saved as "60d" matched no chip, none was checked, and
# the client fell back to seven days — a board quietly showing a tenth of what
# the saved search asked for, with nothing on screen saying so.
#
# "Any time" is gone rather than relabelled. Nothing older than two months is
# ingested or kept, so "all" and "60d" name the same window, and two chips for
# one window is a choice the user cannot get right.
SCOPE_OPTIONS = [("title", "Title only"),
                 ("title+description", "Title and description")]

SORT_OPTIONS = [("new", "Newest"), ("score", "Match %")]

DATE_WINDOWS = [("6h", "6 hours"), ("24h", "24 hours"), ("7d", "7 days"),
                ("14d", "14 days"), ("30d", "30 days"), ("60d", "2 months")]
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


def compact(job, why_table, logo_table=None):
    """One job, trimmed to what a card and its Details panel actually show.

    Deliberately terse. The weights are identical on every job and are hoisted
    out; the rubric's explanations repeat a handful of sentences across
    thousands of records and are interned into a shared table; the score
    breakdown rides as an array in a fixed component order rather than five
    repeated key names. Together that is roughly a third of the payload.
    """
    score = job.get("score") or {}
    location = job.get("location") or {}
    sightings = []
    for source in (job.get("sources") or []):
        link = safe_url(source.get("url"))
        if link:
            sightings.append({"s": SOURCE_LABELS.get(source.get("source") or "",
                                                     (source.get("source") or "").title()),
                              "u": link})
    extracted = job.get("extracted") or {}
    parts = score.get("parts") or {}
    why = score.get("why") or {}
    return {
        "id": job.get("id"),
        "place": place(job),
        "rank": direct_rank(job),
        "co": job.get("company") or "",
        "ti": tidy_title(job.get("title")),
        "lp": company_logos.logo_plan(job, job.get("canonicalUrl") or ""),
        "lg": (logo_table.intern(company_logos.proxy_url(job, job.get("canonicalUrl") or ""))
               if logo_table is not None
                   and company_logos.logo_plan(job, job.get("canonicalUrl") or "")
                       == company_logos.PLAN_NONE
               else -1),
        "url": safe_url(job.get("canonicalUrl")),
        "src": SOURCE_LABELS.get(job.get("canonicalSource") or "",
                                 (job.get("canonicalSource") or "").title()),
        "cty": location.get("country") or "",
        # The slug, not the label: the workplace checkboxes compare against
        # this value, so what ships has to be what they can select. The card
        # turns it back into "On-site" at draw time.
        "wp": job.get("workplace") if job.get("workplace") in WORKPLACE_LABELS else "",
        "pa": (job.get("postedAt") or "")[:10],
        "sc": score.get("total"),
        "pt": [parts.get(name, 0) for name in COMPONENTS],
        "wy": [why_table.intern(why.get(name, "")) for name in COMPONENTS],
        "un": score.get("unknowns") or [],
        "ga": [g.get("label") for g in (score.get("gates") or []) if g.get("label")],
        "bd": len({s.get("source") for s in (job.get("sources") or []) if s.get("source")}),
        "sg": sightings,
        "tg": [tidy_tag(t) for t in (job.get("tags") or [])[:MAX_TAGS] if tidy_tag(t)],
        "sum": " ".join((job.get("descriptionExcerpt") or "").split())[:EMBED_EXCERPT_CHARS],
        "yr": extracted.get("yearsMin") if extracted.get("yearsMin") is not None
              else extracted.get("yearsMax"),
        "yrt": extracted.get("yearsText"),
        "sal": job.get("salary"),
    }


DAY_WORDS = ((" 0 days ago", " today"), (" 1 days ago", " yesterday"))


def tidy_days(text):
    """`posted 1 days ago` is not English, and it is already in the store.

    The rubric writes these when a job is scored, so they are persisted strings
    rather than something recomputed at render. Fixing the writer only helps
    jobs scored after the fix; fixing it here reaches the fourteen thousand
    already on disk without a migration and without touching a single score.
    """
    for wrong, right in DAY_WORDS:
        if wrong in text:
            text = text.replace(wrong, right)
    return text


class StringTable:
    """Interns any repeated string into a shared list of unique values."""

    def __init__(self):
        self.strings = []
        self._index = {}

    def intern(self, text):
        text = str(text or "")
        if text not in self._index:
            self._index[text] = len(self.strings)
            self.strings.append(text)
        return self._index[text]


class WhyTable:
    """Interns the rubric's explanations, which repeat across thousands of jobs."""

    def __init__(self):
        self.strings = []
        self._index = {}

    def intern(self, text):
        text = tidy_days(str(text or ""))
        if text not in self._index:
            self._index[text] = len(self.strings)
            self.strings.append(text)
        return self._index[text]


# Codes are for filtering and storage. Cards and controls always show the full
# name. The picker includes the full sovereign-country set plus Hong Kong,
# Macau, Kosovo, Palestine, and Taiwan because job boards use those location
# codes independently.
COUNTRY_NAMES = {
    "AD": "Andorra", "AE": "United Arab Emirates", "AF": "Afghanistan",
    "AG": "Antigua and Barbuda", "AL": "Albania", "AM": "Armenia",
    "AO": "Angola", "AR": "Argentina", "AT": "Austria", "AU": "Australia",
    "AZ": "Azerbaijan", "BA": "Bosnia and Herzegovina", "BB": "Barbados",
    "BD": "Bangladesh", "BE": "Belgium", "BF": "Burkina Faso", "BG": "Bulgaria",
    "BH": "Bahrain", "BI": "Burundi", "BJ": "Benin", "BN": "Brunei",
    "BO": "Bolivia", "BR": "Brazil", "BS": "Bahamas", "BT": "Bhutan",
    "BW": "Botswana", "BY": "Belarus", "BZ": "Belize", "CA": "Canada",
    "CD": "Democratic Republic of the Congo", "CF": "Central African Republic",
    "CG": "Republic of the Congo", "CH": "Switzerland", "CI": "Côte d'Ivoire",
    "CL": "Chile", "CM": "Cameroon", "CN": "China", "CO": "Colombia",
    "CR": "Costa Rica", "CU": "Cuba", "CV": "Cabo Verde", "CY": "Cyprus",
    "CZ": "Czechia", "DE": "Germany", "DJ": "Djibouti", "DK": "Denmark",
    "DM": "Dominica", "DO": "Dominican Republic", "DZ": "Algeria", "EC": "Ecuador",
    "EE": "Estonia", "EG": "Egypt", "ER": "Eritrea", "ES": "Spain",
    "ET": "Ethiopia", "FI": "Finland", "FJ": "Fiji", "FM": "Micronesia",
    "FR": "France", "GA": "Gabon", "GB": "United Kingdom", "GD": "Grenada",
    "GE": "Georgia", "GH": "Ghana", "GM": "Gambia", "GN": "Guinea",
    "GQ": "Equatorial Guinea", "GR": "Greece", "GT": "Guatemala",
    "GW": "Guinea-Bissau", "GY": "Guyana", "HK": "Hong Kong", "HN": "Honduras",
    "HR": "Croatia", "HT": "Haiti", "HU": "Hungary", "ID": "Indonesia",
    "IE": "Ireland", "IL": "Israel", "IN": "India", "IQ": "Iraq", "IR": "Iran",
    "IS": "Iceland", "IT": "Italy", "JM": "Jamaica", "JO": "Jordan",
    "JP": "Japan", "KE": "Kenya", "KG": "Kyrgyzstan", "KH": "Cambodia",
    "KI": "Kiribati", "KM": "Comoros", "KN": "Saint Kitts and Nevis",
    "KP": "North Korea", "KR": "South Korea", "KW": "Kuwait", "KZ": "Kazakhstan",
    "LA": "Laos", "LB": "Lebanon", "LC": "Saint Lucia", "LI": "Liechtenstein",
    "LK": "Sri Lanka", "LR": "Liberia", "LS": "Lesotho", "LT": "Lithuania",
    "LU": "Luxembourg", "LV": "Latvia", "LY": "Libya", "MA": "Morocco",
    "MC": "Monaco", "MD": "Moldova", "ME": "Montenegro", "MG": "Madagascar",
    "MH": "Marshall Islands", "MK": "North Macedonia", "ML": "Mali",
    "MM": "Myanmar", "MN": "Mongolia", "MO": "Macau", "MR": "Mauritania",
    "MT": "Malta", "MU": "Mauritius", "MV": "Maldives", "MW": "Malawi",
    "MX": "Mexico", "MY": "Malaysia", "MZ": "Mozambique", "NA": "Namibia",
    "NE": "Niger", "NG": "Nigeria", "NI": "Nicaragua", "NL": "Netherlands",
    "NO": "Norway", "NP": "Nepal", "NR": "Nauru", "NZ": "New Zealand",
    "OM": "Oman", "PA": "Panama", "PE": "Peru", "PG": "Papua New Guinea",
    "PH": "Philippines", "PK": "Pakistan", "PL": "Poland", "PS": "Palestine",
    "PT": "Portugal", "PW": "Palau", "PY": "Paraguay", "QA": "Qatar",
    "RO": "Romania", "RS": "Serbia", "RU": "Russia", "RW": "Rwanda",
    "SA": "Saudi Arabia", "SB": "Solomon Islands", "SC": "Seychelles",
    "SD": "Sudan", "SE": "Sweden", "SG": "Singapore", "SI": "Slovenia",
    "SK": "Slovakia", "SL": "Sierra Leone", "SM": "San Marino", "SN": "Senegal",
    "SO": "Somalia", "SR": "Suriname", "SS": "South Sudan",
    "ST": "São Tomé and Príncipe", "SV": "El Salvador", "SY": "Syria",
    "SZ": "Eswatini", "TD": "Chad", "TG": "Togo", "TH": "Thailand",
    "TJ": "Tajikistan", "TL": "Timor-Leste", "TM": "Turkmenistan",
    "TN": "Tunisia", "TO": "Tonga", "TR": "Turkey", "TT": "Trinidad and Tobago",
    "TV": "Tuvalu", "TW": "Taiwan", "TZ": "Tanzania", "UA": "Ukraine",
    "UG": "Uganda", "US": "United States", "UY": "Uruguay", "UZ": "Uzbekistan",
    "VA": "Vatican City", "VC": "Saint Vincent and the Grenadines", "VE": "Venezuela",
    "VN": "Vietnam", "VU": "Vanuatu", "WS": "Samoa", "XK": "Kosovo",
    "YE": "Yemen", "ZA": "South Africa", "ZM": "Zambia", "ZW": "Zimbabwe",
}

# A location picker needs a stable hierarchy, not a flat mixture of countries
# and regions. These are display groups only. Saved searches continue to store
# two-letter country codes, so changing a label or moving a transcontinental
# country later does not change what a search means.
CONTINENT_COUNTRIES = (
    ("north-america", "North America", (
        "AG", "BS", "BB", "BZ", "CA", "CR", "CU", "DM", "DO", "SV", "GD",
        "GT", "HT", "HN", "JM", "MX", "NI", "PA", "KN", "LC", "VC", "TT", "US",
    )),
    ("south-america", "South America", (
        "AR", "BO", "BR", "CL", "CO", "EC", "GY", "PY", "PE", "SR", "UY", "VE",
    )),
    ("europe", "Europe", (
        "AL", "AD", "AT", "BY", "BE", "BA", "BG", "HR", "CY", "CZ", "DK",
        "EE", "FI", "FR", "DE", "GR", "HU", "IS", "IE", "IT", "XK", "LV",
        "LI", "LT", "LU", "MT", "MD", "MC", "ME", "NL", "MK", "NO", "PL",
        "PT", "RO", "RU", "SM", "RS", "SK", "SI", "ES", "SE", "CH", "UA",
        "GB", "VA",
    )),
    ("asia", "Asia", (
        "AF", "AM", "AZ", "BH", "BD", "BT", "BN", "KH", "CN", "GE", "HK",
        "IN", "ID", "IR", "IQ", "IL", "JP", "JO", "KZ", "KW", "KG", "LA",
        "LB", "MO", "MY", "MV", "MN", "MM", "NP", "KP", "OM", "PK", "PS",
        "PH", "QA", "SA", "SG", "KR", "LK", "SY", "TW", "TJ", "TH", "TL",
        "TR", "TM", "AE", "UZ", "VN", "YE",
    )),
    ("africa", "Africa", (
        "DZ", "AO", "BJ", "BW", "BF", "BI", "CV", "CM", "CF", "TD", "KM",
        "CG", "CD", "CI", "DJ", "EG", "GQ", "ER", "SZ", "ET", "GA", "GM",
        "GH", "GN", "GW", "KE", "LS", "LR", "LY", "MG", "MW", "ML", "MR",
        "MU", "MA", "MZ", "NA", "NE", "NG", "RW", "ST", "SN", "SC", "SL",
        "SO", "ZA", "SS", "SD", "TZ", "TG", "TN", "UG", "ZM", "ZW",
    )),
    ("oceania", "Oceania", (
        "AU", "FJ", "KI", "MH", "FM", "NR", "NZ", "PW", "PG", "WS", "SB",
        "TO", "TV", "VU",
    )),
)

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
    """What survives the cut, in the order canonicity is already ranked.

    Direct listings first, then match, then recency — the same key
    `keep_priority` uses for retention, for the same reason: a posting you can
    apply to through the employer's own form beats a redirect to it.
    """
    score = (job.get("score") or {}).get("total")
    return (0 if direct_rank(job) <= 1 else 1,
            -(score if isinstance(score, (int, float)) else -1),
            "" if not job.get("postedAt") else _invert(job.get("postedAt")))


def _invert(stamp):
    """Descending date inside an ascending sort, without a second pass."""
    return "".join(chr(0x10FFFD - ord(c)) if ord(c) < 0x10FFFD else c for c in str(stamp)[:10])


def bucket_of(job, buckets):
    """Which selected location this job answers, or `other`.

    Exclusive by design: a job counts once, so the floors partition the budget
    rather than overlapping. Explicit countries win over a region pack that
    happens to contain them, because someone who typed "IN" meant India rather
    than whichever pack India also falls inside.
    """
    country = str((job.get("location") or {}).get("country") or "").upper()
    if country:
        for key, countries in buckets:
            if key.startswith("country:") and country in countries:
                return key
        for key, countries in buckets:
            if key.startswith("pack:") and country in countries:
                return key
    if str(job.get("workplace") or "").lower() == "remote":
        for key, _ in buckets:
            if key == "remote":
                return key
    return "other"


def selection_buckets(config):
    """(key, countries) for every location the saved search names.

    The unit is a config *entry*, not an expanded country: `{"regionPack":"EU"}`
    is one bucket covering thirty-two countries rather than thirty-two buckets
    competing with the United States for the same budget.
    """
    config = config or {}
    out = []
    for entry in config.get("locations") or []:
        if entry.get("regionPack"):
            pack = entry["regionPack"]
            out.append((f"pack:{pack}", set(search_config.REGION_PACKS.get(pack, []))))
        elif entry.get("country"):
            out.append((f"country:{entry['country']}", {entry["country"]}))
    if config.get("includeRemoteForSelection", True):
        out.append(("remote", set()))
    out.append(("other", set()))
    return out


def select(jobs, config=None, cap=None):
    """(embedded, total) — the pool, cut so every selected region is present.

    A straight top-N by score gives each region a slice proportional to how
    dense it is, and one dense region takes almost everything: measured on a
    12,003-job pool, a filter for Japan reached 46 of 760 and India 154 of
    2,449. Someone filtering for Japan saw 46 results with no way to know 714
    more were on disk.

    So the budget is spent in two passes. Each location the saved search names
    gets a guaranteed floor, filled from its own best; whatever is left — plus
    any floor a thin region could not use — is then spent globally on the same
    ordering. Dense regions still dominate. They just cannot take everything.
    """
    # Resolved here rather than as a default argument, which would bind the
    # constant once at import and quietly ignore any later change to it.
    cap = EMBED_CAP if cap is None else cap
    found = [job for job in (jobs or {}).values() if job.get("state") == "new"]
    found.sort(key=sort_key)
    total = len(found)
    if total <= cap:
        return found, total

    buckets = selection_buckets(config)
    if not buckets:
        return found[:cap], total

    by_bucket = {key: [] for key, _ in buckets}
    for job in found:                                   # already in priority order
        by_bucket[bucket_of(job, buckets)].append(job)

    floor = int(cap * RESERVED_SHARE) // max(1, len(buckets))
    picked, seen = [], set()
    for key, _ in buckets:
        for job in by_bucket[key][:floor]:
            picked.append(job)
            seen.add(job["id"])

    for job in found:                                   # the remainder, globally
        if len(picked) >= cap:
            break
        if job["id"] not in seen:
            picked.append(job)
            seen.add(job["id"])

    picked.sort(key=sort_key)
    return picked[:cap], total


def pool_census(jobs):
    """How many unacted-on jobs the pool holds per country, and remote.

    A few hundred bytes that let the page say something true: the client can
    total the pool for whichever countries the user has selected, instead of
    only ever quoting one global number that is not about their filter.
    """
    counts = {}
    remote = 0
    for job in (jobs or {}).values():
        if job.get("state") != "new":
            continue
        code = str((job.get("location") or {}).get("country") or "").upper() or "??"
        counts[code] = counts.get(code, 0) + 1
        if str(job.get("workplace") or "").lower() == "remote":
            remote += 1
    counts["__remote"] = remote
    return counts


def payload(jobs, census=None, total=None):
    """The JSON document, escaped so no posting can close the script element.

    `</` is the only sequence that can terminate a script element from inside a
    string, and a job title is untrusted text off the open internet.

    Three things ride outside the job list because they are identical or nearly
    identical across every record: the rubric weights, the component order, and
    the interned explanations. Together they were about a third of the payload.
    """
    table = WhyTable()
    logos = StringTable()
    rows = [compact(job, table, logos) for job in jobs]
    document = {
        "w": [MATCH_WEIGHTS.get(name, 0) for name in COMPONENTS],
        "c": list(COMPONENTS),
        "y": table.strings,
        "g": logos.strings,
        "n": census or {},
        "t": total if total is not None else len(rows),
        "j": rows,
    }
    blob = json.dumps(document, separators=(",", ":"), ensure_ascii=False)
    return (blob.replace("</", "<\\/")
                .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _countries(jobs):
    return sorted({job.get("location", {}).get("country") for job in jobs
                   if (job.get("location") or {}).get("country")})


def _options(pairs, selected=""):
    return "".join(
        f'<option value="{esc(v)}"{" selected" if v == selected else ""}>{esc(label)}</option>'
        for v, label in pairs)


# Long enough to say what went wrong, short enough not to wrap the status line.
REASON_CHARS = 72


def short_reason(why):
    """One clause of an error, for a line the user reads rather than debugs."""
    why = " ".join(str(why or "").split())
    if not why:
        return "no reason given"
    # These read as "refusing to merge <16 hex chars>: <the actual reason>".
    # The id is for the log, not the board.
    head, sep, tail = why.partition(": ")
    if sep and len(head.split()) <= 4 and any(c.isdigit() for c in head):
        why = tail or why
    if len(why) <= REASON_CHARS:
        return why
    cut = why[:REASON_CHARS].rsplit(" ", 1)[0]
    return (cut or why[:REASON_CHARS]).rstrip(",;") + "\u2026"


def last_ingest_line(meta):
    """What the last scan did, or an honest admission that none has run.

    A scan that found nothing has to read differently from a scan that never
    happened, so this never renders as an empty string.
    """
    report = (meta or {}).get("lastIngest")
    if not report:
        return ('<p class="jb-ingest none">No scan has run yet. '
                'Save a search below, or press Scan now.</p>')
    when = esc(clock.time_label(report.get("at")))
    bits = [f"Last scan {when}" if when else "Last scan",
            f"{report.get('found', 0)} seen",
            f"{report.get('kept', 0)} new",
            f"{report.get('deduped', 0)} merged",
            f"{report.get('gatedOut', 0)} filtered out"]
    # Only when it happened. A card vanishing between visits is the one board
    # change nothing else explains, so the scan line says when it was a closure
    # rather than leaving the user to wonder what they lost.
    closed = report.get("closed") or 0
    if closed:
        bits.append(f"{closed} closed by the employer")
    line = " &middot; ".join(esc(b) for b in bits)

    extras = []
    skipped = report.get("channelsSkipped") or []
    if skipped:
        names = ", ".join(esc(str(s.get("channel"))) for s in skipped[:6])
        if len(skipped) > 6:
            names += f", and {len(skipped) - 6} more"
        rows = "".join(
            f'<li><b>{esc(str(s.get("channel")))}</b>'
            f'<span>{esc(short_reason(s.get("why")))}</span></li>' for s in skipped)
        extras.append(
            f'<details class="jb-skipped jb-probs"><summary>{len(skipped)} channel'
            f'{"s" if len(skipped) != 1 else ""} skipped: {names}</summary>'
            f'<ul class="jb-problist">{rows}</ul></details>')
    errors = report.get("errors") or []
    if errors:
        # Which channels, and one reason each — not four sentences of internals.
        # Two of these were rendering the raw refusal text with record ids in
        # it, wrapping to two full lines of something nobody can act on, which
        # is how a status line teaches people to stop reading the status line.
        # The full text stays in meta.lastIngest for whoever is debugging.
        per_channel = {}
        for entry in errors:
            channel = str(entry.get("channel") or "a source")
            per_channel.setdefault(channel, []).append(entry.get("why") or "")
        parts = []
        for channel, whys in list(per_channel.items())[:3]:
            reason = short_reason(whys[0])
            count = f" &times;{len(whys)}" if len(whys) > 1 else ""
            parts.append(f"{esc(channel)}{count}: {esc(reason)}")
        more = len(per_channel) - 3
        if more > 0:
            parts.append(f"and {more} more")
        rows = "".join(
            f'<li><b>{esc(channel)}</b>'
            f'{f" &times;{len(whys)}" if len(whys) > 1 else ""}'
            f'<span>{esc(short_reason(whys[0]))}</span></li>'
            for channel, whys in per_channel.items())
        extras.append(
            f'<details class="jb-errored jb-probs"><summary>{len(errors)} problem'
            f'{"s" if len(errors) != 1 else ""}: {" &middot; ".join(parts)}</summary>'
            f'<ul class="jb-problist">{rows}</ul></details>')
    if report.get("usingDefaults"):
        extras.append('<span class="jb-skipped">no saved search yet, so this ran on defaults'
                      '</span>')
    if report.get("failed"):
        extras.append('<span class="jb-errored">the scan itself failed</span>')
    detail = f'<br>{" &middot; ".join(extras)}' if extras else ""
    return f'<p class="jb-ingest">{line}{detail}</p>'


def cadence(minutes):
    """"Every 4 hours, 6 times a day" reads; "Every 240 minutes" does not."""
    minutes = int(minutes or 0) or 60
    if minutes % 60 == 0 and minutes >= 60:
        hours = minutes // 60
        every = "Every hour" if hours == 1 else f"Every {hours} hours"
    else:
        every = f"Every {minutes} minutes"
    a_day = (24 * 60) // minutes
    if a_day >= 2:
        return f"{every}, {a_day} times a day"
    return every


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
    nxt_label = clock.time_label(nxt)
    when = f"next run {esc(nxt_label)}" if nxt_label else "next run shortly"
    every = config.get("tier1IntervalMinutes") or search_config.DEFAULTS[
        "tier1IntervalMinutes"]
    return (f'<div class="jb-banner" data-version="{esc(version)}">'
            f'<div><strong>Search v{esc(version)} '
            f'is live.</strong> <span>{esc(cadence(every))} &middot; {esc(when)}</span>'
            f'<span class="jb-savestate" id="jb-savestate"></span></div>'
            f'<button class="jb-btn primary" id="jb-scan">Scan now</button></div>')


def filter_bar(jobs, config):
    """Every filter the plan asks for, and honest labels on the ones Phase 1
    cannot yet answer."""
    config = config or {}
    available = set(COUNTRY_NAMES) | set(_countries(jobs))
    locations = config.get("locations") or []
    selected = (set(search_config.expand_locations(config)) if locations
                else set(available))
    available |= selected
    assigned = {code for _, _, codes in CONTINENT_COUNTRIES for code in codes}
    groups = [(key, label, tuple(code for code in codes if code in available))
              for key, label, codes in CONTINENT_COUNTRIES]
    other = tuple(sorted(available - assigned, key=lambda code: COUNTRY_NAMES.get(code, code)))
    if other:
        groups.append(("other", "Other locations", other))
    groups = [(key, label, codes) for key, label, codes in groups if codes]

    country_total = sum(len(codes) for _, _, codes in groups)
    selected_total = sum(1 for _, _, codes in groups for code in codes if code in selected)
    if selected_total == country_total:
        summary_title = "Worldwide"
        summary_note = f"All {country_total} countries"
    elif selected_total == 0:
        summary_title = "No countries selected"
        summary_note = "Choose a continent or country"
    elif selected_total == 1:
        only = next(code for _, _, codes in groups for code in codes if code in selected)
        summary_title = COUNTRY_NAMES.get(only, only)
        summary_note = "1 country"
    else:
        summary_title = f"{selected_total} countries"
        summary_note = "Multiple continents"

    group_markup = []
    for key, label, codes in groups:
        group_selected = sum(1 for code in codes if code in selected)
        fully_selected = group_selected == len(codes)
        open_group = bool(group_selected and selected_total != country_total)
        countries_markup = "".join(
            f'<div class="jb-country-row">'
            f'<label class="jb-country"><input type="checkbox" data-loc="{esc(code)}" '
            f'data-continent-country="{esc(key)}" data-country-name="{esc(COUNTRY_NAMES.get(code, code))}"'
            f'{" checked" if code in selected else ""}>'
            f'<span>{esc(COUNTRY_NAMES.get(code, code))}</span></label>'
            f'<button type="button" class="jb-only" data-location-only="{esc(code)}" '
            f'aria-label="Select only {esc(COUNTRY_NAMES.get(code, code))}">Only</button>'
            f'</div>' for code in codes
        )
        group_markup.append(
            f'<details class="jb-continent" data-continent-panel="{esc(key)}"'
            f'{" open" if open_group else ""}>'
            f'<summary><span class="jb-continent-main">'
            f'<input type="checkbox" data-continent="{esc(key)}" '
            f'aria-label="Select all countries in {esc(label)}"'
            f'{" checked" if fully_selected else ""}>'
            f'<span>{esc(label)}</span></span>'
            f'<span class="jb-continent-count" id="jb-continent-count-{esc(key)}">'
            f'{group_selected} of {len(codes)}</span></summary>'
            f'<div class="jb-country-grid">{countries_markup}</div></details>'
        )
    location_picker = (
        f'<details class="jb-location-picker" id="jb-loc" data-location-total="{country_total}">'
        f'<summary><span><span class="jb-location-title" id="jb-location-title">'
        f'{esc(summary_title)}</span><small id="jb-location-note">{esc(summary_note)}</small></span>'
        f'<span class="jb-location-chevron" aria-hidden="true"></span></summary>'
        f'<div class="jb-location-menu">'
        f'<div class="jb-location-actions">'
        f'<label class="jb-world"><input type="checkbox" id="jb-world"'
        f'{" checked" if selected_total == country_total else ""}>'
        f'<span>Worldwide</span></label>'
        f'<button type="button" class="jb-location-clear" id="jb-location-clear">'
        f'Clear all</button></div>'
        f'<div class="jb-continent-list">{"".join(group_markup)}</div>'
        f'</div></details>'
    )
    # The sort and the direct-only toggle come off the saved search too. They
    # used to be hardcoded in the markup, so every republish put them back to
    # the factory setting whatever the user had chosen.
    saved_scope = config.get("searchScope")
    if saved_scope not in dict(SCOPE_OPTIONS):
        saved_scope = search_config.DEFAULTS["searchScope"]
    scope_options = "".join(
        f'<option value="{esc(v)}"{" selected" if v == saved_scope else ""}>'
        f'{esc(label)}</option>' for v, label in SCOPE_OPTIONS)

    saved_sort = config.get("sort")
    if saved_sort not in dict(SORT_OPTIONS):
        saved_sort = search_config.DEFAULTS["sort"]
    sort_options = "".join(
        f'<option value="{esc(v)}"{" selected" if v == saved_sort else ""}>'
        f'{esc(label)}</option>' for v, label in SORT_OPTIONS)
    direct_on = " checked" if config.get(
        "directOnly", search_config.DEFAULTS["directOnly"]) else ""

    # Whatever the saved search says, exactly one chip is checked. An unchecked
    # set reads to the client as "no answer" and it falls back to seven days.
    saved_window = config.get("postedWithin") or "7d"
    if saved_window not in dict(DATE_WINDOWS):
        saved_window = DATE_WINDOWS[-1][0]
    dates = "".join(
        f'<label class="jb-radio"><input type="radio" name="jb-date" value="{esc(v)}"'
        f'{" checked data-shipped" if v == saved_window else ""}>'
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
        <label class="jb-check"><input type="checkbox" id="jb-direct"{direct_on}>
          <span>direct listings only</span></label>
        <select id="jb-sort" aria-label="Sort">{sort_options}</select>
        <span class="jb-count" id="jb-count"></span>
      </div>
      <div class="jb-pills" id="jb-pills"></div>
      <details class="jb-adv">
        <summary>Advanced search</summary>
        <div class="jb-advbody">
          <div class="jb-row">
            <input type="search" id="jb-ex" placeholder="Hide jobs mentioning&hellip;"
                   aria-label="Hide jobs mentioning">
            <select id="jb-scope" aria-label="Where to search">{scope_options}</select>
            <select id="jb-cmode" aria-label="Companies">
              <option value="all">All companies</option>
              <option value="only">Only these companies</option>
              <option value="hide">Hide these companies</option>
            </select>
            <input type="search" id="jb-companies" placeholder="Company names, comma separated"
                   aria-label="Company names">
          </div>
          <div class="jb-row">
            <div class="jb-location-field">
              <span class="jb-location-label">Locations</span>
              {location_picker}
            </div>
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
        # Deliberately not "refine the filters and we will search the rest".
        # Filtering happens in the browser, over these records only, and saying
        # otherwise would be the page claiming a reach it does not have. The
        # count line beside the filters names the pool total for whichever
        # locations are selected, which is the number that is actually useful.
        footer = (f'<p class="jb-footer">{shown:,} of {total:,} jobs are on this page. '
                  f'Filtering searches these {shown:,}; the rest are on disk and reach '
                  f'the page as scores and dates change. Save a narrower search to '
                  f'change which ones are here.</p>')
    return f"""<div class="jb-wide">
    {banner(config, meta)}
    {last_ingest_line(meta)}
    {filter_bar(jobs, config)}
    <div class="jb-grid" id="jb-grid"></div>
    <div id="jb-tail"></div>
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
  .jb-adv > summary::before { content:"\\25B8  "; }
  .jb-adv[open] > summary::before { content:"\\25BE  "; }
  .jb-adv > summary:focus-visible { outline:2px solid var(--blue); outline-offset:3px; }
  .jb-advbody { display:flex; flex-direction:column; gap:.5rem; padding-top:.5rem;
    margin-top:.35rem; border-top:1px solid var(--hair); }
  .jb-pills { display:flex; gap:.35rem; flex-wrap:wrap; }

  /* The location selector stays one compact field until opened. Continents
     supply the hierarchy, country checkboxes keep multi-select familiar, and
     the popup scrolls instead of making Advanced search several screens tall. */
  .jb-location-field { position:relative; flex:1 1 30rem; max-width:44rem;
    display:flex; flex-direction:column; gap:.2rem; }
  .jb-location-label { font-size:.72rem; color:var(--ink-3); padding-left:.15rem; }
  .jb-location-picker { position:relative; }
  .jb-location-picker > summary { list-style:none; min-height:2.7rem; cursor:pointer;
    display:flex; align-items:center; justify-content:space-between; gap:1rem;
    border:1px solid var(--hair); border-radius:12px; padding:.46rem .75rem;
    background:var(--bg); color:var(--ink); }
  .jb-location-picker > summary::-webkit-details-marker,
  .jb-continent > summary::-webkit-details-marker { display:none; }
  .jb-location-picker > summary:hover { border-color:var(--ink-3); }
  .jb-location-picker > summary:focus-visible { outline:2px solid var(--blue);
    outline-offset:2px; }
  .jb-location-picker > summary > span:first-child { display:flex; flex-direction:column;
    min-width:0; }
  .jb-location-title { font-size:.88rem; font-weight:570; overflow:hidden;
    text-overflow:ellipsis; white-space:nowrap; }
  .jb-location-picker > summary small { color:var(--ink-3); font-size:.7rem; }
  .jb-location-chevron { width:.44rem; height:.44rem; flex:none;
    border-right:1.5px solid var(--ink-3); border-bottom:1.5px solid var(--ink-3);
    transform:rotate(45deg) translateY(-2px); transition:transform .16s; }
  .jb-location-picker[open] .jb-location-chevron { transform:rotate(225deg) translateY(-2px); }
  .jb-location-menu { position:absolute; z-index:30; top:calc(100% + .4rem); left:0;
    width:min(42rem, calc(100vw - 3.5rem)); max-height:min(31rem, 68vh); overflow:auto;
    padding:.65rem; border:1px solid var(--hair); border-radius:14px;
    background:var(--card); box-shadow:0 18px 50px rgba(0,0,0,.2); }
  .jb-location-actions { display:flex; align-items:center; justify-content:space-between;
    gap:1rem; padding:.15rem .15rem .65rem; border-bottom:1px solid var(--hair); }
  .jb-world { display:flex; align-items:center; gap:.5rem; font-size:.86rem;
    font-weight:600; cursor:pointer; }
  .jb-location-clear, .jb-only { font:inherit; border:0; background:none;
    color:var(--blue); cursor:pointer; border-radius:6px; }
  .jb-location-clear { font-size:.78rem; padding:.3rem .4rem; }
  .jb-location-clear:hover, .jb-only:hover { background:var(--sunk); }
  .jb-location-clear:focus-visible, .jb-only:focus-visible { outline:2px solid var(--blue);
    outline-offset:1px; }
  .jb-continent-list { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr));
    gap:.45rem; padding-top:.55rem; }
  .jb-continent { border:1px solid var(--hair); border-radius:11px; background:var(--bg); }
  .jb-continent > summary { list-style:none; cursor:pointer; display:flex;
    align-items:center; justify-content:space-between; gap:.5rem; padding:.55rem .65rem;
    min-height:2.45rem; }
  .jb-continent > summary:focus-visible { outline:2px solid var(--blue);
    outline-offset:1px; border-radius:10px; }
  .jb-continent-main { display:flex; align-items:center; gap:.45rem; min-width:0;
    font-size:.82rem; font-weight:570; }
  .jb-continent-main input, .jb-country input, .jb-world input { accent-color:var(--blue); }
  .jb-continent-count { color:var(--ink-3); font-size:.68rem; white-space:nowrap; }
  .jb-country-grid { display:grid; grid-template-columns:1fr;
    gap:.1rem; padding:.15rem .45rem .5rem; border-top:1px solid var(--hair); }
  .jb-country-row { min-width:0; display:flex; align-items:center; gap:.2rem;
    border-radius:7px; }
  .jb-country-row:hover { background:var(--sunk); }
  .jb-country { min-width:0; flex:1; display:flex; align-items:center; gap:.35rem;
    padding:.32rem .25rem; font-size:.75rem; color:var(--ink-2); cursor:pointer; }
  .jb-country span { white-space:normal; line-height:1.25; }
  .jb-only { font-size:.68rem; padding:.24rem .3rem; margin-right:.1rem; }
  @media (max-width:700px) {
    .jb-location-field { flex-basis:100%; max-width:none; }
    .jb-location-menu { width:calc(100vw - 3.5rem); }
    .jb-continent-list { grid-template-columns:1fr; }
  }

  /* "2 problems" with no way to see which two is a number you can only worry
     about. It opens. */
  .jb-probs > summary { cursor:pointer; list-style:none; width:max-content; }
  .jb-probs > summary::-webkit-details-marker { display:none; }
  .jb-probs > summary::before { content:"\\25B8  "; }
  .jb-probs[open] > summary::before { content:"\\25BE  "; }
  .jb-probs > summary:focus-visible { outline:2px solid var(--blue); outline-offset:2px; }
  .jb-problist { margin:.4rem 0 .1rem; padding:0 0 0 1rem; list-style:none;
    display:flex; flex-direction:column; gap:.2rem; }
  .jb-problist li { display:flex; gap:.45rem; align-items:baseline;
    font-size:.8rem; color:var(--ink-2); }
  .jb-problist b { font-weight:600; color:var(--ink-1); }
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
  /* Independent columns, not a grid and not CSS multi-column.
     A grid puts every card in a row on one baseline, so opening one grew its
     three neighbours. CSS `columns` re-balances the whole container when any
     card changes height, so opening one would shuffle cards between columns.
     Separate flex columns do neither: a card that grows pushes only the cards
     below it in its own column, and nothing else on the board moves.
     min-width:0 on the column is what stops one unbreakable string setting the
     column's width — a 156-character tracking URL in a job description took a
     grid track to 956px of a 1419px row and scrolled the page sideways. */
  .jb-grid { display:flex; align-items:flex-start; gap:1.15rem; --cols:4; }
  .jb-col { flex:1 1 0; min-width:0; display:flex; flex-direction:column;
    gap:1.15rem; }
  @media (max-width:1180px) { .jb-grid { --cols:2; } }
  .jb-card { background:var(--card); border-radius:20px; padding:1.3rem 1.4rem 1.1rem;
    box-shadow:var(--sh); display:flex; flex-direction:column; gap:.8rem;
    min-width:0; overflow-wrap:anywhere;
    border:1px solid var(--hair); transition:border-color .22s; }
  .jb-card:hover { border-color:var(--hair-strong); }
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
  .jb-sections { display:grid; gap:.9rem 2.2rem; margin:.2rem 0 .1rem;
    grid-template-columns:1fr 1fr; align-items:start; }
  .jb-section h4 { margin:0 0 .35rem; font-size:.74rem; font-weight:640;
    letter-spacing:.04em; text-transform:uppercase; color:var(--ink-3); }
  .jb-section ul { margin:0; padding-left:1.05rem; display:grid; gap:.22rem; }
  .jb-section li { font-size:.82rem; line-height:1.5; color:var(--ink-2); }
  .jb-loading { margin:0; font-size:.8rem; color:var(--ink-3); }
  @media (max-width:900px) {
    .jb-sections { grid-template-columns:1fr; }
  }
  .jb-facts { display:flex; flex-wrap:wrap; gap:.35rem; }
  .jb-fact { display:grid; grid-template-columns:4.4rem 1fr 2.1rem; align-items:center;
    gap:.4rem; width:100%; font-size:.7rem; color:var(--ink-3); }
  .jb-fact-name { text-transform:capitalize; letter-spacing:-.005em; }
  .jb-fact-track { height:4px; border-radius:4px; background:var(--sunk); overflow:hidden; }
  .jb-fact-fill { display:block; height:100%; border-radius:4px; background:var(--green);
    min-width:2px; }
  .jb-fact-fill.none { background:var(--red); }
  .jb-fact-num { text-align:right; font-variant-numeric:tabular-nums; }
  .jb-acts { display:flex; gap:.45rem; align-items:center; flex-wrap:wrap; margin-top:auto;
    padding-top:.9rem; border-top:1px solid var(--hair); }

  /* The disclosure. Small, lowercase, no button chrome: it is a way in, not an
     action, and it sat under Posting looking like a fifth control. */
  .jb-det { display:inline-flex; align-items:center; gap:.35rem; background:none;
    border:0; padding:.15rem 0; margin:.15rem 0 0; cursor:pointer; font:inherit;
    font-size:.78rem; color:var(--ink-3); width:max-content; }
  .jb-det:hover, .jb-card.open .jb-det { color:var(--ink-2); }
  .jb-det:focus-visible { outline:2px solid var(--blue); outline-offset:3px;
    border-radius:4px; }
  .jb-caret { width:0; height:0; border-left:4px solid currentColor;
    border-top:3.5px solid transparent; border-bottom:3.5px solid transparent;
    transition:transform .22s ease; transform-origin:30% 50%; }
  .jb-card.open .jb-caret { transform:rotate(90deg); }

  /* The height is measured and set in script. The tidier 0fr-to-1fr grid trick
     needs a grid parent, and the card is a flex column, so `1fr` there resolves
     against no free space and computes to zero — an open panel that stays shut.
     The ceiling is never guessed: it is this panel's own scrollHeight, and it
     is released to `none` once the transition ends so a reflow cannot clip it. */
  .jb-slot { max-height:0; overflow:hidden;
    transition:max-height .26s cubic-bezier(.4,0,.2,1); }

  @media (prefers-reduced-motion:reduce) {
    .jb-slot, .jb-caret { transition:none; }
  }
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
  .jb-out { display:inline-block; margin-left:.18rem; font-size:.86em;
    line-height:1; opacity:.7; }
  .jb-sight a:hover .jb-out { opacity:1; }
  .jb-excerpt { margin:0; line-height:1.45; }
  .jb-decidefail { color:var(--red-ink); }
  .jb-footer { text-align:center; color:var(--ink-3); font-size:.85rem; margin:1.1rem 0 0;
    line-height:1.55; }
  .jb-more { text-align:center; color:var(--ink-3); font-size:.85rem;
    padding:1.2rem 0; }
  .jb-addlink { background:var(--card); border-radius:14px; padding:.85rem 1.1rem;
    box-shadow:var(--sh); margin-top:1.2rem; }
  .jb-addlink h4 { margin:0 0 .15rem; font-size:.92rem; font-weight:620; }
  .jb-addlink p { margin:0 0 .5rem; font-size:.84rem; color:var(--ink-2); }
  .jb-linkmsg { font-size:.82rem; color:var(--ink-2); }
  .jb-linkmsg.err { color:var(--orange); }
  .jb-linkmsg.ok { color:var(--green); }
  @media (max-width:900px) {
    .jb-wide { width:auto; margin-left:0; margin-right:0; }
    .jb-grid { --cols:1; }
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
  var DATA = { j: [], w: [], c: [], y: [], n: {}, t: 0 };
  try { DATA = JSON.parse(holder.textContent || '{}') || DATA; } catch (e) {}
  var JOBS = DATA.j || [];
  var WEIGHTS = DATA.w || [];
  var COMPONENTS = DATA.c || [];
  var WHY = DATA.y || [];
  var LOGOS = DATA.g || [];
  var CENSUS = DATA.n || {};
  var POOL_TOTAL = DATA.t || JOBS.length;
  var grid = document.getElementById('jb-grid');
  if (!grid) return;

  var PACKS = {
    EU: ['AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IE','IT','LV',
         'LT','LU','MT','NL','PL','PT','RO','SK','SI','ES','SE','IS','LI','NO','CH','GB'],
    MIDDLE_EAST: ['AE','SA','QA','KW','BH','OM','JO','EG'],
    SEA: ['SG','MY','PH','ID','TH','VN']
  };
  // The widest chip is 60 days, not null. Null meant "skip the date check",
  // which was only ever safe because the payload is already inside the
  // ceiling, and a chip that says two months while applying no limit lies.
  // 'all' is kept for a search saved before the ceiling existed; it names the
  // same window as '60d' now. Null would mean "skip the date check", and a
  // chip that says two months while applying no limit lies.
  var DAYS = { '6h': 0.25, '24h': 1, '7d': 7, '14d': 14, '30d': 30,
               '60d': 60, all: 60 };
  // The sorts that have a branch below. Kept beside them on purpose: an
  // option with no arm is the bug this list exists to make impossible.
  var SORTS = ['new', 'score'];
  var WATCH_KEY = 'jobby.watching', PASS_KEY = 'jobby.passed';
  // The filters the user last set, in this browser. The board is a published
  // snapshot rendered with whatever the saved search was at publish time, so
  // without this a change survives only until the next reload — and Save search
  // cannot help, because the page you are looking at was rendered minutes ago.
  // This is view state, deliberately separate from the saved search: the saved
  // search decides what gets *fetched*, this decides what you are looking at.
  var VIEW_KEY = 'jobby.foundView';
  var TEXT_CONTROLS = ['jb-ex', 'jb-scope', 'jb-sort', 'jb-cmode', 'jb-companies',
                       'jb-exp', 'jb-deg', 'jb-role', 'jb-emp', 'jb-jt'];
  var CHECK_CONTROLS = ['jb-direct', 'jb-remote'];
  var keywords = [];

  function readIds(k) {
    try { return JSON.parse(localStorage.getItem(k) || '[]'); } catch (e) { return []; }
  }
  function writeIds(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function remember(k, id) {
    var ids = readIds(k);
    if (ids.indexOf(id) === -1) { ids.push(id); writeIds(k, ids); }
  }
  // Undo a remembered click. Without this a decision that never reached the
  // queue would be replayed on every load for ever, which is the bug the
  // ten-minute expiry on the Apply memory exists to bound.
  function forget(k, id) {
    var ids = readIds(k), at = ids.indexOf(id);
    if (at !== -1) { ids.splice(at, 1); writeIds(k, ids); }
  }
  function viewState() {
    var state = { keywords: keywords.slice(), values: {}, checks: {},
                  workplaces: [], unknowns: [], locations: [],
                  locationMode: 'all', date: null };
    TEXT_CONTROLS.forEach(function (id) {
      var n = document.getElementById(id);
      if (n) state.values[id] = n.value;
    });
    CHECK_CONTROLS.forEach(function (id) {
      var n = document.getElementById(id);
      if (n) state.checks[id] = !!n.checked;
    });
    var d = document.querySelector('input[name="jb-date"]:checked');
    state.date = d ? d.value : null;
    Array.prototype.forEach.call(document.querySelectorAll('[data-wp]'), function (n) {
      if (n.checked) state.workplaces.push(n.getAttribute('data-wp'));
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-unknown]'), function (n) {
      if (n.checked) state.unknowns.push(n.getAttribute('data-unknown'));
    });
    state.locations = pickedLocations();
    state.locationMode = allLocationsSelected()
      ? 'all' : (state.locations.length ? 'some' : 'none');
    return state;
  }

  function rememberView() {
    try { localStorage.setItem(VIEW_KEY, JSON.stringify(viewState())); } catch (e) {}
  }
  function forgetView() {
    try { localStorage.removeItem(VIEW_KEY); } catch (e) {}
  }

  function restoreView() {
    var raw;
    try { raw = localStorage.getItem(VIEW_KEY); } catch (e) { return false; }
    if (!raw) return false;
    var state;
    try { state = JSON.parse(raw); } catch (e) { forgetView(); return false; }
    if (!state || typeof state !== 'object') { forgetView(); return false; }

    keywords = Array.isArray(state.keywords) ? state.keywords.slice() : [];
    TEXT_CONTROLS.forEach(function (id) {
      var n = document.getElementById(id);
      // A value the page no longer offers is skipped rather than forced: a
      // window or a sort that has been retired must not blank the control.
      if (n && state.values && typeof state.values[id] === 'string') {
        if (n.tagName !== 'SELECT' || hasOption(n, state.values[id])) {
          n.value = state.values[id];
        }
      }
    });
    CHECK_CONTROLS.forEach(function (id) {
      var n = document.getElementById(id);
      if (n && state.checks && typeof state.checks[id] === 'boolean') {
        n.checked = state.checks[id];
      }
    });
    if (state.date) {
      var d = document.querySelector('input[name="jb-date"][value="' + state.date + '"]');
      if (d) d.checked = true;
    }
    if (Array.isArray(state.workplaces)) {
      Array.prototype.forEach.call(document.querySelectorAll('[data-wp]'), function (n) {
        n.checked = state.workplaces.indexOf(n.getAttribute('data-wp')) !== -1;
      });
    }
    if (Array.isArray(state.unknowns)) {
      Array.prototype.forEach.call(document.querySelectorAll('[data-unknown]'), function (n) {
        n.checked = state.unknowns.indexOf(n.getAttribute('data-unknown')) !== -1;
      });
    }
    if (Array.isArray(state.locations)) {
      var restored = expandedLocationValues(state.locations);
      var legacyAll = state.locationMode !== 'none' && restored.length === 0;
      Array.prototype.forEach.call(document.querySelectorAll('[data-loc]'), function (n) {
        n.checked = legacyAll || restored.indexOf(n.getAttribute('data-loc')) !== -1;
        n.setAttribute('aria-pressed', n.checked ? 'true' : 'false');
      });
      updateLocationUI();
    }
    return true;
  }

  function hasOption(select, want) {
    for (var i = 0; i < select.options.length; i++) {
      if (select.options[i].value === want) return true;
    }
    return false;
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

    // Today's date in Eastern, as YYYY-MM-DD. en-CA is the locale that formats
  // that way without any string surgery.
  function easternToday() {
    try {
      return new Intl.DateTimeFormat('en-CA', {
        timeZone: 'America/New_York',
        year: 'numeric', month: '2-digit', day: '2-digit'
      }).format(new Date());
    } catch (e) {
      return new Date().toISOString().slice(0, 10);
    }
  }

  function ago(job) {
    var when = job.pa;
    if (!when) return null;
    // Calendar days between two dates, both read as UTC midnight, so the
    // subtraction cannot be bent by a daylight-saving change. Comparing against
    // Date.now() meant that after 20:00 Eastern — when the UTC date has already
    // rolled over — a job posted this morning said "yesterday".
    var days = Math.round((Date.parse(easternToday() + 'T00:00:00Z')
                           - Date.parse(when + 'T00:00:00Z')) / 86400000);
    if (isNaN(days)) return null;
    var label = days <= 0 ? 'today' : days === 1 ? 'yesterday' : days + ' days ago';
    return 'posted ' + label;
  }
  function ringColour(score) {
    if (score >= 75) return 'var(--green)';
    if (score >= 50) return 'var(--blue)';
    return 'var(--ink-3)';
  }

  var sectionCache = {};
  var SECTION_POINTS = 5;
  function renderSections(holder, data) {
    holder.textContent = '';
    var rows = [['Key responsibilities',
                 (data.responsibilities || []).slice(0, SECTION_POINTS)],
                ['Requirements',
                 (data.requirements || []).slice(0, SECTION_POINTS)]];
    var any = false;
    rows.forEach(function (pair) {
      if (!pair[1].length) return;
      any = true;
      var row = el('div', 'jb-section');
      row.appendChild(el('h4', null, pair[0]));
      var list = el('ul');
      pair[1].forEach(function (line) { list.appendChild(el('li', null, line)); });
      row.appendChild(list);
      holder.appendChild(row);
    });
    if (!any) {
      holder.appendChild(el('p', 'jb-loading',
        'This posting does not use headings this can read.'));
    }
  }
  function loadSections(job, holder) {
    if (sectionCache[job.id]) { renderSections(holder, sectionCache[job.id]); return; }
    if (typeof fetch !== 'function') { holder.textContent = ''; return; }
    fetch('/api/posting?id=' + encodeURIComponent(job.id), { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.ok) { holder.textContent = ''; return; }
        sectionCache[job.id] = data;
        renderSections(holder, data);
      })
      // A saved copy of the board has no route to ask, and that is not an
      // error worth showing; the rest of the panel still stands.
      .catch(function () { holder.textContent = ''; });
  }

  function detailPanel(job) {
    var panel = el('div', 'jb-panel');
    // No excerpt here: the card face already carries it a line above, and
    // repeating it meant every expansion opened on something already read.

    // What the posting itself says, in its own two halves. These are fetched
    // rather than embedded: sections average about 265 bytes a record and the
    // page is capped at 4MB, so carrying them for every job costs more than the
    // whole budget. Only an opened card pays.
    var sections = el('div', 'jb-sections');
    sections.appendChild(el('p', 'jb-loading', 'Reading the posting...'));
    panel.appendChild(sections);
    loadSections(job, sections);

    (job.ga || []).forEach(function (label) {
      panel.appendChild(el('p', 'jb-excerpt', 'Capped at 25: ' + label));
    });
    if ((job.un || []).length) {
      panel.appendChild(el('p', 'jb-excerpt',
        'Scored neutral because the posting does not say: ' + job.un.join(', ') + '.'));
    }
    if ((job.sg || []).length) {
      var links = el('div', 'jb-sight');
      links.appendChild(el('span', 'jb-chip', 'see more on'));
      job.sg.forEach(function (s) {
        var a = document.createElement('a');
        a.className = 'go';
        a.href = s.u; a.target = '_blank'; a.rel = 'noopener';
        a.textContent = s.s || 'link';
        // These open somebody else's site in a new tab. Say so.
        a.appendChild(el('span', 'jb-out', '\u2197'));
        a.title = 'Open on ' + (s.s || 'its board');
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
  var WP_LABELS = {remote: 'Remote', hybrid: 'Hybrid', onsite: 'On-site'};

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

  // The mechanical half of company_logos.py. Which derivation to run is decided
  // there and arrives as job.lp; this only performs it, over co and url, which
  // the payload already carries. tests/test_found_script.py checks the two
  // agree on real records, because they are one contract in two languages.
  var LEGAL_SUFFIXES = {
    inc:1, llc:1, ltd:1, limited:1, corp:1, corporation:1, co:1, company:1,
    gmbh:1, bv:1, nv:1, ag:1, sa:1, plc:1, pvt:1, private:1, pty:1, srl:1,
    spa:1, oy:1, ab:1, as:1, aps:1, holdings:1, group:1
  };
  function compactText(value) {
    return String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  }
  function nameCore(company) {
    var words = String(company || '').toLowerCase().match(/[a-z0-9]+/g) || [];
    while (words.length && LEGAL_SUFFIXES[words[words.length - 1]]) words.pop();
    return words.join('');
  }
  function urlParts(raw) {
    try {
      var u = new URL(raw, 'https://x.invalid');
      return { host: (u.hostname || '').toLowerCase(),
               segments: u.pathname.split('/').filter(Boolean) };
    } catch (e) { return { host: '', segments: [] }; }
  }
  function logoUrl(job) {
    var plan = job.lp || 0;
    if (!plan) {
      return (LOGOS.length && job.lg >= 0) ? (LOGOS[job.lg] || '') : '';
    }
    var parts = urlParts(job.url || '');
    var base = '';
    var exact = false;
    if (plan === 1) { base = parts.host; exact = true; }
    else if (plan === 2) { base = compactText(parts.segments[0] || ''); }
    else if (plan === 3) { base = compactText((parts.host.split('.')[0] || '')); }
    else if (plan === 4) { base = nameCore(job.co || ''); }
    if (!base) return '';
    var domain = exact ? base : base + '.com';
    return '/api/company-logo?domain=' + encodeURIComponent(domain) +
           (exact ? '' : '&alt=1');
  }

  function companyMark(job) {
    var mark = el('span', 'company-mark');
    mark.setAttribute('aria-hidden', 'true');
    var words = String(job.co || '').match(/[A-Za-z0-9]+/g) || [];
    var initial = words.length ? words[0].charAt(0) + (words[1] ? words[1].charAt(0) : '') : '?';
    mark.appendChild(el('span', null, initial.toUpperCase()));
    var logoSrc = logoUrl(job);
    if (logoSrc) {
      var img = document.createElement('img');
      img.alt = '';
      img.loading = 'lazy';
      img.setAttribute('data-company-logo', '');
      img.src = logoSrc;
      if (typeof window !== 'undefined' && window.jobbyPrepareCompanyLogo) {
        window.jobbyPrepareCompanyLogo(img);
      }
      mark.appendChild(img);
    }
    return mark;
  }

  function card(job) {
    var article = el('article', 'jb-card');
    article.setAttribute('data-id', job.id);

    var head = el('div', 'jb-head');
    var identity = el('div', 'jb-identity');
    identity.appendChild(companyMark(job));
    var titles = el('div');
    titles.appendChild(el('p', 'jb-co', job.co));
    titles.appendChild(el('h3', 'jb-ti', job.ti));
    identity.appendChild(titles);
    head.appendChild(identity);
    var ring = el('div', 'jb-ring');
    var pct = typeof job.sc === 'number' ? job.sc : 0;
    ring.style.background = 'conic-gradient(' + ringColour(pct) + ' ' + pct + '%, var(--sunk) 0)';
    ring.appendChild(el('i', null, pct + '%'));
    head.appendChild(ring);
    article.appendChild(head);

    if (job.sum) article.appendChild(el('p', 'jb-sum', job.sum));

    // Chips carry what the posting actually said, and say plainly when it said
    // nothing. A row of "unknown" is honest; a row of invented detail is not.
    var chips = el('div', 'jb-chips');
    if (job.place) chips.appendChild(chip(job.place, 'place'));
    if (job.wp) chips.appendChild(chip(WP_LABELS[job.wp] || job.wp));
    if (job.yrt) chips.appendChild(chip(job.yrt, 'years'));
    else chips.appendChild(chip('Experience not stated', 'unknown'));
    var when = ago(job);
    if (when) chips.appendChild(chip(when));
    else chips.appendChild(chip('Posted date unknown', 'unknown'));
    if (job.sal) chips.appendChild(chip(job.sal, 'pay'));
    if (job.bd > 1) chips.appendChild(chip('On ' + job.bd + ' boards', 'boards'));
    chips.appendChild(chip(job.src || 'Unknown source',
                           job.rank <= 1 ? 'direct' : ''));
    (job.ga || []).forEach(function (label) { chips.appendChild(chip(label, 'gate')); });
    article.appendChild(chips);

    var facts = el('div', 'jb-facts');
    var parts = job.pt || [];
    COMPONENTS.forEach(function (name, i) {
      var earned = parts[i] || 0;
      var possible = WEIGHTS[i] || 0;
      if (!possible) return;
      var row = el('div', 'jb-fact');
      row.appendChild(el('span', 'jb-fact-name', name));
      var track = el('span', 'jb-fact-track');
      var fill = el('span', 'jb-fact-fill');
      fill.style.width = Math.round(100 * earned / possible) + '%';
      if (earned === 0) fill.classList.add('none');
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el('span', 'jb-fact-num', earned + '/' + possible));
      var why = job.wy && WHY[job.wy[i]] ? WHY[job.wy[i]] : '';
      row.title = name + ': ' + earned + ' of ' + possible + (why ? ' - ' + why : '');
      facts.appendChild(row);
    });
    (job.tg || []).slice(0, 4).forEach(function (tag) { facts.appendChild(chip(tag)); });
    article.appendChild(facts);

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

    article.appendChild(acts);

    // Details is a disclosure, not a button. It opens on hover and stays open
    // on click, so a glance costs nothing and a read does not have to be
    // undone. The panel is built once, on first open, because building three
    // thousand of them up front is the whole reason this page is windowed.
    var toggle = el('button', 'jb-det');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', 'false');
    toggle.appendChild(el('span', 'jb-caret'));
    toggle.appendChild(el('span', null, 'details'));
    toggle.title = 'Why this percentage, and where else it was seen';

    var slot = el('div', 'jb-slot');

    var pinned = false, built = false;
    function show(on) {
      if (on && !built) { slot.appendChild(detailPanel(job)); built = true; }
      // Closing from the released state needs a real number to animate from,
      // so put one back and force layout before flipping to zero.
      if (!on && slot.style.maxHeight === 'none') {
        slot.style.maxHeight = slot.scrollHeight + 'px';
        void slot.offsetHeight;
      }
      article.classList.toggle('open', on);
      toggle.setAttribute('aria-expanded', on ? 'true' : 'false');
      slot.style.maxHeight = on ? slot.scrollHeight + 'px' : '0px';
    }
    // Once open, stop capping it. A card that reflows taller than the height we
    // measured would otherwise have its last line cut off.
    slot.addEventListener('transitionend', function (e) {
      if (e.propertyName !== 'max-height') return;
      if (article.classList.contains('open')) slot.style.maxHeight = 'none';
    });
    toggle.addEventListener('click', function () { pinned = !pinned; show(pinned); });
    // Hover is a preview; it must never close what a click opened.
    article.addEventListener('mouseenter', function () { if (!pinned) show(true); });
    article.addEventListener('mouseleave', function () { if (!pinned) show(false); });

    article.appendChild(toggle);
    article.appendChild(slot);
    return article;
  }

  // The board is a published snapshot, so a decision does not show up anywhere
  // until the next republish — and publishing is throttled behind a deploy gap.
  // Without moving the counts here, pressing Apply looks exactly like pressing
  // nothing: the card goes, and no lane admits to having gained anything.
  function bump(tab, by) {
    // Dropped is not a tab any more: it is a dock button with its own count,
    // so looking only for .tab silently did nothing when someone pressed Pass.
    var node = document.querySelector('.tab[data-tab="' + tab + '"] .n')
      || document.querySelector('.' + tab + '-count');
    if (!node) return;
    var n = parseInt(node.textContent, 10);
    if (!isNaN(n)) node.textContent = Math.max(0, n + by);
  }

  // A request that did not land must say so. These used to swallow every
  // failure, which meant a dead network and a successful queue looked
  // identical from the sofa.
  function decide(path, job, article, onFail) {
    return post(path, { applicationId: job.id }).then(function (res) {
      if (res && res.ok === false) throw new Error(res.error || 'refused');
      return res;
    }).catch(function (err) {
      hidden[job.id] = false;
      forget(WATCH_KEY, job.id);
      forget(PASS_KEY, job.id);
      bump('found', 1);
      if (onFail) onFail(err);
      note(article, 'That did not reach the queue. It is still here; try again.');
      throw err;
    });
  }

  function note(article, message) {
    var box = document.getElementById('jb-decision-note');
    if (!box) {
      box = el('p', 'jb-footer jb-decidefail');
      box.id = 'jb-decision-note';
      var grid = document.getElementById('jb-grid');
      if (grid && grid.parentNode) grid.parentNode.insertBefore(box, grid);
    }
    box.textContent = message;
  }

  function leave(article, message) {
    article.remove();
    bump('found', -1);
    render();
  }
  function doWatch(job, article, btn) {
    btn.classList.add('on');
    remember(WATCH_KEY, job.id);
    hidden[job.id] = true;
    bump('watch', 1);
    decide('/api/watch', job, article, function () { bump('watch', -1); });
    leave(article);
  }
  function doPass(job, article) {
    remember(PASS_KEY, job.id);
    hidden[job.id] = true;
    bump('dropped', 1);
    decide('/api/pass', job, article, function () { bump('dropped', -1); });
    leave(article);
  }
  function doApply(job, article, btn) {
    btn.disabled = true;
    var label = btn.querySelector('span');
    if (label) label.textContent = 'Queueing\u2026';
    remember(WATCH_KEY, job.id);
    hidden[job.id] = true;
    // In flight, not Watching: an Apply goes straight past the watch list.
    bump('flight', 1);
    post('/api/watch', { applicationId: job.id })
      .then(function () { return decide('/api/apply', job, article,
                                        function () { bump('flight', -1); }); })
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
  function locationNodes() {
    return Array.prototype.slice.call(document.querySelectorAll('[data-loc]'));
  }
  function pickedLocations() {
    return locationNodes().filter(function (n) {
      // aria-pressed keeps old published boards and the small unit-test DOM
      // compatible during the rollout. New boards use native checkboxes.
      return n.checked || n.getAttribute('aria-pressed') === 'true';
    }).map(function (n) { return n.getAttribute('data-loc'); });
  }
  function expandedLocationValues(values) {
    var out = [];
    (values || []).forEach(function (entry) {
      if (entry.indexOf('pack:') === 0) out = out.concat(PACKS[entry.slice(5)] || []);
      else if (entry) out.push(entry);
    });
    return out;
  }
  function selectedCountries() {
    return expandedLocationValues(pickedLocations());
  }
  function allLocationsSelected() {
    var nodes = locationNodes();
    var picker = document.getElementById('jb-loc');
    var declared = picker && parseInt(picker.getAttribute('data-location-total'), 10);
    var total = declared > 0 ? declared : nodes.length;
    return total === 0 || pickedLocations().length === total;
  }
  function setAllLocations(on) {
    locationNodes().forEach(function (n) {
      n.checked = !!on;
      n.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }
  function updateLocationUI() {
    var nodes = locationNodes();
    var selected = pickedLocations();
    var total = nodes.length;
    var world = document.getElementById('jb-world');
    if (world) {
      world.checked = total > 0 && selected.length === total;
      world.indeterminate = selected.length > 0 && selected.length < total;
    }
    Array.prototype.forEach.call(document.querySelectorAll('[data-continent]'), function (box) {
      var key = box.getAttribute('data-continent');
      var group = Array.prototype.slice.call(
        document.querySelectorAll('[data-continent-country="' + key + '"]')
      );
      var count = group.filter(function (n) { return n.checked; }).length;
      box.checked = group.length > 0 && count === group.length;
      box.indeterminate = count > 0 && count < group.length;
      var label = document.getElementById('jb-continent-count-' + key);
      if (label) label.textContent = count + ' of ' + group.length;
    });
    var title = document.getElementById('jb-location-title');
    var note = document.getElementById('jb-location-note');
    if (!title || !note) return;
    if (selected.length === total) {
      title.textContent = 'Worldwide';
      note.textContent = 'All ' + total + ' countries';
    } else if (!selected.length) {
      title.textContent = 'No countries selected';
      note.textContent = 'Choose a continent or country';
    } else if (selected.length === 1) {
      var only = nodes.filter(function (n) { return n.checked; })[0];
      title.textContent = only ? only.getAttribute('data-country-name') : '1 country';
      note.textContent = '1 country';
    } else {
      title.textContent = selected.length + ' countries';
      note.textContent = 'Multiple continents';
    }
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
      ? job.ti : job.ti + ' ' + job.co + ' ' + job.sum + ' ' + (job.tg || []).join(' ')
    ).toLowerCase();
  }

  function matches(job) {
    if (hidden[job.id]) return false;
    var hay = haystack(job);
    // Any of them, not all of them. Three job titles in the box means "show me
    // all three kinds of job"; requiring every word meant a second title
    // emptied the board, because no posting is a product manager AND a
    // business analyst.
    if (keywords.length) {
      var hit = false;
      for (var i = 0; i < keywords.length; i++) {
        if (hay.indexOf(keywords[i].toLowerCase()) !== -1) { hit = true; break; }
      }
      if (!hit) return false;
    }
    var exclude = value('jb-ex').trim().toLowerCase();
    if (exclude && hay.indexOf(exclude) !== -1) return false;

    var days = dateWindow();
    if (days !== null && days !== undefined) {
      var when = job.pa;
      if (!when) { if (!unknownAllowed('postedAt')) return false; }
      else {
        var age = (Date.now() - Date.parse(when + 'T00:00:00Z')) / 86400000;
        if (age > days) return false;
      }
    }
    var countries = selectedCountries();
    if (!allLocationsSelected()) {
      if (!countries.length) return false;
      // A job that names a country has to be in a selected one, full stop. The
      // old rule let any remote posting through any country filter, so
      // selecting India showed "Remote - Canada: select locations" — a job that
      // is remote *within Canada* and cannot be done from India.
      if (job.cty) {
        if (countries.indexOf(job.cty) === -1) return false;
      } else if (job.wp !== 'remote' || !checked('jb-remote')) {
        // No country at all. Only genuinely location-free remote work answers
        // a search for somewhere specific, and only when asked for. A "Hybrid"
        // posting whose city nobody could parse is not an India job, and
        // letting it through is how Chicago ended up in an India search.
        return false;
      }
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

  // Cards are drawn a window at a time. At the embedded cap a card for every
  // match is around fifty thousand DOM nodes, which is a second of jank on a
  // phone for rows nobody has scrolled to yet.
  var RENDER_WINDOW = 250;
  var visible = RENDER_WINDOW;
  var lastRows = [];

  // How many columns the stylesheet is asking for at this width. Keeping the
  // breakpoints in CSS means one place decides, and the script just reads it.
  function columnCount() {
    if (typeof getComputedStyle !== 'function') return 1;
    var raw = parseInt(getComputedStyle(grid).getPropertyValue('--cols'), 10);
    return raw > 0 ? raw : 1;
  }

  var columns = [];
  function makeColumns(n) {
    grid.textContent = '';
    columns = [];
    for (var i = 0; i < n; i++) {
      var col = el('div', 'jb-col');
      grid.appendChild(col);
      columns.push(col);
    }
  }

  function draw(rows, from, to) {
    for (var i = from; i < to && i < rows.length; i++) {
      // Round robin, so reading across the top of the board is still reading
      // down the sorted list.
      columns[i % columns.length].appendChild(card(rows[i]));
    }
  }

  function more() {
    var sentinel = document.getElementById('jb-more');
    if (sentinel) sentinel.remove();
    var from = visible;
    visible += RENDER_WINDOW;
    draw(lastRows, from, visible);
    addSentinel();
  }

  function addSentinel() {
    if (visible >= lastRows.length) return;
    var sentinel = el('div', 'jb-more');
    sentinel.id = 'jb-more';
    sentinel.textContent = 'Loading the next ' +
      Math.min(RENDER_WINDOW, lastRows.length - visible) + '\u2026';
    // Below the columns, not inside one: it spans the board.
    (document.getElementById('jb-tail') || grid).appendChild(sentinel);
    if (typeof IntersectionObserver === 'undefined') { more(); return; }
    var observer = new IntersectionObserver(function (entries) {
      if (entries[0] && entries[0].isIntersecting) { observer.disconnect(); more(); }
    }, { rootMargin: '600px' });
    observer.observe(sentinel);
  }

  // What the page can honestly say about how much of the pool it holds.
  //
  // Filtering happens here, over the embedded records only. Saying "800 of
  // 12,003 — refine the filters" implied that refining searches the rest, when
  // it re-sorts the same set. The client cannot total the pool for an arbitrary
  // combination of filters, but it can for the location dimension, which is
  // where the gap actually bit: someone filtering for Japan saw 46 results with
  // no way to know 714 more were on disk.
  function poolForSelection() {
    var picked = selectedCountries();
    if (allLocationsSelected()) return POOL_TOTAL;
    if (!picked.length) return 0;
    var total = 0, counted = {};
    picked.forEach(function (code) {
      if (counted[code]) return;
      counted[code] = true;
      total += CENSUS[code] || 0;
    });
    if (checked('jb-remote')) total += CENSUS.__remote || 0;
    return total;
  }

  function countLine(shown) {
    var pool = poolForSelection();
    var scoped = !allLocationsSelected();
    if (!scoped && shown === JOBS.length && JOBS.length >= POOL_TOTAL) {
      return shown + ' shown';
    }
    var line = shown + ' shown';
    if (shown !== JOBS.length) line = shown + ' of ' + JOBS.length + ' on this page';
    if (pool > JOBS.length) {
      line += ' \u00b7 ' + pool.toLocaleString() +
        (scoped ? ' in the pool for these locations' : ' in the pool');
    }
    return line;
  }

  function render() {
    var rows = JOBS.filter(matches);
    // Falls back to what the page ships with, not to a third answer. These
    // disagreed once already, and a default that only appears when a
    // control is missing is the hardest kind to notice.
    // Normalised to something with a branch. "Match %" once had no arm here
    // and fell straight through, which left the rows in payload order and made
    // the control look broken because it did nothing. An unknown value now
    // lands on the default instead of on no sort at all.
    var how = value('jb-sort');
    if (SORTS.indexOf(how) === -1) how = SORTS[0];
    if (how === 'new') {
      rows = rows.slice().sort(function (a, b) {
        return String(b.pa || '').localeCompare(String(a.pa || ''));
      });
    } else if (how === 'score') {
      // This branch did not exist. Choosing "Match %" fell through every arm
      // and left the rows in payload order, so the control looked broken
      // because it was: it did nothing at all.
      rows = rows.slice().sort(function (a, b) { return (b.sc || 0) - (a.sc || 0); });
    }
    rememberView();
    makeColumns(columnCount());
    lastRows = rows;
    visible = Math.min(RENDER_WINDOW, rows.length);
    draw(rows, 0, visible);
    addSentinel();
    var count = document.getElementById('jb-count');
    if (count) count.textContent = countLine(rows.length);
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
  locationNodes().forEach(function (n) {
    n.addEventListener('change', function () {
      n.setAttribute('aria-pressed', n.checked ? 'true' : 'false');
      updateLocationUI();
      render();
    });
  });
  Array.prototype.forEach.call(document.querySelectorAll('[data-continent]'), function (box) {
    box.addEventListener('click', function (event) { event.stopPropagation(); });
    box.addEventListener('change', function () {
      var key = box.getAttribute('data-continent');
      Array.prototype.forEach.call(
        document.querySelectorAll('[data-continent-country="' + key + '"]'),
        function (n) {
          n.checked = box.checked;
          n.setAttribute('aria-pressed', box.checked ? 'true' : 'false');
        }
      );
      updateLocationUI();
      render();
    });
  });
  Array.prototype.forEach.call(document.querySelectorAll('[data-location-only]'), function (button) {
    button.addEventListener('click', function () {
      var wanted = button.getAttribute('data-location-only');
      locationNodes().forEach(function (n) {
        n.checked = n.getAttribute('data-loc') === wanted;
        n.setAttribute('aria-pressed', n.checked ? 'true' : 'false');
      });
      updateLocationUI();
      render();
    });
  });
  var world = document.getElementById('jb-world');
  if (world) {
    world.addEventListener('change', function () {
      setAllLocations(world.checked);
      updateLocationUI();
      render();
    });
  }
  var locationClear = document.getElementById('jb-location-clear');
  if (locationClear) {
    locationClear.addEventListener('click', function () {
      setAllLocations(false);
      updateLocationUI();
      render();
    });
  }
  var locationPicker = document.getElementById('jb-loc');
  if (locationPicker && document.addEventListener) {
    document.addEventListener('click', function (event) {
      if (locationPicker.open && !locationPicker.contains(event.target)) {
        locationPicker.open = false;
      }
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && locationPicker.open) {
        locationPicker.open = false;
        var summary = locationPicker.querySelector('summary');
        if (summary) summary.focus();
      }
    });
  }

  ['jb-ex','jb-scope','jb-sort','jb-remote','jb-cmode','jb-companies',
   'jb-exp','jb-deg','jb-role','jb-emp','jb-jt','jb-direct'].forEach(function (id) {
    var node = document.getElementById(id);
    if (node) { node.addEventListener('input', render); node.addEventListener('change', render); }
  });
  document.querySelectorAll('[data-wp],[data-unknown],input[name="jb-date"]').forEach(
    function (n) { n.addEventListener('change', render); });

  // Rotating a phone crosses a breakpoint. Re-render only when the column count
  // actually changed, because a re-render throws away every open panel.
  if (typeof window !== 'undefined' && window.addEventListener) {
    var lastCols = 0, resizeTimer = null;
    window.addEventListener('resize', function () {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        var n = columnCount();
        if (n !== lastCols) { lastCols = n; render(); }
      }, 150);
    });
  }

  var clear = document.getElementById('jb-clear');
  if (clear) {
    clear.addEventListener('click', function () {
      forgetView();
      keywords = [];
      ['jb-kw','jb-ex','jb-companies'].forEach(function (id) {
        var n = document.getElementById(id); if (n) n.value = '';
      });
      setAllLocations(true);
      updateLocationUI();
      ['jb-exp','jb-deg','jb-role','jb-emp','jb-jt'].forEach(function (id) {
        var n = document.getElementById(id); if (n) n.value = '';
      });
      document.querySelectorAll('[data-wp],[data-unknown]').forEach(function (n) {
        n.checked = true;
      });
      var direct = document.getElementById('jb-direct');
      if (direct) direct.checked = true;
      // Back to what the page shipped with, which is the saved search's window.
      // Clearing to a hardcoded week silently narrowed a two-month search.
      var shipped = document.querySelector('input[name="jb-date"][data-shipped]');
      if (shipped) shipped.checked = true;
      var sort = document.getElementById('jb-sort');
      if (sort) sort.value = 'new';
      drawPills(); render();
    });
  }

  var save = document.getElementById('jb-save');
  if (save) {
    save.addEventListener('click', function () {
      var was = save.textContent;
      var node = document.querySelector('input[name="jb-date"]:checked');
      var selectedLocations = pickedLocations();
      if (!selectedLocations.length) {
        save.textContent = 'Choose a location';
        setTimeout(function () { save.textContent = was; }, 2500);
        return;
      }
      var locations = [];
      if (!allLocationsSelected()) {
        selectedLocations.forEach(function (value) {
          if (value) locations.push({ country: value });
        });
      }
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
        sort: value('jb-sort') || 'new',
        directOnly: checked('jb-direct'),
        includeUnknowns: {
          experience: unknownAllowed('experience'), degree: unknownAllowed('degree'),
          workplace: unknownAllowed('workplace'), postedAt: unknownAllowed('postedAt')
        }
      };
      save.disabled = true;
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

  // Before the first draw, so the board opens on what the user last had rather
  // than flashing the published defaults and then correcting itself.
  restoreView();
  updateLocationUI();
  drawPills();
  render();
})();
"""
