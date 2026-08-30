#!/bin/sh
# The scheduled parity check.
#
#   */30 * * * *  /path/to/scripts/parity_cron.sh
#
# Appends one line per run to ~/.job-application/logs/parity.jsonl, which is
# what the seven-day clock is counted from. `verify_parity` already refuses to
# exit 0 without a real comparison, so a line saying ok=true is evidence that
# both stores were read and agreed.
#
# Silence is not success: a run that cannot conclude exits non-zero like any
# other failure, and its reason is written down rather than reduced to a
# boolean.
set -u
ROOT="${JOB_APPLICATION_DIR:-$HOME/.job-application}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/logs/parity.jsonl"
mkdir -p "$ROOT/logs"

REPORT="$(mktemp)"
# The output goes to a file rather than a variable piped into a heredoc: a
# `python3 - <<'PY'` reads the script from stdin, so anything else sent there
# is discarded. The first version of this logged every run as inconclusive
# with an empty reason for exactly that.
(cd "$REPO" && python3 scripts/verify_parity.py --json) > "$REPORT" 2>&1
CODE=$?

python3 "$REPO/scripts/parity_log.py" "$REPORT" "$CODE" >> "$LOG"
rm -f "$REPORT"
exit $CODE
