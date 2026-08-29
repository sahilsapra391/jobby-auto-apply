# apps/scanner — the sourcing container (Phase 3)

Empty until Phase 3. The existing connector and ingest code moves here
unchanged, with a new entrypoint: §2 rejected rewriting every connector in
TypeScript for Workers, and §13 says the plan "does not rewrite the connectors
in another language."

Two things Phase 3 must establish before this directory is worth anything.

**A connector that errors must never read as a board returning nothing.** The
closure sweep marks a posting closed when it was in the last sweep and is absent
from this one, so one bad sweep can close an employer's whole catalogue. §5
requires a `sweep_runs` row with a status, and closure reading only from sweeps
marked complete. That is the highest-consequence failure in the system.

**Not every connector may move.** Anything that works by rendering an employer's
careers page in a browser stays on the device, which has the residential IP and
the logged-in profile. Phase 3 audits each one and lists the survivors in
`docs/connectors.md`. §12 question 5 defaults to never moving them.
