# Employer identity, and the resolution step Phase 3 needs

One employer, one row. The corpus is shared across accounts (ADR 0006), so an
employer split across two rows splits its postings too, and a board grouped by
employer shows the same company twice with half its jobs under each.

Today identity is `employers.norm_name`, which is
`identity.norm_company(canonical_name)`: lowercased, punctuation collapsed,
legal suffixes stripped, so `Acme Inc` and `ACME, Inc.` are one row. That is
enough for the backfill and is not enough for Phase 3.

## What the backfill measured

1,934 distinct normalised employers over 20,103 records on 2026-08-29. Of
those, 316 carry a website. Exactly one domain is claimed by two of them:

    sharegate.com  <-  sharegate
                   <-  sharegate migrate chezworkleap

Those are one company. The normaliser cannot merge them because the second name
contains real words the first does not, and no amount of suffix-stripping turns
one into the other.

`employers.domain` is unique per §3, so the two cannot both hold it. The
backfill gives it to the lexicographically first claimant, deterministically so
that a re-run does not move it, and sets `merged_into` on the loser
(migration 0003). Both rows survive with their postings.

## Why `merged_into` exists now and the resolver does not

Without it the loser has a null domain and no record of why, so nothing will
ever match it to the winner again and the next sweep re-creates the split. The
column costs one migration and preserves the one signal the backfill is
confident about.

Nothing reads it. That is deliberate: following the pointer changes which
employer a posting appears under, and doing that on the strength of a shared
domain alone would merge companies that share a parent domain but are not the
same employer. A resolver has to weigh more than one signal and has to be
reversible, and neither is Phase 1 work.

**Null means "no known duplicate", not "checked and distinct".** A resolver
must not read a null as evidence.

## What Phase 3 has to build

§5's connector upsert is keyed on `(source, board_slug, external_id)` and takes
the employer from whatever the board's API returns as a company name. That name
arrives fresh from the ATS every sweep, and it is not the name the backfill
normalised. So the upsert needs a resolution step in front of it:

1. Normalise the incoming name.
2. Look it up in `employers.norm_name`.
3. On a miss, try the employer domain if the feed supplied one, following
   `merged_into` to the surviving row.
4. On a miss, create a row.

Step 3 is the part that does not exist. Without it, every sweep creates a new
employer for every spelling variation an ATS emits, and the 1,934 rows grow
without bound while the postings scatter across them.

Two rules for whoever builds it, both from what this repository has already
paid for elsewhere:

**A wrong merge is worse than a missed one.** Merging two employers files one
company's postings under another's name, which is the stranger's-logo failure
`company_logos` is built to avoid, at the scale of a whole board lane. A missed
merge shows the same company twice, which is untidy and correct.

**Follow the chain, and bound it.** `merged_into` can point at a row that is
itself merged. The migration forbids self-reference, which is a cycle of length
one; longer cycles are not forbidden by the schema and a resolver that walks
the pointer without a visit set will loop.

## The sentinel is not an employer

`(unknown employer)` holds the postings whose feed named no company: three on
2026-08-29, all careerjet, each with a real title and apply URL. It is a filing
location, not a company.

It never carries a domain or a logo, and it is excluded from employer counts
and from any grouping a person reads, because a board lane headed "Unknown
employer (3)" invites somebody to treat it as a company with three openings.
`backfill.UNKNOWN_EMPLOYER` is the one spelling of it; the parentheses make it
unreachable by `norm_company`, which emits only lowercase alphanumerics and
spaces, so no real employer can ever normalise onto the bucket.
