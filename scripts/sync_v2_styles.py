#!/usr/bin/env python3
"""Generate the v2 files that still have a source outside `apps/web`.

    python3 scripts/sync_v2_styles.py            write them
    python3 scripts/sync_v2_styles.py --check    exit 1 if any is stale

Every output here is a value two halves of the system have to agree on, so it
is written once and copied rather than retyped:

    lanes.generated.ts       the lane SQL, from scripts/v2_lanes.py
    logos.generated.ts       trusted logo hosts, from scripts/company_logos.py
    bank.generated.ts        Memory Bank labels, from scripts/answer_categories.py
    providers.generated.ts   provider display names, from scripts/provider_status.py
    _ui/*.generated.mjs      the device's KV, queue, edit-rule, key and R2
                             helpers, from deploy/*.mjs

**The name is historical.** This script used to generate v2's stylesheet,
chrome, rubric, icons and search options from the original Python board. That
board is gone, and those files (`board.css`, `chrome.ts`, `rubric.ts`,
`icons.ts`, `lane_chrome.ts`, `search.ts`, and `apps/web/public/`) are now
hand-owned source in `apps/web`. Edit them there.
"""

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

APP = REPO / "apps" / "web" / "app"
LANES = APP / "lanes.generated.ts"
LOGOS = APP / "logos.generated.ts"
BANK = APP / "bank.generated.ts"
PROVIDER_LABELS = APP / "providers.generated.ts"


def lane_module():
    """The lane SQL as TypeScript. One spelling of each predicate, generated,
    for the reason `LogoContractTests` exists."""
    import v2_lanes
    return v2_lanes.as_typescript()


def logos_module():
    """The trusted logo hosts, as TypeScript.

    `company_logos.TRUSTED_SOURCE_HOSTS` is the source of truth, and v2's own
    proxy needs the same set. A host removed from Python and left in a second
    copy is an open redirect target nobody is looking at.

    The candidate TLDs travel with it. They are a constant in the route rather
    than spelled out in each logo URL, which keeps the URLs short.
    """
    import company_logos
    import json
    hosts = sorted(company_logos.TRUSTED_SOURCE_HOSTS)
    return ("// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.\n"
            "// The hosts a feed logo may be fetched from, from\n"
            "// company_logos.TRUSTED_SOURCE_HOSTS. A third hand-written copy\n"
            "// of this set is an open redirect nobody is watching.\n\n"
            f"export const LOGO_HOSTS: ReadonlySet<string> =\n"
            f"  new Set({json.dumps(hosts, indent=4)});\n\n"
            "// `alt=1` means try the other TLDs of the same label. A constant\n"
            "// here rather than in each URL.\n"
            "export const CANDIDATE_TLDS = "
            f"{json.dumps(['.com', '.io', '.ai', '.co'])} as const;\n")


def providers_module():
    """What each provider is called, from `provider_status.PROVIDERS`.

    **The label travels and the model does not.** `provider_status.PROVIDERS`
    carries a display model that can lag `providers.MODEL_IDS`, which is the
    pin every agent task actually invokes. So the display name of the tool
    comes from here -- "Gemini CLI", not the "agy" a fixture invented -- and
    the model comes through the mirror from the pin, because a card that names
    the wrong model is worse than one that names none.
    """
    import json
    import provider_status
    import providers as provider_registry
    labels = {pid: {"name": name, "agent": agent}
              for pid, name, agent, _ in provider_status.PROVIDERS}
    shown = {pid: model for pid, _, _, model in provider_status.PROVIDERS}
    pinned = dict(provider_registry.MODEL_IDS)
    disagree = sorted(k for k in shown if shown[k] != pinned.get(k))
    return ("// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.\n"
            "// Provider display names, from provider_status.PROVIDERS.\n"
            "// The model is NOT here: it comes through the mirror from\n"
            "// providers.MODEL_IDS, which is the pin that actually runs.\n"
            f"// The two disagree today for: {', '.join(disagree) or 'nothing'}.\n\n"
            "export const PROVIDER_LABELS: Record<string, "
            "{ name: string; agent: string }> =\n"
            + json.dumps(labels, indent=2) + ";\n")


def bank_module():
    """The Memory Bank's taxonomy labels, from `answer_categories`.

    The classification itself is stored per row by the mirror -- migration 0014
    says why a TypeScript copy of those word-bounded regexes would be a fourth
    chance at an afternoon this project has lost three times. What travels here
    is only the labels and their order, so the chips read the way the module
    names them and appear in the order it ranks them.

    `other` is emitted even though it can be empty, because the module's own
    docstring says it stays in the UI at zero: a category set that cannot miss
    has stopped classifying.
    """
    import json
    import answer_categories
    labels = [(key, label) for key, label, _ in answer_categories.CATEGORIES]
    labels.append(("other", "Other"))
    kinds = [("question", "Questions"), ("policy", "Your policies"),
             ("story", "Story bank")]
    return ("// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.\n"
            "// The Memory Bank's category labels and order, from\n"
            "// answer_categories.CATEGORIES. The classification itself is a\n"
            "// stored column; only the naming travels.\n\n"
            "export const BANK_CATEGORIES: [string, string][] = "
            + json.dumps(labels) + ";\n\n"
            "// The three kinds of record that share answers.json.\n"
            "export const BANK_KINDS: [string, string][] = "
            + json.dumps(kinds) + ";\n")


#: **One list, walked by the writer, the check and CI.** They were three, and
#: the check named three of the four files this script wrote while CI's
#: porcelain list named three of the five, so one output could be stale and
#: both said current. That is the same shape as every other half-check in
#: this repository, and the fix is to stop having several lists rather than to
#: add to the shortest.
#:
#: `outputs()` is called rather than held, because each entry's second half is
#: a renderer and importing this module must not run them.
KV_SOURCE = REPO / "deploy" / "kv.mjs"
KV_TARGET = REPO / "apps" / "web" / "app" / "_ui" / "kv.generated.mjs"
KVQUEUE_SOURCE = REPO / "deploy" / "kvqueue.mjs"
KVQUEUE_TARGET = REPO / "apps" / "web" / "app" / "_ui" / "kvqueue.generated.mjs"
EDITS_SOURCE = REPO / "deploy" / "profile_edits.mjs"
EDITS_TARGET = REPO / "apps" / "web" / "app" / "_ui" / "profileEdits.generated.mjs"
DEVICE_KEYS_SOURCE = REPO / "deploy" / "device_keys.mjs"
DEVICE_KEYS_TARGET = REPO / "apps" / "web" / "app" / "_ui" / "deviceKeys.generated.mjs"
R2_SOURCE = REPO / "deploy" / "r2.mjs"
R2_TARGET = REPO / "apps" / "web" / "app" / "_ui" / "r2.generated.mjs"


def _generated_header(source_name, why):
    return (f"// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.\n"
            f"//\n"
            f"// Copied from deploy/{source_name}. Change that file.\n"
            f"//\n"
            + "".join(f"// {line}\n" for line in why.split("\n")))


#: The one line the copy may not take verbatim: the generated queue imports the
#: generated kv, not `deploy/kv.mjs`, which is outside v2's traced bundle.
KVQUEUE_IMPORT = ("import { kvGet, kvPut, configured } from './kv.mjs';",
                  "import { kvGet, kvPut, configured } from './kv.generated.mjs';")


def kv_module():
    """`deploy/kv.mjs` verbatim, so v2 speaks to KV the way the device does.

    The queue is the daemon's work list and there is exactly one of it. A
    second client for the same namespace is a second set of assumptions about
    read-modify-write on one key, and that key has already cost this project an
    outage where an apply enqueue ate a watch enqueue.
    """
    return _generated_header(
        "kv.mjs",
        "One KV client, not two. The queue is a single key under\n"
        "read-modify-write and a second implementation of that is a\n"
        "second set of assumptions about it.") + KV_SOURCE.read_text(encoding="utf-8")


def kvqueue_module():
    """`deploy/kvqueue.mjs`, with its one import repointed at the copy beside it.

    v2 is a separate Next project rooted at `apps/web`, so a file outside it is
    not in the traced bundle. The import has to name the sibling copy or the
    build resolves nothing; everything else is verbatim, and `--check` fails
    the PR when the two part.
    """
    body = KVQUEUE_SOURCE.read_text(encoding="utf-8")
    before, after = KVQUEUE_IMPORT
    if body.count(before) != 1:
        raise SystemExit(
            f"kvqueue.mjs no longer has exactly one `{before}`; the generator "
            f"rewrites that line and cannot guess a new shape")
    return _generated_header(
        "kvqueue.mjs",
        "Verbatim but for the kv import, which names the generated copy\n"
        "beside it: deploy/ is outside v2's traced bundle.") + body.replace(before, after)


def profile_edits_module():
    """`deploy/profile_edits.mjs` verbatim: one validator for two boards.

    v2 writes the queue arrays and `watch.py` claims them, so the rules an edit
    has to satisfy are one implementation and this is a copy of it, for the
    reason every other generated module here exists: `deploy/` is outside v2's
    traced bundle, so the file has to be present under `apps/web` at build
    time, and `--check` fails the PR when the two part.

    It has no imports, so unlike `kvqueue_module` nothing is rewritten.
    """
    return _generated_header(
        "profile_edits.mjs",
        "Verbatim. The board writes the queue and the device claims it,\n"
        "so the rules an edit has to satisfy are written once.",
    ) + EDITS_SOURCE.read_text(encoding="utf-8")


def device_keys_module():
    """`deploy/device_keys.mjs` verbatim: the KV keys the device writes.

    The device writes provider status and the owner's resume wording under
    these names, and v2 reads them. A key spelled in two places is a key that
    drifts, and a drifted key reads as "never written", which looks like a
    quiet device rather than a broken board. No imports, so nothing is
    rewritten.
    """
    return _generated_header(
        "device_keys.mjs",
        "Verbatim. The device writes these keys and v2 reads them, so the\n"
        "names are written once.",
    ) + DEVICE_KEYS_SOURCE.read_text(encoding="utf-8")


def r2_module():
    """`deploy/r2.mjs` verbatim, so v2 does not become the third SigV4.

    **It is already written twice on purpose and that is the ceiling.**
    `scripts/r2.py` signs for the device and `deploy/r2.mjs` is the JavaScript
    signer, and `tests/test_r2_contract.py` compares complete URLs across
    twelve cases because they are one contract in two languages. A third
    hand-written copy in `apps/web` would be the one that drifts, and drift in
    a signer is a request that fails with no explanation of which half was
    wrong.

    v2 is a separate Next project rooted at `apps/web`, so a file outside it is
    not in the traced bundle. Copying it through the generator is what makes it
    present at build time and stale-detectable at check time: one source, one
    signer, and `--check` fails the PR when they part.
    """
    body = R2_SOURCE.read_text(encoding="utf-8")
    return ("// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.\n"
            "//\n"
            "// Copied verbatim from deploy/r2.mjs. Change that file.\n"
            "//\n"
            "// SigV4 is written twice, in Python for the device and here in\n"
            "// JavaScript, and tests/test_r2_contract.py holds those two to one\n"
            "// contract. This is a copy rather than a third implementation so\n"
            "// that stays true.\n"
            + body)


def outputs():
    return [
        (LANES, lane_module),
        (LOGOS, logos_module),
        (BANK, bank_module),
        (PROVIDER_LABELS, providers_module),
        (R2_TARGET, r2_module),
        (KV_TARGET, kv_module),
        (KVQUEUE_TARGET, kvqueue_module),
        (EDITS_TARGET, profile_edits_module),
        (DEVICE_KEYS_TARGET, device_keys_module),
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when the file on disk is stale")
    args = ap.parse_args(argv)

    written = outputs()

    if args.check:
        stale = []
        for path, render in written:
            now = path.read_text(encoding="utf-8") if path.exists() else None
            if now != render():
                stale.append(path.relative_to(REPO))
        if stale:
            print("stale, run 'python3 scripts/sync_v2_styles.py' and commit: "
                  + ", ".join(str(p) for p in stale), file=sys.stderr)
            return 1
        print(f"{len(written)} generated files are current")
        return 0

    for path, render in written:
        body = render()
        path.write_text(body, encoding="utf-8")
        print(f"wrote {path.relative_to(REPO)} ({len(body)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
