// Save search sends every field the filter form holds, and nothing it does not.
//
// **The first reader lost the search it was saving.** It read the first
// `[name="q"]` in the card and split it on spaces. The first `q` is the first
// chip's hidden input, so every other chip was dropped, and a chip reading
// "product manager" went out as two keywords that the sweep ORs. It also left
// out any field that was empty, and `search_config.save` merges a save over
// what is stored, so emptying the keywords and pressing Save kept sourcing on
// the old ones.
//
// `configFromSnapshot` is the pure half, compiled out of
// `apps/web/app/_ui/searchConfig.ts` and run. The census at the bottom is the
// other half: every field the filter form draws has to be one the reader
// reads, because the failure was a field the form held and the save never
// sent, and a new control is exactly where that recurs.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const WEB = join(REPO, 'apps/web');
const ts = createRequire(join(WEB, 'package.json'))('typescript');

const SOURCE = readFileSync(join(WEB, 'app/_ui/searchConfig.ts'), 'utf8');

const { configFromSnapshot, noLocationPicked, UNKNOWN_FIELDS } = (() => {
  const js = ts.transpileModule(
    `${SOURCE}\nmodule.exports = { configFromSnapshot, noLocationPicked, UNKNOWN_FIELDS };`,
    { compilerOptions: { module: ts.ModuleKind.CommonJS,
                         target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = { exports: {} };
  new Function('module', 'exports', js)(mod, mod.exports);
  return mod.exports;
})();

/** A form the way the page draws it with writes on and nothing touched. */
function form(overrides = {}) {
  return {
    q: [], not: '', scope: 'title', within: '30d', sort: 'score',
    direct: false, wp: ['remote', 'hybrid', 'onsite'], cos: 'all',
    conames: '', exp: '',
    countries: [{ value: 'GB', checked: true }, { value: 'IN', checked: true }],
    incremote: true,
    unknowns: { experience: true, workplace: true, postedAt: true },
    ...overrides,
  };
}

test('every chip is one keyword, and a two-word chip stays one phrase', () => {
  const out = configFromSnapshot(form({
    q: ['product manager', 'data analyst', 'product manager', '  '] }));
  assert.deepEqual(out.keywords, ['product manager', 'data analyst']);
});

test('an emptied field is sent empty, so the save clears it', () => {
  const out = configFromSnapshot(form());
  // Present and empty, not absent: the device merges, and absent keeps.
  assert.deepEqual(out.keywords, []);
  assert.deepEqual(out.excludeKeywords, []);
  assert.deepEqual(out.companies, []);
  assert.equal(out.companiesMode, 'all');
  assert.ok('maxExperienceYears' in out);
  assert.equal(out.maxExperienceYears, null);
  assert.deepEqual(out.locations, []);
});

test('what the reader set goes out as the store spells it', () => {
  const out = configFromSnapshot(form({
    not: ' intern ', scope: 'title+description', within: '7d', sort: 'new',
    direct: true, wp: ['remote'], cos: 'hide', conames: 'Acme, Initech ,',
    exp: '3', incremote: false,
    countries: [{ value: 'GB', checked: true }, { value: 'IN', checked: false }],
    unknowns: { experience: false, workplace: true, postedAt: false },
  }));
  assert.deepEqual(out, {
    keywords: [], excludeKeywords: ['intern'], searchScope: 'title+description',
    postedWithin: '7d', sort: 'new', directOnly: true, workplace: ['remote'],
    companiesMode: 'hide', companies: ['Acme', 'Initech'],
    maxExperienceYears: 3, locations: [{ country: 'GB' }],
    includeRemoteForSelection: false,
    includeUnknowns: { experience: false, workplace: true, postedAt: false },
  });
});

test('a field the form does not draw is not sent', () => {
  // Writes off: the advanced block is absent, so only the basic row exists.
  const out = configFromSnapshot({
    q: ['analyst'], not: null, scope: null, within: '7d', sort: 'score',
    direct: true, wp: null, cos: null, conames: null, exp: null,
    countries: [], incremote: null, unknowns: {},
  });
  assert.deepEqual(Object.keys(out).sort(),
                   ['directOnly', 'keywords', 'postedWithin', 'sort']);
});

test('no country ticked is refused rather than saved as Worldwide', () => {
  assert.equal(noLocationPicked(form({ countries: [
    { value: 'GB', checked: false }, { value: 'IN', checked: false }] })), true);
  assert.equal(noLocationPicked(form()), false);
  // No picker at all is not "nothing picked".
  assert.equal(noLocationPicked(form({ countries: [] })), false);
});

/** Every literal `name` attribute in a TSX file. */
function namesIn(file) {
  const src = readFileSync(join(WEB, file), 'utf8');
  const tree = ts.createSourceFile(file, src, ts.ScriptTarget.ES2022, true,
                                   ts.ScriptKind.TSX);
  const out = new Set();
  const walk = (node) => {
    if (ts.isJsxAttribute(node) && node.name.getText(tree) === 'name'
        && node.initializer && ts.isStringLiteral(node.initializer)) {
      out.add(node.initializer.text);
    }
    node.forEachChild(walk);
  };
  walk(tree);
  return out;
}

test('every field the filter form draws is one the save reads', () => {
  const drawn = new Set([...namesIn('app/_ui/FoundSurface.tsx'),
                         ...namesIn('app/_ui/LocationPicker.tsx')]);
  // Floor: the census found the form, so the loop below is about something.
  assert.ok(drawn.size >= 12, `only ${drawn.size} named fields found: ${[...drawn]}`);
  // `lane` routes the GET and is not a filter. The countries are read by
  // `data-country-name`, which every `loc` box carries.
  const how = { loc: 'data-country-name' };
  const unread = [...drawn].filter((name) => name !== 'lane')
    .filter((name) => !SOURCE.includes(how[name] ?? `name="${name}"`)
                      && !SOURCE.includes(`text("${name}")`)
                      && !SOURCE.includes(`box("${name}")`));
  assert.deepEqual(unread, [],
    `the filter form draws these and Save search never reads them, so a ` +
    `reader who sets one and saves keeps the old value: ${unread.join(', ')}`);
});

test('the unknown switches are read under the names the form gives them', () => {
  const surface = readFileSync(join(WEB, 'app/_ui/FoundSurface.tsx'), 'utf8');
  const block = surface.match(/UNKNOWN_PARAM: Record<string, string> = \{([^}]*)\}/);
  assert.ok(block, 'FoundSurface no longer declares UNKNOWN_PARAM');
  const drawn = Object.fromEntries([...block[1].matchAll(/(\w+): "(\w+)"/g)]
    .map((m) => [m[1], m[2]]));
  assert.deepEqual(UNKNOWN_FIELDS, drawn);
});

test('a refused save is recognised by the sentence the device writes', () => {
  const route = readFileSync(join(WEB, 'app/api/search-config/route.ts'), 'utf8');
  const prefix = route.match(/const REFUSED_PREFIX = "([^"]+)"/);
  assert.ok(prefix, 'the route no longer names the refusal prefix');
  const watch = readFileSync(join(REPO, 'scripts/watch.py'), 'utf8');
  assert.ok(watch.includes(`f"${prefix[1]}: {reason}"`),
    `watch.take_saved_config no longer writes "${prefix[1]}: ...", so a save ` +
    `the schema refused would read as one still waiting`);
});
