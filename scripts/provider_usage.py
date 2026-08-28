#!/usr/bin/env python3
"""What a provider run spent, in tokens, and what that costs.

Three questions this file exists to settle, none of which anything could answer
before it:

  what does one application cost      the number the hosted-versus-self-hosted
                                      decision turns on, and the number a
                                      pricing page cannot be written without
  where does the spend go             documents or the form agent, input or
                                      output, cached or fresh
  how much of the spend is measured   coverage, because a mean over the runs
                                      that happened to report usage is a
                                      number about reporting, not about cost

Two rules keep it safe to run in the apply path.

**Telemetry never fails an application.** Every entry point returns None or a
zero-coverage record rather than raising. A provider whose output shape changes
tomorrow, or a disk that is full, costs a usage row and nothing else. Callers
are expected to wrap calls here in nothing at all, because there is nothing to
catch.

**An unmeasured run is recorded, not skipped.** A run whose stdout carried no
usage block writes a row with `measured: false`. Dropping it instead would make
coverage invisible and the averages a lie by omission: today the OpenAI apply
path reports usage and the document path does not, so a naive mean would
describe form-filling and be quoted as the cost of an application.

Prices live in references/model-prices.json, dated, because they go stale and a
stale price silently reported as a cost is worse than no cost at all.

  python3 scripts/provider_usage.py summary       # per-application totals
  python3 scripts/provider_usage.py prices        # the table in force, or why not
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PRICES = REPO / "references" / "model-prices.json"

# A price table older than this is refused rather than applied. Provider pricing
# has changed more than once during this project's life, and a cost computed
# from a forgotten table reads exactly like a cost computed from a current one.
PRICE_MAX_AGE_DAYS = 120

# The usage object is nested at different depths by different CLIs and is not
# always at a predictable key path, so it is found by scanning rather than by
# walking. Bounded so a large transcript cannot turn a log read into a stall.
MAX_SCAN_BYTES = 8 * 1024 * 1024


@dataclass
class Usage:
    """One provider invocation's token spend, normalised across CLI shapes.

    `input_tokens` is the total the provider billed as input, INCLUDING the
    cached portion. `cached_input_tokens` is the discounted subset of it, not a
    separate charge. Getting that relationship backwards double-counts the
    largest number in the record, which on the two real OpenAI apply runs
    measured here would overstate input by roughly 77%.
    """

    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    blocks_seen: int = 0
    shape: str = "unknown"

    @property
    def fresh_input_tokens(self):
        """Input billed at full rate: the total less the cached subset."""
        return max(0, self.input_tokens - self.cached_input_tokens)

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens


def _iter_json_objects(text, key):
    """Yield each JSON object that is the value of `key`, by brace matching.

    A regex cannot do this: the objects nest, and the CLIs emit them inside
    event streams where a line-oriented parse would need to know each provider's
    envelope. Brace matching needs to know only that JSON is balanced.
    """
    for match in re.finditer(r'"%s"\s*:\s*\{' % re.escape(key), text):
        start = match.end() - 1
        depth = 0
        for index in range(start, len(text)):
            char = text[index]
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    try:
                        yield json.loads(text[start:index + 1])
                    except ValueError:
                        pass
                    break


def _int(payload, *names):
    for name in names:
        value = payload.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
    return 0


def _non_empty(usage):
    """A usage block with every count at zero carries no information.

    Returned as None rather than a measured zero. A zero-token row would be
    indistinguishable from a real cheap run in every average computed over the
    ledger, and would drag the median of a small sample toward nothing.
    """
    if usage.total_tokens or usage.cache_write_input_tokens:
        return usage
    return None


def parse(stdout):
    """Normalise the last usage block in a provider's stdout, or None.

    The LAST block, not the sum. Every CLI checked reports a session cumulative
    rather than per-turn deltas, so summing would multiply a long run's cost by
    the number of times it reported. `blocks_seen` is carried through so that
    assumption stays falsifiable: a run with many blocks whose totals are small
    relative to the run is the signal that some provider reports deltas after
    all, and nobody has to re-derive why the number looked wrong.
    """
    if not stdout:
        return None
    text = stdout[:MAX_SCAN_BYTES]

    blocks = list(_iter_json_objects(text, "usage"))
    if blocks:
        last = blocks[-1]
        # OpenAI/Codex and Anthropic both use input_tokens/output_tokens and are
        # told apart by which cache fields they carry.
        anthropic = "cache_read_input_tokens" in last or "cache_creation_input_tokens" in last
        return _non_empty(Usage(
            input_tokens=_int(last, "input_tokens", "prompt_tokens"),
            cached_input_tokens=_int(last, "cached_input_tokens", "cache_read_input_tokens"),
            cache_write_input_tokens=_int(
                last, "cache_write_input_tokens", "cache_creation_input_tokens"),
            output_tokens=_int(last, "output_tokens", "completion_tokens"),
            reasoning_output_tokens=_int(last, "reasoning_output_tokens"),
            blocks_seen=len(blocks),
            shape="anthropic" if anthropic else "openai",
        ))

    # Gemini reports under usageMetadata with camelCase counts, and its input
    # total is promptTokenCount with the cached portion broken out beside it.
    meta = list(_iter_json_objects(text, "usageMetadata"))
    if meta:
        last = meta[-1]
        return _non_empty(Usage(
            input_tokens=_int(last, "promptTokenCount"),
            cached_input_tokens=_int(last, "cachedContentTokenCount"),
            output_tokens=_int(last, "candidatesTokenCount"),
            reasoning_output_tokens=_int(last, "thoughtsTokenCount"),
            blocks_seen=len(meta),
            shape="gemini",
        ))
    return None


def load_prices(path=None):
    """The dated price table, or None when it is missing, malformed or stale.

    Returns None rather than an empty table so that "no prices configured" and
    "prices configured as zero" cannot be confused at the call site.
    """
    path = Path(path or PRICES)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    as_of = payload.get("asOf")
    try:
        stamped = datetime.strptime(str(as_of), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    age = (datetime.now(timezone.utc) - stamped).days
    if age > PRICE_MAX_AGE_DAYS:
        return None
    models = payload.get("models")
    return payload if isinstance(models, dict) else None


def unpriced_reason(usage, model, prices):
    """Why this row has no cost, or None when it does have one."""
    if usage is None:
        return "no usage reported"
    if not prices:
        return "no price table"
    rates = (prices.get("models") or {}).get(model)
    if not isinstance(rates, dict):
        return f"no price entry for model {model or '(unnamed)'}"
    expires = rates.get("ratesExpireOn")
    if expires:
        try:
            deadline = datetime.strptime(str(expires), "%Y-%m-%d").replace(
                tzinfo=timezone.utc)
        except ValueError:
            return f"unreadable ratesExpireOn for {model}"
        if datetime.now(timezone.utc).date() > deadline.date():
            return f"rates for {model} expired on {expires}"
    return None


def estimate_usd(usage, model, prices):
    """Dollar cost for one invocation, or None when it cannot be known.

    None is a real answer here and is reported as one. Every alternative --
    zero, a guess, the uncached rate applied to everything -- produces a number
    that looks like a measurement.
    """
    if usage is None or not prices:
        return None
    rates = (prices.get("models") or {}).get(model)
    if not isinstance(rates, dict):
        return None
    # Per-model expiry, not just a table date. Two of the three pinned models
    # are on introductory pricing, and one of them lapses while the table as a
    # whole is still well inside PRICE_MAX_AGE_DAYS. A single table date cannot
    # express "this row is stale and the others are fine".
    expires = rates.get("ratesExpireOn")
    if expires:
        try:
            deadline = datetime.strptime(str(expires), "%Y-%m-%d").replace(
                tzinfo=timezone.utc)
        except ValueError:
            return None
        if datetime.now(timezone.utc).date() > deadline.date():
            return None
    try:
        per_million = (
            usage.fresh_input_tokens * float(rates["inputPerMTok"])
            + usage.cached_input_tokens * float(rates.get("cachedInputPerMTok", rates["inputPerMTok"]))
            + usage.cache_write_input_tokens * float(
                rates.get("cacheWritePerMTok", rates["inputPerMTok"]))
            + usage.output_tokens * float(rates["outputPerMTok"])
        )
    except (KeyError, TypeError, ValueError):
        return None
    return round(per_million / 1_000_000, 6)


@dataclass
class Record:
    at: str
    applicationId: str
    provider: str
    model: str
    taskKind: str
    measured: bool
    usage: dict = field(default_factory=dict)
    costUsd: float = None
    priceAsOf: str = None
    # Why a cost is missing, when it is. None simply meant "no number" and read
    # identically whether the table was absent, the rates had lapsed, or the
    # pinned model had been renamed out from under its own price entry. The
    # last of those is what happened: the table said gemini-3.7-flash while
    # providers.MODEL_IDS pinned gemini-3.7-flash-medium, so every Gemini row
    # costed as null and nothing anywhere said so.
    unpricedReason: str = None


def ledger_path(root=None):
    root = Path(root).expanduser() if root else Path(
        os.environ.get("JOB_APPLICATION_DIR", "~/.job-application")).expanduser()
    return root / "usage.jsonl"


def live_ledger():
    """The device's own ledger, ignoring wherever this process has been pointed.

    Named separately from `ledger_path()` because the two answer different
    questions: that one is "where does this call write", this one is "which file
    is the real measurement", and the guard below compares them. It has to
    ignore JOB_APPLICATION_DIR for that comparison to mean anything: a guard
    that followed the redirect would protect the temporary directory and leave
    the ledger open.
    """
    return ledger_path(Path("~/.job-application"))


def _under_test_runner():
    """True when this process is a test run rather than a real one.

    Measured rather than assumed. Nothing under scripts/ imports unittest or
    pytest, and importing run_apply, document_worker, watch, providers, publish
    or ingest leaves neither name in sys.modules, so this cannot be true on the
    apply path. `LiveLedgerGuardTests` pins both halves of that.
    """
    return "unittest" in sys.modules or "pytest" in sys.modules


def refusal_reason(path):
    """Why a test run may not write to `path`, or None when the write proceeds.

    A fixture row is not distinguishable from a real one once it is a line in
    this file. It carries a plausible application id, one of the three pinned
    model names and a real taskKind, and `summary` reads every line it finds.
    Measured on this device 2026-08-28: 3,372 fabricated rows against 29 real
    ones, accumulated since 2026-08-25 at fifteen a suite run, unnoticed,
    because telemetry is the one thing here designed never to complain.

    The comparison is against the one real path rather than against "was a root
    passed", so a test that redirects JOB_APPLICATION_DIR to a temporary
    directory is allowed and a test that names the device store explicitly is
    not. Which is the right way round: what matters is the file, not the syntax
    the caller reached it with.
    """
    if not _under_test_runner():
        return None
    live = live_ledger()
    try:
        if Path(path).resolve() != live.resolve():
            return None
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return (f"refusing to write {live} from a test run. Point the store at a "
            "temporary directory (root=, or JOB_APPLICATION_DIR) so fixture "
            "rows never reach the ledger `summary` reports from.")


def record(application_id, provider, model, task_kind, usage, *,
           root=None, prices=None, now=None):
    """Append one usage row. Returns the row, or None if it could not be written.

    Never raises. This runs immediately after a real application has been
    filled in and possibly submitted, and losing the telemetry for that run is
    an acceptable outcome in a way that losing the run is not.
    """
    try:
        path = ledger_path(root)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    refusal = refusal_reason(path)
    if refusal is not None:
        # sys.stderr.write, not print. The tests that trigger this are the ones
        # driving run_apply.main(), and they patch builtins.print to keep the
        # runner's output readable -- so a printed diagnostic was swallowed by
        # the exact runs it exists to explain. Measured: the guard correctly
        # refused all ten rows and said so zero times.
        #
        # Loud, and once per refused row rather than once per process. A correct
        # suite writes nothing here, so any line at all is the regression.
        sys.stderr.write(f"provider_usage: {refusal}\n")
        return None
    prices = prices if prices is not None else load_prices()
    row = Record(
        at=now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        applicationId=str(application_id or "")[:200],
        provider=str(provider or "")[:40],
        model=str(model or "")[:80],
        taskKind=str(task_kind or "")[:40],
        measured=usage is not None,
        usage=asdict(usage) if usage is not None else {},
        costUsd=estimate_usd(usage, model, prices),
        priceAsOf=(prices or {}).get("asOf"),
        unpricedReason=unpriced_reason(usage, model, prices),
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(row), sort_keys=True)
        # Append rather than rewrite: concurrent apply slots write here, and a
        # read-modify-write would lose rows exactly the way ingest loses jobs.
        # A single write() under O_APPEND is atomic for a line this size.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, (line + "\n").encode("utf-8"))
        finally:
            os.close(descriptor)
    except (OSError, TypeError, ValueError):
        return None
    return row


def read_ledger(root=None):
    rows = []
    try:
        text = ledger_path(root).read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def summarise(rows):
    """Per-application totals, and the coverage that qualifies them."""
    by_app = {}
    for row in rows:
        app = by_app.setdefault(row.get("applicationId") or "?", {
            "invocations": 0, "measured": 0, "inputTokens": 0, "cachedInputTokens": 0,
            "outputTokens": 0, "costUsd": 0.0, "costKnown": True, "providers": set(),
        })
        app["invocations"] += 1
        app["providers"].add(row.get("provider") or "?")
        if not row.get("measured"):
            app["costKnown"] = False
            continue
        app["measured"] += 1
        usage = row.get("usage") or {}
        app["inputTokens"] += int(usage.get("input_tokens") or 0)
        app["cachedInputTokens"] += int(usage.get("cached_input_tokens") or 0)
        app["outputTokens"] += int(usage.get("output_tokens") or 0)
        cost = row.get("costUsd")
        if cost is None:
            app["costKnown"] = False
        else:
            app["costUsd"] += float(cost)
    for app in by_app.values():
        app["providers"] = sorted(app["providers"])
    return by_app


def _median(values):
    values = sorted(values)
    if not values:
        return 0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="Provider token usage and cost.")
    parser.add_argument("command", choices=["summary", "prices"], nargs="?",
                        default="summary")
    parser.add_argument("--store", help="store directory (default ~/.job-application)")
    args = parser.parse_args(argv)

    if args.command == "prices":
        prices = load_prices()
        if prices is None:
            print("provider_usage: no usable price table at "
                  f"{PRICES}\n"
                  "Tokens are still recorded; cost is reported as unknown until a\n"
                  "dated table exists and is newer than "
                  f"{PRICE_MAX_AGE_DAYS} days.", file=sys.stderr)
            return 1
        print(json.dumps(prices, indent=2, sort_keys=True))
        return 0

    rows = read_ledger(args.store)
    if not rows:
        print(f"provider_usage: no usage recorded yet at {ledger_path(args.store)}")
        return 0
    apps = summarise(rows)
    measured = [a for a in apps.values() if a["measured"] and a["measured"] == a["invocations"]]
    partial = [a for a in apps.values() if 0 < a["measured"] < a["invocations"]]
    unmeasured = [a for a in apps.values() if a["measured"] == 0]
    costed = [a["costUsd"] for a in apps.values() if a["costKnown"] and a["measured"]]

    print(f"applications with usage rows : {len(apps)}")
    print(f"  fully measured             : {len(measured)}")
    print(f"  partly measured            : {len(partial)}")
    print(f"  no usage reported          : {len(unmeasured)}")
    if measured:
        print(f"\nper application, fully measured only (n={len(measured)}):")
        print(f"  median input tokens      : {_median([a['inputTokens'] for a in measured]):,.0f}")
        print(f"  median cached input      : {_median([a['cachedInputTokens'] for a in measured]):,.0f}")
        print(f"  median output tokens     : {_median([a['outputTokens'] for a in measured]):,.0f}")
    if costed:
        print(f"  median cost USD          : {_median(costed):.4f}")
        print(f"  mean cost USD            : {sum(costed)/len(costed):.4f}")
    else:
        print("\ncost: unknown. Tokens are recorded; no usable price table.")
        print(f"      see {PRICES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
