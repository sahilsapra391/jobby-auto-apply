# Schema migrations

    python3 packages/schema/migrate.py status
    python3 packages/schema/migrate.py plan
    python3 packages/schema/migrate.py apply

Migrations are `packages/schema/migrations/NNNN_name.sql`, four digits, applied
in order, each inside one transaction together with its own ledger row in
`schema_migrations`. The runner is `packages/schema/migrate.py`.

## Expand, then contract

**Never ship a destructive migration in the same release as the code that stops
using the column.** A runner in the field may be a version behind for minutes,
and during those minutes it is reading a column that no longer exists.

    Release N     add the new column, write both, read the old one
    Release N+1   read the new one
    Release N+2   drop the old one

Three releases, and the gap between them is however long it takes for every
reader to be on the new code. `min_supported` in the runner's update handshake
is the enforcement point when "a few minutes" is not good enough: a runner
older than it refuses new leases rather than working against a schema it does
not understand.

The migration is the only surface with no rollback. Vercel promotes a previous
deployment, `wrangler rollback` and `flyctl releases rollback` exist, the device
runner takes a manifest pointing at the previous version — and a dropped column
is gone. That asymmetry is the whole argument for expand-then-contract, and it
is why the runner refuses the destructive half by default rather than trusting
the policy to be remembered.

## What counts as destructive

Refused unless declared:

| Statement | Why |
|---|---|
| `drop table` / `schema` / `database` / `index` / `view` / `sequence` / `type` / `function` / `trigger` / `extension` | data or a shape a live reader depends on |
| `alter table ... drop column` | same |
| `alter table ... drop constraint` | the guarantee other code relies on |
| `alter column ... drop not null` | a reader may branch on the absence of nulls |
| `alter column ... type` | rewrites the column and can truncate silently |
| `rename to` / `rename column` | a drop and an add at once, to every reader still on the old name |
| `truncate` | data |
| `delete from` with no `where` | data |

Not refused: `create`, `add column`, `create index`, `alter column ... drop
default`. Dropping a default changes no stored row, and it is the ordinary
expand step of making a backfilled column required.

The scanner strips comments, string literals and dollar-quoted bodies before
matching, and matches on word boundaries. `dropped_at` is a column name and
`-- we drop this in N+2` is a note. Both directions are tested in
`tests/test_migration_guards.py`, and the tests were verified by disabling each
guard and confirming they fail — one of them survived the first two attempts
and was rewritten twice before it bound.

## Writing the contract half

When the expand half is live everywhere and the old column is genuinely unread:

```sql
-- jobby:contract release 2.4.0 stopped reading jobs.legacy_blob on 2026-09-14
alter table jobs drop column legacy_blob;
```

    python3 packages/schema/migrate.py apply --allow-contract

Both halves are required. The declaration is what a reviewer sees in the diff;
the flag is what stops a contract migration riding along unnoticed inside an
ordinary deploy. A declaration with no reason is refused, because the reason is
where you say which release stopped reading it — and that is the fact the next
person needs in order to check the expand half actually shipped.

A declaration inside a string literal does not count. Only a real comment does.

## Rules the runner enforces, and why each one is not paranoia

**An applied migration is history.** Its SHA-256 is stored, and editing the file
afterwards is refused. Otherwise the repository and the database disagree about
what the schema is, and nothing says so until a fresh deployment builds a
different schema from the same commit.

**A recorded migration missing from disk is refused.** Deleting the file makes
the schema unreproducible. Restore it rather than deleting the row.

**A migration numbered below one already applied is refused.** Two branches each
add "the next" migration; one merges, the other rebases badly, and 0002 arrives
after 0003 has run. Applying it gives a schema no fresh database will ever
reproduce.

**Nothing half-applies.** The DDL and the ledger row share one transaction, so a
failure leaves neither the change nor a record claiming it succeeded. Postgres
gives this away because its DDL is transactional — but only to a runner that
does not commit the change and then record it.

**One runner at a time.** A session-level advisory lock, released in a `finally`
so a failed migration cannot wedge every later deploy.

## Never through a connection pooler

`migrate.py` rewrites a pooled URL to the direct endpoint and says so on stderr.
Neon names its pooled endpoint `<endpoint>-pooler.<region>...`; PgBouncer sets
`pgbouncer=true`.

This is not tidiness. A pooler in transaction mode hands each transaction
whatever backend session is free, and two things here need a session:

- `pg_advisory_lock` is session-scoped. Taken through a pooler it is held on
  one backend while the migrations run on another, so the lock appears to work
  and excludes nobody. That failure only appears when two deploys overlap.
- A `set` outside a transaction lands on a session the next statement may not
  get.

The second one was measured rather than reasoned about. An earlier version of
`tests/test_migrations_live.py` isolated itself with `set search_path`, passed
all 17 of its tests, and created `a`, `b` and `schema_migrations` in `public` of
the live Neon database holding 15,400 real jobs. Neon's pooler refuses the
`options=-c search_path=...` form outright, and its error names the fix: use an
unpooled connection.

## The unmanaged tables

Seven tables in the Neon database predate this runner: `answers`, `companies`,
`history`, `jobs`, `passed`, `roles`, `sessions`. They were created by
`scripts/migrate_to_postgres.py`, they hold real data (15,400 jobs, 190 answers,
88 sessions as of 2026-08-29), and **no migration adopts them.**

That is deliberate rather than an omission. §3 of the rebuild plan replaces all
of them: a normalised `jobs` with a uuid primary key and a generated `tsvector`,
a separate `employers`, and `job_decisions` where the absence of a row means
Found. Writing a baseline migration for tables that Phase 1 deletes would be
writing history for a schema that never existed anywhere it mattered.

The consequence to know about: a fresh database built from `migrations/` alone
does not have those seven tables, and the Neon database has them alongside the
managed ones. The two converge in Phase 1, when the §3 schema lands as migration
0002 and the old tables become a contract migration once nothing reads them.

Nothing in `0001` references any of them, which is what makes it safe to apply
to both.

## Where it runs

CI runs the migration tests against a throwaway `postgres:18` service container,
so no secret is needed and every branch gets its own database. `apply` against
Neon is the `migrate` job in the deploy pipeline (§7), ordered before the worker
and scanner deploys and after the tests.
