# Storage contract

All persistent state lives under `~/.job-application/` and is reached only through `scripts/store.py`. Going around the helper produces files the next session cannot read.

```
~/.job-application/
  profile.json         identity, work history, education, skills
  answers.json         reusable answers, each with a confidence state
  applications.jsonl   append-only event log
  sessions/            resumable in-progress applications
  meta.json            small operational values: dashboard artifact URL, last sweep time
```

Delivered documents do **not** live in the store. They live beside the skill in `applications/<Company>/`, one folder per company, so they are browsable in Finder rather than buried in a dotfile directory. That folder is gitignored (and `*.pdf` is ignored repo-wide as a second layer) because the documents carry a real street address and phone number. `store.py paths` prints its location.

They are built by the `resume-tailor` skill from the region-matched base resume in `profile.resumes`. The base resume is never edited. **Every application gets its own pair**, named `Sahil_Sapra_<Company>_Resume.pdf` so the filename still means something in a recruiter's inbox. When a company has more than one open role, the role is added (`Sahil_Sapra_Anthropic_ClaudeCodeModelPerformance_Resume.pdf`), because company-only names collide and the second application silently overwrites the first. `scripts/file_docs.py --check` audits every application for its pair. Both filenames go on the `completed` event, as `resumeVariant` and `coverLetter`, so the user can pull up exactly what an interviewer is reading. See `references/tailoring.md` for the checks a pair has to pass before upload.

## Commands

```bash
python3 store.py init                                  # create store, print paths
python3 store.py profile-get
python3 store.py profile-replace --input profile.json
python3 store.py answer-put --input answer.json [--remember-sensitive]
python3 store.py answer-find --question "..."
python3 store.py answers-resolve --input questions.json   # whole form in one call
python3 store.py history-append --input event.json
python3 store.py history-list
python3 store.py stats                                 # funnel, per-source conversion, needsVerification
python3 store.py session-save --id <app-id> --input session.json
python3 store.py session-load --id <app-id>
python3 store.py session-list
python3 store.py session-delete --id <app-id>
python3 store.py meta-set --key artifactUrl --value <url>
python3 store.py meta-get [--key artifactUrl]
```

Pass JSON through a private temp file with `umask 077`, and delete it afterwards. Never echo stored values into logs or into the conversation unless the user asked to see them.

## Base resumes, one per region

`profile.resumes` is a list, because one resume does not serve every market. Each entry:

```json
{"key": "us", "default": true, "appliesTo": ["US"],
 "path": "~/…/Sahil Resume 2026 PM.pdf",
 "identity": {"phone": "(216) 848-7236", "location": "Charlotte, NC 28273"}}
```

`store.py profile-replace` rejects a resume without `identity.phone` and `identity.location`, and rejects a set without exactly one `default`. Both guards exist because the failure they prevent is silent: an application that went out with the wrong country's phone number looks completely normal.

`identity.fullAddress` is for **form fields only**. The resume and cover letter headers use the base document's own contact line, which carries city and state and no street address. The `identity` block otherwise governs the cover letter **and the form's own contact fields**. Match the base to the posting's real country, confirmed from the body rather than the listing's location line.

## Confidence states

Every stored answer and every inferred profile field carries a state. This is the mechanism that stops a guess from hardening into a fact across applications.

| State | Meaning | Reuse without asking? |
|---|---|---|
| `confirmed` | The user stated or approved it | Yes, when non-sensitive and the question matches |
| `inferred` | You derived it from context | No. Show it and confirm |
| `missing` | Unknown | No. Ask |
| `sensitive` | Compensation, visa, demographics, disability | Never silently. Confirm before every use |

`inferred` is the state that matters most in practice. When a form needs a job's city and the resume only gives years, that value is inferred forever after — not because it is likely wrong, but because neither you nor the next session can tell it apart from a fact once the label is gone.

## Matching answers to form questions

`answer-find` returns an exact match when the normalised keys agree. Below that it fuzzy-matches, and the rules are deliberately conservative:

- A fuzzy match needs at least 0.75 token overlap. "Years of experience in product management" versus "…in project management" scores 0.71 — one word apart, and the wrong answer. Below the bar the result is `match: none` plus up to three `candidates` carrying key, stored question, state and score — never values. To use a candidate, confirm it means the same thing, then fetch it exactly via `answer-find` with the candidate's own stored question.
- **Sensitive answers never fuzzy-match.** A compensation figure landing in a near-miss field is the worst mis-fill this store can produce. Exact key or nothing.
- A `match: fuzzy` result is a lead, not an authority: the score measures wording overlap, not meaning. Confirm it fits the question before filling.

`answers-resolve` takes a JSON list of question strings and returns the full map in one call. Use it to build a form's fill plan upfront — which fields are covered, which need confirming, which block — instead of paying one round-trip per field.

## Sensitive answers: two separate permissions

Using a sensitive answer now and storing it for later are different decisions. Ask them separately:

1. May I use this for this application?
2. May I save it for future applications?

Only pass `--remember-sensitive` after a clear yes to the second. Permission to fill is not permission to remember. Even a stored sensitive answer gets shown and reconfirmed before each future use, because circumstances change — expected compensation in particular.

## Events

```json
{"event": "reviewed", "company": "...", "role": "...", "ats": "greenhouse", "applicationId": "..."}
```

Useful optional fields: `source` (the channel the role came from — `linkedin`, `greenhouse`, `wellfound` — put it on every `completed` event; it is what powers the per-channel conversion in `stats`) and `resumeVariant` plus `coverLetter` (the two documents the employer received, per `tailoring.md`).

`reviewed` means the form reached final review. `submitting` is written immediately **before** any potentially-final interaction — Submit, closing a completed modal, navigating away. `completed` means the user confirmed submission, or you directly observed a confirmation state.

`submitting` is a write-ahead record. Its value is what it means when it is the *last* event for an application: a session died between the click and the confirmation check. On seeing that, verify on the platform whether the application went through, then log `completed` or `abandoned` — never treat the role as un-applied and fill the form again.

Keeping these apart is what prevents duplicate applications weeks later. When you cannot tell whether something submitted, leave the `submitting` event as the latest and say so — an honest gap is more useful than a confident wrong entry.

After submission, log outcomes as the user reports them: `response`, `interview`, `offer`, `rejected`. These stay on the Submitted tab with their status, and they are what lets sourcing shift budget toward the channels that actually convert (see the outcomes section in `sourcing.md`).

## Sessions

Save a session whenever an application is left incomplete. Use status `inflight` while an Apply run is under way, with `phase`, `phaseDetail` and `phaseAt` carrying live progress; the role leaves Watching and appears under **In flight** until it is submitted or parked. Use status `review` for anything waiting on the user (account creation, password, OTP, emailed verification link, CAPTCHA, truth attestation, legal agreement) so the dashboard shows it under **Needs you**; `watch` means found-but-not-started and nothing else. Getting this wrong hides a blocked application among sixty untouched ones. Record the ATS, company, role, URL, the step reached, and each pending field with the reason it is pending — "user must answer", "needs CAPTCHA", "unknown to assistant".

Sessions hold field *descriptions*, never answer values. Values live in the answer store, which has the confidence machinery around them.

Delete a session once the user confirms submission or abandons it. History is separate and stays, and the role index keeps the company, role and URL so the Submitted tab can still link to the posting after the session is gone. `scripts/sync_board.py` enforces this and republishes in one step; without it a submitted role sits in Watching and Submitted simultaneously.
