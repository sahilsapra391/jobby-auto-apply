import { DROPPED_DOCK } from "../chrome.generated";
import { raw } from "./Chrome";
import { LaneEmpty } from "./LaneChrome";
import { LaneList } from "./LaneList";
import type { Cursor, Row } from "./db";

/**
 * Dropped is an archive, not a queue, and that is why it is a dock at the
 * lower left and a dialog rather than a sixth tab in the workflow strip.
 * CLAUDE.md is explicit about it; the plan's six-lane table is about query
 * predicates, which is (d), not about the tab bar.
 *
 * The dock markup comes from the board so the icon and the count pill match.
 */
export function DroppedDock({ count, rows, cursor, done, query, writesEnabled }: {
  count: number;
  /** The archive's first page. The dialog draws it; the dock only opens it. */
  rows?: Row[];
  cursor?: Cursor;
  done?: boolean;
  query?: string;
  writesEnabled?: boolean;
}) {
  const dock = DROPPED_DOCK.replace(
    /<span class="dropped-count">\d+<\/span>/,
    `<span class="dropped-count">${count}</span>`,
  );
  return (
    <>
      <span {...raw(dock)} />
      <dialog className="dropped-dialog" id="p-dropped">
        <div className="dialog-head">
          <div>
            <p className="eyebrow">Archive</p>
            <h2>Dropped</h2>
          </div>
          <button className="dialog-close" type="button" aria-label="Close">×</button>
        </div>
        <p className="hint">
          Roles you dropped, kept as a record. {count} of them.
        </p>
        {/* A GET form on the board route, like every other lane search: the
            dialog is markup on the same page, so a filtered archive is a URL
            the reader can keep. */}
        <form className="filters" method="get" action="/">
          <input type="hidden" name="lane" value="dropped" />
          <input type="search" name="q" placeholder="Search dropped…"
                 aria-label="Search dropped" />
          <button className="btn" type="submit">Search</button>
        </form>
        {/* **The archive draws its rows.** It said "Rows arrive with Postgres
            in the next piece" over a count pill showing the real number, which
            is the one lane v2 never wired. Each row says which lane it came
            from and how far it had got -- `dropped_from` and `dropped_stage`,
            mirrored by 0012 -- and offers Restore, which is what the board's
            archive is for. */}
        {rows && rows.length ? (
          <LaneList lane="dropped" shape="dropped" initial={rows}
                    initialCursor={cursor ?? null} done={done ?? true}
                    query={query ?? "lane=dropped"}
                    writesEnabled={writesEnabled} />
        ) : (
          <LaneEmpty lane="dropped" />
        )}
      </dialog>
    </>
  );
}
