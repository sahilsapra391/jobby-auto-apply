# packages/schema

The database schema, its migrations, and the per-account feature flags every
cutover in the rebuild is gated on. Filled in Phase 0; this is the one package
that is not a scaffold.

    migrate.py                the runner: discovery, expand-only, checksums, lock
    migrations/NNNN_name.sql  applied in order, one transaction each
    flags.py                  the flag registry and its reader

Start with [`docs/migrations.md`](../../docs/migrations.md). It carries the
expand-then-contract policy, what counts as destructive and why, how to write
the contract half, and the reason nothing here may run through a connection
pooler.

Two commands cover ordinary use:

```
python3 packages/schema/migrate.py status
python3 packages/schema/migrate.py apply
```

`flags.py` is executable and prints the registry:

```
python3 packages/schema/flags.py
```

Tests are `tests/test_migration_guards.py` (no database, the expand-only
scanner), `tests/test_migrations_live.py` and `tests/test_account_flags.py`
(a real Postgres, via `tests/pgtest.py`).

## The shared types are not here yet

§9 describes this package as "shared types + migrations". The migrations are
here; the types are not, and that is deliberate rather than forgotten. Shared
types mean TypeScript shared between `apps/web` and `apps/worker`, and neither
exists before Phase 2. Writing them now would mean guessing at the shape of a
schema Phase 1 has not landed, in a language nothing in the repository yet
compiles, with no consumer to notice when they drifted from the SQL.

They land in Phase 2, generated from or checked against the migrations rather
than hand-written beside them. A type that can disagree with the column is the
same failure as a renderer that can disagree with `scripts/`, and that one is
already covered by a CI check for exactly this reason.
