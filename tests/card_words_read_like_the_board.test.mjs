// The words a Found card prints.
//
// `apps/web/app/_ui/cardWords.ts` tidies tags, mends "posted 1 days ago" and
// names a source. It was ported from the original Python board, and this test
// compared the two implementations over one set of inputs. That board is gone,
// so the expectations below are its outputs for those inputs, recorded on the
// day it was removed (2026-10-20). A port that restates "capitalise each word
// unless it is small" passes hand-written expectations while disagreeing on
// the tag nobody thought to write down; a recorded corpus keeps that edge.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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

const rubric = compile(
  readFileSync(join(WEB, 'app/rubric.ts'), 'utf8'),
  ['DAY_WORDS', 'KEEP_UPPER', 'MAX_TAGS', 'SMALL_WORDS', 'SOURCE_LABELS']);

/** cardWords.ts with its one import supplied from rubric.ts. */
const words = compile(
  readFileSync(join(WEB, 'app/_ui/cardWords.ts'), 'utf8')
    .replace(/^import[\s\S]*?from "..\/rubric";\n/m, ''),
  ['tidyTag', 'cardTags', 'tidyDays', 'sourceLabel'],
  rubric);

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

/** What the original board printed for INPUTS, recorded when it was removed. */
const board = {
  tags: ['Full-time', 'Strategy & Product', 'AI', 'DevOps Engineer',
         'Head of Product', 'SQL and AWS', 'Already Styled', 'AI/ML',
         'B2B SaaS', 'VP of the Year', 'Padded Tag', '', 'Mid-level',
         'Product Performance & Lifecycle Intelligence', 'SaaS', 'IoT'],
  card: [['A', 'B', 'C', 'D'], [], ['AI', 'DevOps'], []],
  days: ['posted today', 'posted yesterday', 'posted 12 days ago',
         'posted yesterday and yesterday', ''],
  sources: ['Greenhouse', 'Successfactors', 'We_Work', 'Oracle_Orc', 'The Muse',
            'X1Y', ''],
};

test('every tag tidies the way the board tidies it', () => {
  assert.deepEqual(INPUTS.tags.map(words.tidyTag), board.tags);
});

test('a card keeps the tags the board keeps, four at most', () => {
  assert.deepEqual(INPUTS.groups.map(words.cardTags), board.card);
  // The cap is four, not a coincidence of the inputs.
  assert.equal(words.cardTags(INPUTS.groups[0]).length, 4);
});

test('a reason is mended the way the board mends it', () => {
  assert.deepEqual(INPUTS.days.map(words.tidyDays), board.days);
});

test('a source is named the way the board names it', () => {
  assert.deepEqual(INPUTS.sources.map(words.sourceLabel), board.sources);
});
