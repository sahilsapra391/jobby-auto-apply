// The resume wording reaches the signed-in owner and nobody else.
//
// **Four doors, and each has its own check.** The wording is the owner's
// employment history in his own words, and the site around it is public.
//
//   the read door   only `/api/profile/wording` reads the KV key that holds
//                   it, and it asks `isOwner()` before reading
//   the write door  the same route asks `isOwner()` before enqueueing, and the
//                   public `/api/profile` refuses a `content` edit outright
//   the side door   `edit_outcomes` mirrors what each edit submitted, and the
//                   public outcome list drops it for a wording edit
//   the gate itself `isOwner()` is called from the wording surfaces and the
//                   sign-in page, and from nothing else, because the owner's
//                   decision is that nothing else on the site asks who you are
//
// Parsed rather than grepped where the question is about calls, for the
// reason this repository has paid for three times: a comment explaining why
// something is not called would satisfy any pattern.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const APP = join(REPO, 'apps/web/app');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

function sources(dir = APP, out = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) sources(full, out);
    else if (/\.(tsx?|mjs)$/.test(name)) out.push(full);
  }
  return out;
}

const rel = (file) => relative(APP, file);
const stripComments = (text) =>
  text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');

/** Every identifier this file calls, and every name it imports. */
function scan(file) {
  const tree = ts.createSourceFile(file, readFileSync(file, 'utf8'),
    ts.ScriptTarget.ES2022, true, file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const found = { calls: [], names: new Set() };
  const walk = (node) => {
    if (ts.isCallExpression(node)) {
      const t = node.expression;
      const name = ts.isIdentifier(t) ? t.text
        : ts.isPropertyAccessExpression(t) ? t.name.text : null;
      if (name) found.calls.push({ name, at: node.getStart() });
    }
    if (ts.isImportDeclaration(node) && node.importClause?.namedBindings
        && ts.isNamedImports(node.importClause.namedBindings)) {
      for (const el of node.importClause.namedBindings.elements) {
        found.names.add(el.name.text);
      }
    }
    node.forEachChild((child) => { walk(child); });
  };
  walk(tree);
  return found;
}

const ALL = sources();
const WORDING_ROUTE = join(APP, 'api/profile/wording/route.ts');

test('the walk reads the app, or nothing below is evidence', () => {
  assert.ok(ALL.length > 40, `only ${ALL.length} source files found`);
  assert.ok(ALL.includes(WORDING_ROUTE), 'the wording route is not where this looks');
});

test('only the owner-gated route reads the wording key', () => {
  const readers = ALL
    .filter((f) => !f.endsWith('deviceKeys.generated.mjs'))
    .filter((f) => scan(f).names.has('OWNER_WORDING_KEY')
                   || /jobby:owner:/.test(stripComments(readFileSync(f, 'utf8'))))
    .map(rel);
  assert.deepEqual(readers, ['api/profile/wording/route.ts'],
    'a second file reads the owner wording key; the wording is owner-only and ' +
    'every other route on this site is public');
});

test('the wording route asks who you are before it reads or enqueues', () => {
  const { calls } = scan(WORDING_ROUTE);
  const first = (name) => calls.filter((c) => c.name === name).map((c) => c.at);
  const src = readFileSync(WORDING_ROUTE, 'utf8');
  // Both handlers, each on its own: a gate in GET says nothing about POST.
  for (const handler of ['GET', 'POST']) {
    const start = src.indexOf(`export async function ${handler}(`);
    assert.ok(start > 0, `the wording route has no ${handler}`);
    const next = src.indexOf('export async function', start + 10);
    const end = next < 0 ? src.length : next;
    const inside = (at) => at > start && at < end;
    const gate = first('refuseUnlessOwner').filter(inside);
    assert.equal(gate.length, 1, `${handler} does not call refuseUnlessOwner exactly once`);
    for (const later of ['readDeviceJson', 'order', 'requireWritesEnabled', 'json']) {
      for (const at of first(later).filter(inside)) {
        if (later === 'json' && at < gate[0]) {
          assert.fail(`${handler} reads the request body before the owner check`);
        }
        if (later !== 'json') {
          assert.ok(at > gate[0], `${handler} calls ${later}() before the owner check`);
        }
      }
    }
  }
  // And the gate itself refuses a visitor.
  const gateBody = stripComments(src.slice(src.indexOf('async function refuseUnlessOwner'),
                                           src.indexOf('type Published')));
  assert.match(gateBody, /if \(!\(await isOwner\(\)\)\)/,
    'refuseUnlessOwner no longer refuses when isOwner() is false');
  assert.match(gateBody, /status: 401/);
});

test('the public write path refuses a wording edit', () => {
  const route = stripComments(readFileSync(join(APP, 'api/profile/route.ts'), 'utf8'));
  const refusal = route.indexOf('body.action === "content"');
  const plan = route.indexOf('plan(body)');
  assert.ok(refusal > 0 && plan > refusal,
    '/api/profile plans a content edit without refusing it first, so a visitor ' +
    'could overwrite the owner wording through the public write path');
  assert.match(route.slice(refusal, plan), /status: 403/);
});

test('the public outcome list drops what a wording edit submitted', () => {
  const db = stripComments(readFileSync(join(APP, '_ui/db.ts'), 'utf8'));
  const body = db.slice(db.indexOf('export async function editOutcomes'),
                        db.indexOf('export function isWordingEdit'));
  assert.match(body, /submitted: wording \? null/,
    'editOutcomes returns a wording edit\'s submission, which is the wording');
  assert.match(body, /held: wording \? null/);
  // And the predicate is the real one: the wording is the one resume edit
  // with no target.
  const js = ts.transpileModule(
    db.slice(db.indexOf('export function isWordingEdit'),
             db.indexOf('}', db.indexOf('export function isWordingEdit')) + 1),
    { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  const mod = { exports: {} };
  new Function('module', 'exports', js)(mod, mod.exports);
  assert.equal(mod.exports.isWordingEdit('resumeEdits', null), true);
  assert.equal(mod.exports.isWordingEdit('resumeEdits', 'us'), false);
  assert.equal(mod.exports.isWordingEdit('answerEdits', null), false);
});

test('nothing but the wording surfaces asks whether you are the owner', () => {
  const askers = ALL.filter((f) => scan(f).calls.some((c) => c.name === 'isOwner'))
    .map(rel).sort();
  assert.deepEqual(askers, [
    'api/owner/session/route.ts',
    'api/profile/wording/route.ts',
    'profile/page.tsx',
    'signin/page.tsx',
  ], 'isOwner() is called somewhere new. The site is public by the owner\'s ' +
     'decision and the resume wording editor is the one exception; a second ' +
     'gated surface is a product decision, not a refactor');
});

test('the Profile page never puts the wording in its own markup', () => {
  // The editor fetches it after the owner opens it. The page component
  // reading KV, or passing content into the editor, would serialise the
  // wording into the HTML every visitor downloads.
  const page = scan(join(APP, 'profile/page.tsx'));
  assert.equal(page.names.has('readDeviceJson'), false);
  const src = stripComments(readFileSync(join(APP, 'profile/page.tsx'), 'utf8'));
  const editor = src.slice(src.indexOf('<WordingEditor'), src.indexOf('/>', src.indexOf('<WordingEditor')));
  assert.doesNotMatch(editor, /content=|render=/,
    'the Profile page hands the wording to the editor as a prop');
});
