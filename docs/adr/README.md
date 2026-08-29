# Architecture decision records

One record per decision in §2 of `docs/plans/jobby-v2-rebuild-plan.md`. They
exist so the next agent does not relitigate a settled question, and so that
when one of them is genuinely wrong there is somewhere to say why rather than
quietly choosing something else.

| # | Decision | Status |
|---|---|---|
| [0001](0001-neon-postgres-is-the-system-of-record.md) | Neon Postgres is the system of record | accepted |
| [0002](0002-durable-objects-carry-realtime-and-dispatch.md) | A Durable Object carries realtime and device dispatch | accepted |
| [0003](0003-scan-workers-are-python-containers.md) | Scan workers are Python containers, not rewritten Workers | accepted |
| [0004](0004-the-web-app-stays-on-vercel.md) | The web app stays on Vercel, git-deployed | accepted, with a correction |
| [0005](0005-two-api-hosts-is-the-price-of-durable-objects.md) | Two API hosts is the price of Durable Objects | accepted |
| [0006](0006-one-global-job-corpus.md) | One global job corpus, per-account decisions | accepted |
| [0007](0007-match-scores-are-computed-lazily.md) | Match scores are computed lazily on the rendered page | accepted |
| [0008](0008-one-device-process.md) | One device process, not three | accepted |
| [0009](0009-r2-stays-the-artifact-store.md) | R2 stays the artifact store | accepted |

An ADR is not a plan. It records what was chosen and what was turned down, so a
later reader can tell a decision from an accident. Where a record disagrees with
the plan it says so in the open, because a silent divergence between a plan and
its records is worse than either being wrong.
