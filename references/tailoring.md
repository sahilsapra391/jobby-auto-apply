# Tailoring: answers that sound like the user and survive an interview

Filling forms fast is table stakes. What decides outcomes is the free text: screening answers, cover notes, referral messages. Generic AI-flavoured text is easy for a recruiter to spot and easy to reject. This file is how drafted text stays specific, true, and in the user's voice.

## Voice

At intake, ask for two or three samples of the user's own professional writing — a cover note they were happy with, a LinkedIn post, a work email that sounds like them. Record the file paths in the profile and read them once per working session, not per answer.

Draft in that register. Match their sentence length, formality, and how much they hedge. The test: could the user read the draft aloud in an interview without flinching? Words like "passionate", "keen", and "delve" fail that test for almost everyone.

## The truth ledger

Every claim in a drafted answer must trace to one of three sources: the resume, the answer store, or something the user said in conversation. No fourth source exists. If a sentence cannot be traced, it does not go in — the same rule as form fields, applied to prose.

When a question invites a claim you cannot source — "tell us about a time you led a team through ambiguity" — that is a question **for the user**, once. Get the story, store it as a confirmed answer, and it is reusable forever after.

## The story bank

Screening questions recycle the same competencies: leadership, conflict, failure, shipping under pressure, influencing without authority. During intake or the first working session, collect three to five real stories — situation, what they did, what happened — and store each as a confirmed answer tagged by competency.

Drafting a screening answer then becomes selection and voice, not invention: pick the story that fits, compress it to the question's scope, write it in their register. Three to six sentences unless the form demands more. Answer the question asked, not the question you prepared for.

## Per-application documents

**Every application gets its own resume and its own cover letter.** No exceptions, no reuse between postings. The serial document worker builds both on Your Device, and the separate form agent does not create or rewrite either file.

The watcher chooses the base resume, reduces the document identity, and sends the posting, confirmed story facts, writing samples, and base PDF through an AES-256-GCM document queue. The queue may sit in Cloudflare KV, but decryption and all agent work happen on Your Device. The document model has no application browser or file tools. It returns structured prose, then trusted worker code renders inside a private task directory. The authenticated result is installed as one complete set before form filling starts.

The job names the first live healthy provider in connection order. The document process keeps that provider until it finishes. A later form stage or retry can hand off to the next healthy account. If none pass login, usage, and exact-model checks, the job blocks. Every invocation also names its model: OpenAI uses `gpt-5.6-luna`, Anthropic uses `claude-sonnet-5`, and Gemini uses `gemini-3.7-flash`. No document task inherits a CLI default. Gemini output telemetry must name only `gemini-3.7-flash`, or the run is rejected.

### What the document pipeline guarantees, and what it does not

The fixed resume template keeps education, certifications, employers, employment titles, dates, and contact structure outside the model's editable fields. The provider supplies bounded prose overrides. `resume_build.py` rejects the wrong number of sections or experience entries, and identity comes from the reduced profile at render time. Its integrity gate also rejects exact and near-duplicate bullets, changed numeric facts or versions, lost source meaning, changed bullet counts, and rewrites that grow beyond a keyword-level edit.

Claude and OpenAI share the same logical document contract through their native structured-output flags. Codex uses `--output-schema`; its stricter required-property rules represent inherited resume fields as null, then trusted Python removes those sentinels before the common validator runs. Luna replacements must stay at or below the original text length, and its cover-letter paragraphs are capped for the verified one-page template. The low-memory Chromium profile is Linux-only because a fresh profile can hang on macOS.

The provider's exit code is not approval. After it exits, `document_worker.py` independently requires exactly four regular output files, rejects unresolved template tokens and unsafe paths, checks PDF signatures and size limits, requires `unsourcedTerms` to be zero, geometrically compares the resume with the base, and verifies the cover letter's content, identity, readability, one-page layout, and omission of the street address. The document client checks names, hashes, types, application id, and provider again, validates the whole set before installing any file, and replaces each file atomically.

Those gates do not make arbitrary prose true. A language model still rewrites next to a job description full of attractive claims, which is exactly the situation that produces drift. The base resume and confirmed story facts remain the only factual sources, and anything they do not support stays out.

### One deliberate exception to "titles are frozen"

The **profile title** at the top of the resume is *meant* to move. The skill adapts it toward the target role without copying the posting's wording, so "Product Manager" may legitimately become "Product Manager & Product Analyst" for an analyst-leaning posting.

**Employment titles inside the experience section are frozen.** If the resume says Associate Product Manager at a company, no tailored version may promote it to Product Manager. Check these two separately; conflating them is how a real inflation slips through a check that looked like it passed.

### Keyword coverage, and the honest ceiling on it

An ATS ranks partly on vocabulary overlap, so speaking the posting's language matters. Maximise it, under one constraint that does not move: **the underlying fact must already be true.**

Three cases, and only the third is forbidden:

1. **True and already worded the same.** Nothing to do.
2. **True but worded differently.** Adopt the posting's phrasing. If the base says "test coverage from 62% to 81%" and the posting says "automated quality assurance", both describe the same work, so use theirs. This is where nearly all real gains live, and it costs no honesty at all. Same story, same numbers, their vocabulary.
3. **Not true.** Cut it. A posting asking for Kubernetes and Terraform does not make those appear on a resume that never had them.

**On a 95% target.** Coverage against a posting's *entire* keyword set is not truthfully reachable, because postings list requirements the candidate does not have. Measured against the Ridgeline posting, the tailored resume scored 57%, and the missing terms included Kubernetes and Terraform. Forcing those to 95% would mean claiming both.

So the target is **95% of the keywords the candidate can truthfully claim**, and the residue is reported rather than closed. That residue is useful on its own: a posting where half the unmatched keywords are real requirements he lacks is a fit signal, not a resume problem.

### Weave the posting's vocabulary in, section by section

Case 2 above is where the gains are, and it is worth being concrete about where to spend them. Run this **before writing anything**:

```bash
python3 scripts/ats_match.py --suggest --jd posting.txt --base <base>.pdf \
  --facts ~/.job-application/story-facts.txt
```

It splits the posting's vocabulary in two. **`rewritable`** are terms the posting uses that the base resume or story bank already supports under a different name, each with the evidence that licenses it: `{"term": "experimentation", "evidence": "launchdarkly"}`. Those are a rewrite, not a claim. **`notClaimable`** is the residue, and nothing from it goes anywhere near the document.

Then rewrite these three, aggressively, using only `rewritable`:

**Areas of Expertise.** This is a keyword list, so treat it as one. Re-pick it for this posting out of what he has actually done, use the posting's exact wording for each, and order it the way the posting orders its requirements. An entry the posting has no interest in is taking a slot from one it cares about; drop it. Nothing here is a claim about seniority or scope, so the only limit is that each entry has evidence behind it.

**Technical Proficiencies.** Same treatment, tighter limit. Tools and platforms are checkable, so a name goes in only if he has genuinely used it. What does move: the posting's *name* for a thing he uses, its grouping, and its order. "Postgres" becomes "relational databases" if that is how the posting asks, and back again if it names the product.

**Professional Experience bullets.** The numbers, employers, dates and titles are frozen. The verbs and nouns around them are not. If the base says "cut latency 68% in a monolith-to-microservices migration" and the posting talks about "service decomposition" and "p99", the same bullet becomes "service decomposition that cut p99 latency 68%". Same work, same number, their words. Lead each bullet with the phrasing the posting would search for.

**The profile title at the top** adapts toward the role, as documented above. Employment titles never do.

### Then prove nothing was invented

```bash
python3 scripts/ats_match.py --jd posting.txt --resume <tailored>.pdf \
  --base <base>.pdf --facts ~/.job-application/story-facts.txt
```

It reports `coverage`, `missing`, and `unsourcedTerms`. **`unsourcedTerms` is the number that matters, and it must be zero.** It lists terms appearing in both the posting and the tailored resume but in neither the base resume nor the story bank, which is exactly the shape of a keyword lifted off the posting. A high coverage with a non-empty unsourced list is not a good resume; it is a resume that started inventing.

The aggressive pass and that check are a pair. Push the vocabulary as hard as the evidence allows, then let the check tell you where you crossed. If a term you want is genuinely unsourced and you still want it, the fix is to add the real story to the story bank, not to add the word to the resume.
