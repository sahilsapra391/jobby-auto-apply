# ADR 0009: R2 stays the artifact store

- Status: accepted
- Date: 2026-08-29
- Source: §2 and §8, `docs/plans/jobby-v2-rebuild-plan.md`

## Context

Generated resumes and cover letters, cached job descriptions and per-application
answer records already live in a private R2 bucket, keyed
`u/{userId}/app/{applicationId}/{kind}.{ext}`.

## Decision

Unchanged. It works, it is cheap, and it is already the only copy.

## Rejected

Nothing. There is no reason to move a working store during a rebuild.

## Consequences

The key layout was designed for this. `u/account-1/` today means multi-tenant is
the same layout with a different id rather than a migration, and a deletion
request stays one prefix sweep. Phase 5 changes the prefix to
`t/<account_id>/` and nothing else.

Measured 2026-08-25: 621 objects, 31.1 MB, 287 attributed across 120
applications, 334 unfiled. The unfiled ones are working drafts from before the
naming contract existed, and every document an employer actually received is
attributed.

Two properties to carry forward rather than rediscover. The function reads the
object and returns the bytes; it does not redirect. Proxying is one round trip
where a presigned redirect is two, measured at 104 ms against 122 ms for a
119 KB resume, and no signed URL reaches the browser, so none lands in history
or in a shared link. And local files cannot be abolished: the form agent hands a
PDF to a file chooser by path, pinned by device, inode, size and hash, so R2
cannot be the source at submit time. What can be true, and is, is that nothing
is retained locally after an application reaches a terminal outcome.
