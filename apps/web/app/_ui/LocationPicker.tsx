import { COUNTRY_NAMES, CONTINENT_COUNTRIES } from "../search";
import { ClearAllLocations, OnlyThisCountry } from "./LocationActions";

/**
 * The 199-country picker, which is the saved search's storage contract as much
 * as it is a control.
 *
 * CLAUDE.md sets the contract and it is carried, not re-decided: **empty saved
 * `locations` means Worldwide**, a partial selection stores explicit country
 * codes, and `CONTINENT_COUNTRIES` assigns each of the 199 to exactly one
 * display group. Both tables are generated into `search.ts` from
 * `found_lane.py` rather than retyped, because a second answer to "which group
 * is Turkey in" parts from the first on the first edit.
 *
 * **Not gated on `live_board`, and the gate that was here rested on a premise
 * that had stopped being true.** Every box, both actions and the continent
 * toggles were `disabled={!writesEnabled}`, because choosing locations used to
 * change the saved search -- `LocationActions` still says so: "the write is
 * Save search, which reads the form afterwards". #234 deleted `SaveFilters`
 * and the actions row went with it, so a country box is now a `loc=` parameter
 * on a GET form and nothing else. A filter is a read.
 *
 * It cost nothing today, which is the only reason it survived: `live_board` is
 * on, so 0 of 203 boxes render disabled on production. It would have taken the
 * entire picker dark the first time the flag went off, for a write it no
 * longer performs.
 */
export function LocationPicker({ selected, extra = [] }: {
  /** Country codes, or empty for Worldwide, straight off the saved search. */
  selected: string[];
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
  // **The summary's two lines, in the board's own words.** `found_lane.py`
  // writes four pairs and v2 wrote `all 203` / `of 203`, which is a ratio
  // where the board writes a sentence. Clicked on both, 2026-09-09: the board
  // reads "Worldwide / All 202 countries", "India / 1 country", "2 countries /
  // Multiple continents", "No countries selected / Choose a continent or
  // country".
  //
  // "Multiple continents" is what the board says for any multi-country
  // selection, including 24 countries that are all in North America. It is
  // carried rather than corrected: this is a parity port, and a summary line
  // that disagrees with the board a reader is comparing against is the thing
  // being fixed.
  const [title, note] = worldwide
    ? ["Worldwide", `All ${total} countries`]
    : chosen.size === 0
      ? ["No countries selected", "Choose a continent or country"]
      : chosen.size === 1
        ? [COUNTRY_NAMES[[...chosen][0]] ?? [...chosen][0], "1 country"]
        : [`${chosen.size} countries`, "Multiple continents"];

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
                   />
            <span>Worldwide</span>
          </label>
          <ClearAllLocations />
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
                    <input type="checkbox" defaultChecked={picked === codes.length}
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
                              `loc` at all is a legal and meaningful state.

                              **`checked` with `readOnly` made every one of
                              these inert.** React refuses the toggle on a
                              controlled checkbox with no `onChange`, and
                              `readOnly` tells the browser the same thing, so
                              clicking a country did nothing at all: the box did
                              not tick, no request went out, and the lane never
                              moved. It was a picture of the saved selection
                              wearing a control's clothes. `defaultChecked` hands
                              the toggle back to the browser, which is what every
                              other control in this form already does -- the form
                              is a plain GET and the server renders the next
                              state, so nothing here needs to be controlled. */}
                          <input type="checkbox" name="loc" value={code}
                                 defaultChecked={chosen.has(code)}
                                       data-loc={code} data-continent-country={key}
                                 data-country-name={name} />
                          <span>{name}</span>
                        </label>
                        <OnlyThisCountry name={name} />
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
