// Every browser mutation on v2 carries `x-jobby-request: 1`.
//
// **The board is public now and its writes are not, which is the whole point
// of the header.** `middleware.ts` refuses a POST that does not carry it, so a
// sender written without it renders, is clickable, and answers 403 with a
// message nobody reads.
//
// This is not hypothetical. `found_lane.py` grew its own `post()` on
// 2026-08-19 without the header, and Watch, Pass and Apply from Found all 403ed
// for five days while the dashboard's own buttons kept working, because those
// sent it. Fifteen sessions of bug-fixing walked past it. The Python board has
// `tests/test_click_requests_carry_the_token.py` for exactly this; v2 had
// nothing, and two of its five senders were missing the header when the
// password gate came off.
//
// It parses rather than greps: a `fetch` in a comment is not a fetch.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const APP = join(REPO, 'apps/web/app');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

/**
 * A `fetch` that is not a board mutation, with the reason.
 *
 * **A server-to-server POST is a different thing and must not be swept in.**
 * `writes.ts` publishes to the tenant channel with a Bearer token from a
 * server component; it never passes through the middleware and the header
 * would mean nothing there.
 */
const NOT_A_BOARD_MUTATION = {
  'writes.ts': 'server-side publish to the tenant channel, Bearer auth, never '
             + 'a browser request and never through the middleware',
};

function everySource() {
  const out = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.tsx?$/.test(name)) out.push(full);
    }
  };
  walk(APP);
  return out;
}

/**
 * The header names an options object supplies, following one level of
 * indirection.
 *
 * **`headers: headers()` is the real pattern and an inline-literal reader
 * calls it unheaded.** `FoundActions.tsx` sends the token from a helper and the
 * first version of this test reported it as a stranded POST -- a false positive
 * of exactly the kind CLAUDE.md records: a static check can only see the names
 * that are written down, and it is confidently wrong in whichever direction its
 * default points.
 *
 * So it resolves a call to a same-file function whose body returns an object
 * literal, which is the pattern in use, and returns `null` for anything else.
 * `null` is "could not look", not "absent": the caller reports those separately
 * rather than counting them as either a pass or a failure.
 */
function headerNames(tree, initializer, text) {
  if (ts.isObjectLiteralExpression(initializer)) {
    return initializer.properties.filter(ts.isPropertyAssignment)
      .map((p) => text(p.name).replace(/["']/g, '').toLowerCase());
  }
  if (ts.isCallExpression(initializer) && ts.isIdentifier(initializer.expression)) {
    const wanted = text(initializer.expression);
    let names = null;
    const find = (node) => {
      if (ts.isFunctionDeclaration(node) && node.name && text(node.name) === wanted) {
        const walkBody = (n) => {
          if (ts.isReturnStatement(n) && n.expression) {
            const ret = ts.isParenthesizedExpression(n.expression)
              ? n.expression.expression : n.expression;
            if (ts.isObjectLiteralExpression(ret)) {
              names = ret.properties.filter(ts.isPropertyAssignment)
                .map((p) => text(p.name).replace(/["']/g, '').toLowerCase());
            }
          }
          n.forEachChild(walkBody);
        };
        walkBody(node);
      }
      node.forEachChild(find);
    };
    find(tree);
    return names;
  }
  return null;
}

/** Every `fetch(...)` whose options object names a mutating method. */
function mutatingFetches(file) {
  const src = readFileSync(file, 'utf8');
  const tree = ts.createSourceFile(file, src, ts.ScriptTarget.ES2022, true,
                                   ts.ScriptKind.TSX);
  const found = [];
  const text = (n) => n.getText(tree);

  const walk = (node) => {
    if (ts.isCallExpression(node) && text(node.expression).endsWith('fetch')) {
      const opts = node.arguments[1];
      if (opts && ts.isObjectLiteralExpression(opts)) {
        const props = new Map();
        for (const p of opts.properties) {
          if (ts.isPropertyAssignment(p)) props.set(text(p.name).replace(/["']/g, ''), p);
        }
        const method = props.get('method');
        const verb = method ? text(method.initializer).replace(/["']/g, '').toUpperCase() : 'GET';
        if (verb !== 'GET' && verb !== 'HEAD') {
          const headers = props.get('headers');
          const hs = headers ? headerNames(tree, headers.initializer, text) : [];
          const { line } = tree.getLineAndCharacterOfPosition(node.getStart());
          found.push({
            file: relative(APP, file), line: line + 1, verb,
            unreadable: hs === null,
            token: hs !== null && hs.includes('x-jobby-request'),
            jsonType: hs !== null && hs.includes('content-type'),
          });
        }
      }
    }
    node.forEachChild(walk);
  };
  walk(tree);
  return found;
}

const all = everySource().flatMap(mutatingFetches);
const board = all.filter((f) => !NOT_A_BOARD_MUTATION[f.file.split('/').pop()]);

test('the walk finds the senders, so this cannot pass vacuously', () => {
  assert.ok(all.length >= 3,
    `only ${all.length} mutating fetches found across apps/web/app; a walker ` +
    `that has stopped walking reports full compliance`);
  assert.ok(board.length >= 3, `only ${board.length} board mutations after exemptions`);
});

test('a sender this cannot read is named, never counted as either', () => {
  // "Could not look" is never "is not there". An options object built somewhere
  // this parser cannot follow is a real gap in coverage and it says so, rather
  // than passing quietly or failing a working control.
  const unreadable = board.filter((f) => f.unreadable);
  assert.deepEqual(unreadable, [],
    'these fetches build their headers somewhere this cannot follow, so their ' +
    'token is unverified. Give them an inline literal or a same-file helper:\n' +
    unreadable.map((f) => `  ${f.file}:${f.line}`).join('\n'));
});

test('every board mutation carries x-jobby-request', () => {
  const missing = board.filter((f) => !f.unreadable && !f.token);
  assert.deepEqual(missing, [],
    'these POSTs are refused by the middleware as cross-site. They render, ' +
    'they are clickable, and they answer 403:\n' +
    missing.map((f) => `  ${f.file}:${f.line} ${f.verb}`).join('\n'));
});

test('every board mutation declares application/json', () => {
  const missing = board.filter((f) => !f.unreadable && !f.jsonType);
  assert.deepEqual(missing, [],
    'the middleware answers 415 without it:\n' +
    missing.map((f) => `  ${f.file}:${f.line}`).join('\n'));
});

test('an exemption names a file that exists and says why', () => {
  // Same rule the decorative marker carries: "this one is fine" with no reason
  // is how an exemption list becomes the thing it guards against.
  const names = new Set(all.map((f) => f.file.split('/').pop()));
  for (const [file, why] of Object.entries(NOT_A_BOARD_MUTATION)) {
    assert.ok(names.has(file),
      `${file} is exempted and has no mutating fetch; delete the exemption`);
    assert.ok(why.length > 30, `${file}'s exemption is not a reason`);
  }
});
