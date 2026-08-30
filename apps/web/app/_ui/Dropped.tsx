import { DROPPED_DOCK } from "../chrome.generated";
import { DROPPED_COUNT } from "./fixture";
import { raw } from "./Chrome";

/**
 * Dropped is an archive, not a queue, and that is why it is a dock at the
 * lower left and a dialog rather than a sixth tab in the workflow strip.
 * CLAUDE.md is explicit about it; the plan's six-lane table is about query
 * predicates, which is (d), not about the tab bar.
 *
 * The dock markup comes from the board so the icon and the count pill match.
 */
export function DroppedDock() {
  const dock = DROPPED_DOCK.replace(
    /<span class="dropped-count">\d+<\/span>/,
    `<span class="dropped-count">${DROPPED_COUNT}</span>`,
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
          Roles you passed on, kept as a record. {DROPPED_COUNT} of them.
        </p>
        <div className="filters">
          <input type="search" placeholder="Search dropped…" aria-label="Search dropped" />
          <select aria-label="Sort dropped" defaultValue="new">
            <option value="new">Newest</option>
            <option value="company">Company</option>
          </select>
        </div>
        <div className="sheet">
          <div className="empty">Rows arrive with Postgres in the next piece.</div>
        </div>
      </dialog>
    </>
  );
}
