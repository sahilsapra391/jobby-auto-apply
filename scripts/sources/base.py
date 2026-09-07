#!/usr/bin/env python3
"""What every connector shares: polite fetching, and loud failure.

The rule that shapes this file is that **a connector must never fail quietly**.
An empty list is a legitimate answer — a board with nothing open returns one
every hour — so "returned nothing" and "could not be asked" have to be
different values, reported separately, and visible on the board. That is the
same failure the watcher had in §13 item 1: a crashed check and an empty queue
both looked like an empty queue, and it ran for hours.

So every connector returns the same envelope. `status` is one of:

  ok        the channel was asked and answered
  skipped   the channel was not asked, and why (almost always: no API key)
  error     the channel was asked and could not answer

and `postings` is only meaningful when `status` is `ok`.

Courtesy, because these are other people's servers and nobody asked them:
at least a second between requests to the same host, an honest User-Agent
naming the project, `If-None-Match` / `If-Modified-Since` where the host offers
a validator, exponential backoff on 429 and 5xx, and a hard byte cap so a
misbehaving endpoint cannot exhaust memory.

Verified behaviour, 2026-08-19, recorded because it is not what the plan
assumed: Lever returns an ETag and honours `If-None-Match` with a 304.
Greenhouse, Ashby and The Muse returned no validator at all from this edge,
having returned one from another edge minutes earlier — so conditional headers
are sent only when a validator was actually stored, and their absence is
normal rather than an error. A dead board token is a 404 on all three
platforms, which is a wrong token rather than an outage and is reported as
such.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "schema"))
import board_key  # noqa: E402
import extract  # noqa: E402
import posting_sections  # noqa: E402
import company_logos  # noqa: E402
import identity  # noqa: E402
import jobs_store  # noqa: E402
import toolpath  # noqa: E402

USER_AGENT = "Jobby/1.0 (+https://github.com/sahilsapra391/jobby-auto-apply)"
# One request per host per second. These are other people's servers.
MIN_INTERVAL_PER_HOST = 1.0
TIMEOUT_SECONDS = 20
# Two retries after the first attempt. A third does not fix a rate limit, it
# deepens one.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2.0
# A misbehaving endpoint must not be able to exhaust memory, but the cap has to
# clear the boards that actually exist. OpenAI's Ashby board is 12.1 MB — 737
# jobs at a mean description of 8,600 characters — and the old 12 MB cap cut it
# mid-document, so it arrived as unparseable JSON reported as "HTTP 200". One
# real company was silently absent from every sweep. 48 MB clears it four times
# over and is still a bounded read.
MAX_RESPONSE_BYTES = 48_000_000
# How much of a description is worth storing. The rest is benefits boilerplate.
EXCERPT_CHARS = 1500

# The most country-or-locale queries one keyed channel may make in one run.
#
# This exists because of a measured failure, not a hunch. The plan's nine
# regions expand to about fifty countries, and a keyed connector querying every
# country against every search term is fifty times four requests — per channel,
# per hour. Adzuna, Jooble and Careerjet together came to roughly four hundred
# requests and blew a ten-minute timeout on the first supervised dry run.
#
# That is not what "Tier 1 costs ~nothing" meant, and at twenty-four runs a day
# it would exhaust the free quotas these channels are on within hours. So each
# run asks a slice and the slice rotates, which covers everything across a day
# rather than everything every hour. The slice actually used is reported, because
# a channel that asked six of fifty countries this hour must say so.
MAX_QUERIES_PER_RUN = 8


def query_slice(items, cursor, cap=MAX_QUERIES_PER_RUN):
    """A rotating window over `items`, so a day covers all of them.

    Deterministic in `cursor` (the hour of the run), so two runs in the same
    hour ask the same thing and a retry is not a fresh burst of traffic.
    """
    items = list(items)
    if not items or cap is None or len(items) <= cap:
        return items, 0
    start = (int(cursor) * cap) % len(items)
    doubled = items + items
    return doubled[start:start + cap], start

RETRY_STATUSES = (429, 500, 502, 503, 504)
TRANSIENT_STATUSES = (0,) + RETRY_STATUSES


class Passthrough(urllib.request.HTTPErrorProcessor):
    """Every status is data. 304 is a cache hit, 404 is a wrong token, 429 is
    backoff — none of them are exceptional enough to be an exception."""

    def http_response(self, request, response):
        return response

    https_response = http_response


class Response:
    def __init__(self, status, body=b"", headers=None, error=""):
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.error = error

    @property
    def ok(self):
        return self.status == 200

    @property
    def unchanged(self):
        return self.status == 304

    def json(self):
        return json.loads(self.body.decode("utf-8", "replace"))


class Fetcher:
    """Throttled HTTP with conditional requests, shared across a whole ingest.

    The validator cache lives in the store rather than in memory, because the
    point of it is the *next* hourly run, not this one.
    """

    def __init__(self, root=None, opener=None, sleep=time.sleep, clock=time.monotonic):
        self.root = Path(root).expanduser() if root else None
        self.opener = opener or urllib.request.build_opener(Passthrough)
        self._sleep = sleep
        self._clock = clock
        self._last_call = {}
        self.requests = 0
        self.bytes = 0
        self.validators = self._load_validators()
        self._open_hosts = {}

    def _cache_path(self):
        return None if self.root is None else self.root / "source-cache.json"

    def _load_validators(self):
        path = self._cache_path()
        if path is None or not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            # A damaged cache is a slow run, never a wrong one: every request
            # simply goes out unconditioned.
            return {}

    def save_validators(self):
        path = self._cache_path()
        if path is None:
            return
        # Bounded, or a cache of every job URL ever seen outgrows the pool.
        trimmed = dict(sorted(self.validators.items(),
                              key=lambda kv: kv[1].get("at", ""), reverse=True)[:5000])
        jobs_store.store.write_private(path, trimmed)

    def _throttle(self, host):
        last = self._last_call.get(host)
        if last is not None:
            wait = MIN_INTERVAL_PER_HOST - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_call[host] = self._clock()

    def get(self, url, headers=None, conditional=True):
        """One GET, throttled, retried on the statuses worth retrying."""
        host = urllib.parse.urlparse(url).netloc
        if host in self._open_hosts:
            return Response(0, error=f"circuit open after {self._open_hosts[host]}")
        stored = self.validators.get(url) if conditional else None
        request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        request_headers.update(headers or {})
        if stored:
            if stored.get("etag"):
                request_headers["If-None-Match"] = stored["etag"]
            if stored.get("lastModified"):
                request_headers["If-Modified-Since"] = stored["lastModified"]
        # A header whose value is None raises deep inside http.client with a
        # message that names neither the header nor the URL.
        request_headers = {k: v for k, v in request_headers.items() if v}

        last = Response(0, error="never attempted")
        for attempt in range(MAX_ATTEMPTS):
            self._throttle(host)
            self.requests += 1
            try:
                request = urllib.request.Request(url, headers=request_headers)
                with self.opener.open(request, timeout=TIMEOUT_SECONDS) as raw:
                    body = raw.read(MAX_RESPONSE_BYTES)
                    self.bytes += len(body)
                    last = Response(raw.status, body, dict(raw.headers))
                    if len(body) >= MAX_RESPONSE_BYTES:
                        # Reading exactly the cap almost always means the body
                        # was longer. Saying so beats letting it surface as a
                        # JSON parse error, which names neither the cause nor
                        # the fix.
                        last.error = (f"response hit the {MAX_RESPONSE_BYTES // 1_000_000} MB "
                                      "cap and was truncated")
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last = Response(0, error=f"{type(exc).__name__}: {exc}"[:200])
            if last.status not in RETRY_STATUSES and last.status != 0:
                break
            if attempt < MAX_ATTEMPTS - 1:
                self._sleep(BACKOFF_SECONDS * (2 ** attempt))

        if last.ok:
            etag = last.headers.get("ETag") or last.headers.get("etag")
            modified = last.headers.get("Last-Modified") or last.headers.get("last-modified")
            if etag or modified:
                self.validators[url] = {"etag": etag, "lastModified": modified,
                                        "at": jobs_store.now_stamp()}
        elif last.status in TRANSIENT_STATUSES:
            # One exhausted retry sequence is enough evidence that this host
            # is unhealthy for the current sweep. Stop hammering every tenant
            # sharing it. A new Fetcher on the next sweep closes the circuit.
            self._open_hosts[host] = describe(last, host)
        return last

    def post_json(self, url, payload, headers=None):
        """JSON POST with the same throttle, retry and circuit semantics as GET."""
        host = urllib.parse.urlparse(url).netloc
        if host in self._open_hosts:
            return Response(0, error=f"circuit open after {self._open_hosts[host]}")
        request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json",
                           "Content-Type": "application/json"}
        request_headers.update(headers or {})
        request_headers = {k: v for k, v in request_headers.items() if v}
        body_out = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        last = Response(0, error="never attempted")
        for attempt in range(MAX_ATTEMPTS):
            self._throttle(host)
            self.requests += 1
            try:
                request = urllib.request.Request(url, data=body_out,
                                                 headers=request_headers, method="POST")
                with self.opener.open(request, timeout=TIMEOUT_SECONDS) as raw:
                    body = raw.read(MAX_RESPONSE_BYTES)
                    self.bytes += len(body)
                    last = Response(raw.status, body, dict(raw.headers))
                    if len(body) >= MAX_RESPONSE_BYTES:
                        last.error = (f"response hit the {MAX_RESPONSE_BYTES // 1_000_000} MB "
                                      "cap and was truncated")
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last = Response(0, error=f"{type(exc).__name__}: {exc}"[:200])
            if last.status not in TRANSIENT_STATUSES:
                break
            if attempt < MAX_ATTEMPTS - 1:
                self._sleep(BACKOFF_SECONDS * (2 ** attempt))
        if last.status in TRANSIENT_STATUSES:
            self._open_hosts[host] = describe(last, host)
        return last

    def get_json(self, url, headers=None, conditional=True):
        """(data, response). `data` is None on anything but a parseable 200."""
        response = self.get(url, headers=headers, conditional=conditional)
        if not response.ok:
            return None, response
        try:
            return response.json(), response
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            response.error = f"response was not JSON: {exc}"[:200]
            return None, response


# --- the envelope -----------------------------------------------------------

def rfc822_date(value):
    """An RSS date as a UTC stamp, or None.

    RSS dates are RFC 822, which `fromisoformat` cannot read. The conversion to
    UTC is the part that matters: `parsedate_to_datetime` returns an aware
    datetime in the feed's own offset, so formatting it straight to a `Z`
    stamp writes the local wall clock and calls it UTC. A feed publishing
    "18:00:00 -0400" was being stored as 18:00Z, four hours early, which the
    freshness gate and every "posted N days ago" on the board then read.
    """
    from datetime import timezone
    from email.utils import parsedate_to_datetime
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def ok(source, postings, requests=0, errors=None, note="", complete=False):
    """A channel that answered.

    `complete` says whether what came back is the whole of every board asked
    for. It matters because absence from a complete listing is evidence a
    posting was taken down, and the staleness sweep acts on that. A feed that
    returns "the most recent twenty" is not complete, however direct its
    postings are, and a connector reading one must say so or the sweep will
    expire live jobs that simply fell out of the window.

    **It defaults to False, and the default is the point.** A connector that
    forgets the flag gets the harmless answer: its postings are never evidence
    of anything, so the worst it can do is leave a stale card on the board. The
    other default fails the other way -- a new connector reading a recency feed
    would mass-close on its first sweep, and it would look exactly like the
    feature working. Completeness has to be claimed, not assumed.
    """
    return {"source": source, "status": "ok", "reason": note,
            "postings": list(postings), "requests": requests,
            "listingComplete": bool(complete),
            "errors": list(errors or [])}


def skipped(source, reason, requests=0):
    """The channel was not asked. A missing key is normal, not a failure —
    but it is never silent, or coverage quietly shrinks and nobody knows."""
    # Never complete: a channel that was not asked cannot be evidence that
    # anything is gone. Same reason for `failed` below. The three envelopes
    # carry identical keys on purpose, and False is the only safe value here.
    return {"source": source, "status": "skipped", "reason": reason,
            "postings": [], "requests": requests,
            "listingComplete": False, "errors": []}


def failed(source, reason, requests=0, errors=None):
    return {"source": source, "status": "error", "reason": reason,
            "postings": [], "requests": requests,
            "listingComplete": False, "errors": list(errors or [])}


def describe(response, what):
    """A one-line reason a request did not work, naming what was being asked."""
    if response.status == 404:
        return f"{what}: 404, the board or token does not exist"
    if response.status in (401, 403):
        return f"{what}: {response.status}, the key was refused"
    if response.status == 429:
        return f"{what}: rate limited after {MAX_ATTEMPTS} attempts"
    if response.status == 0:
        return f"{what}: {response.error or 'no response'}"
    if response.error:
        # A 200 whose body will not parse is the confusing one: without this it
        # reported as "HTTP 200", which reads like a success sitting in the
        # error list. Seen live — one Ashby board answers 200 with something
        # that is not its usual JSON.
        return f"{what}: HTTP {response.status} but {response.error}"
    return f"{what}: HTTP {response.status}"


def api_key(name, settings=None):
    """A key from ~/.job-application/config.env, or "" when it is not installed."""
    values = settings if settings is not None else toolpath.settings()
    return str(values.get(name) or "").strip()


# --- normalisation ----------------------------------------------------------

TAG = re.compile(r"<[^>]+>")
WHITESPACE = re.compile(r"[ \t\r\f\v]+")
BLANKS = re.compile(r"\n{3,}")


def strip_html(text):
    """Readable text out of a board's HTML description.

    Not a sanitiser — the board escapes everything it renders — but a
    fingerprint taken over markup measures the ATS's template rather than the
    job, and every Greenhouse posting would look like every other one.

    Unescaping happens BEFORE tags are stripped, and then again after, because
    Greenhouse serves its `content` field HTML-escaped: the payload contains
    `&lt;h2&gt;About the Role&lt;/h2&gt;`. Stripping first and unescaping
    afterwards turned those entities into tags a moment too late to remove
    them, so every Greenhouse description carried its own markup as visible
    text — into the card, into the simhash, and into experience extraction.
    Two passes, because one level of escaping is the common case and two is
    Greenhouse.
    """
    raw = unescape(str(text or ""))
    for _ in range(2):
        raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
        raw = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6])[^>]*>", "\n", raw)
        raw = TAG.sub(" ", raw)
        raw = unescape(raw)
    raw = WHITESPACE.sub(" ", raw)
    return BLANKS.sub("\n\n", raw).strip()


COUNTRY_NAMES = {
    "united states": "US", "united states of america": "US", "usa": "US", "u.s.": "US",
    "u.s.a.": "US", "america": "US",
    "india": "IN", "canada": "CA", "australia": "AU", "new zealand": "NZ",
    "united kingdom": "GB", "uk": "GB", "england": "GB", "scotland": "GB",
    "wales": "GB", "northern ireland": "GB", "great britain": "GB",
    "ireland": "IE", "germany": "DE", "deutschland": "DE", "france": "FR",
    "netherlands": "NL", "the netherlands": "NL", "holland": "NL",
    "spain": "ES", "italy": "IT", "portugal": "PT", "poland": "PL",
    "austria": "AT", "belgium": "BE", "switzerland": "CH", "sweden": "SE",
    "norway": "NO", "denmark": "DK", "finland": "FI", "iceland": "IS",
    "czechia": "CZ", "czech republic": "CZ", "slovakia": "SK", "hungary": "HU",
    "romania": "RO", "bulgaria": "BG", "greece": "GR", "croatia": "HR",
    "slovenia": "SI", "estonia": "EE", "latvia": "LV", "lithuania": "LT",
    "luxembourg": "LU", "malta": "MT", "cyprus": "CY",
    "united arab emirates": "AE", "uae": "AE", "saudi arabia": "SA",
    "qatar": "QA", "kuwait": "KW", "bahrain": "BH", "oman": "OM",
    "jordan": "JO", "egypt": "EG", "israel": "IL", "turkey": "TR",
    "japan": "JP", "south korea": "KR", "korea": "KR", "china": "CN",
    "hong kong": "HK", "taiwan": "TW", "singapore": "SG", "malaysia": "MY",
    "philippines": "PH", "indonesia": "ID", "thailand": "TH", "vietnam": "VN",
    "brazil": "BR", "mexico": "MX", "argentina": "AR", "chile": "CL",
    "colombia": "CO", "south africa": "ZA", "nigeria": "NG", "kenya": "KE",
}

US_STATES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
}
CA_PROVINCES = {"ab", "bc", "mb", "nb", "nl", "ns", "nt", "nu", "on", "pe", "qc", "sk", "yt"}

# Every ISO code the country map yields, for recognising a trailing code.
KNOWN_CODES = set(COUNTRY_NAMES.values()) | US_STATES.union() | set()
KNOWN_CODES = {c.upper() for c in COUNTRY_NAMES.values()}

REMOTE_WORDS = ("remote", "anywhere", "work from home", "distributed", "wfh")

# Cities whose name belongs to exactly one country, so reading a country off
# them is not a guess. The comment above `parse_location` is right that London,
# Ontario is how city inference goes wrong, which is why every name with a
# well-known twin is deliberately absent: London, Cambridge, Birmingham,
# Manchester, Richmond, Windsor, Hamilton, Perth, Newcastle, Waterloo, Victoria,
# Wellington, Springfield, Columbus.
#
# This exists because "Chicago", "San Francisco" and "New York" were reaching
# the board with no country at all, so they answered a search for India.
# State and province names spelled out. "California - Remote" and "Ontario,
# Canada" name a country as surely as a code does. Georgia is deliberately
# absent: it is also a country. Washington is kept — DC and the state are both
# in the United States, so the country is unambiguous either way.
US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland",
    "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
    "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "north carolina", "north dakota", "ohio", "oklahoma",
    "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
}
CA_PROVINCE_NAMES = {
    "alberta", "british columbia", "manitoba", "new brunswick",
    "newfoundland", "nova scotia", "ontario", "prince edward island",
    "quebec", "saskatchewan",
}

UNAMBIGUOUS_CITIES = {
    # United States
    "chicago": "US", "san francisco": "US", "new york": "US",
    "new york city": "US", "nyc": "US", "los angeles": "US", "seattle": "US",
    "boston": "US", "austin": "US", "denver": "US", "atlanta": "US",
    "philadelphia": "US", "houston": "US", "dallas": "US", "phoenix": "US",
    "san diego": "US", "san jose": "US", "minneapolis": "US", "detroit": "US",
    "pittsburgh": "US", "baltimore": "US", "nashville": "US", "charlotte": "US",
    "salt lake city": "US", "st. louis": "US", "brooklyn": "US",
    "mountain view": "US", "palo alto": "US", "menlo park": "US",
    "sunnyvale": "US", "cupertino": "US", "redmond": "US", "bellevue": "US",
    "washington dc": "US", "washington, d.c.": "US",
    # India
    "bengaluru": "IN", "bangalore": "IN", "mumbai": "IN", "hyderabad": "IN",
    "pune": "IN", "chennai": "IN", "kolkata": "IN", "gurgaon": "IN",
    "gurugram": "IN", "noida": "IN", "new delhi": "IN", "ahmedabad": "IN",
    "navi mumbai": "IN", "thane": "IN", "kochi": "IN", "jaipur": "IN",
    "indore": "IN", "coimbatore": "IN",
    # elsewhere, where the name is not shared
    "singapore": "SG", "dubai": "AE", "abu dhabi": "AE", "amsterdam": "NL",
    "rotterdam": "NL", "utrecht": "NL", "eindhoven": "NL", "berlin": "DE",
    "munich": "DE", "muenchen": "DE", "hamburg": "DE", "frankfurt": "DE",
    "cologne": "DE", "stuttgart": "DE", "dusseldorf": "DE",
    "dublin": "IE", "cork": "IE", "galway": "IE",
    "sydney": "AU", "melbourne": "AU", "brisbane": "AU", "canberra": "AU",
    "adelaide": "AU", "toronto": "CA", "vancouver": "CA", "montreal": "CA",
    "ottawa": "CA", "calgary": "CA", "edmonton": "CA", "winnipeg": "CA",
    "edinburgh": "GB", "glasgow": "GB", "leeds": "GB", "bristol": "GB",
    "liverpool": "GB", "sheffield": "GB", "belfast": "GB",
    "paris": "FR", "lyon": "FR", "toulouse": "FR", "madrid": "ES",
    "barcelona": "ES", "lisbon": "PT", "porto": "PT", "warsaw": "PL",
    "krakow": "PL", "stockholm": "SE", "gothenburg": "SE", "copenhagen": "DK",
    "oslo": "NO", "helsinki": "FI", "zurich": "CH", "geneva": "CH",
    "vienna": "AT", "brussels": "BE", "milan": "IT", "rome": "IT",
    "tel aviv": "IL", "tokyo": "JP", "seoul": "KR", "manila": "PH",
    "jakarta": "ID", "bangkok": "TH", "kuala lumpur": "MY", "hong kong": "HK",
    "sao paulo": "BR", "mexico city": "MX", "buenos aires": "AR",
    "cape town": "ZA", "johannesburg": "ZA", "nairobi": "KE", "lagos": "NG",
}
HYBRID_WORDS = ("hybrid", "flexible")
ONSITE_WORDS = ("on-site", "onsite", "in office", "in-office", "on site")


def parse_location(raw, fallback_country=None):
    """A location object from whatever string a board wrote.

    Country only when the string actually says so, directly or through a state
    or province code. Guessing a country from a city name is how a London,
    Ontario role ends up filtered as a UK one.
    """
    text = str(raw or "").strip()
    location = {"raw": text, "city": None, "region": None, "country": fallback_country}
    if not text:
        return location
    lowered = text.lower()
    for name, code in sorted(COUNTRY_NAMES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"(?:^|[^a-z]){re.escape(name)}(?:$|[^a-z])", lowered):
            location["country"] = code
            break
    if not location["country"]:
        # A trailing two-letter code is a country: "Remote (New Jersey, US)",
        # "Bengaluru, IN". Only checked at the end of a segment, so "US" inside
        # a sentence cannot trigger it.
        tail = re.search(r"[,(]\s*([A-Za-z]{2})\s*\)?\s*$", text)
        if tail and tail.group(1).upper() in KNOWN_CODES:
            location["country"] = tail.group(1).upper()
    if not location["country"]:
        # "Remote - US", "US Remote", "Remote, US: Select locations". The code
        # is stated outright; it just is not in a trailing comma position. Only
        # uppercase counts, so the preposition "in" can never read as India.
        for token in re.findall(r"\b([A-Z]{2})\b", text):
            if token in KNOWN_CODES:
                location["country"] = token
                break
    parts = [p.strip(" -\u2013\u2014")
             for p in re.split(r"[,/|();\u2013\u2014]|\s-\s|-(?=[A-Z])", text)
             if p.strip(" -\u2013\u2014")]
    if parts:
        # "Hybrid - New York City" splits to ["Hybrid", "New York City"], and
        # the arrangement is not the city. Take the first segment that is not
        # one of the words describing how the job is worked.
        arrangement = REMOTE_WORDS + HYBRID_WORDS + ONSITE_WORDS
        for part in parts:
            token = part.strip().lower().rstrip(".")
            if any(word in token for word in arrangement):
                continue
            # A country is not a city: "Remote - US" has no city in it.
            if part.strip().upper() in KNOWN_CODES or token in COUNTRY_NAMES:
                continue
            location["city"] = part
            break
    for part in parts[1:]:
        token = part.strip().lower().rstrip(".")
        if len(token) == 2 and token in US_STATES:
            location["region"] = part.strip().upper()
            location["country"] = location["country"] or "US"
        elif len(token) == 2 and token in CA_PROVINCES:
            location["region"] = part.strip().upper()
            location["country"] = location["country"] or "CA"
    if not location["country"]:
        # Last resort, and only for names that belong to one country. A board
        # that writes "Chicago" and nothing else still means the United States,
        # and leaving it countryless meant it answered a search for anywhere.
        for part in parts:
            name = part.strip().lower().rstrip(".")
            if name in UNAMBIGUOUS_CITIES:
                location["country"] = UNAMBIGUOUS_CITIES[name]
                break
    if not location["country"]:
        for part in parts:
            name = part.strip().lower().rstrip(".")
            if name in US_STATE_NAMES:
                location["country"] = "US"
                break
            if name in CA_PROVINCE_NAMES:
                location["country"] = "CA"
                break
    if not location["country"]:
        # "San Francisco Bay Area", "Greater Toronto". The city name is in
        # there as a whole phrase; it just is not the whole segment. Word
        # boundaries on both sides, so "Berlin" cannot match "Berliner".
        lowered_text = " " + re.sub(r"[^a-z ]+", " ", lowered) + " "
        for name, code in sorted(UNAMBIGUOUS_CITIES.items(), key=lambda kv: -len(kv[0])):
            if " " + name + " " in lowered_text:
                location["country"] = code
                break
    return location


def detect_workplace(*texts):
    """remote, hybrid, onsite, or None. None is a real answer."""
    blob = " ".join(str(t or "") for t in texts).lower()
    if not blob.strip():
        return None
    if any(word in blob for word in HYBRID_WORDS):
        return "hybrid"
    if any(word in blob for word in REMOTE_WORDS):
        return "remote"
    if any(word in blob for word in ONSITE_WORDS):
        return "onsite"
    return None


def iso_stamp(value):
    """A store timestamp from an ISO string or an epoch, or None.

    Boards use seconds, milliseconds and half a dozen ISO spellings between
    them. Anything unrecognised becomes None rather than today, because a
    posting dated today because nobody could parse its date sorts to the top of
    a board ordered newest first.
    """
    from datetime import datetime, timezone
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 10_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OSError, OverflowError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def posting(source, source_class, tier, company, title, url, *, location=None,
            workplace=None, posted_at=None, description="", tags=None, salary=None,
            lang=None, seen_at=None, url_stable=True, logo_url=None,
            board_slug=None, external_id=None):
    """One board's version of a posting, in the one shape everything downstream
    expects. Connectors differ; this does not.

    **`board_slug` and `external_id` are the §3 record key, emitted by the
    connector that knows them.** `packages/schema/board_key.py` exists to
    recover that key from a URL for records written before this existed, and it
    says in its own docstring that a connector fetching
    `boards.greenhouse.io/<slug>` knows all three by construction. It was still
    the only source: every posting went in with a URL and the parser guessed.
    Measured over the live pool on 2026-08-29 it recovers a real tenant for
    70.3%, and a large part of the rest is not a parse failure but an
    aggregator that knows its own posting id and was never asked.

    Both or neither. A key with one real half and one invented one is wrong in
    the way that looks right, and `board_key.stored` refuses a partial value
    rather than repairing it, so passing one here quietly buys nothing.
    """
    seen = seen_at or jobs_store.now_stamp()
    clean_url = identity.canonical_url(url)
    body = strip_html(description)
    record = jobs_store.blank_job("pending", str(company or "").strip(),
                                  str(title or "").strip(), firstSeenAt=seen)
    record.update({
        "canonicalUrl": clean_url,
        "canonicalSource": source,
        "logoUrl": logo_url,
        "companyWebsite": company_logos.website_from_description(company, description),
        "sources": [{"source": source, "sourceClass": source_class, "tier": tier,
                     "url": clean_url, "firstSeenAt": seen,
                     # Declared false by a channel that hands out a fresh
                     # tracking URL per request, so its sightings collapse to
                     # one rather than one per hour.
                     **({} if url_stable else {"urlStable": False})}],
        "location": location or {"raw": "", "city": None, "region": None, "country": None},
        "workplace": workplace,
        "postedAt": iso_stamp(posted_at),
        "lastSeenAt": seen,
        "seenCount": 1,
        "lang": lang,
        "descriptionExcerpt": body[:EXCERPT_CHARS],
        "descFingerprint": identity.fingerprint(body),
        # The sections are what the board shows when a card is expanded, and
        # they are also the best input to the year figure: read on its own, a
        # requirements bullet has no responsibilities and no company blurb in it
        # to be confused by.
        "sections": posting_sections.split_sections(body),
        # Extracted here, from the whole body, because the requirements section
        # is almost always below the excerpt cap: only 4 of 195 stored excerpts
        # in the supervised run carried a year figure at all. The full text is
        # in hand exactly once, right now, and only the number is kept.
        "extracted": {**record["extracted"],
                      **extract.extract(body, posting_sections.split_sections(body))},
        "tags": sorted({str(t).strip().lower() for t in (tags or []) if str(t).strip()}),
        "salary": salary,
    })
    # Absent rather than null when the connector does not know it, so a record
    # written before the contract and one written by a connector that cannot
    # emit a key are the same shape, and `board_key.for_record` has one thing
    # to check rather than two.
    if board_slug and external_id:
        record[board_key.RECORD_FIELD] = {
            "slug": str(board_slug).strip().lower(),
            "externalId": str(external_id).strip(),
        }
    return record
