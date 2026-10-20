// The words a Found card prints, from both boards, compared.
//
// `found_lane.tidy_tag`, `tidy_days` and the source label's `.title()`
// fallback run in Python on the board; `apps/web/app/_ui/cardWords.ts` runs
// them on v2. Their word lists are generated from the Python, so the lists
// cannot drift, but the rules can: a port that restates "capitalise each word
// unless it is small" passes every hand-written expectation while disagreeing
// with the board on the tag nobody thought to write down. So this runs both
// implementations over one set of inputs and compares outputs, which is how
// this repository holds its other two-language contracts.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const WEB = join(REPO, 'apps/web');
const ts = createRequire(join(WEB, 'package.json'))('typescript');

function compile(source, names, globals = {}) {
  const js = ts.transpileModule(
    `${source}\nmodule.exports = { ${names.join(', ')} };`,
    { compilerOptions: { module: ts.ModuleKind.CommonJS,
                         target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = { exports: {} };
  const keys = Object.keys(globals);
  new Function('module', 'exports', ...keys, js)(
    mod, mod.exports, ...keys.map((k) => globals[k]));
  return mod.exports;
}

const generated = compile(
  readFileSync(join(WEB, 'app/rubric.generated.ts'), 'utf8'),
  ['DAY_WORDS', 'KEEP_UPPER', 'MAX_TAGS', 'SMALL_WORDS', 'SOURCE_LABELS']);

/** cardWords.ts with its one import supplied from the generated module. */
const words = compile(
  readFileSync(join(WEB, 'app/_ui/cardWords.ts'), 'utf8')
    .replace(/^import[\s\S]*?from "..\/rubric.generated";\n/m, ''),
  ['tidyTag', 'cardTags', 'tidyDays', 'sourceLabel'],
  generated);

/** The board's own functions, run on the same inputs. */
function python(inputs) {
  const program = `
import json, sys
sys.path.insert(0, ${JSON.stringify(join(REPO, 'scripts'))})
import found_lane
data = json.load(sys.stdin)
tags = [found_lane.tidy_tag(t) for t in data["tags"]]
card = [[found_lane.tidy_tag(t) for t in group[:found_lane.MAX_TAGS]
         if found_lane.tidy_tag(t)][:4] for group in data["groups"]]
days = [found_lane.tidy_days(t) for t in data["days"]]
sources = [found_lane.SOURCE_LABELS.get(s, s.title()) for s in data["sources"]]
print(json.dumps({"tags": tags, "card": card, "days": days, "sources": sources}))
`;
  return JSON.parse(execFileSync('python3', ['-c', program],
    { input: JSON.stringify(inputs), encoding: 'utf8' }));
}

const INPUTS = {
  tags: ['full-time', 'strategy & product', 'ai', 'devops engineer',
         'head of product', 'sql and aws', 'Already Styled', 'AI/ML',
         'b2b saas', 'vp of the year', '  padded tag  ', '', 'mid-level',
         'product performance & lifecycle intelligence', 'saas', 'iot'],
  groups: [
    ['a', 'b', 'c', 'd', 'e', 'f', 'g'],
    ['', '', '', '', '', '', 'late'],
    ['ai', '', 'devops'],
    [],
  ],
  days: ['posted 0 days ago', 'posted 1 days ago', 'posted 12 days ago',
         'posted 1 days ago and 1 days ago', ''],
  sources: ['greenhouse', 'successfactors', 'we_work', 'oracle_orc', 'themuse',
            'x1y', ''],
};

const board = python(INPUTS);

test('every tag tidies the way the board tidies it', () => {
  assert.deepEqual(INPUTS.tags.map(words.tidyTag), board.tags);
});

test('a card keeps the tags the board keeps, four at most', () => {
  assert.deepEqual(INPUTS.groups.map(words.cardTags), board.card);
  // The cap is the board's, not a coincidence of the inputs.
  assert.equal(words.cardTags(INPUTS.groups[0]).length, 4);
});

test('a reason is mended the way the board mends it', () => {
  assert.deepEqual(INPUTS.days.map(words.tidyDays), board.days);
});

test('a source is named the way the board names it', () => {
  assert.deepEqual(INPUTS.sources.map(words.sourceLabel), board.sources);
});
