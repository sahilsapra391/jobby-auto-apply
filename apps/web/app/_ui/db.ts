import { Pool } from "pg";
import { LANES, type Lane } from "../lanes.generated";

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

function pool(): Pool {
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
async function accountId(): Promise<string> {
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
export async function page(lane: Lane, after: Cursor, limit = 50) {
  const account = await accountId();
  const { rows } = await pool().query(LANES[lane].page, [
    account, after?.at ?? null, after?.id ?? null, limit,
  ]);
  // The cursor column differs by lane shape: postings order on posted_at,
  // applications on updated_at. Reading it off the row rather than restating
  // which is which keeps the two in step with the generated SQL.
  const last = rows[rows.length - 1];
  const at = last === undefined ? null
    : (("posted_at" in last ? last.posted_at : last.updated_at) ?? null);
  return {
    rows: rows as Row[],
    next: last ? { at: at ? new Date(at).toISOString() : null, id: last.id } : null,
    done: rows.length < limit,
  };
}

export async function counts(): Promise<Record<Lane, number>> {
  const account = await accountId();
  const out = {} as Record<Lane, number>;
  await Promise.all(
    (Object.keys(LANES) as Lane[]).map(async (lane) => {
      const { rows } = await pool().query(LANES[lane].count, [account]);
      out[lane] = Number(rows[0].count);
    }),
  );
  return out;
}
