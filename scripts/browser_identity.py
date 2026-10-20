#!/usr/bin/env python3
"""Whether a slot browser is signed in to Google, and when that matters.

**The slot browsers are supposed to be signed in, and for nine days nothing
checked.** The user signed in once on 2026-08-19 and `clone_logins` carried
that session to every slot. On 2026-08-28 `--use-mock-keychain` landed, Chrome
could no longer decrypt anything written under the old Safe Storage key, and
every cookie older than that day was dropped. Measured across all five profiles
that existed then: **zero cookies created before 2026-08-28 survived**, the
Google auth set included. The profiles kept their *history*, which is
unencrypted, so each one still showed a successful sign-in from nine days
earlier while holding no session at all.

Nothing failed. A signed-out slot looks exactly like a signed-in one until a
form asks for a Google account, and then it looks like an ordinary park. The
user found it by noticing applications behaving differently, which is the worst
available detector.

**A fresh sign-in does persist, and that was measured rather than assumed.**
CLAUDE.md predicted it (the mock key is a fixed constant, so cookies written
under it decrypt on the next launch) and said the reasoning "has not been
confirmed against a profile yet". Confirmed on 2026-08-28 against apply-5: a
cookie written through CDP, browser stopped, verified present on disk as 67
bytes of ciphertext, browser relaunched, cookie read back decrypted. So the
sign-in is a one-time act per profile, not a recurring chore, and a check that
finds a slot signed out is reporting a real regression rather than normal decay.

## Reading the state without opening a browser

The cookie *values* are encrypted and this module never decrypts one. It asks a
narrower question: does the store hold rows for the cookies Google sets on a
signed-in session. That needs only the sqlite file, so the check costs no
browser launch, works on a slot that is stopped, and can run inside a health
tick without touching the fleet.

The file is copied before it is read. Chrome holds the live database open and a
concurrent reader gets `database is locked` often enough to matter, and a check
that intermittently reports "unknown" is a check nobody trusts.

## The profile a slot uses is asked for, never constructed

`browser._profile_dir` owns that mapping and this module calls it. apply-1 uses
the unsuffixed `browser-profile` while every other slot is
`browser-profile-<slot>`, so the obvious f-string reports apply-1 as
unprovisioned every single time. A peer session hit exactly that on 2026-08-29
and said so, which is the only reason it is written down here rather than
discovered again.
"""

import json
import pathlib
import re
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import browser  # noqa: E402

# What Google sets on a signed-in session. `SID` and its siblings are the
# long-lived host cookies; `__Secure-1PSID` and `__Secure-3PSID` are the
# partitioned pair modern Chrome carries alongside them.
#
# Presence is the signal, never the value. A count rather than a single name
# because a session mid-refresh can be briefly missing one of them, and a check
# that flickers to "signed out" during a token refresh would fire an alarm at
# the user for something that fixed itself a second later.
GOOGLE_AUTH_COOKIES = ("SID", "SSID", "HSID", "SAPISID",
                       "__Secure-1PSID", "__Secure-3PSID")

# Measured on the real fleet on 2026-08-28: a signed-in profile carried all six,
# in all eight profiles, immediately after a hand sign-in and a clone. Three is
# the floor rather than six so a partial refresh does not read as signed out.
SIGNED_IN_FLOOR = 3


def _cookie_rows(profile):
    """Google auth cookie names present in this profile, or None if unreadable.

    None and an empty tuple are different answers and the caller must be able to
    tell them apart: no cookie store at all is an unprovisioned profile, and an
    empty result is a profile that exists and holds no session. Reporting both
    as "signed out" would tell a new user to sign in again on a machine where
    the browser has never once been launched.
    """
    store = pathlib.Path(profile)/"Default"/"Cookies"
    if not store.exists():
        return None
    tmp = pathlib.Path(tempfile.mkdtemp())/"Cookies"
    try:
        shutil.copy2(store, tmp)
        db = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        try:
            placeholders = ",".join("?" * len(GOOGLE_AUTH_COOKIES))
            rows = db.execute(
                f"select distinct name from cookies where name in ({placeholders}) "
                "and host_key like '%google.com'", GOOGLE_AUTH_COOKIES).fetchall()
        finally:
            db.close()
        return tuple(sorted(r[0] for r in rows))
    except (OSError, sqlite3.DatabaseError):
        # Unreadable is not signed out. A corrupt or half-written store during a
        # browser start would otherwise fire the alarm this exists to make
        # trustworthy.
        return None
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


def slot_state(slot):
    """One slot's Google sign-in state. Never raises, never opens a browser."""
    profile = browser._profile_dir(slot)
    names = _cookie_rows(profile)
    if names is None:
        return {"slot": slot, "profile": str(profile), "state": "unknown",
                "signedIn": False, "cookies": 0,
                "why": "no readable cookie store; this profile has never been used"}
    signed = len(names) >= SIGNED_IN_FLOOR
    return {"slot": slot, "profile": str(profile),
            "state": "signed-in" if signed else "signed-out",
            "signedIn": signed, "cookies": len(names), "found": list(names)}


def signed_in_slot(slot):
    """Whether this one slot carries a Google session."""
    return slot_state(slot)["signedIn"]


def fleet():
    """Every slot's state, for the status command and the health tick."""
    return [slot_state(slot) for slot in browser.SLOTS]


def any_signed_in():
    """True when at least one slot carries a session.

    The first-run gate asks this rather than "are all of them", because
    `clone_logins` is what makes the rest true and it needs one signed-in
    profile to copy from. A fleet with one signed-in slot is a fleet one
    command away from being done; a fleet with none has never been set up.
    """
    # Short-circuits on the first signed-in slot rather than building the whole
    # fleet. `do_not_start` calls this once per queued application per tick, and
    # each slot costs a copy of a ~200 KB sqlite file: the healthy case now
    # reads one profile instead of eight, and only a genuinely signed-out fleet
    # pays for all of them, which is the case that is about to stop everything
    # anyway.
    return any(signed_in_slot(slot) for slot in browser.SLOTS)


def signed_out_slots():
    return [s["slot"] for s in fleet() if not s["signedIn"]]


# --- when a Google session is actually required ----------------------------
#
# Two separate questions, and only the first is answerable before a run starts.
#
# Measured against the 88 real applications in the store on 2026-08-28:
#
#   1 of 88   the posting is hosted on a Google property   (knowable from the URL)
#   3 of 88   parked on a verification code sent to Gmail  (knowable only mid-form)
#
# So the pre-run gate below covers one real case in four. The other three were
# three different employers, all on Greenhouse and Ashby and
# similar, and no URL on earth predicts them. That is why `verification_wall`
# exists beside it rather than the host check being sold as the whole answer.

# Host suffixes, matched on the parsed netloc, never as a substring of the whole
# URL. `"google.com" in url` is true of
# `greenhouse.io/track?ref=https://google.com/...` and of any posting that
# happens to link to Google, and this repo has lost three separate afternoons to
# substring matching on a semantic category.
GOOGLE_APPLY_HOSTS = ("google.com", "withgoogle.com", "youtube.com")


def needs_google(url):
    """True when this posting is itself a Google property.

    Deliberately narrow. It answers "is the application form served by Google",
    not "might this form offer a Google sign-in button", because the second
    question has no reliable answer from a URL and a gate that guesses it would
    refuse runs on forms that never needed an account.
    """
    from urllib.parse import urlparse
    try:
        host = (urlparse(str(url or "")).netloc or "").lower()
    except ValueError:
        return False
    host = host.split("@")[-1].split(":")[0]
    if not host:
        return False
    return any(host == h or host.endswith("." + h) for h in GOOGLE_APPLY_HOSTS)


# The mid-form half. An employer mails a code and the agent has to read it,
# which needs the mailbox the code went to. Word-bounded, and measured: it
# matches the three real parks named above and none of the other 85.
_VERIFICATION = re.compile(
    r"\b(?:verification|confirmation)\s+code\b"
    r"|\bverify\s+your\s+e-?mail\b"
    r"|\bcode\s+(?:was\s+)?sent\s+to\b"
    r"|\bone[-\s]time\s+(?:code|password|pin)\b",
    re.I)
_MAILBOX = re.compile(r"\b(?:g-?mail|google\s+account)\b|@gmail\.com\b", re.I)


def verification_wall(text):
    """True when a blocker is 'we mailed a code to the user's Google mailbox'.

    Both halves are required. A verification code sent to an address Jobby
    cannot read is a genuine blocker for the user to clear; the same code sent
    to a mailbox the browser could open, in a browser that is signed out, is
    Jobby's own fault and must be classified as one.
    """
    blob = str(text or "")
    return bool(_VERIFICATION.search(blob) and _MAILBOX.search(blob))


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--require", action="store_true",
                    help="exit 1 when no slot is signed in (the first-run gate)")
    args = ap.parse_args(argv)
    states = fleet()
    if args.json:
        print(json.dumps({"fleet": states, "anySignedIn": any(s["signedIn"] for s in states),
                          "signedOut": [s["slot"] for s in states if not s["signedIn"]]},
                         indent=2))
    else:
        for s in states:
            mark = "signed in " if s["signedIn"] else "SIGNED OUT"
            print(f"  {s['slot']:<9} {mark}  {s['cookies']} of "
                  f"{len(GOOGLE_AUTH_COOKIES)} Google session cookies")
    if args.require and not any(s["signedIn"] for s in states):
        print("\nNo slot browser is signed in to Google. Applications that need an\n"
              "account cannot be filled. Sign in once:\n\n"
              "  python3 scripts/browser.py --login https://accounts.google.com/signin\n"
              "  python3 scripts/browser.py --stop\n"
              "  python3 scripts/browser.py --clone-logins\n", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
