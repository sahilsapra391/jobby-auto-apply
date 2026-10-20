# apps/web: the board

Built in Phase 2 of `docs/plans/jobby-v2-rebuild-plan.md`, and serving the
board since. Next.js on Vercel, deployed from `main` on merge. It reads Postgres
(`JOBBY_DATABASE_URL`) from server components, writes clicks as rows and as
entries in the device's KV work queue, and proxies generated documents out of
the private R2 bucket. The environment it needs is listed in the repository
README under "The board".

Most of `app/` is ordinary source. The files named `*.generated.ts` and
`_ui/*.generated.mjs` are not: `scripts/sync_v2_styles.py` copies each from the
one source the device also reads, and CI fails when a copy drifts. Edit the
source and re-run the script.

`board.css`, `chrome.ts`, `rubric.ts`, `icons.ts`, `lane_chrome.ts`, `search.ts`
and `public/` began as copies of the original Python board's and are hand-owned
now.

`tools/check_deployed_shape.mjs` builds the app with standalone output, removes
the source tree, serves the traced bundle and crawls it, so a file the server
reads that the tracer never carried fails CI instead of production.
