#!/usr/bin/env python3
"""Publish the dashboard to Vercel, behind a password, at a stable URL.

Publishing writes sanitized board state and application documents to Cloudflare KV. It
is not a deployment. The board changes dozens of times a day, Vercel's free plan
allows a hundred deployments a day, and inlining the board into the function
spent the lot in an afternoon. Code reaches production by merging to main.

  python3 scripts/publish.py --setup     # link the project, set the password
  python3 scripts/publish.py             # write board state to KV
  python3 scripts/publish.py --deploy    # also push code from here
  python3 scripts/publish.py --local     # serve it on localhost instead

Design notes:
  - The board state holds real application data and never enters the repo or a build.
  - Vercel owns the renderer. KV owns only sanitized state and documents, so a
    code deployment cannot silently keep serving an older interface.
  - Documents are written only when their contents change, because KV allows a
    thousand writes a day and forty unchanged PDFs per refresh would spend it.
  - The URL is recorded in the store as dashboardUrl, so a fresh session can
    find the board without asking.
"""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolpath  # noqa: E402
import settings_panel  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"
PASSWORD_ENV = "DASHBOARD_PASSWORD"


class PublishError(Exception):
    pass


def vercel_cmd():
    exe = toolpath.vercel(required=False)
    if exe:
        return [exe]
    npx = toolpath.find("npx", required=False)
    if npx:
        return [npx, "--yes", "vercel@latest"]
    raise PublishError(
        "the Vercel CLI is not installed. Install it with: npm install -g vercel"
    )


def run(cmd, **kw):
    kw.setdefault("cwd", DEPLOY)
    kw.setdefault("text", True)
    # The Vercel CLI is a `#!/usr/bin/env node` script, not a binary, so running
    # it by absolute path is not enough: the kernel execs `/usr/bin/env node`
    # and env resolves node against THIS child's PATH. Under launchd that PATH
    # is /usr/bin:/bin:/usr/sbin:/sbin, node is invisible, and every publish
    # died with exit 127 while the watcher reported a healthy cycle.
    kw.setdefault("env", toolpath.enriched_env())
    return subprocess.run(cmd, **kw)


def build_dashboard(root):
    """Render the board and hand back its sanitized state and summary.

    It used to be written into deploy/api/_dashboard.js for the function to
    import, which meant a deployment for every board change and an untracked
    file a GitHub build would not have. Sanitized renderer state goes to KV now.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "board.html"
        state_out = Path(tmp) / "board-state.json"
        cmd = [sys.executable, str(REPO / "scripts" / "dashboard.py"),
               "--out", str(out), "--state-out", str(state_out)]
        if root:
            cmd += ["--root", root]
        proc = subprocess.run(cmd, text=True, capture_output=True)
        if proc.returncode != 0:
            raise PublishError((proc.stderr or "dashboard build failed").strip())
        html = out.read_text(encoding="utf-8")
        state = json.loads(state_out.read_text(encoding="utf-8"))
        summary = json.loads(proc.stdout)

    from datetime import datetime, timezone

    summary["generatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary["html"] = html
    summary["state"] = state
    return summary


def build_documents(root):
    """Inline the resume and cover letter each submitted application used.

    They live outside the deployment, so the board could not otherwise link to
    them. Only documents referenced by a completed event are included, which
    keeps the bundle to what the Submitted tab can actually show.
    """
    import base64

    store = [sys.executable, str(REPO / "scripts" / "store.py")]
    if root:
        store += ["--root", root]
    hist = subprocess.run(store + ["history-list"], capture_output=True, text=True)
    events = json.loads(hist.stdout or "[]")
    wanted = set()
    for e in events:
        if e.get("event") != "completed":
            continue
        for key in ("resumeVariant", "coverLetter"):
            value = e.get(key) or ""
            if value.endswith(".pdf"):
                # Some runs recorded a path rather than a filename. rglob found
                # nothing for those and the documents were dropped from the
                # bundle in silence, leaving dead links on the Submitted row.
                wanted.add(PurePosixPath(value).name)

    apps = REPO / "applications"
    docs, total = {}, 0
    for name in sorted(wanted):
        hits = list(apps.rglob(name))
        if not hits:
            continue
        raw = hits[0].read_bytes()
        total += len(raw)
        docs[name] = base64.b64encode(raw).decode()

    return {"documents": len(docs), "bytes": total, "docs": docs}


def logged_in(base):
    """(ok, why). Distinguishes 'not signed in' from 'could not run at all'.

    A bare returncode check reported exit 127, which is the node shim failing to
    find its interpreter, as a Vercel login problem, and sent the user off to run
    `vercel login` for a fault that had nothing to do with their account.
    """
    proc = run(base + ["whoami"], capture_output=True)
    if proc.returncode == 0:
        return True, ""
    blob = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode == 127 or "No such file or directory" in blob:
        return False, f"could not run the Vercel CLI at all: {blob[:200]}"
    return False, ""


def require_login(base):
    ok, why = logged_in(base)
    if ok:
        return
    if why:
        raise PublishError(why)
    raise PublishError(
        "not signed in to Vercel. Run `vercel login` in your terminal first "
        "(it opens a browser), then re-run this."
    )


def cmd_setup(args, base):
    require_login(base)

    if not (DEPLOY / ".vercel" / "project.json").exists():
        # Name the project explicitly. Without --project the CLI names it after
        # the directory it links, which here is "deploy", and that becomes the
        # production hostname.
        print(f"Linking the Vercel project as {args.project}...")
        if run(base + ["link", "--yes", "--project", args.project]).returncode != 0:
            raise PublishError("vercel link failed")

    password = args.password or secrets.token_urlsafe(18)

    # Replace rather than stack: a project can hold several values for one key,
    # and two live passwords is a confusing gate.
    run(base + ["env", "rm", PASSWORD_ENV, "production", "--yes"],
        capture_output=True)
    proc = run(base + ["env", "add", PASSWORD_ENV, "production"],
               input=password + "\n", capture_output=True)
    if proc.returncode != 0:
        raise PublishError(f"could not set {PASSWORD_ENV}: {(proc.stderr or '').strip()}")

    print("\n" + "=" * 62)
    print("  Dashboard password (save this in your password manager now,")
    print("  it is not stored anywhere on disk):")
    print(f"\n      {password}\n")
    print("  Username at the browser prompt can be anything.")
    print("=" * 62 + "\n")
    return 0


def cmd_local(args, _base):
    # Local serving uses the exact rendered page without touching shared KV.
    summary = build_dashboard(args.root)
    password = os.environ.get(PASSWORD_ENV) or "local"
    print(f"Serving on http://localhost:{args.port} (password: {password})")
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "board.html"
        page.write_text(summary["html"], encoding="utf-8")
        env = {**os.environ, PASSWORD_ENV: password, "JOBBY_LOCAL_BOARD_FILE": str(page)}
        return run([toolpath.node(), "dev-server.mjs", str(args.port)], env=env).returncode


def current_url(root):
    store = [sys.executable, str(REPO / "scripts" / "store.py")]
    if root:
        store += ["--root", root]
    r = subprocess.run(store + ["meta-get", "--key", "dashboardUrl"],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout or "{}").get("dashboardUrl") or ""
    except json.JSONDecodeError:
        return ""


def expected_counts(root):
    """What the store itself says, as the authority a render is checked against."""
    store = [sys.executable, str(REPO / "scripts" / "store.py")]
    if root:
        store += ["--root", root]
    hist = subprocess.run(store + ["history-list"], capture_output=True, text=True)
    if hist.returncode != 0:
        raise PublishError(f"cannot read history to check the render: {(hist.stderr or '')[:200]}")
    latest = {}
    for e in sorted(json.loads(hist.stdout or "[]"), key=lambda e: e.get("at") or ""):
        if e.get("applicationId"):
            latest[e["applicationId"]] = e
    prof = subprocess.run(store + ["profile-get"], capture_output=True, text=True)
    profile = json.loads(prof.stdout or "{}") if prof.returncode == 0 else {}
    return {
        "submitted": sum(1 for e in latest.values() if e.get("event") == "completed"),
        "hasProfile": bool(profile.get("firstName")),
    }


def guard_render(summary, root):
    """Refuse a board that lost data the store still has.

    Three applications running at once meant three processes publishing, and a
    render that caught the store mid-write produced a board with no profile name
    and an empty Submitted tab. It then overwrote a perfectly good board, so
    thirty-four submitted applications appeared to vanish while every one of them
    was still on disk. The store is the authority; a render that disagrees with
    it is the thing that is wrong.
    """
    expected = expected_counts(root)
    if summary["submitted"] < expected["submitted"]:
        raise PublishError(
            f"refusing to publish: the render shows {summary['submitted']} submitted but the "
            f"store has {expected['submitted']}. Something read the store mid-write. "
            "The published board is untouched; run this again.")
    if expected["hasProfile"] and summary.get("greeting") == "there":
        raise PublishError(
            "refusing to publish: the render has no profile name, which means it read an "
            "empty or half-written store. The published board is untouched.")


def provider_payload(root=None):
    """Read provider state without publishing CLI output or credentials."""
    store_root = Path(root).expanduser().resolve() if root else Path(
        os.environ.get("JOB_APPLICATION_DIR", "~/.job-application")
    ).expanduser().resolve()
    state_path = store_root / "providers.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        state = {}
    return settings_panel.public_payload(state)


def push_content(html, state, docs, providers=None):
    """Write sanitized board state and documents into KV.

    The legacy HTML remains a migration fallback until all local publishers run
    this release. New Vercel deployments render ``state`` with their own code.
    Files are used instead of argv because the payloads are several megabytes.
    """
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "board.html"
        state_path = Path(tmp) / "board-state.json"
        docs_path = Path(tmp) / "documents.json"
        providers_path = Path(tmp) / "providers.json"
        html_path.write_text(html, encoding="utf-8")
        state_path.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
        docs_path.write_text(json.dumps(docs), encoding="utf-8")
        providers_path.write_text(
            json.dumps(providers or settings_panel.public_payload({})), encoding="utf-8"
        )
        r = subprocess.run([toolpath.node(), "publish-content.mjs",
                            str(html_path), str(docs_path), str(providers_path),
                            str(state_path)],
                           cwd=DEPLOY, capture_output=True, text=True,
                           env=toolpath.queue_env())
    if r.returncode != 0:
        raise PublishError(f"could not write board state to KV: {(r.stderr or '').strip()[:400]}")
    return json.loads(r.stdout or "{}")


def sync_renderer():
    """Regenerate the deployed renderer bundle, and say if it moved.

    deploy/renderer is a copy of the modules the Vercel function imports, and
    Vercel builds it from git. So a change to scripts/found_lane.py that nobody
    remembered to sync deploys the *old* interface, silently, while every local
    test passes. That is not hypothetical: three commits of UI work sat in the
    repo and never reached the board that way.

    Running it here means a publish can never write state that the deployed
    renderer was not built to draw. It cannot deploy the result — Vercel builds
    from git — so when the bundle moves this says so and asks for a commit,
    which is the one thing a script cannot do on someone's behalf.
    """
    before = {}
    target = REPO / "deploy" / "renderer"
    for path in sorted(target.glob("*.py")):
        before[path.name] = path.read_bytes()

    sync_module = REPO / "scripts" / "sync_vercel_renderer.py"
    proc = subprocess.run([sys.executable, str(sync_module)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise PublishError((proc.stderr or "renderer sync failed").strip()[:300])

    moved = [path.name for path in sorted(target.glob("*.py"))
             if before.get(path.name) != path.read_bytes()]
    return moved


def cmd_publish(args, base):
    """Publish the board.

    This writes sanitized state and documents to KV. It is not a deployment: the
    board changes dozens of times a day and Vercel's free plan allows a hundred
    deployments, which is how a day's worth got spent in an afternoon. Code
    reaches production by merging to main, which is what Vercel is connected to.
    Pass --deploy to push code from here anyway.
    """
    moved = sync_renderer()
    if moved:
        print("Renderer bundle refreshed from scripts/: " + ", ".join(moved))
        print("  Commit deploy/renderer/ or the deployment keeps serving the old "
              "interface — Vercel builds it from git, not from here.")

    summary = build_dashboard(args.root)
    docs = build_documents(args.root)
    print(f"Board: {summary['blocked']} blocked, {summary.get('inflight', 0)} in flight, "
          f"{summary['submitted']} submitted, {docs['documents']} documents "
          f"({docs['bytes'] / 1024 / 1024:.1f} MB)")

    guard_render(summary, args.root)

    if getattr(args, "dry_run", False):
        # The hatch for tests. Everything up to here runs, so a render fault or
        # a guard_render failure still surfaces; nothing reaches KV. This exists
        # instead of a --root override because the board is served from one
        # namespace fixed in deploy/.env.local, so any store that publishes
        # overwrites the real board. There is no safe way to let a temp store
        # through, only a safe way to stop short.
        html = summary.pop("html")
        print(json.dumps({"published": False, "dryRun": True,
                          "wouldWriteBytes": len(html),
                          "documents": docs["documents"],
                          "submitted": summary.get("submitted")}, indent=2))
        return 0

    result = push_content(
        summary.pop("html"), summary.pop("state"), docs.pop("docs"),
        provider_payload(args.root)
    )
    print(f"Board state written to KV: {result['state']['bytes'] / 1024:.0f} KB compressed, "
          f"{result['documents']['written']} documents written, "
          f"{result['documents']['skipped']} unchanged")

    if not args.deploy:
        url = current_url(args.root) or "https://jobby-board.vercel.app"
        print(f"\nBoard updated: {url}\n")
        return 0

    require_login(base)
    if not (DEPLOY / ".vercel" / "project.json").exists():
        raise PublishError("project is not linked yet. Run with --setup first.")

    cmd = base + ["deploy", "--yes"]
    if not args.preview:
        cmd.append("--prod")
    proc = run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise PublishError((proc.stderr or "vercel deploy failed").strip())

    urls = re.findall(r"https://[^\s]+", (proc.stdout or "") + (proc.stderr or ""))
    url = urls[-1] if urls else None
    if not url:
        raise PublishError("deployed, but no URL was reported; check `vercel ls`")

    if not args.preview:
        store = [sys.executable, str(REPO / "scripts" / "store.py")]
        if args.root:
            store += ["--root", args.root]
        subprocess.run(store + ["meta-set", "--key", "dashboardUrl", "--value", url],
                       capture_output=True, text=True)

    print(f"\nBoard published: {url}\n")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Publish the job-application dashboard to Vercel.")
    ap.add_argument("--setup", action="store_true", help="link the project and set the password")
    ap.add_argument("--local", action="store_true", help="serve locally instead of deploying")
    ap.add_argument("--deploy", action="store_true",
                    help="also push code to Vercel from here. Normally unnecessary: "
                         "merging to main deploys, and publishing board state is a KV write")
    ap.add_argument("--preview", action="store_true", help="deploy to a preview URL, not production")
    ap.add_argument("--password", help="use this password instead of generating one")
    # Deliberately bland: this becomes the public hostname, and the board is
    # readable over someone's shoulder before the password prompt is answered.
    ap.add_argument("--project", default="jobby-board",
                    help="Vercel project name, which becomes the hostname (default jobby-board)")
    ap.add_argument("--port", type=int, default=3000, help="port for --local (default 3000)")
    ap.add_argument("--root", help="store directory (default ~/.job-application)")
    ap.add_argument("--dry-run", action="store_true",
                    help="render and check, write nothing to KV. The way to exercise "
                         "publishing from a test store, since publishing one for real "
                         "would overwrite the live board.")
    args = ap.parse_args()

    # Refuse to publish a store that is not the one this board belongs to.
    #
    # The KV namespace comes from deploy/.env.local, never from the store, so a
    # board rendered from a redirected store still lands on the real one. Callers
    # are supposed to pass --root, and one of them forgot: phase.py publishes
    # whenever --root is absent, and a test store reached it through there and
    # rendered a two-row fixture over the live board. Guarding at the caller
    # fixes that caller. Guarding here fixes every caller, including the next one
    # written.
    # There is deliberately no --root escape hatch here. I gave it one, tested
    # the refusal, then ran the "allowed" branch and it wrote a 0-submitted
    # board over the live one, which is the incident this guard exists to stop.
    # guard_render cannot catch it either: it compares the render against the
    # same redirected store, so nothing-versus-nothing agrees.
    default_store = Path("~/.job-application").expanduser().resolve()
    resolved = Path(args.root).expanduser().resolve() if args.root \
        else Path(os.environ.get("JOB_APPLICATION_DIR", default_store)).expanduser().resolve()
    if resolved != default_store and not args.local and not args.dry_run:
        print(json.dumps({
            "published": False,
            "error": f"refusing to publish from {resolved}. The board is served from one "
                     f"KV namespace fixed in deploy/.env.local, so publishing any other "
                     f"store overwrites the real board with that store's contents. Use "
                     f"--local to look at it instead."}))
        return 0

    try:
        base = [] if args.local else vercel_cmd()
        if args.setup:
            return cmd_setup(args, base)
        if args.local:
            return cmd_local(args, base)
        return cmd_publish(args, base)
    except PublishError as exc:
        print(f"publish: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
