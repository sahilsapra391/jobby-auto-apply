// One map, two languages, and they disagreed about the key.
//
// `documentSiblings` groups on `lower(split_part(payload->>'resumeVariant',
// '/', -1))` -- the basename. `DocLink` looked the map up with
// `siblings.get(name.toLowerCase())`, the whole recorded value. **18 of the
// 100 recorded `resumeVariant` values are paths**, so every one of those
// missed, `shared` fell back to 1, and the row rendered "not stored" when the
// truth was "not attributable" -- the document exists and more than one
// application claims that name.
//
// Measured on production 2026-09-10: 3 rows keyed wrongly, 2 visibly changing
// state, because `stored.has(key)` catches the third first.
//
// **Compared rather than restated**, which is how the other two contracts in
// this repository are held (`LogoContractTests` over the logo plans,
// `test_r2_contract` over twelve signed URLs). A test asserting "DocLink calls
// basename" would pass on a `basename` that did something else; this runs both
// halves over the same inputs and requires the same answer.

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
  const src = readFileSync(file, 'utf8');
  const tree = ts.createSourceFile(file, src, ts.ScriptTarget.ES2022, true);
  assert.equal(tree.parseDiagnostics?.length ?? 0, 0,
    `${rel} does not parse: ` +
    `${(tree.parseDiagnostics ?? []).map((d) => String(d.messageText)).join('; ')}`);
  return { src, tree };
}

/**
 * The words a `<span>` actually renders, off the AST.
 *
 * **Not a slice of the source, and the mutation is why.** The first version
 * sliced the cover-letter block out of the text and looked for "not recorded"
 * in it; the comment three lines above that branch says `This said "not
 * recorded" and DocLink said "never recorded"`, so the control run failed on
 * prose. The second version tried to blank comments with `ts.createScanner`
 * and the scanner desynchronises on JSX, so the comment survived and the
 * control still failed.
 *
 * Sixth instance of a scanner reading text about the thing as the thing, and
 * the first two attempts at a fix were both more scanning. An AST never yields
 * a comment, which is the answer this repository already wrote down.
 */
function renderedWords(tree, inside) {
  const words = [];
  let region = null;
  const find = (n) => {
    if (region) return;
    if (ts.isIfStatement(n) && n.expression.getText() === inside) region = n;
    else n.forEachChild(find);
  };
  find(tree);
  assert.ok(region, `no \`if (${inside})\` in the file`);
  const collect = (n) => {
    if (ts.isJsxText(n)) {
      const t = n.getText().trim();
      if (t) words.push(t);
    }
    n.forEachChild(collect);
  };
  collect(region);
  return words;
}

/** `basename`, compiled out of Rows.tsx and run. */
function tsKey() {
  const { src, tree } = parse('apps/web/app/_ui/Rows.tsx');
  let decl = null;
  const walk = (n) => {
    if (ts.isFunctionDeclaration(n) && n.name && n.name.getText() === 'basename') {
      decl = n.getText();
    }
    n.forEachChild(walk);
  };
  walk(tree);
  assert.ok(decl, 'Rows.tsx declares no `basename`');
  const js = ts.transpileModule(`${decl}\nmodule.exports = basename;`,
    { compilerOptions: { module: ts.ModuleKind.CommonJS }, fileName: 'b.ts' }).outputText;
  const mod = { exports: {} };
  new Function('module', 'exports', js)(mod, mod.exports);
  // And the lookup really uses it, because a correct helper nothing calls is
  // the shape this repository has met five times.
  assert.match(src, /siblings\.get\(basename\(name\)\)/,
    'DocLink does not look the sibling map up through basename, so the helper ' +
    'below is measured and unused');
  assert.doesNotMatch(src, /siblings\.get\(name\.toLowerCase\(\)\)/,
    'the whole recorded value is still being used as the key');
  return mod.exports;
}

/**
 * `lower(split_part(x, '/', -1))`, read off the SQL rather than reimplemented.
 *
 * The expression is extracted from `db.ts` and its shape asserted, so a change
 * to the grouping key fails here instead of silently making the two halves
 * agree about the wrong thing.
 */
function sqlKey() {
  const { src } = parse('apps/web/app/_ui/db.ts');
  const at = src.indexOf('export async function documentSiblings');
  assert.notEqual(at, -1, 'db.ts declares no documentSiblings');
  const body = src.slice(at, src.indexOf('\n}', at));
  const m = [...body.matchAll(
    /lower\(split_part\(e\.payload->>'(\w+)',\s*'\/',\s*-1\)\)/g)];
  assert.equal(m.length, 2,
    `documentSiblings groups on ${m.length} split_part expressions, expected ` +
    `two (resumeVariant and coverLetter). The key changed shape and this ` +
    `comparison no longer knows what it is comparing against.`);
  assert.deepEqual(m.map((x) => x[1]).sort(), ['coverLetter', 'resumeVariant']);
  // split_part with -1 returns the whole string when the separator is absent.
  return (v) => v.slice(v.lastIndexOf('/') + 1).toLowerCase();
}

const fromTs = tsKey();
const fromSql = sqlKey();

/** Recorded-value shapes from production, with the employers replaced. */
const RECORDED = [
  'applications/Acme/Alex_Example_Acme_Resume.pdf',
  'applications/Initech/Alex_Example_Initech_Resume.pdf',
  'applications/Globex/Alex_Example_Globex_Resume.pdf',
  'applications/Umbrella Web Services/Alex_Example_UWS_Resume.pdf',
  'applications/Hooli.io/Alex_Example_Hooli.io_Resume.pdf',
  'Alex_Example_Contoso_Resume.pdf',
  'Alex_Example_Vandelay_CoverLetter.pdf',
  'Alex_Example_Stark_Resume.PDF',
];

test('the two halves agree on every recorded shape', () => {
  // The floor: a comparison over an empty list passes.
  assert.ok(RECORDED.length >= 8, 'too few cases to be worth running');
  for (const value of RECORDED) {
    assert.equal(fromTs(value), fromSql(value),
      `TypeScript keys ${JSON.stringify(value)} as ` +
      `${JSON.stringify(fromTs(value))} and the SQL keys it as ` +
      `${JSON.stringify(fromSql(value))}`);
  }
});

test('a path keys as its basename, not as itself', () => {
  // The case that was wrong. Without this the test above passes on a helper
  // that returns its argument unchanged, since both halves would agree.
  assert.equal(fromTs('applications/Acme/Alex_Example_Acme_Resume.pdf'),
               'alex_example_acme_resume.pdf');
  assert.notEqual(fromTs('applications/Acme/Alex_Example_Acme_Resume.pdf'),
                  'applications/acme/alex_example_acme_resume.pdf');
});

test('a bare filename keys as itself, lowercased', () => {
  assert.equal(fromTs('Alex_Example_Contoso_Resume.pdf'),
               'alex_example_contoso_resume.pdf');
  assert.equal(fromTs('Alex_Example_Stark_Resume.PDF'),
               'alex_example_stark_resume.pdf');
});

test('the two absence words are one word', () => {
  // Two strings for one fact, three cells apart: the cover column said "not
  // recorded" and DocLink said "never recorded" for the identical condition.
  //
  // **The first version of this could not fail**, and the mutation is what
  // said so. `src.indexOf("not accepted")` searches from position 0 rather
  // than from the start of the block, so the slice was not the cover-letter
  // column at all; and the pattern asked for `>not recorded<` where the markup
  // wraps the words on their own line. Both halves wrong, in the same
  // direction, in a check three lines long.
  const { tree } = parse('apps/web/app/_ui/Rows.tsx');
  // Both absence branches, read as what they draw rather than as source text.
  const cover = renderedWords(tree, '!cover');
  const doc = renderedWords(tree, '!name');
  assert.ok(cover.length >= 1 && doc.length >= 1,
    `the branches rendered ${cover.length} and ${doc.length} pieces of text, ` +
    `so this is comparing nothing`);
  assert.deepEqual(cover, doc,
    `the cover-letter column draws ${JSON.stringify(cover)} and DocLink draws ` +
    `${JSON.stringify(doc)} for the identical condition -- no filename was ` +
    `recorded -- so one row can carry both words three cells apart`);
});
