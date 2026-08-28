"""Point a test module's store writes at a temporary directory.

The suite ran against the device's own `~/.job-application` for as long as it
has existed, and five modules wrote into it. What that cost, measured on this
device 2026-08-28 before this file existed:

    usage.jsonl                  3,372 fixture rows against 29 real ones
    sessions/acme-pm.json        a fabricated application, status "review",
                                 pending on "Enter the OTP"
    roles.json                   acme-pm filed among 190 real roles
    browser-mcp-apply-1.json     the live slot's firewall config, rewritten
    browser-profile/             created by a test that never opened a browser
    providers.json               the registry the three services read for
                                 connection order and health

None of it announced itself. A usage row is telemetry, which is designed never
to complain; a session file is written by the same store CLI a real run uses,
so it is indistinguishable from one; and the board reads both.

Two levers, because the code reaches the store two ways and neither lever
covers the other:

`JOB_APPLICATION_DIR` is read at call time by `store.py` and by
`provider_usage.ledger_path`, and it is inherited by the store subprocesses a
test drives through `watch`. Setting it in `os.environ` moves all of those.

Module constants like `browser.STATE` and `watch.STATE_DIR` are `Path.home()`
expressions evaluated at import, so the environment cannot reach them. They are
rebound here instead, and **discovered rather than listed**: the module graph is
walked for module-level `Path` values naming something inside the real store. A
constant added tomorrow is covered without anyone remembering this file, which
is the only kind of rule that holds. Walked, not iterated over `sys.modules`,
for the reason in `_scripts_modules`.

Three limits, stated because each is a way to believe this worked when it did
not. A module imported after `begin()` keeps its own constants. A path built
from `Path.home()` inside a function body is not a module constant and is not
reached, which is why `document_worker._base_resume_content` had to be fixed
rather than swept. And this is a guarantee about **writes**: a test that reads
the device's own `resume-content.json` and passes because of what it found
there is still non-hermetic, and no count of files that appeared will say so.

Usage, per module, which is where it belongs. The whole point is that a class
added next year inherits the isolation instead of re-earning it:

    import store_isolation

    def setUpModule():
        store_isolation.begin()

    def tearDownModule():
        store_isolation.end()

`isolate_store(self)` does the same for one test case when a module genuinely
needs a fresh root per test.

Finding the next one. All five were found the same way and there is no cheaper
way: run each module with `HOME` pointed somewhere disposable and look at what
appears in it. `HOME` rather than `JOB_APPLICATION_DIR` alone, because the
constants are `Path.home()` expressions and two of the five leaks were invisible
to a `JOB_APPLICATION_DIR` redirect. This file runs that sweep:

    python3 tests/store_isolation.py

It exits non-zero and names the module and the files. It is not in CI: a CI
runner's home is disposable, so the whole class is harmless there and only ever
hurts on the machine that also holds the real applications. Which is exactly why
it went unnoticed for as long as it did.

**This file can promise things about the filesystem. It can promise nothing
about a port, a process or a lock.** That is the boundary, and it is worth
stating as a rule rather than as a list of excluded modules, because a rule is
something the next person can apply to a module that does not exist yet.

`test_apply_end_to_end` is the module that falls outside it, by construction
rather than by exception. It does not skip under a redirect: `browser.running`
probes the real CDP ports and never consults `HOME`, so it drives a real apply
slot and contends for a real browser either way, and it reserves nothing while
doing so. Its file writes do follow `HOME` -- `browser.ARTIFACTS` derives from
`Path.home()` in this process -- so the sweep would report it clean and would be
answering a question about the filesystem while the interesting hazard is the
slot. Excluding it says so out loud instead.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# Redirects nest: a module-level begin() with a per-test isolate_store() inside
# it is a reasonable thing to want, and a single saved snapshot would restore
# the wrong values on the inner end().
_ACTIVE = []


def live_store():
    """The device's own store, which no test may write to."""
    return Path("~/.job-application").expanduser()


def begin(*extra_modules):
    """Redirect store writes to a fresh temporary root. Returns that root.

    Pass any module the caller loaded through `importlib` rather than `import`.
    `test_watch` builds its modules with `spec_from_file_location`, which never
    registers them in `sys.modules`, so the scan below cannot see them and no
    amount of cleverness will make it: they are reachable only from the test
    module that made them.
    """
    temp = tempfile.TemporaryDirectory(prefix="jobby-store-")
    root = Path(temp.name)
    patch = mock.patch.dict(os.environ, {"JOB_APPLICATION_DIR": str(root)})
    patch.start()
    # Move from wherever the store currently is, not from the device store. A
    # nested begin() finds every constant already pointing at the outer root,
    # and matching them against ~/.job-application would find nothing and
    # silently leave the inner redirect empty.
    modules = _scripts_modules(list(sys.modules.values()) + list(extra_modules))
    _ACTIVE.append((temp, patch, root,
                    _redirect_constants(modules, root, _current_base())))
    return root


def _current_base():
    return _ACTIVE[-1][2] if _ACTIVE else live_store()


def end():
    """Undo the most recent begin()."""
    if not _ACTIVE:
        raise RuntimeError("store_isolation.end() with no begin() outstanding")
    temp, patch, _root, moved = _ACTIVE.pop()
    for module, name, original in moved:
        setattr(module, name, original)
    patch.stop()
    temp.cleanup()


def isolate_store(test, *extra_modules):
    """begin() for the length of one test, undone by the TestCase's cleanup."""
    root = begin(*extra_modules)
    test.addCleanup(end)
    return root


def _under_scripts(module):
    source = getattr(module, "__file__", None)
    if not source:
        return False
    try:
        return Path(source).resolve().parent == SCRIPTS
    except (OSError, RuntimeError, ValueError):
        return False


def _scripts_modules(seeds):
    """Every scripts/ module reachable from `seeds`, following module attributes.

    `sys.modules` alone is not enough, and finding out why took a full-suite
    sweep. Four test modules execute a scripts module by hand and assign the
    result over its `sys.modules` entry. That orphans the original object --
    and the original is the one `run_apply.providers` and every other
    already-imported module still points at. Redirecting what `sys.modules`
    holds therefore moved the copy nobody calls and left the live one naming
    the real store, so `providers.work_order()` inside a run_apply test wrote
    the device's own providers.json from a module that had been "redirected".

    It is invisible when a module is run on its own, because the swap only
    happens if the swapping module is loaded, and it is loaded whenever the
    whole suite is. Following the graph one hop past sys.modules reaches the
    orphan, and traversal stops at scripts/ so this never walks the stdlib.
    """
    found = {}
    queue = list(seeds)
    while queue:
        module = queue.pop()
        if id(module) in found or not _under_scripts(module):
            continue
        found[id(module)] = module
        for value in list(vars(module).values()):
            if isinstance(value, ModuleType) and id(value) not in found:
                queue.append(value)
    return list(found.values())


def _redirect_constants(modules, root, live):
    """Rebind scripts/ constants that name a path inside the real store.

    Returns what was moved, as (module, name, original) triples, so `end()` can
    put every one back rather than recomputing them and hoping the expression
    still means the same thing.
    """
    moved = []
    for module in modules:
        for name, value in list(vars(module).items()):
            if not isinstance(value, Path):
                continue
            try:
                relative = value.relative_to(live)
            except ValueError:
                continue
            moved.append((module, name, value))
            setattr(module, name, root / relative)
    return moved


# Modules the sweep cannot judge. The two render modules read the real base
# resume out of ~/.job-application and are excluded from CI for the same reason.
# The end-to-end module drives a real Chrome on a real apply slot, so the
# firewall writing an activity record under browser-artifacts is the behaviour
# under test rather than a leak.
_SWEEP_SKIPS = ("test_resume_pdf", "test_resume_build", "test_apply_end_to_end")


def sweep(argv=None):
    """Run each test module under a throwaway home; report what reached a store.

    One subprocess per module, because the point is to attribute a write, and a
    single run of everything says only that something wrote.
    """
    modules = list(argv or [])
    if not modules:
        modules = sorted(
            path.stem for path in (ROOT / "tests").glob("test_*.py")
            if path.stem not in _SWEEP_SKIPS)
    leaked = []
    for name in modules:
        with tempfile.TemporaryDirectory(prefix="jobby-sweep-") as home:
            fake = Path(home)
            environment = dict(
                os.environ,
                HOME=str(fake),
                JOB_APPLICATION_DIR=str(fake / ".job-application"),
            )
            subprocess.run(
                [sys.executable, "-m", "unittest", f"tests.{name}"],
                cwd=str(ROOT), env=environment,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
            # Only the store. A fake home also collects ~/.npm and Playwright's
            # browser cache, which are noise rather than leaks.
            store = fake / ".job-application"
            written = sorted(
                str(p.relative_to(store)) for p in store.rglob("*")
            ) if store.exists() else []
            if written:
                leaked.append((name, written))
                print(f"LEAK {name}: {', '.join(written)}")
    if leaked:
        print(f"\n{len(leaked)} module(s) wrote to the store. Give each one a "
              f"setUpModule that calls store_isolation.begin().")
        return 1
    print(f"{len(modules)} modules, none wrote to the store.")
    return 0


if __name__ == "__main__":
    raise SystemExit(sweep(sys.argv[1:]))
