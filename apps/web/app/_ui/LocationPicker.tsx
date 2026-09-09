import { COUNTRY_NAMES, CONTINENT_COUNTRIES } from "../search.generated";
import { ClearAllLocations, OnlyThisCountry } from "./LocationActions";
import { READ_ONLY } from "./actions";

/**
 * The 199-country picker, which is the saved search's storage contract as much
 * as it is a control.
 *
 * CLAUDE.md sets the contract and it is carried, not re-decided: **empty saved
 * `locations` means Worldwide**, a partial selection stores explicit country
 * codes, and `CONTINENT_COUNTRIES` assigns each of the 199 to exactly one
 * display group. Both tables are generated into `search.generated.ts` from
 * `found_lane.py` rather than retyped, because a second answer to "which group
 * is Turkey in" parts from the first on the first edit.
 *
 * **Rendered with the boxes disabled and the summary honest.** Choosing
 * locations changes the saved search, which is a write, and writes are behind
 * `live_board`. The reader can see the whole scope and what it currently
 * covers; what they cannot do yet is change it. Absent would say the board
 * cannot do this at all.
 */
export function LocationPicker({ selected, writesEnabled, extra = [] }: {
  /** Country codes, or empty for Worldwide, straight off the saved search. */
  selected: string[];
  writesEnabled: boolean;
  /** Codes the pool holds that the taxonomy does not place. */
  extra?: string[];
}) {
  // **"Other locations" is derived from the pool, not a seventh constant.**
  // The board builds it as `COUNTRY_NAMES | countries(jobs)` minus everything
  // `CONTINENT_COUNTRIES` assigns, so it is however many unplaced codes the
  // corpus currently holds -- 2 today, and the difference between the picker
  // saying 199 and saying 201. A static seventh group would be right until the
  // next sweep found a country nobody had listed.
  const groups: [string, string, string[]][] = [
    ...CONTINENT_COUNTRIES,
    ...(extra.length ? [["other", "Other locations", extra] as
                        [string, string, string[]]] : []),
  ];
  const total = Object.keys(COUNTRY_NAMES).length + extra.length;
  // Empty is Worldwide, which is the contract rather than a display choice: a
  // saved search that names no country is not a search that matches none.
  const worldwide = selected.length === 0;
  const chosen = new Set(worldwide
    ? [...Object.keys(COUNTRY_NAMES), ...extra] : selected);
  const title = worldwide ? "Worldwide"
    : chosen.size === 1
      ? (COUNTRY_NAMES[[...chosen][0]] ?? [...chosen][0])
      : `${chosen.size} countries`;
  const note = worldwide ? `all ${total}` : `of ${total}`;
  const dark = writesEnabled ? undefined : READ_ONLY;

  return (
    <details className="jb-location-picker" data-location-total={total}>
      <summary>
        <span>
          <span className="jb-location-title">{title}</span>
          <small>{note}</small>
        </span>
        <span className="jb-location-chevron" aria-hidden="true" />
      </summary>
      <div className="jb-location-menu">
        <div className="jb-location-actions">
          <label className="jb-world">
            {/* An aggregate over the country boxes below, not a value of its
                own. Worldwide is the storage contract's empty selection, which
                is what submitting no `loc` already means. */}
            <input type="checkbox" checked={worldwide} readOnly
                   data-decorative="aggregate over the country boxes; empty loc already means worldwide"
                   disabled={!writesEnabled} title={dark} />
            <span>Worldwide</span>
          </label>
          <ClearAllLocations enabled={writesEnabled} title={dark} />
        </div>
        <div className="jb-continent-list">
          {groups.map(([key, label, codes]) => {
            const picked = codes.filter((c) => chosen.has(c)).length;
            return (
              <details className="jb-continent" data-continent-panel={key} key={key}>
                <summary>
                  <span className="jb-continent-main">
                    {/* Also an aggregate: it selects and clears its children,
                        and the children are what carry the value. */}
                    <input type="checkbox" checked={picked === codes.length}
                           readOnly disabled={!writesEnabled} title={dark}
                           data-decorative="selects and clears its children; the countries carry the value"
                           aria-label={`Select all countries in ${label}`} />
                    <span>{label}</span>
                  </span>
                  <span className="jb-continent-count">
                    {picked} of {codes.length}
                  </span>
                </summary>
                {/* `jb-country-row > jb-country`, with the Only button beside
                    it, which is the board's markup. `jb-check` renders as the
                    workplace toggles instead: wrong grid, wrong hit area, and
                    no Only. */}
                <div className="jb-country-grid">
                  {codes.map((code) => {
                    const name = COUNTRY_NAMES[code] ?? code;
                    return (
                      <div className="jb-country-row" key={code}>
                        <label className="jb-country">
                          {/* **The country boxes are the ones that submit.**
                              An empty selection is Worldwide, which is the
                              storage contract the board already holds, so no
                              `loc` at all is a legal and meaningful state. */}
                          <input type="checkbox" name="loc" value={code}
                                 checked={chosen.has(code)}
                                 readOnly disabled={!writesEnabled} title={dark}
                                 data-loc={code} data-continent-country={key}
                                 data-country-name={name} />
                          <span>{name}</span>
                        </label>
                        <OnlyThisCountry name={name} enabled={writesEnabled}
                                         title={dark} />
                      </div>
                    );
                  })}
                </div>
              </details>
            );
          })}
        </div>
      </div>
    </details>
  );
}
