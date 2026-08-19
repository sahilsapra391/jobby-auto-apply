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

**Every application gets its own resume and its own cover letter.** No exceptions, no reuse between postings. Both are built by the **`resume-tailor` skill**, which clones the source resume's layout exactly and changes only the words.

Drafting runs on **Claude Fable 5** (`claude-fable-5`). Delegate it to a subagent pinned to that model, one subagent per application, which also keeps the posting text and PDF work out of the orchestrating session's context. The subagent returns the two file paths and a list of what it changed; it does not return page dumps.

### What resume-tailor guarantees, and what it does not

The skill freezes the things most likely to be quietly falsified: education, certifications, name, address, phone, email and hyperlinks are copied verbatim, and it is instructed never to invent accomplishments or metrics. Its stated method is to keep each bullet's existing accomplishment as the lead and weave job-description language into it, rather than rewriting the bullet around the posting.

It does not guarantee the result. It is a language model rewriting prose next to a job description full of attractive claims, which is exactly the situation that produces drift. So verify every output before it is uploaded.

### One deliberate exception to "titles are frozen"

The **profile title** at the top of the resume is *meant* to move. The skill adapts it toward the target role without copying the posting's wording, so "Product Manager" may legitimately become "Product Manager & Product Analyst" for an analyst-leaning posting.

**Employment titles inside the experience section are frozen.** If the resume says Associate Product Manager at a company, no tailored version may promote it to Product Manager. Check these two separately; conflating them is how a real inflation slips through a check that looked like it passed.

### Verifying before upload

Diff the tailored resume against the base on content, not impression:

- Every skill, tool and technology named must appear in the base resume, the answer store, or something the user said. A term that arrived from the posting and nowhere else gets cut.
- Employment titles, employers, and all dates match the base exactly.
- Every number (team size, revenue, percentage, user count) matches the base exactly. Numbers are the claims most likely to be "improved" and the easiest for an interviewer to probe.
- Seniority verbs do not drift. "Led" and "contributed to" are different claims.
- Education and certifications are byte-identical to the base.

Then verify the *format*, which is the other half of what this skill promises:

```bash
python3 scripts/resume_pdf.py verify --pdf <tailored>.pdf --against <base>.pdf
```

It exits non-zero and names the differences: page count, margins, content extent, a missing accent colour, a font size with no counterpart. Fix and re-render until it passes.

Anything that fails the content check goes back to the base wording. If a posting's core requirement genuinely is not in the user's history, that is a fit problem to report, not a resume problem to write around.

### Where the files go

The skill's own docs assume a Linux sandbox: `/mnt/user-data/uploads/` for input, `/mnt/user-data/outputs/` for output, `apt-get` and WeasyPrint for rendering. None of that exists on a Mac. Locally:

- **Source** is the profile's `resumePath`, which is never edited.
- **Rendering** goes through `scripts/resume_pdf.py render`, which drives the Chrome already installed for the browser extension. Run `scripts/resume_pdf.py check --resume <base>.pdf` once before the first application: it reports missing fonts and the exact `brew install --cask` for a metric-compatible substitute. Substituting a font with different metrics reflows the page, which is how a "pixel-faithful" clone silently stops being one.
- **Resumes** land in `~/.job-application/resumes/`, cover letters in `~/.job-application/cover-letters/`, keeping the skill's own naming: `<First>_<Last>_<Company-or-Role>_Resume.pdf`. That name is what an interviewer sees in their inbox, so it stays human-readable rather than becoming an application id.
- **Record both** on the `completed` event, as `resumeVariant` and `coverLetter`. Before a call, the user must be able to pull up exactly the two documents that employer received.

### Cover letters

One per application, same source pair, same verification. The skill mirrors the resume's typography and accent colour, so the two documents read as a set.

Keep it short: three paragraphs, one of which uses a specific fact from the company brief (`sourcing.md`) that a template could not contain. If the letter would read the same with the company name swapped, it is not done. Ground every claim in the resume and the answer store, in the user's voice per the rules above.

## Referral messages

The company brief and the story bank feed these too. A referral message earns a reply when it is specific about the role, honest about the ask, and short enough to answer from a phone. Two or three sentences: the role, one line on why this person fits it, the ask. The connection's time is the scarce resource; respect it and the reply rate follows.
