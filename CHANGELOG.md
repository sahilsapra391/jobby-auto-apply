# Changelog

## Unreleased

### Safety and correctness
- Page content is now explicitly data, not instructions: prompt-injection defenses for postings and forms, out-of-scope data requests (bank details, government ID) are hard stops, sensitive answers only go to fields that plainly ask for them.
- Write-ahead `submitting` event makes submission crash-safe: a session dying mid-submit can no longer cause a duplicate application. Orphans surface in `stats` (`needsVerification`) and on the dashboard.
- Answer matching hardened: fuzzy threshold raised to 0.75 ("product management" no longer matches "project management"), sensitive answers never fuzzy-match, near-misses return value-free candidates instead of a silent best guess.
- Exclusion list at intake — never applies to the current employer or listed companies.
- Dashboard sanitizes scraped URLs (http/https only) and escapes all store data.

### Accuracy and cost per application
- `answers-resolve` resolves a whole form's questions in one call; missing info is asked once, not field by field.
- Verification is read-back (DOM value vs. fill plan, string compare) instead of screenshot-eyeballing; a pre-submit manifest diffs every field.
- Staged triage: metadata gate before full-body reads; posting extractions and company briefs cached in watch entries, read once ever.
- Heavy browser work isolated per sweep/per fill so page dumps don't compound across a cycle.

### Hosting
- `scripts/publish.py` renders the board and deploys it to Vercel behind HTTP Basic Auth, overwriting one production URL per publish so the bookmark stays stable. Vercel's own Deployment Protection leaves production domains public on the Hobby plan, so the gate lives in the function and fails closed when no password is set.
- The board goes out with `noindex`, `no-store` and a `default-src 'none'` content security policy; the generated module holding application data is gitignored and owner-readable.
- Dashboard HTML now declares `<meta charset="utf-8">` and is read and written as UTF-8 explicitly. Without it, any non-ASCII in the store (accented names, em-dashes in drafted messages) rendered as mojibake wherever the server omitted a charset, including plain `file://` opens.

### Research
- Persistent sweep plan (queries × geos × channels) with per-cell last-swept stamps for incremental cycles.
- Explicit fit rubric: intake-derived hard filters, then weighted scoring with recorded reasons.
- Five-fact company brief per shortlisted role.
- Outcome events (`response`, `interview`, `offer`, `rejected`) with per-source conversion via `store.py stats`; sweep budget follows what converts.

### Resumes and cover letters
- Every application gets its own resume **and** cover letter, built by the `resume-tailor` skill from the base resume at `profile.resumePath` so the user's own layout is cloned exactly. Replaces the previous one-variant-per-role-family rule and the previous default of skipping cover letters.
- Drafting runs on Claude Fable 5, in one subagent per application, which also keeps posting text and PDF work out of the orchestrating session.
- Two checks before upload. Content: skills, numbers, dates, employers and employment titles must trace back to the base resume, the answer store, or the user, and a term that arrived only from the posting is cut. The profile title is the one field allowed to adapt, since `resume-tailor` retargets it by design; employment titles stay frozen. Format: `resume_pdf.py verify` diffs page count, margins, content extent, accent colours and font sizes against the source, and exits non-zero on drift. A failed check falls back to the base resume and says so.
- New `scripts/resume_pdf.py`: the macOS equivalent of the skill's Linux recipe. `check` reports Chrome, pymupdf and whether the resume's own fonts are installed (with the metric-compatible substitute to `brew install`); `render` drives the already-installed Chrome instead of WeasyPrint; `verify` does the geometric diff. The skill's `/mnt/user-data/` paths and `apt-get` steps do not exist on macOS.
- Store gains `resumes/` and `cover-letters/`, `0700`, one pair per application. Both filenames go on the `completed` event, as `resumeVariant` and `coverLetter`.

### Quality and trust
- `references/tailoring.md`: user's voice from writing samples, story bank, truth ledger (every claim traceable), resume variants with per-application tracking.
- Practice form fixture reproducing real ATS hazards, including a planted injection trap — first runs rehearse before touching a real application.
- 18-test unit suite for the store and dashboard.
- `meta-set`/`meta-get` for operational state (dashboard artifact URL, sweep stamps); volume caps and pacing rules.
