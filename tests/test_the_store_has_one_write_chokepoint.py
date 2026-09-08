"""Every atomic writer under `scripts/` is accounted for.

**`store.write_private` said it was "the only function that puts bytes in the
store", and that was false six days before the code relying on it was written.**
`ProviderRegistry._write_locked` has written `providers.json` with its own
`mkstemp`/`os.replace` since 2026-08-23; `class_b.py` arrived 2026-08-29 and
hung the class B mirror off `write_private`. So provider state was declared
class B in the prose of `docs/write-paths.md`, had no row in the table
`write_path_coverage` derives from, and was never mirrored once.

Nothing failed, which is the point. `~/.job-application/logs/dual-write.log`
holds 2,771 lines back to 2026-08-30 and not one mentions a provider: a mirror
that broke would have complained 2,771 times, and this one never tried. The
three rows in `provider_links` came from a hand-run `class_b.backfill` and share
`updated_at` to the microsecond.

A premise nobody can check is a premise that quietly stops being true. So this
enumerates every `os.replace` in `scripts/` and requires each to be the
chokepoint, a declared second writer that hands its write to
`store.after_private_write`, or exempt with the reason it writes nothing the
class B mirror is about. A new writer fails here until somebody decides which.
"""

import ast
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

#: The chokepoint. Everything that can go through it should.
CHOKEPOINT = ("scripts/store.py", "write_private")

#: Writers with their own bytes that DO write a class B store artifact, and so
#: must hand the write to `store.after_private_write` themselves. Each is here
#: because it needs something the chokepoint does not give it.
DECLARED = {
    ("scripts/providers.py", "_write_locked"):
        "fsyncs the parent directory after the rename, which write_private does "
        "not, on the file three services read for connection state",
}

#: Writers that put bytes somewhere the class B mirror has no opinion about,
#: each with the reason. An absence would be ambiguous between "writes nothing
#: mirrored" and "nobody looked", and the second reading is what cost two weeks.
EXEMPT = {
    ("scripts/runlock.py", "_write"):
        "a lock file. Ephemeral, machine-local, and deliberately not data.",
    ("scripts/watch.py", "_write_lock"):
        "a slot lock file, same as runlock.",
    ("scripts/worker_key.py", "_atomic_write"):
        "config.env and the shared worker key. Credentials, which must never "
        "reach the database at all.",
    ("scripts/provider_secrets.py", "store"):
        "an owner-only provider API key. Same reason, more so.",
    ("scripts/providers.py", "_write_private_json"):
        "the Gemini hardened-settings file inside a task's work directory, not "
        "the store.",
    ("scripts/document_worker.py", "private_json"):
        "a task-private bundle file under document-tasks/, which the worker "
        "deletes when the job ends.",
    ("scripts/cover_letter.py", "private_write"):
        "rendered HTML in a temporary render directory.",
    ("scripts/document_contract.py", "install_result"):
        "the generated PDF and HTML into applications/. Class 1 artifacts, "
        "mirrored to R2 by r2_sync rather than to Postgres.",
    ("scripts/answer_record.py", "write"):
        "the per-application answer record beside those PDFs. Class 1, same "
        "path to R2.",
    ("scripts/r2_sync.py", "save_manifest"):
        "the local R2 upload manifest, which is bookkeeping about the bucket.",
    ("scripts/r2_sync.py", "_restore_object"):
        "a file brought back down from R2, restoring bytes the mirror has "
        "already seen.",
    ("scripts/backfill_sections.py", "_main"):
        "a one-off that rewrites the pool file directly; it is not a service "
        "and it prints what it changed.",
}


def atomic_writers():
    """Every `os.replace(...)` under `scripts/`, as (relative path, function).

    Derived from the AST rather than grepped, so a call written inside a
    comment or a docstring is not a call. This repository has mistaken prose
    for code three times.
    """
    out = {}
    for path in sorted((REPO / "scripts").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):        # pragma: no cover
            continue
        functions = [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "replace"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"):
                continue
            owner = "<module>"
            best = -1
            for fn in functions:
                end = fn.end_lineno or fn.lineno
                if fn.lineno <= node.lineno <= end and fn.lineno > best:
                    owner, best = fn.name, fn.lineno
            out[(str(path.relative_to(REPO)), owner)] = node.lineno
    return out


def calls_after_private_write(rel, function):
    """Does that function hand its write to the mirror?"""
    tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if not (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                and fn.name == function):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("after_private_write", "_after_write")):
                return True
    return False


class ChokepointTests(unittest.TestCase):
    def test_the_derivation_finds_something(self):
        found = atomic_writers()
        self.assertGreater(
            len(found), 5,
            "the os.replace scan found almost nothing, so every case below is "
            "vacuous")
        self.assertIn(CHOKEPOINT, found, "write_private no longer replaces")

    def test_every_atomic_writer_is_the_chokepoint_declared_or_exempt(self):
        unaccounted = sorted(
            key for key in atomic_writers()
            if key != CHOKEPOINT and key not in DECLARED and key not in EXEMPT)
        self.assertEqual(
            unaccounted, [],
            f"{unaccounted} write bytes atomically and are neither the "
            f"chokepoint, a declared class B writer, nor exempt. If it writes a "
            f"store artifact the mirror cares about, call "
            f"store.after_private_write; if it does not, say so in EXEMPT.")

    def test_a_declared_writer_actually_hands_its_write_to_the_mirror(self):
        """Declaring it is not wiring it, and this is the half that was
        missing: `providers.json` was declared class B in prose for two weeks
        while nothing called the hook."""
        self.assertTrue(DECLARED, "nothing is declared; this check is vacuous")
        for (rel, function), reason in DECLARED.items():
            self.assertTrue(
                calls_after_private_write(rel, function),
                f"{rel}:{function} is declared a class B writer and never calls "
                f"store.after_private_write, so it mirrors nothing")
            self.assertGreater(len(reason.split()), 6,
                               f"{rel}:{function} has no real reason")

    def test_an_exemption_carries_a_reason(self):
        for key, reason in EXEMPT.items():
            self.assertGreater(
                len(str(reason).split()), 5,
                f"{key} is exempt with no reason; an exemption without one is "
                f"an absence wearing a label")

    def test_every_declared_and_exempt_entry_still_exists(self):
        """An entry for a function that has been renamed silently stops
        covering anything, and reads as coverage."""
        found = atomic_writers()
        for key in list(DECLARED) + list(EXEMPT):
            self.assertIn(key, found,
                          f"{key} is listed here and no longer writes "
                          f"atomically; the entry covers nothing")

    def test_the_chokepoint_still_mirrors(self):
        import store
        self.assertTrue(hasattr(store, "after_private_write"))
        source = (REPO / "scripts" / "store.py").read_text()
        body = source[source.index("def write_private("):]
        body = body[:body.index("\ndef ", 1)]
        self.assertIn("after_private_write", body,
                      "write_private stopped mirroring its own writes")

    def test_providers_json_is_declared_class_b_in_the_table_not_only_the_prose(self):
        """The reason nothing noticed.

        `write_path_coverage.required()` derives from the class B *table* in
        docs/write-paths.md. `providers.json` appeared only in that file's
        prose, so the coverage checklist never asked for it and its absence
        looked like every other unlisted file.
        """
        import write_path_coverage
        required = write_path_coverage.required()
        provider = [key for key in required if key[0] == "providers"]
        self.assertTrue(
            provider,
            "no providers row reaches write_path_coverage.required(), so the "
            "coverage checklist cannot ask for it and the mirror's absence is "
            "invisible again. Asked through the parser rather than by grepping "
            "the document, because the document's prose said this file was "
            "class B for two weeks while the table it derives from did not.")
