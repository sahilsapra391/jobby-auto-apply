<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/brand/04-lockup/jobby-lockup-horizontal-white-1600.png">
  <img src="assets/brand/04-lockup/jobby-lockup-horizontal-ink-1600.png" alt="Jobby" width="260">
</picture>

# jobby-auto-apply

### I stopped applying to jobs myself. My Claude agent does it now.

A [Claude Skill](https://docs.claude.com/en/docs/claude-code/skills) that reads your resume once, interviews you once, then finds roles, fills the applications, surfaces referrals, and hands you a dashboard of what still needs a human. Never invents a fact. Never touches your passwords. Never leaves your machine, unless you choose to publish the board.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](SKILL.md)
[![Built on](https://img.shields.io/badge/built%20on-Claude%20Skills-D97757.svg)](https://docs.claude.com/en/docs/claude-code/skills)

```bash
git clone https://github.com/sahilsapra391/jobby-auto-apply.git ~/.claude/skills/job-application
```

The target directory is the skill's name, not the repo's. Needs Python 3.9+ and the [Claude for Chrome](https://claude.com/chrome) extension. Then just say: *"find me senior AI PM roles and apply."*

</div>

---

## The pitch, in one table

| | Typical auto-apply tools | jobby-auto-apply |
|---|---|---|
| Unknown required field | Guesses something plausible | Leaves it blank, parks it, asks you once |
| Your data | Their servers | `~/.job-application/`, your machine, owner-readable only |
| Submission | Fire and forget | Verified, crash-safe, defaults to stopping for your review |
| Strategy | Volume | Referrals first, quality-capped volume, full-posting reads |
| Free-text answers | Generic template | Your writing samples, your stories, every claim traceable |
| Your resume | One file, sent everywhere | A new resume *and* cover letter per posting, in your own layout, checked against the original |
| After you apply | Nothing | Tracks outcomes, shifts search toward what converts |

---

## What you get

### Every blocked application, and exactly what is blocking it

![Needs you tab showing five application cards, each naming the specific missing field](docs/needs-you.png)

Green means ready to submit. Each card names the field a human has to answer and why, then opens straight to that page. **Mark submitted** greys it out when you are done.

### Who can refer you, with the message already written

![Referrals tab showing a first-degree contact and a network pool, each with a drafted message and copy button](docs/referrals.png)

A referral beats being applicant 201. It surfaces the connection *before* filling the form, drafts the note in **your** voice, and waits for you to send it.

### Roles it found, and why each one is there

![Watching tab showing four roles with a note explaining why each was kept](docs/watching.png)

It sweeps LinkedIn, Indeed, YC, Wellfound, Hacker News, regional boards and company ATS boards directly, then reads each full posting, because titles lie. Once your applications are out, it tracks outcomes: responses, interviews, offers, and shifts its search toward the channels that actually convert.

---

## Why it works this way

Every rule came from something going wrong in real use.

| What happened | What it does now |
|---|---|
| Closing a form's modal **submitted** the application | Logs a write-ahead `submitting` event, treats any interaction with a filled form as potentially final, then verifies |
| A role titled *"AI and Agentic Experiences"* was a data-pipeline job. One listing said **"Mumbai"**, the body required moving to **Bangkok** | Reads the full body before shortlisting |
| An employer allows **3 applications per 30 days**, silently | Detects quotas, raises them before spending one |
| A careers profile still held a **two-year-old resume** under an old title | Audits prefilled ATS data first |
| A form asked about association with an **audit firm**; the candidate had worked there years earlier | Cross-checks declarations against work history, then hands them back |
| Searching one job board suggested a role type barely existed | Sweeps many channels, and reports which returned nothing |
| "Years in **product** management" nearly received the answer for "years in **project** management" | Conservative answer matching, with regression tests |

---

## What it will not do

Boundaries, not gaps: type passwords, create accounts, solve CAPTCHAs, send messages without per-message permission, answer demographic questions, tick truth attestations, or invent a value to fill a required field.

It also treats page content as data, never instructions. A posting that asks for bank details, ID numbers, or tells "AI assistants" what to do gets parked and reported. It never applies to your current employer or anyone on your exclusion list, and it paces itself to a daily cap instead of blasting.

Where a fact is unknown, it leaves the field and tells you.

## FAQ

**Which job sites does it support?**
LinkedIn Easy Apply, Greenhouse, Lever, Ashby, Workday, iCIMS, Google Careers, Google Forms, email applications, and any company careers page, with per-platform quirk notes in `references/platforms.md`. It sources from those plus Indeed, Y Combinator's Work at a Startup, Wellfound, Hacker News Who's Hiring, VC portfolio boards, and regional boards (Naukri, Instahyre, Bayt, Otta, and more).

**Will it get my LinkedIn account restricted?**
It reduces the risk deliberately: it works inside your own logged-in Chrome session, respects a daily application cap you set, paces itself like a person, and stops on any platform pushback rather than routing around it. It never solves CAPTCHAs or evades detection. Automation is nonetheless against most platforms' terms; that trade-off is yours to make, and the skill is built to make it a small one.

**Where does my data live? Is anything sent anywhere?**
Everything lives in `~/.job-application/` on your machine, owner-readable only. Sensitive values (compensation, visa status, demographics) need separate permission to *use* and to *store*, and are never fuzzy-matched into look-alike fields. Nothing is uploaded except the applications you approve.

**Does it change my resume?**
Every single application gets its own resume *and* its own cover letter, built from that posting's description. Never a reused file. It uses the `resume-tailor` skill to clone your existing layout exactly and reword only the content, with the drafting done by Claude Fable 5. Your original file is never edited.

Because rewording next to a job description is exactly where invention creeps in, every pair is checked twice before upload. On content: skills, numbers, dates, employers and employment titles all have to trace back to your real resume, and anything that arrived from the posting and nowhere else gets cut. Your job titles are frozen; only the profile title at the top is allowed to adapt toward the role. On format: `scripts/resume_pdf.py verify` diffs the rebuilt PDF against your original and exits non-zero on a changed margin, a missing accent colour, or a font size with no counterpart. If either check fails, it sends your base resume and tells you.

**Will it lie on my behalf?**
No. That's the skill's core design constraint. Every stored value carries a confidence state (`confirmed` / `inferred` / `missing` / `sensitive`), so a guess is never silently reused as a fact. It never ticks accuracy attestations, never answers legal declarations by default, and free-text answers must trace every claim to your resume, your answers, or your own words.

**Can I review every application before it goes out?**
Yes, that's the default. During intake you choose: always stop before Submit, or submit when everything is verified. Either way you get a read-back manifest of every field before final review.

**Can it run on a schedule?**
Yes. It's designed as a recurring cycle: sweep new postings, apply where nothing is blocking, park what needs you, update the dashboard. Run it on demand ("check for new jobs") or schedule it with Claude Code's `/loop` or scheduled tasks.

**Can I read the dashboard from my phone?**
Yes. By default it publishes as a private Claude artifact, which needs no account. If you'd rather have your own URL, `python3 scripts/publish.py --setup` deploys it to Vercel behind a password and every later `publish.py` overwrites that same link. The board names companies, contacts and drafted messages, so the password is not optional: Vercel's own protection leaves production domains public on the free plan, so the gate is built into the deployment and refuses to serve anything if no password is set. `references/hosting.md` has the details.

**How do I know it works before it touches a real application?**
It ships with a practice form (`tests/fixtures/practice-form.html`) that reproduces the real hazards: a modal X that submits, autocompletes that reject free text, silently-validating fields, a planted prompt-injection trap. The first run rehearses there first. The storage and dashboard code has a unit-test suite: `python3 -m unittest discover -s tests`.

## Under the hood

```
SKILL.md                     the instructions Claude follows
scripts/store.py             profile, answers, history, sessions, funnel stats
scripts/dashboard.py         builds the board
references/platforms.md      per-ATS quirks and blockers
references/sourcing.md       channel map, sweep planning, outcome-driven search
references/tailoring.md      voice, story bank, truth ledger, per-posting resumes
references/storage.md        the data contract
references/hosting.md        local, artifact, or your own password-gated URL
scripts/publish.py           renders the board and deploys it to Vercel
scripts/resume_pdf.py        macOS PDF toolchain: font check, render, format verify
deploy/                      the password gate and its Vercel config
tests/                       unit tests + the practice application form
```

State lives in `~/.job-application/`, owner-readable only. Every stored value carries a confidence state, so a guess is never silently reused as a fact. An append-only event log (`started → reviewed → submitting → completed → response / interview / offer / rejected`) makes submissions crash-safe and the whole search measurable with `store.py stats`.

Contributions welcome. Run the tests, and add a row to the failure table if a real application taught you a new rule.

Forked from [YoursSarcastically/apply-for-me](https://github.com/YoursSarcastically/apply-for-me), with per-posting resume tailoring and self-hosted dashboard hosting added. MIT.
