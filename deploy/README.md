# deploy/

Device-side helpers, not a deployment. The board itself is `apps/web`.

The name is a leftover: this directory used to hold the original Python board's
Vercel project as well. That board is gone. What remains is what the device on
the user's Mac runs, plus two container and recovery recipes:

    queue.mjs, kvqueue.mjs, kv.mjs   the click queue in Cloudflare KV, which the
                                     watcher polls and claims from
    document-jobs.mjs, kvjobs.mjs    the encrypted document-job transport
    local-status.mjs                 publishes provider status for Settings
    worker-status.mjs                the document worker's status record
    owner-wording.mjs                publishes the owner-only resume wording
    device_keys.mjs                  the KV keys the device writes, read by v2
    profile_edits.mjs                the rules a Profile edit has to satisfy
    r2.mjs                           the JavaScript R2 signer and reader
    sourcing/                        the sourcing container (Fly)
    oci/                             a retired remote document worker, kept only
                                     as a recovery path

The scripts in `scripts/` run these with `node` from this directory, and the
launchd services find them by absolute path, so renaming the directory is a
coordinated deploy rather than a refactor. `kv.mjs`, `kvqueue.mjs`,
`profile_edits.mjs`, `device_keys.mjs` and `r2.mjs` are also copied into
`apps/web/app/_ui/` by `scripts/sync_v2_styles.py`; edit them here.

Credentials come from `deploy/.env.local` and `~/.job-application/config.env`
(`toolpath.queue_env`), neither of which is ever committed.
