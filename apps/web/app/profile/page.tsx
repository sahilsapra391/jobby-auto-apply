import { cookies } from "next/headers";
import { AppBar } from "../_ui/Chrome";
import { LANE_COOKIE, THEME_COOKIE, themeFrom } from "../_ui/theme";
import { profile } from "../_ui/db";
import { BANK_CATEGORIES, BANK_KINDS } from "../bank.generated";
import { READ_ONLY } from "../_ui/actions";

/**
 * Profile: what every tailored resume starts from, and what Jobby answers with.
 *
 * **The read half.** Make default, Remove, Add a base resume and Edit on every
 * Memory Bank answer are all writes, and writes are behind `live_board`. They
 * render disabled with the reason rather than absent, so the shape is the
 * board's and wiring them is one step rather than two.
 *
 * The page was a single sentence before this, against a registry of two base
 * resumes and a bank of 195 entries that were already mirrored.
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
  // **Counted over the tab, not the whole bank.** A chip saying 28 that
  // filters to nothing is worse than no chip: the categories only classify
  // questions, so a count taken across all 195 would promise policies and
  // story entries a category filter cannot reach.
  const counts = new Map<string, number>();
  for (const entry of inTab) {
    const key = entry.category ?? "other";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const needle = (q ?? "").trim().toLowerCase();
  const rows = inTab.filter((e) =>
    (!cat || (e.category ?? "other") === cat)
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
          <div className="settings-titlebar">
            <h2>Profile</h2>
            <p className="eyebrow">What Jobby answers with</p>
          </div>

          <h3>Base resumes</h3>
          <div className="provider-grid">
            {resumes.map((r) => (
              <div className="provider-card" key={r.key ?? r.document}>
                <div className="provider-head">
                  <div>
                    <h3>{(r.key ?? "").toUpperCase() || "Base resume"}</h3>
                    <p>{r.appliesTo.join(", ") || "no countries listed"}</p>
                  </div>
                  {r.isDefault ? <span className="chip ready">Default</span> : null}
                </div>
                <div className="host-row">
                  <div>
                    <strong>{r.location ?? "no location"}</strong>
                    <span>{r.address ?? "no address recorded"}</span>
                    <span>{r.phone ?? "no phone recorded"}</span>
                  </div>
                </div>
                <p className="provider-detail">
                  {r.document
                    ? <>Document: {r.document}</>
                    : <span className="muted">no document recorded</span>}
                </p>
                <div className="rowacts-btns">
                  {/* Writes. Dark with the reason, and the shape is the
                      board's, so wiring them is one step. */}
                  <button className="btn" type="button" disabled
                          title={READ_ONLY}>Make default</button>
                  <button className="btn ghost" type="button" disabled
                          title={READ_ONLY}>Remove</button>
                </div>
              </div>
            ))}
            {resumes.length === 0 ? (
              <p className="empty">
                <strong>No base resumes recorded.</strong>
                <span>Add one on your device; the board reads what it mirrors.</span>
              </p>
            ) : null}
          </div>
          <div className="rowacts-btns">
            <button className="btn" type="button" disabled title={READ_ONLY}>
              Add a base resume
            </button>
          </div>

          <h3>Memory Bank</h3>
          <p className="hint">
            {bank.length.toLocaleString()} stored.{" "}
            {/* The three kinds are a real distinction and the board's own:
                121 questions an employer asked, the user's standing policies,
                and prepared story material nobody asked for. A reader hunting
                a question should not scroll past the third. */}
            What an employer asked, what you have decided to answer, and what
            you prepared in advance.
          </p>

          <div className="seg" role="tablist">
            {TABS.map(([key, label]) => {
              const n = bank.filter((e) => tabOf(e) === key).length;
              return (
                <a key={key} className={key === tab ? "tab on" : "tab"}
                   href={href({ tab: key, cat: "" })} role="tab"
                   aria-selected={key === tab}>
                  {label}<span className="n">{n}</span>
                </a>
              );
            })}
          </div>

          <form className="filters" method="get" action="/profile">
            {tab !== "question" ? <input type="hidden" name="tab" value={tab} /> : null}
            {cat ? <input type="hidden" name="cat" value={cat} /> : null}
            <input type="search" name="q" defaultValue={q ?? ""}
                   placeholder="Search the bank…" aria-label="Search the bank" />
            {q ? <a className="btn ghost" href={href({ q: "" })}>Clear</a> : null}
            <span className="fcount">
              {rows.length === inTab.length
                ? `${rows.length} shown`
                : `${rows.length} of ${inTab.length} shown`}
            </span>
          </form>

          {/* Category chips, for the tab that has categories. `other` shows at
              zero on purpose: a category set that cannot miss has stopped
              classifying, and the next corpus will not be this one. */}
          {tab === "question" ? (
            <div className="jb-pills">
              {BANK_CATEGORIES.map(([key, label]) => (
                <a key={key} href={href({ cat: key === cat ? "" : key })}
                   className={key === cat ? "chip lane" : "chip"}>
                  {label} <b>{counts.get(key) ?? 0}</b>
                </a>
              ))}
            </div>
          ) : null}

          <div className="grid">
            {rows.map((entry) => (
              <article className="card" key={entry.key}>
                <header>
                  <div>
                    <h3>{entry.question ?? entry.key}</h3>
                  </div>
                  {entry.state ? (
                    <span className={entry.state === "dismissed"
                            ? "chip" : "chip ready"}>{entry.state}</span>
                  ) : null}
                </header>
                {/* **Drawn plainly, including the sensitive ones.** CLAUDE.md
                    settles this: one reader, who knows his own salary and his
                    own demographics, and a tap to reveal was friction
                    protecting nobody. The flag stays on the row because it is
                    what a second account will mask on. */}
                <p className="jb-sum">
                  {entry.value ?? <span className="muted">no answer stored</span>}
                </p>
                <div className="jb-chips">
                  {entry.sensitive
                    ? <span className="jb-chip gate">sensitive</span> : null}
                  {entry.category && entry.kind === "question" ? (
                    <span className="jb-chip">
                      {BANK_CATEGORIES.find(([k]) => k === entry.category)?.[1]
                        ?? entry.category}
                    </span>
                  ) : null}
                  {entry.tags.map((t) => (
                    <span className="jb-chip" key={t}>{t}</span>
                  ))}
                  {entry.updatedAt ? (
                    <span className="jb-chip">
                      Updated {String(entry.updatedAt).slice(0, 10)}
                    </span>
                  ) : null}
                </div>
                <div className="act">
                  <button className="btn" type="button" disabled
                          title={READ_ONLY}>Edit</button>
                </div>
              </article>
            ))}
            {rows.length === 0 ? (
              <p className="empty">
                <strong>Nothing here.</strong>
                <span>
                  {needle || cat
                    ? "No entry in this tab matches. Clear the filter to see the rest."
                    : "Answers arrive as employers ask them."}
                </span>
              </p>
            ) : null}
          </div>
        </section>
      </div>
    </>
  );
}
