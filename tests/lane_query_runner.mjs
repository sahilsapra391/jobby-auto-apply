// Runs one lane URL the way the deployed board does, and prints what it got.
//
// Not a test: the name does not match `*.test.mjs`, so `node --test` does not
// collect it. `tests/test_application_lane_search.py` drives it against a
// scratch schema it has seeded, because the fixture and the migrations are
// Python and the half that was wrong is TypeScript.
//
// **Everything between the URL and Postgres is the real thing.**
// `filtersFrom`, `filterValues` and `argsFor` are compiled out of `db.ts`, the
// SQL and its parameter lists come from `lanes.generated.ts`, and the driver
// is the `pg` the app ships, which is what serialises a JS array into the
// array literal `{"Umbrella Corp"}`. A test that hands psycopg a Python list
// skips exactly that serialisation, and that is where #235 broke.
//
//   node tests/lane_query_runner.mjs <database-url> <schema> <account> <query>
//
// Prints `{"count": n, "rows": [legacy_id, ...]}` on stdout.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const WEB = join(REPO, 'apps/web');
const need = createRequire(join(WEB, 'package.json'));
const ts = need('typescript');
const pg = need('pg');

function compile(source, exportsList, globals = {}) {
  const js = ts.transpileModule(
    `${source}\nmodule.exports = { ${exportsList.join(', ')} };`,
    { compilerOptions: { module: ts.ModuleKind.CommonJS,
                         target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = { exports: {} };
  const names = Object.keys(globals);
  new Function('module', 'exports', ...names, js)(
    mod, mod.exports, ...names.map((n) => globals[n]));
  return mod.exports;
}

/** `lanes.generated.ts` is data and types only, so it transpiles whole. */
const generated = compile(
  readFileSync(join(WEB, 'app/lanes.generated.ts'), 'utf8'),
  ['LANES', 'WINDOWS']);

/** The three functions the page and the route both go through. */
function loadDb() {
  const src = readFileSync(join(WEB, 'app/_ui/db.ts'), 'utf8');
  const grab = (name) => {
    const at = src.indexOf(`function ${name}(`);
    if (at === -1) throw new Error(`${name} not found in db.ts`);
    let i = src.indexOf('{', src.indexOf(')', at)), depth = 0;
    for (let j = i; j < src.length; j++) {
      if (src[j] === '{') depth++;
      else if (src[j] === '}' && --depth === 0) return src.slice(at, j + 1);
    }
    throw new Error(`${name} never closes`);
  };
  const keys = src.slice(src.indexOf('const FILTER_KEYS'),
                         src.indexOf('] as const;') + 11);
  return compile(
    `${keys}\n${grab('argsFor')}\n${grab('filtersFrom')}\n${grab('filterValues')}`,
    ['argsFor', 'filtersFrom', 'filterValues'],
    { URLSearchParams, WINDOWS: generated.WINDOWS });
}

const [url, schema, account, query] = process.argv.slice(2);
if (!url || !schema || !account || query === undefined) {
  console.error('usage: lane_query_runner.mjs <url> <schema> <account> <query>');
  process.exit(2);
}

const { argsFor, filtersFrom, filterValues } = loadDb();
const params = new URLSearchParams(query);
const lane = params.get('lane');
const sort = params.get('sort') ?? 'new';
const spec = generated.LANES[lane];
if (!spec) {
  console.error(`unknown lane ${lane}`);
  process.exit(2);
}
// The dispatch `page()` and `counts()` do, without the pool and the account
// lookup they wrap it in.
const values = {
  account, after_at: null, after_id: null, limit: 50,
  ...filterValues(filtersFrom(params)),
};
const pageQuery = spec.sorts[sort] ?? { sql: spec.page, params: spec.pageParams };

const client = new pg.Client({ connectionString: url });
await client.connect();
try {
  await client.query(`set search_path to "${schema.replace(/"/g, '')}"`);
  const counted = await client.query(spec.count, argsFor(spec.countParams, values));
  const paged = await client.query(pageQuery.sql, argsFor(pageQuery.params, values));
  process.stdout.write(JSON.stringify({
    count: Number(counted.rows[0].count),
    rows: paged.rows.map((r) => r.legacy_id).sort(),
  }));
} finally {
  await client.end();
}
