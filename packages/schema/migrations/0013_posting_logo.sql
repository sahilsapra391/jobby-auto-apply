-- The employer mark a posting resolves to, derived once and stored.
--
-- **`company_logos.proxy_url` is the one implementation and it is Python.**
-- The board runs it at render time; v2 renders in Node, so it either
-- re-implements the derivation or reads the answer. Re-implementing it is what
-- `LogoContractTests` exists to prevent -- it already pins the Python plan
-- against the board's own JavaScript half, and a third copy in TypeScript is
-- the copy that drifts.
--
-- Measured 2026-09-02 over the 18,926 open Found rows: `employers.domain` is
-- present for 8,816 of them, 46.6%. For the 9,246 with neither a domain nor a
-- feed logo, `logo_plan` returns a usable derivation for 399 of a 400-row
-- sample. So roughly half the lane's marks depend on this and v2 was drawing a
-- monogram for every one of them, against 891 of 906 resolved on the board it
-- replaces.
--
-- `logo_src` is a trusted feed URL when the record carries one; `logo_domain`
-- is the employer domain, known or derived; `logo_alt` says the domain was a
-- guess, which the route reads as "try the other TLDs of this label". Exactly
-- the three things `proxy_url` decides between, stored rather than recomputed,
-- so the route's URL shape stays the route's business.
--
-- This also collapses the `logo_plan` split that `docs/plans/phase2-live-board.md`
-- lists as outstanding. That split exists because the Python board's Found
-- payload has about 21 bytes a job to spare and a domain string does not fit.
-- v2 pages from Postgres and has no such cap, so the digit and the client-side
-- derivation buy nothing here.

alter table postings
  add column if not exists logo_src    text,
  add column if not exists logo_domain text,
  add column if not exists logo_alt    boolean;
