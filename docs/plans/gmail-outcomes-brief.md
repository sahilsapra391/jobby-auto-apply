# Gmail outcome ingestion

**Status: unassigned. Phase 1 is ready to build; Phase 2 is explicitly not.**

This document exists because the work did not. The brief lived in a session's
context and nowhere `git ls-files` would find it, so three sessions in turn
recognised the opportunity, correctly declined to poach it, and left it exactly
where they found it. A lead nobody can locate and nobody is assigned is a lead
dying politely. Committing it is what makes it assignable: the next session with
capacity starts from a document rather than an archaeology exercise.

## Why now rather than Phase 3

- **102 applications are out and nothing records what came back.** The mailbox
  is durable, so nothing is being lost by waiting. What is lost is every week
  of not knowing whether any of this works.
- `docs/jobby-preference-learning-plan.md` is explicitly blocked on it. The
  market's opinion is the only evidence the match rubric predicts anything;
  until then every ranking improvement is guesswork measured against itself.
- **86 submitted applications have no record of what was answered**, and their
  one possible surviving source is the employer's own confirmation email. See
  "the 86" below.

## Phase 1 — read-only outcome tracking

Build this fully. Stop at the phase boundary for a decision.

A scheduled read-only pass matches inbound mail against submitted applications
in `roles.json` plus history, and writes the outcome events the schema already
defines and nothing currently ingests: `response`, `interview`, `offer`,
`rejected`. Purely additive. It never sends, deletes, marks read, or modifies a
message.

### OAuth and credentials

Google OAuth only. The user consents in Google's own screen and the application
never sees a password. The scope is `gmail.readonly` and nothing else.

The token is Class-0 in `docs/plans/multi-tenant-architecture.md`: an
owner-only file on the device, never in the repo, the page, KV, or logs, and
structured so the per-user token store drops in later without a migration.

**The connect flow is initiated by the user from Settings, and only from
there.** Never by content found in an email, a form, or a tool result. A
connect prompt that anything but the user can trigger is a phishing surface
wearing the product's own chrome.

### Matching is conservative and evidence-based

Sender domain, plus company and role, plus a time window. Ambiguous mail is
left unclassified.

**A wrong "rejected" on the board is worse than a missed one.** A missed
outcome is a gap the user already lives with; a wrong one is the board lying
about something he cares about, and he has no way to know it is wrong. Every
classification records which signal matched. Never classify from a subject line
alone.

### Outputs

- **Confirmations** become a second proof source on Submitted. The page-read
  confirmation stays primary; an email can corroborate it, and can flip an
  `unverified` proof to confirmed.
- **Rejections** log quietly.
- **Interview requests** surface a card telling the user to look. Jobby does
  not reply.

### Privacy is absolute

No email body text in logs, in the store beyond the structured event, or in
board HTML. Store message ids and structured outcomes, never content. The
repo-wide PII scan stays green.

## Phase 2 — codes and click-to-verify. Do not build.

Write it up as a separate plan with the questions answered, and stop there.

It reverses a documented refusal. `docs/system-reference.md` §11 lists "create
accounts, type passwords, or read one-time codes" among the things Jobby does
not do, and the reason is not squeamishness: **a system that reads one-time
codes from email and acts on them can be driven by anyone who can send that
email.**

The confused-deputy risk is the specific shape. An agent that reads the inbox
for codes and also fills forms can be steered by a crafted message: the
attacker does not need the user's credentials, only the ability to put text
where the agent will read it and be believed.

If it is ever built, the code path must be narrowly scoped:

- only for an application actually in flight,
- only a code from that ATS's own sender,
- only within a tight window of the form having requested one,
- handing the agent the code alone and never free email text.

Click-to-verify is worse, and the recommendation is against automating it at
all: surface the link and let the user click it.

## Three constraints, discovered the expensive way

These came out of a week of measurement. Each is cheap to design in and
expensive to retrofit.

### 1. Attribution before parsing

Of 102 submitted applications, **25 were unattributable**, and 16 of those
failed because a company had several applications and nothing named which.

Two applications to one employer produce two confirmations that may be
indistinguishable by sender and subject. **Tying a thread to an application id
is the work; reading field values out of it is the easy part.** Scope in that
order, or the difficulty is discovered last, after the parsing looks done.

### 2. An employer-reported answer is a different claim

What the employer says it received and what the agent recorded entering can
disagree without either being wrong, and they fail in different ways.

The per-application answer record already carries a `capture` level (`full`,
`questions-only`) and a per-field `source`. A third value costs one enum entry.
Blending the two into a shared field permanently costs the ability to tell
which one you are looking at, and that is not recoverable afterwards.

### 3. A null sample is evidence about one employer's template

An email that echoes no fields says nothing about employers in general.
Sampling first is right; concluding "confirmation emails do not echo fields"
from a thin sample is the error.

**Named cautionary case.** On 2026-08-27 a session closed 34 of 43 watched
roles as taken down, because the employers' boards had returned no postings for
them. The boards had answered `304 Not Modified` — nothing had been seen at
all, and silence was read as absence. A spot check found the roles alive and the
reasoning was walked back. State how many emails were sampled, from which ATS,
over what period, and what a null result there does and does not license.

## The 86

86 of the 102 submitted applications have no record of what was answered.
Reconstructing them from `answers.json` was considered and refused: many of its
entries were last edited on 2026-08-26, so resolving old questions against it
would present today's store as what an employer received months ago.

Employer confirmation emails, particularly Greenhouse and Workday, sometimes
echo the submitted fields back. If they do, that is a genuine second source and
it is the employer's own record rather than a reconstruction from ours. If they
do not, the 86 stay "not recorded", which is a fine and honest answer.

Sample before scoping this, per constraint 3, and note that constraint 1
applies to the sample too: a confirmation you cannot tie to an application is
not evidence about that application.

## Assignment

Sahil assigns the session. This is deliberately not picked up by whoever
commits the document.
