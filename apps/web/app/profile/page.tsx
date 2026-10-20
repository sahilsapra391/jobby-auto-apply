import { cookies } from "next/headers";
import { AppBar } from "../_ui/Chrome";
import { LANE_COOKIE, THEME_COOKIE, themeFrom } from "../_ui/theme";
import { accountId, boardMeta, profile } from "../_ui/db";
import { writesEnabled } from "../_ui/writes";
import { BANK_KINDS } from "../bank.generated";
import { EditsProvider } from "../_ui/ProfileEdits";
import { AddBaseResume, BankRow, ResumeCard } from "../_ui/ProfileSurface";
import { categoryLabel } from "../_ui/bankLabels";

/**
 * Profile: what every tailored resume starts from, and what Jobby answers with.
 *
 * **Built out of the board's own components, which were shipping unused.**
 * `board.css` is generated from the board, so 37 rules across 16 classes --
 * `profile-block`, `block-head`, `identity-grid`, `identity-card`,
 * `bank-tools`, `bank-search`, `bank-filters`, `bank-chip`, `chip-n`,
 * `bank-cats`, `bank-cat`, `bank-groups`, `bank-row`, `bank-q`, `bank-a`,
 * `bank-count` -- were in v2's stylesheet from the day it was generated. The
 * first version of this page used Settings' `provider-card` and the lane tab
 * strip instead, which is why it looked substantially different from the board
 * while being made of the board's CSS.
 *
 * **The write half is enqueue-only and lives in `_ui/ProfileSurface`.** An
 * answer belongs to `answers.json` on the device and reaches `answer_bank`
 * through the Class B mirror, so the device is the only writer; a control here
 * puts the edit on the queue and shows what the device decided, read back from
 * `edit_outcomes`. Nothing on this page draws a change it has not been told
 * happened.
 *
 * **The wording editor is the one control still dark, and it is a security
 * property rather than unfinished work.** The resume wording travels to a
 * provider inside the AES-256-GCM job bundle;
 * `class_b._PROFILE_EXCLUDED` keeps it out of `profiles`, so mirroring it to
 * make it editable here would put a person's employment history in their own
 * words in the clear in Postgres, on a connection that is the database owner
 * today, behind a shared password. That is Class 1 plaintext where the plan
 * puts Class 2 ciphertext. Reversing it is gated on the per-account
 * encryption keys in Phase 5, which is where the requirement is written.
 */
export const dynamic = "force-dynamic";

/** Which bank tab a row belongs to. Four are the record's own kind or state. */
function tabOf(entry: { kind: string | null; state: string | null }) {
  if (entry.state === "dismissed") return "dismissed";
  if (entry.state === "needs-you") return "needs";
  return entry.kind ?? "question";
}

const TABS: [string, string][] = [
  ...BANK_KINDS,
  ["needs", "Needs you"],
  ["dismissed", "Dismissed"],
];

/** The board's own wording for a base's claim, from `claimText`. */
function claim(countries: string[]) {
  if (!countries.length) return "the fallback for every other country";
  return `for ${countries.join(", ")}`;
}

export default async function Profile({ searchParams }: {
  searchParams: Promise<{ tab?: string; cat?: string; q?: string }>;
}) {
  const { tab: askedTab, cat, q } = await searchParams;
  const jar = await cookies();
  const theme = themeFrom(jar.get(THEME_COOKIE)?.value);
  const home = `/?lane=${jar.get(LANE_COOKIE)?.value ?? "found"}`;
  const [{ resumes, bank, outcomes }, writes, meta] = await Promise.all([
    profile(), accountId().then(writesEnabled),
    // Only for the page-wide stopped banner; a board with no meta has none.
    boardMeta().catch(() => null),
  ]);
  // What a removed base's postings fall back to, named rather than described,
  // because "the default" is not a thing a reader can check against the cards
  // in front of them.
  const fallback = resumes.find((r) => r.isDefault);
  const fallbackName = fallback
    ? `the ${(fallback.key ?? "").toUpperCase() || "default"} base`
    : "no base at all, and the application fails";

  const tab = TABS.some(([k]) => k === askedTab) ? askedTab! : "question";
  const inTab = bank.filter((e) => tabOf(e) === tab);
  // **Only the categories that exist, commonest first**, which is the board's
  // rule (`profile_panel.py:1302`). The first version drew all fourteen
  // including empty ones; the board counts what is in the tab, sorts by count
  // and leads with All.
  const counts = new Map<string, number>();
  for (const entry of inTab) {
    if (!entry.category) continue;
    counts.set(entry.category, (counts.get(entry.category) ?? 0) + 1);
  }
  const cats = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  // From `_ui/bankLabels`, which is not a client module. Both this component
  // and the client rows read it, and it has been in the wrong place in both
  // directions already; that file records what each one threw.
  const catLabel = categoryLabel;
  // Categories only classify questions, so the strip is absent on the tabs
  // where every chip would read zero.
  const showCats = ["question", "needs", "dismissed"].includes(tab);

  const needle = (q ?? "").trim().toLowerCase();
  const rows = inTab.filter((e) =>
    (!cat || e.category === cat)
    && (!needle
        || `${e.question ?? ""} ${e.value ?? ""} ${e.tags.join(" ")}`
             .toLowerCase().includes(needle)));

  const href = (next: { tab?: string; cat?: string; q?: string }) => {
    const p = new URLSearchParams();
    const t = next.tab ?? tab;
    if (t !== "question") p.set("tab", t);
    const c = next.cat === undefined ? cat : next.cat;
    if (c) p.set("cat", c);
    const term = next.q === undefined ? q : next.q;
    if (term) p.set("q", term);
    return p.size ? `/profile?${p}` : "/profile";
  };

  return (
    <>
      <AppBar here="profile" theme={theme} home={home}
              stopped={meta?.blocked} />
      <EditsProvider enabled={writes} outcomes={outcomes}>
      <div className="wrap">
        <section className="profile-page">
          {/* **Eyebrow then title, in that order.** v2 had them swapped: an
              `h2` reading "Profile" over an eyebrow reading "What Jobby
              answers with". The board names the page in the eyebrow and says
              what it is for in the heading. */}
          <div className="settings-titlebar">
            <div>
              <p className="eyebrow">Your profile</p>
              <h2>What Jobby answers with</h2>
              <p>
                Your base resumes and every answer Jobby has learned. Nothing on
                this page is stored in the page itself; it is read from Postgres
                when you open it, behind the same password.
              </p>
            </div>
          </div>

          <section className="profile-block" aria-labelledby="bases-title">
            <header className="block-head">
              <div>
                <h3 id="bases-title">Base resumes</h3>
                <p>
                  One per right to work. The default is what a posting falls
                  back to when its country matches none of them.
                </p>
              </div>
            </header>

            <div className="identity-grid">
              {resumes.map((r) => (
                <ResumeCard key={r.key ?? r.document} resume={r}
                            fallback={fallbackName}>
                  <div className="identity-head">
                    <div>
                      <h4>{(r.key ?? "").toUpperCase() || "Base"}</h4>
                      <p className="identity-scope">{claim(r.appliesTo)}</p>
                    </div>
                    {r.isDefault
                      ? <span className="identity-default">Default</span> : null}
                  </div>
                  {/* A definition list, which is what these are: a label and
                      the value it names. Rows with nothing in them are left
                      out rather than drawn empty, as the board does. */}
                  <dl className="identity-rows">
                    {([["Location", r.location], ["Phone", r.phone],
                       ["Address", r.address], ["Document", r.document]] as
                       [string, string | null][])
                      .filter(([, v]) => v)
                      .map(([label, value]) => (
                        <div className="identity-row" key={label}>
                          <dt>{label}</dt><dd>{value}</dd>
                        </div>
                      ))}
                  </dl>
                </ResumeCard>
              ))}
              {resumes.length === 0 ? (
                <p className="bank-note">No base resumes recorded.</p>
              ) : null}
            </div>

            <AddBaseResume resumes={resumes} />

            {/* **The resume editor is dark because the wording is not
                mirrored, not because writes are off.** `_PROFILE_EXCLUDED`
                keeps `resume-content.json` out of `profiles`, so Postgres has
                nothing for v2 to render and an editor over nothing is worse
                than a stage that says why. The stage says why. */}
            <details className="wording">
              <summary>
                <span>Edit your resume</span>
                <span className="wording-hint">
                  The document itself. Click any line and type.
                </span>
              </summary>
              <div className="wording-body">
                <div className="split-note">
                  <strong>The words are yours, the layout is the
                    template&apos;s.</strong> You are editing the real document,
                  so what you see is what the PDF will be. Lines can say
                  anything; there is always the same number of them, because the
                  layout is what the page-fit check measures against.
                </div>
                <div className="resume-stage">
                  <p className="bank-note">
                    Not shown here, and that is deliberate. Your resume wording
                    is your employment history in your own words, and it
                    reaches an AI provider inside an encrypted bundle rather
                    than sitting in the board&apos;s database. Putting it here
                    would mean storing it in the clear, so it waits for
                    per-account encryption. Editing it on your device still
                    works, and nothing about your applications is affected.
                  </p>
                </div>
                <div className="wording-act">
                  <span className="profile-message" role="status">
                    Not editable here, by design. It stays encrypted.
                  </span>
                </div>
              </div>
            </details>
          </section>

          <section className="profile-block" aria-labelledby="bank-title">
            <header className="block-head">
              <div>
                <h3 id="bank-title">Memory Bank</h3>
                <p>
                  Every question answered once, kept for the next form.{" "}
                  <span className="bank-count">
                    {bank.length.toLocaleString()} stored
                  </span>
                </p>
              </div>
            </header>

            <div className="bank-tools">
              <form className="bank-search" method="get" action="/profile">
                {tab !== "question"
                  ? <input type="hidden" name="tab" value={tab} /> : null}
                {cat ? <input type="hidden" name="cat" value={cat} /> : null}
                <span className="vh">Search the Memory Bank</span>
                <input type="search" name="q" defaultValue={q ?? ""}
                       placeholder="Search questions and answers"
                       autoComplete="off" spellCheck={false} />
              </form>
              <div className="bank-filters" role="group"
                   aria-label="Which kind of record">
                {TABS.map(([key, label]) => {
                  const n = bank.filter((e) => tabOf(e) === key).length;
                  // The board hides a filter whose set is empty: "a filter for
                  // an empty set is an invitation to wonder what is missing."
                  if (n === 0 && key !== tab) return null;
                  return (
                    <a key={key} href={href({ tab: key, cat: "" })}
                       className={key === tab ? "bank-chip on" : "bank-chip"}>
                      {label}<span className="chip-n">{n}</span>
                    </a>
                  );
                })}
              </div>
            </div>

            {showCats && cats.length ? (
              <div className="bank-cats" role="group"
                   aria-label="Filter by category">
                <a className={cat ? "bank-cat" : "bank-cat on"}
                   href={href({ cat: "" })}>All</a>
                {cats.map(([slug, n]) => (
                  <a key={slug} href={href({ cat: slug === cat ? "" : slug })}
                     className={slug === cat ? "bank-cat on" : "bank-cat"}>
                    {catLabel(slug)}<span className="chip-n">{n}</span>
                  </a>
                ))}
              </div>
            ) : null}

            <div className="bank-groups">
              {/* Whole class strings below, for the reason the state chip
                  gives: a name assembled with `+` exists only after the
                  expression runs, and both guards read the source. `state`
                  holds one value, so these are exclusive rather than
                  combinable. */}
              {rows.map((entry) => (
                <BankRow key={entry.key} entry={entry} />
              ))}
              {rows.length === 0 ? (
                <p className="bank-empty">
                  {needle || cat
                    ? "No entry in this tab matches. Clear the filter to see the rest."
                    : "Answers arrive as employers ask them."}
                </p>
              ) : null}
            </div>
          </section>

          <p className="credential-note">
            Answers are stored on Your Device and mirrored to the board behind
            this password so you can read them here, including compensation and
            demographics: this is your board and your data. That changes when a
            second account exists, and the per-user encryption in the
            multi-tenant plan is what changes it.
          </p>
        </section>
      </div>
      </EditsProvider>
    </>
  );
}
