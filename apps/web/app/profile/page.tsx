import { cookies } from "next/headers";
import { AppBar } from "../_ui/Chrome";
import { LANE_COOKIE, THEME_COOKIE, themeFrom } from "../_ui/theme";
import { profile } from "../_ui/db";
import { BANK_CATEGORIES, BANK_KINDS } from "../bank.generated";
import { READ_ONLY } from "../_ui/actions";

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
 * **The write half is deliberately absent, not forgotten.** `details.wording`
 * (edit the resume in place) and `div.base-form` (add a base resume) are both
 * writes and both go behind `live_board` with item 21. The button that opens
 * the form renders disabled carrying its reason, rather than as a control with
 * nothing behind it.
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
  const { resumes, bank } = await profile();

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
  const catLabel = (slug: string) =>
    BANK_CATEGORIES.find(([k]) => k === slug)?.[1] ?? slug;
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
      <AppBar here="profile" theme={theme} home={home} />
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
                <article className="identity-card" key={r.key ?? r.document}>
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
                  {!r.isDefault ? (
                    <div className="identity-act">
                      <button className="btn ghost" type="button" disabled
                              title={READ_ONLY}>Make default</button>
                    </div>
                  ) : null}
                </article>
              ))}
              {resumes.length === 0 ? (
                <p className="bank-note">No base resumes recorded.</p>
              ) : null}
            </div>

            <div className="identity-add">
              <button className="btn ghost" type="button" disabled
                      title={READ_ONLY}>Add a base resume</button>
              {/* **The form the button opens, rendered dark rather than
                  absent.** Creating a base writes `profiles.resumes`, which is
                  a write, so it is behind `live_board`. Present because a
                  button that opens nothing is worse than a form you can see and
                  cannot submit: the reader learns what the board will ask for,
                  and wiring it later is one step rather than two. */}
              <div className="base-form">
                <label>Name
                  <input type="text" placeholder="Europe" disabled
                         title={READ_ONLY} /></label>
                <label>Key
                  <input type="text" placeholder="eu" disabled
                         title={READ_ONLY} /></label>
                <label>Countries
                  <input type="text" placeholder="GB, DE, FR, NL" disabled
                         title={READ_ONLY} /></label>
                <label>Copy from<span className="vh"> an existing base</span>
                  <select disabled title={READ_ONLY}>
                    {resumes.map((r) => (
                      <option key={r.key ?? ""} value={r.key ?? ""}>
                        {(r.key ?? "").toUpperCase() || "Base"}
                      </option>))}
                  </select></label>
                <label>Document
                  <span className="base-file">
                    <input type="file" accept="application/pdf,.pdf" disabled
                           title={READ_ONLY} />
                    <span className="bank-note">
                      Using the copied base&apos;s PDF. Choose a file to use
                      your own instead.
                    </span>
                  </span></label>
                <div className="bank-act">
                  <button className="btn" type="button" disabled
                          title={READ_ONLY}>Create</button>
                  <button className="btn ghost" type="button" disabled
                          title={READ_ONLY}>Cancel</button>
                </div>
                <p className="bank-note">
                  Contact details always come from the base you copy. A PDF you
                  upload becomes what this base is <em>based on</em>: the text a
                  tailored resume is written against. The wording itself still
                  starts from the copied base, because the layout a resume
                  renders through is the template&apos;s, not the uploaded
                  file&apos;s.
                </p>
                <span className="profile-message">{READ_ONLY}</span>
              </div>
            </div>

            {/* **The resume editor, open and read-only.** Editing the wording
                writes it, so the controls are dark; the document itself is a
                read and v2 does not carry it yet, which the stage says rather
                than pretending to load forever. */}
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
                    The wording is on your device. v2 reads what the mirror
                    carries, and the resume text is not mirrored yet, so there
                    is nothing here to edit.
                  </p>
                </div>
                <div className="wording-act">
                  <button className="btn" type="button" disabled
                          title={READ_ONLY}>Save resume</button>
                  <button className="btn ghost" type="button" disabled
                          title={READ_ONLY}>Revert</button>
                  <span className="profile-message" role="status">
                    {READ_ONLY}
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
                <article key={entry.key}
                         className={entry.state === "needs-you" ? "bank-row open"
                           : entry.state === "dismissed" ? "bank-row struck"
                           : "bank-row"}>
                  <p className="bank-q">{entry.question ?? entry.key}</p>
                  {/* **Drawn plainly, including the sensitive ones.**
                      CLAUDE.md settles this: one reader, who knows his own
                      salary and his own demographics, and a tap to reveal was
                      friction protecting nobody. The flag stays on the row
                      because it is what a second account will mask on. */}
                  {entry.value ? (
                    <p className="bank-a">{entry.value}</p>
                  ) : (
                    <p className="bank-a none">
                      {entry.state === "dismissed"
                        ? "You dismissed this. Jobby will not ask again."
                        : "No answer yet."}
                    </p>
                  )}
                  <p className="bank-meta">
                    {/* **The three states written out, not composed.**
                        `"bank-tag state-" + state` reads the same and is
                        invisible to both guards: `test_v2_matches_the_board`
                        proves no class is invented and
                        `test_v2_styles_are_reachable` proves none is
                        unreachable, and neither can resolve a name that only
                        exists once the expression has run. Concatenating it
                        made all three `state-*` rules look dead. These are
                        also exactly the three the stylesheet defines. */}
                    <span className={
                      entry.state === "sensitive" ? "bank-tag state-sensitive"
                      : entry.state === "dismissed" ? "bank-tag state-dismissed"
                      : entry.state === "missing" ? "bank-tag state-missing"
                      : "bank-tag"}>
                      {entry.state === "needs-you"
                        ? "needs you" : (entry.state ?? "confirmed")}
                    </span>
                    {entry.category && entry.kind === "question" ? (
                      <span className="bank-tag">{catLabel(entry.category)}</span>
                    ) : null}
                    {entry.tags.map((t) => (
                      <span className="bank-tag" key={t}>{t}</span>
                    ))}
                    {entry.updatedAt ? (
                      <span className="bank-tag">
                        Updated {String(entry.updatedAt).slice(0, 10)}
                      </span>
                    ) : null}
                  </p>
                </article>
              ))}
              {rows.length === 0 ? (
                <p className="bank-note">
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
    </>
  );
}
