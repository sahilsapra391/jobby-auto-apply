"""Run the corpus modules and file the result. Test code, living in tests/.

**This is here rather than in `scripts/` because no module under `scripts/`
may import a test runner, and that rule is load-bearing rather than tidy.**
`provider_usage.record` and `migrate.device_url_refusal` both decide whether
to refuse a write by asking whether `unittest` is in `sys.modules`. A
production module that imports it makes both backstops misfire: the device's
own usage ledger stops being written during a real run, and says so only on a
stderr nobody is reading.

The first version put `run()` in `scripts/corpus_check.py` and imported
`unittest` lazily inside it, which kept `import corpus_check` clean --
verified in a fresh interpreter -- and still failed
`test_no_module_under_scripts_imports_a_test_runner`, which walks the AST and
does not care how deep the import is. The guard was right and the lazy import
was a dodge: `watch.py` imports `corpus_check`, so the moment anyone called
`run()` in-process rather than spawning it, the watcher would have acquired a
test runner's signature and quietly stopped recording what applications cost.

Splitting it removes the footgun rather than hiding it. There is no `run()`
under `scripts/` left for anyone to call. `corpus_check` keeps the
configuration and the reading -- which modules, the skip ceiling, the warning,
whether a run is due -- and never imports unittest at any depth.
"""

import json
import os
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import corpus_check  # noqa: E402

def run(modules=corpus_check.MODULES):
    """Run the corpus modules in this process and write the result.

    In-process and structured rather than parsing `unittest`'s prose. The
    prose is stable enough in practice and that is not the point: a parser
    that stops matching reports zero failures, which is the same green as
    success. `TestResult` cannot do that.
    """
    sys.path.insert(0, str(REPO))
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    load_errors = []
    for name in modules:
        try:
            suite.addTests(loader.loadTestsFromName(name))
        except Exception as exc:                          # a module that will
            load_errors.append(f"{name}: {exc!r}")        # not import is a fault
    started = time.time()
    result = unittest.TextTestRunner(
        stream=open(os.devnull, "w"), verbosity=0).run(suite)
    failures = [str(t) for t, _ in result.failures]
    errors = [str(t) for t, _ in result.errors] + load_errors
    skipped = len(result.skipped)
    reasons = {}
    for _, why in result.skipped:
        reasons[why] = reasons.get(why, 0) + 1
    over = skipped > corpus_check.MAX_SKIPPED
    payload = {
        "ranAt": int(started),
        "seconds": round(time.time() - started, 1),
        "modules": list(modules),
        "ran": result.testsRun,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "skipCeiling": corpus_check.MAX_SKIPPED,
        "skipReasons": reasons,
        "ok": not failures and not errors and not over,
    }
    corpus_check.STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = corpus_check.RESULT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(corpus_check.RESULT)
    return payload




if __name__ == "__main__":
    payload = run()
    print(json.dumps(payload, indent=2, sort_keys=True))
    raise SystemExit(0 if payload["ok"] else 1)
