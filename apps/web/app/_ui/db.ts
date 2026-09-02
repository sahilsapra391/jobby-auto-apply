import { Pool } from "pg";
import { LANES, WINDOWS, type Lane, type Sort } from "../lanes.generated";

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
    ?? { sql: LANES[lane].page, params: LANES[lane].pageParams };
  const { rows } = await pool().query(query.sql, argsFor(query.params, {
    account, after_at: after?.at ?? null, after_id: after?.id ?? null, limit,
    ...filterValues(filters),
  }));
  // **The cursor column follows the sort, not the lane.** Ordering Found by
  // match score walks `total`, and handing the next page a `posted_at` cursor
  // would compare a date against a smallint: either an error or, worse, a
  // silent restart at the top of the lane on every page.
  const last = rows[rows.length - 1];
  const column = sort === "score" ? "score"
    : (last !== undefined && "posted_at" in last ? "posted_at" : "updated_at");
  const at = last === undefined ? null : (last[column] ?? null);
  return {
    rows: rows as Row[],
    next: last ? { at: cursorValue(at, sort), id: last.id } : null,
    done: rows.length < limit,
  };
}

/**
 * A cursor value the generated SQL can cast back.
 *
 * A date has to go out as an ISO string; a score is a number and must not be
 * put through `new Date`, which would turn 70 into 1970 and then fail the
 * `::int` cast the score query applies to it.
 */
function cursorValue(at: unknown, sort: Sort): string | null {
  if (at === null || at === undefined) return null;
  if (sort === "score") return String(at);
  return new Date(at as string).toISOString();
}

export async function counts(filters?: Filters): Promise<Record<Lane, number>> {
  const account = await accountId();
  const out = {} as Record<Lane, number>;
  const values = { account, ...filterValues(filters) };
  await Promise.all(
    (Object.keys(LANES) as Lane[]).map(async (lane) => {
      // Only Found's count names the filters; the others ask for the account
      // alone, and `argsFor` gives each what it declares.
      const { rows } = await pool().query(
        LANES[lane].count, argsFor(LANES[lane].countParams, values));
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
