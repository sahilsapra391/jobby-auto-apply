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

Pick the base resume first, then draft. Drafting runs on **Claude Opus 5** (`claude-opus-5`). Delegate it to a subagent pinned to that model, one subagent per application, which also keeps the posting text and PDF work out of the orchestrating session's context. The subagent returns the two file paths and a list of what it changed; it does not return page dumps.

### What resume-tailor guarantees, and what it does not

The skill freezes the things most likely to be quietly falsified: education, certifications, name, address, phone, email and hyperlinks are copied verbatim, and it is instructed never to invent accomplishments or metrics. Its stated method is to keep each bullet's existing accomplishment as the lead and weave job-description language into it, rather than rewriting the bullet around the posting.

It does not guarantee the result. It is a language model rewriting prose next to a job description full of attractive claims, which is exactly the situation that produces drift. So verify every output before it is uploaded.

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

Measure it:

```bash
python3 scripts/ats_match.py --jd posting.txt --resume <tailored>.pdf \
  --base <base>.pdf --facts ~/.job-application/story-facts.txt
```

It reports `coverage`, `missing`, and `unsourcedTerms`. **`unsourcedTerms` is the number that matters.** It lists terms appearing in both the posting and the tailored resume but in neither the base resume nor the story bank, which is exactly the shape of a keyword lifted off the posting. Drive it to zero. A high coverage with a non-empty unsourced list is not a good resume; it is a resume that started inventing.

### Optional sections, included when the posting earns them

The story bank holds more than the base resume shows. Add a bank entry to **Entrepreneurial Ventures** when the posting makes it relevant, and leave it out otherwise rather than padding every resume with everything.

- **Skeptic**, the adversarial options-backtesting engine, goes in for finance, fintech, trading, quantitative or risk-flavoured postings, and for any posting that prizes frugality or self-directed building. It shows domain proximity to markets plus an engineering instinct for disconfirming your own results. Keep the framing on discipline and truth-seeking; no options mechanics, no profit-and-loss talk.
- Judge each optional addition against the posting, and say in the change list why it was included or dropped.

### One build

**The first pair produced for an application is the pair that gets sent**, provided it passes the gates below. Do not rebuild a passing document for polish, for consistency with how another application came out, or for a second opinion. Show it to the user and move on.

Rebuild only when an objective gate fails, and say which one:

- `resume_pdf.py verify` reports any difference against the regional base
- `ats_match.py` reports a non-empty `unsourcedTerms` list
- a frozen field (employment title, date, number, employer, education, certification) does not match the base
- the wrong regional base or identity block was used
- the rendered PDF is corrupted (duplicated text, spliced fragments, missing sections)

Iterating *inside* one build, rendering and re-rendering until the gates pass, is part of building. That is expected and unlimited. What is ruled out is coming back to a document that already passed and doing it again.

The reason the rule bites: every rebuild is another sampling of a language model over the same source, and repeated sampling drifts. A passing document reworked is at least as likely to get worse as better, and it costs a full cycle to find out.

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

- **Source** is whichever base resume matches the posting's country, from `profile.resumes`. Never edited.
- **The identity block outranks the base for contact details.** Name, phone, location, email and profile links come from `profile.resumes[].identity` and the answer store, not from the base document. The GitHub URL (`https://github.com/sahilsapra391`) is user-provided and belongs in the header even though the base resume predates it, as a real hyperlink rather than unlinked text. Everything else about hyperlinks stays frozen: never invent a URL that the user has not given.
- **The resume header is the base's header in FORM, verbatim.** Copy the contact line exactly as the base resume has it (city and state, not a street address). The full street address in `profile.resumes[].identity.fullAddress` is for **application form fields only**, never for the resume or cover letter header. Putting it in the header overflows the line, wraps it to two, and silently reflows every line below; `resume_pdf.py verify` now catches that, but the rule exists so it never gets built in the first place.
- **Identity follows the base, not the person.** Each base carries its own `identity` block: phone and location. An Indian posting gets the Indian number and address, a US posting the US pair. This applies to the cover letter *and to the application form's own contact fields*, which is the easier one to get wrong: a US phone number typed into an Indian employer's form is a silent mis-fill that no later check catches.
- **Pick the base from the posting's real location**, the one confirmed by reading the body, not the location line on the listing. Postings routinely list one city and require another.
- **Rendering** goes through `scripts/resume_pdf.py render`, which drives the Chrome already installed for the browser extension. Run `scripts/resume_pdf.py check --resume <base>.pdf` once before the first application: it reports missing fonts and the exact `brew install --cask` for a metric-compatible substitute. Substituting a font with different metrics reflows the page, which is how a "pixel-faithful" clone silently stops being one.
- **Both documents go to `applications/<Company>/`** in the skill directory, one folder per company, gitignored. Name them `Sahil_Sapra_<Company>_Resume.pdf` and `..._CoverLetter.pdf`; when the company has more than one open role, insert the role (`Sahil_Sapra_Sierra_AgentStudio_Resume.pdf`) or the second application overwrites the first. The name is what a recruiter sees in their inbox, so it stays human-readable rather than becoming an application id. `scripts/file_docs.py --check` audits which applications are missing a document.
- **Record both** on the `completed` event, as `resumeVariant` and `coverLetter`. Before a call, the user must be able to pull up exactly the two documents that employer received.

### Cover letters

One per application, same source pair, same verification. The skill mirrors the resume's typography and accent colour, so the two documents read as a set.

Keep it short: three paragraphs, one of which uses a specific fact from the company brief (`sourcing.md`) that a template could not contain. If the letter would read the same with the company name swapped, it is not done. Ground every claim in the resume and the answer store, in the user's voice per the rules above.

## Screening questions: his voice, from real samples

`~/.job-application/voice/answers_sample.txt` holds 25 answers he actually submitted. Read it before drafting; it is the ground truth for register, and it beats any description of his style including this one.

**Six sentences at most. Every answer, no exceptions.** Three or four for a simple why-us. Even a question that invites depth ("go five levels deep", "describe the architecture") gets six: compress it rather than run long. Some of the samples in that file are longer than this, because he wrote them before setting the rule. Match their **voice**, not their length.

Patterns that recur in his real answers, worth keeping:

- **Opens by connecting their problem to his own experience**, not by praising them. Exa: "I am obsessed with Exa's mission because I've felt the exact pain point you are solving." Backpack: "Most of my work so far has lived right where Backpack is building."
- **"Honestly," as a pivot** into the real reason. "Honestly, the role itself is what got me." "Honestly, it pushed me to be more intentional about what I want next."
- **Names the gap before they find it.** "I know I do not have the traditional seven years of product experience, but I have had dense, hands-on experience owning and building technical products across two B2B SaaS markets."
- **Closes on standing, not enthusiasm.** "I'm not coming in to learn BI from scratch." "That's the kind of thing I'd be proud to put real years into." "That's a team I want to be part of."
- **Rejects the cliché explicitly** while making the point. "not in a cliché way, but because government clients are some of the hardest implementations you'll do."
- **Every claim carries its number.** 104% YoY ARR, 62% to 81%, 400% release frequency, 75 customers, $100K at a $1M cap, 7-month estimate delivered in 2.
- **Recurring phrases that are genuinely his**: "blur the line between PM and engineering", "zero to one", "hands-on", "deploy-aware", "ship with zero surprises".
- **A personal hook where one honestly exists.** Backpack: he went to Miami University on scholarships and remembered how opaque paying for school was.

Avoid: opening with praise for the company, "I am passionate about", "thrilled", "excited to", manufactured symmetry, and em-dashes.

## Screening questions: short, casual, his own words

Employers ask two shapes of free-text question over and over. Both get answered in **three to four sentences, maximum**. Not a paragraph. Not a compressed cover letter. If it reads like something a company wrote, it is wrong.

Register: first person, plain, contractions fine. No "passionate", no "thrilled", no "excited to", no em-dashes, no rhetorical-question opener, no three-part corporate symmetry.

**"Why us / what excites you about this role"** goes: I looked at what you build and I like it, then one concrete thing about *this* company taken from the stored posting extraction or company brief, then how it connects to work he has already done and where he is heading. The specific detail is what makes it not a template, and it must come from the extraction rather than from invention. If the letter would read the same with the company name swapped, it is not done.

> I looked at what you're building with agent evals and it's the same problem I've been living in. I spent the last year building an agentic QA platform that grades its own test runs, so the questions you're asking are ones I've already answered badly a few times. That's the work I want to keep doing.

**"Have you used X / done something like this before"** gets one real example, named, with its number attached. No hedging, no generalising.

> Closest thing I've done is a RAG test-case generator at CAMP, grounded on about 2,000 of our own ticket-to-test conversions. It took coverage from 62% to 81%. Same shape of problem, smaller scale.

Short does not mean loose with facts. Every claim still traces to the resume, the answer store, or the story bank. When he genuinely has not done the thing, say so in one line and name the nearest real thing rather than stretching something adjacent to cover it.

## Referral messages

The company brief and the story bank feed these too. A referral message earns a reply when it is specific about the role, honest about the ask, and short enough to answer from a phone. Two or three sentences: the role, one line on why this person fits it, the ask. The connection's time is the scarce resource; respect it and the reply rate follows.
