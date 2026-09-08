# The sourcing worker: the same Python the laptop runs, with a different
# entrypoint. `scripts/ingest.py` is not in the image and neither is the pool;
# this fetches and stages, and the device merges. See scripts/sweep_worker.py.
#
# 3.12 because that is what CI builds on and what Vercel runs. A sourcing path
# that only works on the laptop's 3.13 is a difference nobody would find until
# a connector behaved differently in production.
FROM python:3.12-slim

# **psycopg is the only third-party dependency in the whole sourcing closure.**
# Measured by walking the import graph from `sweep_worker`: the connectors are
# stdlib plus `base`, and the only non-stdlib imports anywhere in reach are
# psycopg (for the staging write) and pymupdf, which `ats_match` imports lazily
# inside a function that this path never calls. Verified by importing the
# entrypoint with pymupdf blocked from `sys.meta_path`.
#
# `[binary]` so there is no libpq build step and no compiler in the image.
RUN pip install --no-cache-dir "psycopg[binary]==3.3.4"

WORKDIR /app
COPY scripts/ /app/scripts/
COPY packages/ /app/packages/
# `write_path_coverage` reads this at import and prints a FileNotFoundError
# without it. The hook is inert here -- the container makes no class A pool
# write -- but a diagnostic that fires on every run is noise that hides the
# next real one, and the file is 4 KB.
COPY docs/write-paths.md /app/docs/write-paths.md

# Ephemeral by design. A Fly machine is not a device and must not accumulate a
# store; what has to survive a run is in Postgres. The one thing kept here is
# the HTTP validator cache, and losing it makes a run slower rather than wrong
# (`base.Fetcher` treats a damaged cache as empty, deliberately).
ENV JOB_APPLICATION_DIR=/var/lib/jobby \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    JOBBY_FETCH_PROGRESS=1
RUN mkdir -p /var/lib/jobby && chmod 700 /var/lib/jobby

# Non-root: this process makes outbound HTTP to two dozen third parties and
# parses what comes back. It needs no privilege at all.
RUN useradd --system --uid 10001 sweeper \
 && chown -R sweeper:sweeper /var/lib/jobby /app
USER sweeper

ENTRYPOINT ["python3", "/app/scripts/sweep_worker.py"]
