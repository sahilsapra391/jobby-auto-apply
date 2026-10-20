import { DROPPED_DOCK } from "../chrome";
import { raw } from "./Chrome";
import { DroppedArchive } from "./DroppedArchive";
import { LaneEmpty } from "./LaneChrome";
import type { Cursor, Row } from "./db";

/**
 * Dropped is an archive, not a queue, and that is why it is a dock at the
 * lower left and a dialog rather than a sixth tab in the workflow strip.
 * CLAUDE.md is explicit about it; the plan's six-lane table is about query
 * predicates, which is (d), not about the tab bar.
 *
 * The dock markup comes from the board so the icon and the count pill match.
 * The dialog's head, hint and labels are the board's too (`dashboard.py`,
 * the `p-dropped` dialog): v2 had drifted to "Dropped", a bare "Close" and a
 * hint the board never wrote.
 */
export function DroppedDock({ count, rows, cursor, done, query, writesEnabled }: {
  count: number;
  /** The archive. The dialog draws and filters it; the dock only opens it. */
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
      {/* Raw markup, so React replaces it wholesale whenever the count
          changes. `DroppedOpen` listens on the document for that reason
          rather than on this button. */}
      <span {...raw(dock)} />
      <dialog className="dropped-dialog" id="p-dropped"
              aria-labelledby="dropped-title">
        <div className="dialog-head">
          <div>
            <p className="eyebrow">Archive</p>
            <h2 id="dropped-title">Dropped applications</h2>
          </div>
          <button className="dialog-close" id="dropped-close" type="button"
                  aria-label="Close dropped applications">×</button>
        </div>
        {/* **The archive draws its rows.** Each says which lane it came from
            and how far it had got -- `dropped_from` and `dropped_stage`,
            mirrored by 0012 -- and offers the posting and Restore, which is
            what the board's archive is for. */}
        {rows && rows.length ? (
          <DroppedArchive rows={rows} cursor={cursor ?? null}
                          done={done ?? true}
                          query={query ?? "lane=dropped"}
                          writesEnabled={writesEnabled} />
        ) : (
          <LaneEmpty lane="dropped" />
        )}
      </dialog>
    </>
  );
}
