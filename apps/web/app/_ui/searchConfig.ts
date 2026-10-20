/**
 * The Found filter form, read, and the saved-search config built from it.
 *
 * **Two halves, so the half that decides can be run.** `readFilterForm` is the
 * DOM walk and does nothing but collect what the form holds;
 * `configFromSnapshot` is pure and is what `tests/save_search_sends_what_the_form_holds.test.mjs`
 * compiles out and runs. A test of the mapping that has to stand up a DOM to
 * reach it ends up testing the stand-in.
 *
 * **What the first version got wrong, and why each matters.** It read the
 * first `[name="q"]` and split it on spaces. The first `q` in the form is the
 * first chip's hidden input, so a second chip was lost, and a chip reading
 * "product manager" became two keywords, `product` and `manager` -- which the
 * sweep then matched separately, because `ingest` ORs keywords. It also left a
 * field out whenever it was empty, and `search_config.save` merges a save over
 * what is stored, so an omitted field keeps its old value: clearing the
 * keywords and pressing Save left the old keywords in place. Every field the
 * form holds now goes out, and an emptied one goes out empty.
 *
 * A field the form does not hold stays out. The advanced controls are only
 * drawn while writes are on, and the config's degree, role, employment and job
 * type have no control at all; sending a default for any of them would be this
 * board asserting a filter the reader never set, over one they did.
 */

/** What the form holds, field by field. `null` is "the form has no such field". */
export type FilterSnapshot = {
  /**
   * Every keyword chip, from its hidden input. The box is not a keyword until
   * Enter makes it a chip, which is the board's rule too: its Save sends
   * `keywords.slice()`, the chips, and never the half-typed box.
   */
  q: string[];
  not: string | null;
  scope: string | null;
  within: string | null;
  sort: string | null;
  direct: boolean | null;
  /** The ticked workplace boxes, or null when there are none to tick. */
  wp: string[] | null;
  cos: string | null;
  conames: string | null;
  /** "" is "Open to all". */
  exp: string | null;
  countries: { value: string; checked: boolean }[];
  incremote: boolean | null;
  /** The unknown-bucket switches the form draws, by the config's key. */
  unknowns: Record<string, boolean>;
};

/** Which form field carries each `includeUnknowns` key. Degree has none. */
export const UNKNOWN_FIELDS: Record<string, string> = {
  experience: "unkexp", workplace: "unkwp", postedAt: "unkposted",
};

export function configFromSnapshot(form: FilterSnapshot): Record<string, unknown> {
  const config: Record<string, unknown> = {};

  // One keyword per chip, each a phrase, deduped the way the chips are.
  // Always sent: an empty list is how a cleared keyword set is saved.
  config.keywords = [...new Set(form.q.map((k) => k.trim()).filter(Boolean))];

  if (form.not !== null) {
    const not = form.not.trim();
    config.excludeKeywords = not ? [not] : [];
  }
  if (form.scope) config.searchScope = form.scope;
  if (form.within) config.postedWithin = form.within;
  if (form.sort) config.sort = form.sort;
  if (form.direct !== null) config.directOnly = form.direct;

  // None ticked is stored as all three, which is `search_config.validate`'s
  // own reading of an empty list and the lane's: no workplace opinion.
  if (form.wp !== null) config.workplace = form.wp;

  if (form.cos !== null) config.companiesMode = form.cos || "all";
  if (form.conames !== null) {
    config.companies = form.conames.split(",").map((c) => c.trim()).filter(Boolean);
  }

  // "" is "Open to all", which the store spells `null`, and that is a real
  // value rather than an absent one.
  if (form.exp !== null) {
    config.maxExperienceYears = form.exp === "" ? null : Number(form.exp);
  }

  // Empty means every country, which is the storage contract, so a fully
  // ticked picker and an untouched one save the same Worldwide.
  if (form.countries.length) {
    const picked = form.countries.filter((c) => c.checked);
    config.locations = picked.length === form.countries.length
      ? []
      : picked.map((c) => ({ country: c.value }));
  }
  if (form.incremote !== null) config.includeRemoteForSelection = form.incremote;

  // Only the switches the form draws. `search_config.save` merges this object
  // into the stored one key by key, so degree keeps whatever it was.
  if (Object.keys(form.unknowns).length) config.includeUnknowns = { ...form.unknowns };
  return config;
}

/** Whether a save would store "no country at all", which the board refuses. */
export function noLocationPicked(form: FilterSnapshot): boolean {
  return form.countries.length > 0 && !form.countries.some((c) => c.checked);
}

/** Walk the filter card and collect what it holds. */
export function readFilterForm(root: ParentNode): FilterSnapshot {
  const all = (selector: string) =>
    Array.from(root.querySelectorAll<HTMLInputElement>(selector));
  const field = (name: string) =>
    root.querySelector<HTMLInputElement | HTMLSelectElement>(`[name="${name}"]`);
  const text = (name: string) => {
    const node = field(name);
    return node ? node.value : null;
  };
  /** A box that rides with a hidden `=0` companion: read the box itself. */
  const box = (name: string) => {
    const node = root.querySelector<HTMLInputElement>(
      `input[type="checkbox"][name="${name}"]`);
    return node ? node.checked : null;
  };
  const wpBoxes = all('input[type="checkbox"][name="wp"]');
  const unknowns: Record<string, boolean> = {};
  for (const [key, name] of Object.entries(UNKNOWN_FIELDS)) {
    const on = box(name);
    if (on !== null) unknowns[key] = on;
  }
  return {
    q: all('input[type="hidden"][name="q"]').map((n) => n.value),
    not: text("not"),
    scope: text("scope"),
    within: root.querySelector<HTMLInputElement>('input[name="within"]:checked')?.value
      ?? null,
    sort: text("sort"),
    direct: box("direct"),
    wp: wpBoxes.length ? wpBoxes.filter((b) => b.checked).map((b) => b.value) : null,
    cos: text("cos"),
    conames: text("conames"),
    exp: text("exp"),
    countries: all("input[data-country-name]")
      .map((b) => ({ value: b.value, checked: b.checked })),
    incremote: box("incremote"),
    unknowns,
  };
}
