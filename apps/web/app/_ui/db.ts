import { Pool, types } from "pg";
// @ts-expect-error -- generated from deploy/r2.mjs, plain JS with no types.
import { configured as r2Configured, listKeys } from "./r2.generated.mjs";
import { LANES, WINDOWS, type Lane, type Sort } from "../lanes.generated";
import { COUNTRY_NAMES } from "../search.generated";

/**
 * Read-only access to the board's Postgres.
 *
 * **Nothing here writes, and nothing will while the parity clock runs.** A new
 * endpoint writing straight to Postgres is a third write path parity does not
 * know about, and the file store would silently fall behind on exactly the
 * records it touched.
 *
 * One pool for the process. Vercel keeps a function warm across requests, so a
 * connection per request would open one every time and Neon's pooler would be
 * doing the work twice.
 */
/**
 * A timestamp reaches the app as an ISO string, once, here.
 *
 * **`node-postgres` returns `timestamptz` as a `Date`, and `String(date)` is a
 * locale string**: `String(d).slice(0, 10)` is `"Wed Sep 02"`, which then fails
 * to parse. That is one bug and it surfaced twice. On Found it produced
 * "Posted date unknown" over a posting dated that morning, which reads as
 * missing data. On Submitted it produced `"Wed Aug 19"`, which reads as a date
 * and is a truncated locale string -- the same fault wearing a plausible face,
 * which is why the second instance survived a screen recording.
 *
 * Converting at each render site fixes the two that are known. Converting here
 * means the next component to receive a timestamp cannot reintroduce it,
 * because a `Date` never reaches one.
 *
 * 1114 is `timestamp`, 1184 is `timestamptz`, 1082 is `date`. The first two
 * become full ISO strings and the third stays a plain `YYYY-MM-DD`, which is
 * what it is.
 */
types.setTypeParser(1184, (value) =>
  value === null ? null : new Date(value).toISOString());
types.setTypeParser(1114, (value) =>
  value === null ? null : new Date(value + "Z").toISOString());
types.setTypeParser(1082, (value) => value);

declare global {
  // eslint-disable-next-line no-var
  var __jobbyPool: Pool | undefined;
}

export function pool(): Pool {
  if (!global.__jobbyPool) {
    const connectionString = process.env.JOBBY_DATABASE_URL;
    if (!connectionString) {
      throw new Error(
        "JOBBY_DATABASE_URL is not set. The board reads Postgres directly; " +
        "without it there is nothing to render and saying so beats an empty " +
        "lane that looks like an empty pool.");
    }
    global.__jobbyPool = new Pool({
      connectionString,
      max: 4,
      // A query that hangs is worse than one that fails: it holds the request
      // until the platform kills it and reports nothing.
      connectionTimeoutMillis: 8000,
      statement_timeout: 10000,
    });
  }
  return global.__jobbyPool;
}

/** The single account, until §6's multi-tenant work gives it a real one. */
export async function accountId(): Promise<string> {
  const { rows } = await pool().query("select id from accounts order by created_at limit 1");
  if (!rows.length) throw new Error("no account row exists");
  return rows[0].id;
}

export type Cursor = { at: string | null; id: string } | null;

export type Row = Record<string, unknown> & { id: string };

/**
 * One page of a lane, plus the cursor for the next.
 *
 * Keyset, never OFFSET: the parameters are [account, afterAt, afterId, limit]
 * and the SQL is generated from scripts/v2_lanes.py, so the ordering, the
 * `nulls last` and the three cases the cursor predicate has are written once.
 */
/** The Found lane's filters. Absent means off, never "match everything". */
export type Filters = {
  q?: string[]; within?: string; direct?: boolean;
  /** Words to exclude from title and summary. */
  not?: string;
  /** Which columns `q` searches: title, or title and summary. */
  scope?: string;
  /** "only" or "hide", against the comma-separated names in `conames`. */
  cos?: string;
  conames?: string;
  /** ISO country codes. Empty is Worldwide, which is the board's contract. */
  loc?: string[];
  /** Workplace kinds to keep. */
  wp?: string[];
  /** Max years of experience the posting may ask for. */
  exp?: string;
  /**
   * The four widening switches. Undefined is the default, which is on.
   *
   * `unkposted` keeps postings with no date under a date window, `unkwp` keeps
   * postings with no workplace under a workplace selection, `unkexp` keeps
   * postings with no stated years under a Max experience, and `incremote`
   * keeps location-free remote work under a scoped location search.
   */
  unkposted?: boolean;
  unkwp?: boolean;
  unkexp?: boolean;
  incremote?: boolean;
};

/**
 * The arguments a generated query asks for, in the order it asks for them.
 *
 * **Each query is numbered from 1 over the parameters it actually names**, so
 * the caller cannot assume a fixed layout: the Found count takes `account` and
 * the three filters and never mentions the cursor. Building the array from the
 * query's own `params` list is what keeps the two in step, and it is the same
 * reason the SQL is generated rather than written twice.
 */
function argsFor(params: readonly string[], values: Record<string, unknown>) {
  return params.map((name) => {
    if (!(name in values)) {
      throw new Error(`the query asks for ${name}, which this caller has not got`);
    }
    return values[name];
  });
}

/**
 * Every filter as a SQL parameter, with the shapes the generated predicates
 * expect.
 *
 * **`null` is the off switch throughout**, because each predicate is written
 * `(%(x)s is null or <test>)`. A filter that arrived as an empty string or an
 * empty array would be a predicate that matches nothing rather than one that
 * does not apply, which is the difference between "no filter" and "no
 * results" -- the two states this board has already confused once.
 */
/**
 * Every filter, read from one URL, in one place.
 *
 * **The lane route hand-listed three of them and `page.tsx` read seven.** So
 * page one filtered and Load more did not: the same query string produced a
 * filtered first page from the server component and an unfiltered second page
 * from `/api/lane`, and the count in the header agreed with neither. Two
 * readers of one URL is the declared-twice shape this repository has met in
 * `GATE_PATHS`, `verify_parity.FIELDS`, the migrations list and the generated
 * file list; here it was caught by checking that a filter changed the rows
 * rather than only the count.
 *
 * Both callers come through this now, so a filter added to `Filters` is read
 * by both or by neither.
 */
/**
 * The filter keys, once, with how each rides a URL.
 *
 * **`filtersFrom` read ten and `laneQuery` wrote three.** That is the
 * declared-twice shape this file's own docstring lists four instances of, in
 * the one place it claimed to have fixed: the route read every filter
 * correctly and the *writer* dropped seven, so page one was filtered and every
 * page after it was not. Measured on production with `exp=2`: page one 50 rows
 * and 0 violations, page two 50 rows and 25 violations carrying `years_min` up
 * to 12.
 *
 * It survived because almost nobody reached the advanced controls while they
 * needed a Filter button nobody found. Making them apply on change is exactly
 * what turns this from latent into the common case, so it is fixed in the same
 * series rather than after it.
 *
 * Both directions are generated from this list now. A filter added to
 * `Filters` and not added here is read by neither, which is a visible bug
 * rather than a silent one.
 */
export const FILTER_KEYS = [
  { key: "q", kind: "many" },
  { key: "within", kind: "one" },
  { key: "direct", kind: "flag" },
  { key: "not", kind: "one" },
  { key: "scope", kind: "one" },
  { key: "cos", kind: "one" },
  { key: "conames", kind: "one" },
  { key: "loc", kind: "many" },
  { key: "wp", kind: "many" },
  { key: "exp", kind: "one" },
  // **The three unknown buckets and the picker's remote toggle. On by
  // default, which is what makes them `onflag` rather than `flag`.**
  //
  // A checkbox submits when it is ticked and not when it is not, so a control
  // whose default is ticked has no HTML shape that means "absent is on":
  // either every URL carries the default, or absence means the opposite of it.
  // Each of these therefore rides with a hidden `=0` companion of the same
  // name, and the **last** value wins:
  //
  //   ticked     `unkposted=0&unkposted=1`   last "1"  -> on
  //   unticked   `unkposted=0`               last "0"  -> off
  //   absent      nothing                              -> on, the default
  //
  // Which keeps the bare `/?lane=found` case honest: a reader who has touched
  // none of these gets the board's defaults, and only one who unticked
  // something carries a parameter.
  { key: "unkposted", kind: "onflag" },
  { key: "unkwp", kind: "onflag" },
  { key: "unkexp", kind: "onflag" },
  { key: "incremote", kind: "onflag" },
] as const;

export function filtersFrom(params: URLSearchParams): Filters {
  const out: Record<string, unknown> = {};
  for (const { key, kind } of FILTER_KEYS) {
    if (kind === "flag") out[key] = params.get(key) === "1";
    // On unless the last value says otherwise. `get` returns the *first*,
    // which is the hidden companion and always "0", so a box that is ticked
    // would read as unticked on every single load.
    else if (kind === "onflag") {
      const all = params.getAll(key);
      out[key] = all.length ? all[all.length - 1] !== "0" : undefined;
    }
    else if (kind === "many") {
      const all = params.getAll(key);
      out[key] = all.length ? all : undefined;
    } else out[key] = params.get(key) ?? undefined;
  }
  return out as Filters;
}

function filterValues(f: Filters | undefined) {
  const list = (xs?: string[]) => (xs && xs.length ? xs : null);
  const names = (f?.conames ?? "").split(",").map((x) => x.trim()).filter(Boolean);
  return {
    // **A list, because a keyword is a chip and chips are OR'd.** One string
    // meant one tsquery and `product manager` meant product AND manager
    // anywhere in the row.
    // **`[] || null` is `[]`, because an empty array is truthy in JavaScript.**
    // The keyword box submits alongside the hidden chips, so an empty box put
    // `q=` in the URL, this produced `[]` rather than null, and the predicate
    // below then matched nothing at all: `unnest('{}')` yields no rows, so the
    // `exists` is false for every posting. The loosest filter the form can
    // produce returned "0 of 22,735".
    //
    // Deduped as well, like the board -- `if (word && keywords.indexOf(word)
    // === -1)`. The box submits alongside the chips, so pressing Enter on a
    // keyword that is already a chip would otherwise add it twice and the same
    // predicate would run twice for one result.
    q: (() => {
      const words = [...new Set((f?.q ?? []).map((k) => k.trim()).filter(Boolean))];
      return words.length ? words : null;
    })(),
    within: f?.within && f.within in WINDOWS ? WINDOWS[f.within] : null,
    direct: f?.direct ?? false,
    // `exclude` rather than `not`: the SQL parameter name ends up in
    // generated statements and `not` is a reserved word there.
    exclude: f?.not?.trim() || null,
    // **"Title and description" was a no-op, and this line was the whole
    // reason.** It compared against `"all"`, which nothing on this axis has
    // ever emitted: the select's vocabulary is `title` | `title+description`
    // (`found_lane.SCOPE_OPTIONS`, and v2 copies it), so both choices
    // collapsed to `"title"` and the title-only predicate stayed on either
    // way. It was the only occurrence of that string on this axis anywhere in
    // the repository, which is what a value nobody produces looks like.
    //
    // The SQL reads it as: `scope = 'title'` restricts to the title, anything
    // else does not. So the narrower choice is the one that has to be named,
    // and null is the wider one.
    scope: f?.scope === "title" ? "title" : null,
    // A mode with no names is not a filter. Both halves or neither, or "hide"
    // with an empty box would hide everything.
    cos: names.length && (f?.cos === "only" || f?.cos === "hide") ? f.cos : null,
    conames: names.length ? names : null,
    loc: list(f?.loc),
    // All three ticked is the same as no opinion, and passing it as a filter
    // would drop the 69.8% of postings whose workplace is not recorded.
    wp: f?.wp && f.wp.length && f.wp.length < 3 ? f.wp : null,
    exp: f?.exp && /^\d+$/.test(f.exp) ? Number(f.exp) : null,
    // Undefined is the default, which for all four is on.
    unk_posted: f?.unkposted !== false,
    unk_wp: f?.unkwp !== false,
    unk_exp: f?.unkexp !== false,
    inc_remote: f?.incremote !== false,
  };
}

export async function page(lane: Lane, after: Cursor, limit = 50,
                           sort: Sort = "new", filters?: Filters) {
  const account = await accountId();
  // One query per (lane, sort), all generated. Substituting an ORDER BY on
  // this side would be a second spelling of the predicate.
  const query = LANES[lane].sorts[sort]
    ?? { sql: LANES[lane].page, params: LANES[lane].pageParams,
         cursor: LANES[lane].cursor };
  const { rows } = await pool().query(query.sql, argsFor(query.params, {
    account, after_at: after?.at ?? null, after_id: after?.id ?? null, limit,
    ...filterValues(filters),
  }));
  // **The cursor column comes from the query, not from a guess about it.**
  // This used to be inferred here -- score if the sort was score, else
  // posted_at if the row had one, else updated_at -- which restated the
  // ORDER BY in a second language. It happened to be right for four lanes and
  // would have been silently wrong for Needs you the moment that lane started
  // ordering by blocker count: a cursor naming the wrong column does not
  // error, it compares the wrong values and restarts the lane at the top on
  // every page. `v2_lanes` emits the key beside the SQL that orders by it.
  // **The document columns are enriched here so both paths get them.** The
  // Submitted row needs to know which artifacts the bucket holds and which
  // filenames more than one application claims, and `LaneList` fetches page
  // two through `/api/lane` rather than through the server component -- so
  // enriching in `page.tsx` would give page one four honest states and page
  // two the old two.
  if (lane === "done" && rows.length) {
    const [stored, siblings] = await Promise.all(
      [storedArtifacts(), documentSiblings()]);
    for (const row of rows) {
      row.stored_artifacts = stored === null ? null : [...stored];
      row.document_siblings = Object.fromEntries(siblings);
    }
  }
  const last = rows[rows.length - 1];
  const at = last === undefined ? null : (last[query.cursor] ?? null);
  return {
    rows: rows as Row[],
    next: last ? { at: cursorValue(at), id: last.id } : null,
    done: rows.length < limit,
  };
}

/**
 * A cursor value the generated SQL can cast back.
 *
 * A date has to go out as an ISO string; a number must not be put through
 * `new Date`, which would turn 70 into 1970 and then fail the `::int` cast
 * its query applies to it.
 *
 * **Keyed on what the value is, not on which sort produced it.** The first
 * version asked `sort === "score"`, which meant every new numeric ordering had
 * to remember to add itself here; Needs you walks a negative blocker rank and
 * would have been silently converted to a date. A `Date` from the driver and a
 * number from the driver are distinguishable on sight, so this asks.
 */
function cursorValue(at: unknown): string | null {
  if (at === null || at === undefined) return null;
  if (typeof at === "number") return String(at);
  if (at instanceof Date) return at.toISOString();
  return new Date(at as string).toISOString();
}

export async function counts(active: Lane,
                             filters?: Filters): Promise<Record<Lane, number>> {
  const account = await accountId();
  const out = {} as Record<Lane, number>;
  // **The filter counts the lane it was typed into, and no other.** The
  // comment here used to say the other lanes "ask for the account alone", and
  // that was not true of the generated SQL: every lane's `countParams`
  // declares `q`, so `argsFor` supplied it and every badge moved. Typing
  // "engineer" into Found collapsed Watching to 2, which is the kind of thing
  // that makes a reader distrust the whole board -- a number they did not ask
  // to change, changing.
  //
  // Each lane has its own search row, so a keyword still filters the lane it
  // belongs to. It is `active` that decides which one that is, not the mere
  // presence of a parameter in a URL every lane shares.
  const filtered = { account, ...filterValues(filters) };
  const plain = { account, ...filterValues(undefined) };
  await Promise.all(
    (Object.keys(LANES) as Lane[]).map(async (lane) => {
      const { rows } = await pool().query(
        LANES[lane].count,
        argsFor(LANES[lane].countParams, lane === active ? filtered : plain));
      out[lane] = Number(rows[0].count);
    }),
  );
  return out;
}

/**
 * One row of a lane, if the lane still holds it.
 *
 * **The answer to "is this still here" comes from the lane's own predicate.**
 * A board applying an event could infer it from the event's `to` field
 * instead, and that inference is a second spelling of the `where` clause in
 * another language: it would be wrong first for Submitted, whose membership is
 * two correlated subqueries over the event log rather than a status.
 *
 * `null` means gone from this lane. Not an error, and deliberately not told
 * apart from moved, dropped or never-existed: the board's next act is the same
 * in every case, which is to stop drawing it.
 */
export async function row(lane: Lane, id: string): Promise<Row | null> {
  const account = await accountId();
  const { rows } = await pool().query(
    LANES[lane].row, argsFor(LANES[lane].rowParams, { account, row_id: id }));
  return (rows[0] as Row) ?? null;
}

/**
 * Everything that identifies a lane view, as one string.
 *
 * **One prop, because three were forgettable and were forgotten.** `LaneList`
 * has always taken `sort` and `filters` and has always documented why they
 * must travel with the cursor; `page.tsx` passed neither. Measured against the
 * live database on 2026-09-02: page one of Found by Match % ends on score 93,
 * page two asks the date query for rows before the timestamp "93", Postgres
 * answers `date/time field value out of range`, and the lane draws "50 shown,
 * and that is all of them" over 17,341 rows.
 *
 * Splitting a view across several optional props means a caller can supply
 * some of them. Splitting it across one means it cannot.
 */
/**
 * Is any filter set at all: **read off `FILTER_KEYS`, never a list beside it.**
 *
 * `FoundSurface` asked `!!(q || within || direct)` -- three of the ten -- so a
 * lane narrowed only by locations, workplace, an exclude term or a company
 * list reported itself unfiltered. Clear went dark on a filtered board, and
 * the count line dropped the pool number, which is the third place in this
 * repository where a subset of a field list has been hand-written next to the
 * list itself.
 */
export function anyFilterSet(f: Filters | undefined): boolean {
  if (!f) return false;
  for (const { key, kind } of FILTER_KEYS) {
    const v = (f as Record<string, unknown>)[key];
    if (v == null) continue;
    if (kind === "many") { if (Array.isArray(v) && v.length) return true; continue; }
    if (kind === "flag") { if (v) return true; continue; }
    if (String(v).trim() !== "") return true;
  }
  return false;
}

export function laneQuery(lane: Lane, sort: Sort, filters?: Filters): string {
  const p = new URLSearchParams({ lane, sort });
  if (!filters) return p.toString();
  const bag = filters as unknown as Record<string, unknown>;
  for (const { key, kind } of FILTER_KEYS) {
    const value = bag[key];
    if (kind === "flag") { if (value) p.set(key, "1"); continue; }
    if (kind === "many") {
      if (Array.isArray(value)) for (const v of value) p.append(key, String(v));
      continue;
    }
    if (value !== undefined && value !== null && value !== "") p.set(key, String(value));
  }
  return p.toString();
}

/**
 * The board's own scan state, from `board_meta`.
 *
 * **v2 drew a hardcoded fixture here.** `_ui/fixture.ts` carried
 * `nextRun: "19:47 EDT"`, `lastScan: "15:51 EDT"` and five invented problem
 * entries summing to 11, and those are exactly the three numbers a reader saw
 * at 21:28 with a next run in the past. Behind the fixture there was nothing:
 * the table had the right columns and zero rows until `class_b._mirror_meta`.
 *
 * Null when nothing has been mirrored, and the banner says so rather than
 * inventing a substitute. That is the whole lesson of the fixture.
 */
export type BoardMeta = {
  lastIngest: Record<string, unknown> | null;
  nextScanAt: string | null;
  scanStartedAt: string | null;
  scanFinishedAt: string | null;
  blocked: string | null;
  slotsParked: string | null;
  /**
   * When the mirror last wrote, which is this board's "Updated".
   *
   * **The analogue of the old board's render time, not of `now()`.** The board
   * is a published document, so the moment it was generated really was the age
   * of its data. A server-rendered page has no such moment -- "rendered just
   * now" is true on every request and says nothing -- so the honest stamp is
   * when the store last reached Postgres.
   */
  updatedAt: string | null;
};

export async function boardMeta(): Promise<BoardMeta | null> {
  const account = await accountId();
  const { rows } = await pool().query(
    `select last_ingest, next_scan_at, scan_started_at, scan_finished_at,
            blocked, slots_parked, updated_at
       from board_meta where account_id = $1`, [account]);
  if (!rows.length) return null;
  const r = rows[0];
  return {
    lastIngest: r.last_ingest ?? null,
    nextScanAt: r.next_scan_at ? new Date(r.next_scan_at).toISOString() : null,
    scanStartedAt: r.scan_started_at ? new Date(r.scan_started_at).toISOString() : null,
    scanFinishedAt: r.scan_finished_at ? new Date(r.scan_finished_at).toISOString() : null,
    blocked: r.blocked ?? null,
    slotsParked: r.slots_parked ?? null,
    updatedAt: r.updated_at ? new Date(r.updated_at).toISOString() : null,
  };
}

/**
 * The reader's first name, for the board's own stamp.
 *
 * `identity` is the profile the device keeps; the board greets from
 * `firstName` and falls back to "there" (`dashboard.py:673`), which is carried
 * across rather than re-decided here.
 */
export async function readerName(): Promise<string> {
  const account = await accountId();
  const { rows } = await pool().query(
    "select identity from profiles where account_id = $1", [account]);
  const identity = (rows[0]?.identity ?? {}) as Record<string, unknown>;
  const first = typeof identity.firstName === "string"
    ? identity.firstName.trim() : "";
  return first || "there";
}

/**
 * The provider connections, from `provider_links`.
 *
 * The same fixture said OpenAI was connected and the other two were not, while
 * all three were connected and healthy. It also called Gemini's agent "agy"
 * and pinned `gemini-3.7-flash-medium`; the model pin is real and comes from
 * `providers.MODEL_IDS`, the tool label was invented.
 *
 * **Never a credential.** `provider_links` has no column for one and must not
 * get one: keys live in `~/.job-application/provider-secrets/` and reach no
 * queue, no Vercel environment and no KV.
 */
export type ProviderLink = {
  provider: string;
  status: string;
  model: string | null;
  health: string | null;
  detail: string | null;
  order: number | null;
  connectedAt: string | null;
};

export async function providers(): Promise<ProviderLink[]> {
  const account = await accountId();
  const { rows } = await pool().query(
    `select provider, status, model_pinned, health, detail, connection_order,
            connected_at
       from provider_links where account_id = $1
      order by connection_order nulls last, provider`, [account]);
  return rows.map((r) => ({
    provider: r.provider,
    status: r.status,
    model: r.model_pinned ?? null,
    health: r.health ?? null,
    detail: r.detail ?? null,
    order: r.connection_order ?? null,
    connectedAt: r.connected_at ? new Date(r.connected_at).toISOString() : null,
  }));
}

/**
 * The saved search's location scope, in the words the board uses.
 *
 * **An empty `locations` is Worldwide**, which is the storage contract, not an
 * absence. v2 displayed neither, so a page led by UK listings read as a fault
 * when it was the scope working exactly as saved. The picker that changes it
 * is a separate piece of work; saying what it currently is costs one line.
 */
export async function locationScope(): Promise<string | null> {
  const { words } = await savedSearch();
  return words;
}

/**
 * The saved search, as both the sentence Found shows and the raw selection the
 * picker needs.
 *
 * **One read, because they are one fact.** The chip saying "Worldwide" and the
 * picker's 199 checkboxes both come from `search_config.locations`, and asking
 * twice invites them to disagree about the same row -- which for this field
 * would be visible, since an empty list means Worldwide rather than nothing.
 */
/**
 * Country groups the board offers as one pill, mirroring
 * `search_config.REGION_PACKS`.
 *
 * Stored unexpanded on purpose, so a pack that gains a country later applies to
 * configs already saved. That means a reader of the stored value has to expand
 * it, and `expand_locations` is the function v1 does it with.
 */
const REGION_PACKS: Record<string, string[]> = {
  EU: ["AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
       "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
       "SI", "ES", "SE", "IS", "LI", "NO", "CH", "GB"],
  MIDDLE_EAST: ["AE", "SA", "QA", "KW", "BH", "OM", "JO", "EG"],
  SEA: ["SG", "MY", "PH", "ID", "TH", "VN"],
};

/**
 * The countries a saved `locations` selects, packs expanded.
 *
 * **This was `.map(String)` over a list of objects.** `search_config.validate`
 * stores `[{country:"US"}, {regionPack:"EU"}]` and enforces that shape, so
 * every entry stringified to `"[object Object]"`: the chip would have read
 * that, the picker would have shown nothing ticked over a real saved search,
 * and a `regionPack` would have been lost outright rather than expanded into
 * its thirty-two countries.
 *
 * It was invisible only because the stored list is empty today. The commit
 * that makes the country boxes tick is the one that lets a reader put
 * something in it, so this is fixed in the same series rather than after
 * somebody saves one.
 */
function expandLocations(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const e = entry as { country?: unknown; regionPack?: unknown };
    if (typeof e.regionPack === "string") {
      out.push(...(REGION_PACKS[e.regionPack.toUpperCase()] ?? []));
    } else if (typeof e.country === "string") {
      out.push(e.country.toUpperCase());
    }
  }
  return [...new Set(out)].sort();
}

export async function savedSearch(): Promise<{
  words: string | null; locations: string[]; scope: string; extra: string[];
}> {
  const account = await accountId();
  const [{ rows }, unplaced] = await Promise.all([
    pool().query(
      "select search_config from board_meta where account_id = $1", [account]),
    // The picker's "Other locations" group: codes the pool holds that the
    // taxonomy does not place. Derived rather than listed, for the reason
    // `LocationPicker` gives.
    pool().query(
      `select distinct country from postings
        where country is not null and country <> '' and closed_at is null`),
  ]);
  const known = new Set(Object.keys(COUNTRY_NAMES));
  const extra = unplaced.rows.map((r) => String(r.country))
    .filter((c) => !known.has(c)).sort();
  const config = rows[0]?.search_config as Record<string, unknown> | null;
  const locations = expandLocations(config?.locations);
  const scope = typeof config?.searchScope === "string"
    ? (config!.searchScope as string) : "title";
  if (!config) return { words: null, locations, scope, extra };
  const words = !locations.length ? "Worldwide"
    : locations.length === 1 ? `${locations[0]}`
    : `${locations.length} countries`;
  return { words, locations, scope, extra };
}

/** One base resume, as the registry describes it. No wording, no path. */
export type BaseResume = {
  key: string | null;
  appliesTo: string[];
  isDefault: boolean;
  location: string | null;
  phone: string | null;
  address: string | null;
  document: string | null;
};

/** One Memory Bank entry, classified by `answer_categories`. */
export type BankEntry = {
  key: string;
  question: string | null;
  value: string | null;
  state: string | null;
  tags: string[];
  sensitive: boolean;
  kind: string | null;
  category: string | null;
  updatedAt: string | null;
  /**
   * The version an edit must be made against.
   *
   * **Sent back on every write, never guessed.** `store.py` compares it and
   * refuses a mismatch as `conflict`, which is the guarantee that stops one
   * board overwriting an answer another has moved. Until migration 0017 this
   * column did not exist, so v2 sent 0: right once per entry and wrong every
   * time after, since the first successful edit takes a row to 1.
   */
  version: number;
};

/**
 * What happened to one edit the reader submitted.
 *
 * **Three facts that do not share a field**, per migration 0016: what was
 * sent, what the store held instead, and the store's own words. A refusal the
 * reader cannot compare against is a refusal they cannot act on.
 */
export type EditOutcome = {
  editId: string;
  queue: string;
  state: "pending" | "accepted" | "refused";
  target: string | null;
  submitted: unknown;
  held: unknown;
  reason: string | null;
  at: string | null;
};

/** How many outcomes a page carries. The device keeps 200; this is a page. */
const OUTCOMES_SHOWN = 60;

/**
 * The account's recent edit outcomes, newest first.
 *
 * **An absent row is not an accepted edit**, and nothing here turns one into
 * the other. Edits made before migration 0016 have no row, edits still in the
 * queue have no row, and a device that stopped has no row: all three read as
 * "the board has not heard", which is what they are. The page says so rather
 * than filling the silence in.
 */
export async function editOutcomes(): Promise<EditOutcome[]> {
  const account = await accountId();
  const { rows } = await pool().query(
    `select edit_id, queue, state, target, submitted, held, reason, at
       from edit_outcomes where account_id = $1
      order by at desc limit $2`, [account, OUTCOMES_SHOWN]);
  return rows.map((r) => ({
    editId: r.edit_id,
    queue: r.queue,
    state: r.state,
    target: r.target ?? null,
    submitted: r.submitted ?? null,
    held: r.held ?? null,
    reason: r.reason ?? null,
    at: r.at ?? null,
  }));
}

/**
 * The Profile page's data: the resume registry and the Memory Bank.
 *
 * **The answer's own value travels and is drawn plainly.** CLAUDE.md settles
 * that: the board has one reader who knows his own salary and his own
 * demographics, and covering them behind a tap was friction protecting nobody.
 * `sensitive` stays on the row because it is what a second account will mask
 * on and what Phase 5's field encryption will key on, and re-deriving it from
 * question text alone would lose every row the answer store's own state caught.
 */
export async function profile(): Promise<{
  resumes: BaseResume[]; bank: BankEntry[]; outcomes: EditOutcome[];
}> {
  const account = await accountId();
  const [profileRows, bankRows, outcomes] = await Promise.all([
    pool().query("select resumes from profiles where account_id = $1", [account]),
    pool().query(
      `select key, question, value, state, tags, sensitive, kind, category,
              version, updated_at
         from answer_bank where account_id = $1
        order by updated_at desc nulls last, key`, [account]),
    editOutcomes(),
  ]);
  const raw = (profileRows.rows[0]?.resumes ?? []) as Record<string, unknown>[];
  return {
    resumes: raw.map((r) => {
      const identity = (r.identity ?? {}) as Record<string, unknown>;
      const address = (identity.fullAddress ?? {}) as Record<string, unknown>;
      const parts = ["line1", "line2", "city", "region", "postalCode", "country"]
        .map((k) => (address[k] == null ? "" : String(address[k]).trim()))
        .filter(Boolean);
      return {
        key: (r.key as string) ?? null,
        appliesTo: Array.isArray(r.appliesTo) ? (r.appliesTo as string[]) : [],
        isDefault: r.default === true,
        location: identity.location ? String(identity.location) : null,
        phone: identity.phone ? String(identity.phone) : null,
        address: parts.length ? parts.join(", ") : null,
        document: (r.document as string) ?? null,
      };
    }),
    bank: bankRows.rows.map((r) => ({
      key: r.key,
      question: r.question ?? null,
      value: r.value ?? null,
      state: r.state ?? null,
      tags: Array.isArray(r.tags) ? r.tags : [],
      sensitive: r.sensitive === true,
      kind: r.kind ?? null,
      category: r.category ?? null,
      updatedAt: r.updated_at ?? null,
      version: Number(r.version ?? 0),
    })),
    outcomes,
  };
}

// ---------------------------------------------------------------- artifacts

/**
 * Which application artifacts R2 actually holds, and which documents share a
 * filename. Both answer the Submitted lane's document columns.
 */

const ARTIFACT_USER = process.env.JOBBY_USER_ID || "account-1";
const ARTIFACT_TTL_MS = 60_000;
let artifactCache: { at: number; keys: Set<string> } | null = null;

/**
 * Every artifact key under this account, or `null` when it could not be read.
 *
 * **`null` and an empty set are different claims and only one of them is about
 * the bucket.** Returning an empty set on a credential fault would make every
 * row on the page say the document could not be attributed, which is a
 * statement about 104 applications drawn from a statement about this
 * deployment's environment. The caller renders "unknown" for `null`.
 *
 * One listing, not one probe per row. Measured against the live bucket: 347
 * keys in a single 699 ms call, where fifty per-row probes would be the shape
 * that put fifty failing logo requests on every Found page load. Cached for a
 * minute because artifacts change when an application finishes, which is not
 * a per-render event.
 */
export async function storedArtifacts(): Promise<Set<string> | null> {
  if (!r2Configured(process.env)) return null;
  if (artifactCache && Date.now() - artifactCache.at < ARTIFACT_TTL_MS) {
    return artifactCache.keys;
  }
  try {
    const keys: string[] = await listKeys(process.env, `u/${ARTIFACT_USER}/app/`);
    artifactCache = { at: Date.now(), keys: new Set(keys) };
    return artifactCache.keys;
  } catch {
    // Never an empty set. See above.
    return null;
  }
}

export function artifactKey(legacyId: string, object: string) {
  return `u/${ARTIFACT_USER}/app/${legacyId}/${object}`;
}

/**
 * How many completed applications record each document filename.
 *
 * **A count above one is why a document cannot be attributed.** Three Glance
 * applications to three different roles all recorded
 * `Sahil_Sapra_Glance_Resume.pdf`; KV is keyed by filename and holds one
 * object, so the old board serves the same PDF for all three and nothing says
 * which application received it. `document_contract` now puts six characters
 * of the application id in the name, so this cannot happen again -- but the
 * fourteen documents written before that day are still ambiguous and the row
 * has to say so.
 */
export async function documentSiblings(): Promise<Map<string, number>> {
  const rows = await pool().query(`
    select lower(split_part(e.payload->>'resumeVariant', '/', -1)) as name,
           count(*) as n
      from application_events e
     where e.kind = 'completed' and e.payload->>'resumeVariant' like '%.pdf'
     group by 1
    union all
    select lower(split_part(e.payload->>'coverLetter', '/', -1)) as name,
           count(*) as n
      from application_events e
     where e.kind = 'completed' and e.payload->>'coverLetter' like '%.pdf'
     group by 1`);
  const out = new Map<string, number>();
  for (const row of rows.rows) {
    out.set(String(row.name), Number(row.n));
  }
  return out;
}
