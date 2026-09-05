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
export type Filters = { q?: string; within?: string; direct?: boolean };

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

function filterValues(f: Filters | undefined) {
  return {
    q: f?.q?.trim() || null,
    within: f?.within && f.within in WINDOWS ? WINDOWS[f.within] : null,
    direct: f?.direct ?? false,
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
export function laneQuery(lane: Lane, sort: Sort, filters?: Filters): string {
  const p = new URLSearchParams({ lane, sort });
  if (filters?.q) p.set("q", filters.q);
  if (filters?.within) p.set("within", filters.within);
  if (filters?.direct) p.set("direct", "1");
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
  const locations = Array.isArray(config?.locations)
    ? (config!.locations as unknown[]).map(String) : [];
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
};

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
  resumes: BaseResume[]; bank: BankEntry[];
}> {
  const account = await accountId();
  const [profileRows, bankRows] = await Promise.all([
    pool().query("select resumes from profiles where account_id = $1", [account]),
    pool().query(
      `select key, question, value, state, tags, sensitive, kind, category,
              updated_at
         from answer_bank where account_id = $1
        order by updated_at desc nulls last, key`, [account]),
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
    })),
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
