// The Found card's details panel behaves the way the board always has, and
// that is run rather than read.
//
// `CardDetails.tsx` claimed rule-for-rule parity with the original Python
// board and broke three of the rules: a second click did not close the panel
// while the pointer was on the card, a failed read told the reader the posting
// had no headings, and it drew six points a section where the board drew
// five. Each test below fails when its fix is reverted.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

function parse(rel) {
  const file = join(REPO, rel);
  const tree = ts.createSourceFile(file, readFileSync(file, 'utf8'),
    ts.ScriptTarget.ES2022, true,
    rel.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  // A recovering parser hands back a tree for a file the build refuses.
  assert.equal(tree.parseDiagnostics?.length ?? 0, 0,
    `${rel} does not parse: ` +
    `${(tree.parseDiagnostics ?? []).map((d) => String(d.messageText)).join('; ')}`);
  return tree;
}

/** cardDisclosure.ts, compiled and run. It imports nothing, so it runs whole. */
function rules() {
  const rel = 'apps/web/app/_ui/cardDisclosure.ts';
  const tree = parse(rel);
  assert.ok(!tree.statements.some(ts.isImportDeclaration),
    `${rel} grew an import, so compiling it alone no longer runs it`);
  const js = ts.transpileModule(readFileSync(join(REPO, rel), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: rel }).outputText;
  const mod = { exports: {} };
  new Function('module', 'exports', js)(mod, mod.exports);
  return mod.exports;
}

function run(events) {
  const { CLOSED, disclose } = rules();
  return events.reduce((state, event) => disclose(state, event), CLOSED);
}

test('hover previews, and a second click closes with the pointer still on the card', () => {
  assert.equal(run(['enter']).shown, true, 'hover opens a preview');
  assert.equal(run(['enter', 'leave']).shown, false, 'leaving closes a preview');
  assert.equal(run(['enter', 'click']).shown, true, 'a click pins it open');
  assert.equal(run(['enter', 'click', 'leave']).shown, true,
    'hover never closes what a click opened');
  // The defect: `open = pinned || hovered` kept this open, because the button
  // is on the card and the pointer is therefore always over it.
  assert.equal(run(['enter', 'click', 'click']).shown, false,
    'a second click closes the panel while the pointer is still on the card');
  // And it stays closed until the pointer leaves and comes back, as the board.
  assert.equal(run(['enter', 'click', 'click', 'leave']).shown, false);
  assert.equal(run(['enter', 'click', 'click', 'leave', 'enter']).shown, true);
  // A keyboard click with no hover at all pins and unpins the same way.
  assert.equal(run(['click']).shown, true);
  assert.equal(run(['click', 'click']).shown, false);
});

test('the component takes what it shows from those rules, not from pinned || hovered', () => {
  const src = readFileSync(join(REPO, 'apps/web/app/_ui/CardDetails.tsx'), 'utf8');
  const tree = parse('apps/web/app/_ui/CardDetails.tsx');
  const calls = [];
  const walk = (node) => {
    if (ts.isCallExpression(node) && node.expression.getText() === 'disclose') {
      const arg = node.arguments[1];
      if (arg && ts.isStringLiteral(arg)) calls.push(arg.text);
    }
    node.forEachChild(walk);
  };
  walk(tree);
  assert.deepEqual([...new Set(calls)].sort(), ['click', 'enter', 'leave'],
    'CardDetails must route click, enter and leave through disclose()');
  assert.doesNotMatch(src, /pinned\s*\|\|\s*hovered/);
});

test('a failed read says it failed; only an ok, empty answer says no headings', () => {
  const { readPosting, sectionNote } = rules();
  const NO_HEADINGS = 'This posting does not use headings this can read.';
  const read = (data) => {
    const sections = readPosting(data);
    return sectionNote(sections, sections === null);
  };
  // The route's 404 body for a pruned posting, a 400, and a non-ok status the
  // caller turns into null before it gets here.
  for (const body of [{ error: 'no such posting' }, { error: 'id must be a posting uuid' },
                      null, undefined, 'oops', { ok: 'true' }]) {
    const note = read(body);
    assert.ok(note, `a failed read draws a line, got nothing for ${JSON.stringify(body)}`);
    assert.notEqual(note, NO_HEADINGS,
      `${JSON.stringify(body)} is a failed read and was told it has no headings`);
  }
  // A network error: the component sets failed with no sections.
  assert.notEqual(sectionNote(null, true), NO_HEADINGS);
  // Still loading: nothing.
  assert.equal(sectionNote(null, false), null);
  // Read, and empty.
  assert.equal(read({ ok: true, responsibilities: [], requirements: [] }), NO_HEADINGS);
  assert.equal(read({ ok: true }), NO_HEADINGS);
  // Read, and one half present.
  assert.equal(read({ ok: true, responsibilities: ['Ship it'], requirements: [] }), null);
  assert.equal(read({ ok: true, responsibilities: [], requirements: ['5 years'] }), null);
});

test('the component draws the note sectionNote returns and no headings line of its own', () => {
  const src = readFileSync(join(REPO, 'apps/web/app/_ui/CardDetails.tsx'), 'utf8');
  assert.match(src, /sectionNote\(sections, failed\)/);
  assert.doesNotMatch(src, /does not use headings this can read/,
    'CardDetails spells the no-headings line itself again, beside the rule that decides it');
  const fetchRel = src.slice(src.indexOf('fetch(`/api/posting'));
  assert.match(fetchRel, /readPosting\(data\)/);
});

test('v2 draws five points a section, from one constant', () => {
  // Five is what the original board drew. Six was the regression.
  const rubric = readFileSync(join(REPO, 'apps/web/app/rubric.ts'), 'utf8');
  const v2 = rubric.match(/export const SECTION_POINTS = (\d+);/);
  assert.ok(v2, 'rubric.ts carries no SECTION_POINTS');
  assert.equal(v2[1], '5');

  // And the component slices by that constant, not by a number of its own.
  const tree = parse('apps/web/app/_ui/CardDetails.tsx');
  const slices = [];
  const walk = (node) => {
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
        && node.expression.name.getText() === 'slice' && node.arguments.length === 2) {
      slices.push(node.arguments[1].getText());
    }
    node.forEachChild(walk);
  };
  walk(tree);
  assert.ok(slices.length >= 2, `found ${slices.length} slices in CardDetails.tsx`);
  for (const bound of slices) assert.equal(bound, 'SECTION_POINTS');
  const imported = tree.statements.some((s) => ts.isImportDeclaration(s)
    && s.moduleSpecifier.text === '../rubric'
    && /\bSECTION_POINTS\b/.test(s.importClause?.getText() ?? ''));
  assert.ok(imported, 'CardDetails.tsx must import SECTION_POINTS from rubric');
});
