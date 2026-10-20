// Every phase string the device can write must resolve to a step.
//
// **The step indicator was an exact match against five labels the device does
// not write.** `PHASES.indexOf(phase ?? "")` returned -1 for ten of the
// fourteen strings in circulation, so the strip was unlit for the whole
// document stage -- which is where a run spends most of its wall clock -- and
// lit for the first time only when the agent reached "Filling the form".
// The original Python board always prefix-matched on eight characters with a
// default of 0, under a comment saying why: "Match on prefix so an agent's
// wording does not have to be exact."
//
// Ported in #238. That board is gone; its rule is written out below as the
// reference, and this is the census that keeps v2 on it.
//
// **The list is read out of the device's own code, not typed here.** That is
// the whole point: a phase added to `DOCUMENT_PHASES` or a new `set_phase`
// literal is a string the board must already know how to draw, and a test
// holding its own copy of the list would go quietly stale exactly when a new
// phase appeared. Same move as parity deriving its columns from the mirror's
// INSERT and `GATE_PATHS` from the write path's call sites.
//
// The 8-character prefix is load-bearing and must not be shortened:
// "Building documents" and "Building documents on Your Device" share exactly
// that much, and a shorter prefix starts matching things it should not.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

/** Every phase the device or the agent can write, from the code that writes it. */
function phasesInCirculation() {
  const watch = readFileSync(join(REPO, 'scripts/watch.py'), 'utf8');
  const found = new Set();

  // `DOCUMENT_PHASES` maps a document-job state to (phase, detail). The phase
  // is the first of the pair; taking both would put the detail sentences in
  // here, and those are drawn in `.why` rather than matched against a step.
  const at = watch.indexOf('DOCUMENT_PHASES = {');
  assert.notEqual(at, -1, 'watch.py no longer declares DOCUMENT_PHASES');
  const block = watch.slice(at, watch.indexOf('\n}\n', at));
  for (const m of block.matchAll(/\(\s*\n\s*"([^"]+)",\s*\n\s*"([^"]+)",\s*\n\s*\)/g)) {
    found.add(m[1]);
  }
  assert.ok(found.size >= 3,
    `DOCUMENT_PHASES parsed as ${found.size} phases; the matcher has stopped ` +
    `matching and every check below is about almost nothing`);

  // Every literal handed to a `set_phase` call anywhere in `scripts/`.
  for (const name of ['watch.py', 'phase.py', 'run_apply.py', 'document_job.py',
                      'promote.py', 'finish.py', 'drop.py']) {
    let src;
    try { src = readFileSync(join(REPO, 'scripts', name), 'utf8'); }
    catch { continue; }
    for (const m of src.matchAll(/set_phase[_a-z]*\([^,)]+,\s*"([^"]+)"/g)) {
      if (!m[1].startsWith('--')) found.add(m[1]);
    }
  }

  // The states the board itself stores that are not phases of progress. These
  // are declared rather than scraped: they arrive through `applications.phase`
  // from paths that do not call `set_phase`, and CLAUDE.md records four rows
  // holding `Needs you` and one holding `Failed`.
  for (const terminal of ['Failed', 'Stalled', 'Stopped', 'Needs you']) {
    found.add(terminal);
  }

  // The phases `phase.py` writes on its own flags (`--unconfirmed`,
  // `--not-sent`), read off the constants that hold them rather than listed.
  const phasePy = readFileSync(join(REPO, 'scripts/phase.py'), 'utf8');
  const own = [...phasePy.matchAll(/^[A-Z_]+_PHASE = "([^"]+)"$/gm)].map((m) => m[1]);
  assert.ok(own.length >= 2,
    `phase.py declared ${own.length} phases of its own; the matcher has stopped matching`);
  for (const phase of own) found.add(phase);
  return [...found].sort();
}

/** `PHASES` and `phaseStep`, compiled out of Rows.tsx and run. */
function helpers() {
  const file = join(REPO, 'apps/web/app/_ui/Rows.tsx');
  const src = readFileSync(file, 'utf8');
  const tree = ts.createSourceFile(file, src, ts.ScriptTarget.ES2022, true);
  assert.equal(tree.parseDiagnostics?.length ?? 0, 0,
    (tree.parseDiagnostics ?? []).map((d) => String(d.messageText)).join('; '));
  const want = new Set(['PHASES', 'phaseStep']);
  const parts = [];
  const walk = (n) => {
    if (ts.isFunctionDeclaration(n) && n.name && want.has(n.name.getText())) {
      parts.push(n.getText());
    } else if (ts.isVariableStatement(n)) {
      for (const d of n.declarationList.declarations) {
        if (d.name && want.has(d.name.getText())) parts.push(n.getText());
      }
    }
    n.forEachChild(walk);
  };
  walk(tree);
  assert.equal(parts.length, want.size,
    `extracted ${parts.length} of ${want.size} declarations from Rows.tsx`);
  const js = ts.transpileModule(
    `${parts.join('\n')}\nmodule.exports = { PHASES, phaseStep };`,
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
      fileName: 'rows.ts' }).outputText;
  const mod = { exports: {} };
  new Function('module', 'exports', 's', js)(
    mod, mod.exports, (v) => (v == null ? null : String(v)));
  return mod.exports;
}

/** The original board's rule, as it stood when that board was removed:
 *  default a missing phase to Queued, prefix-match each step on eight
 *  characters, and fall back to step one. */
function boardRule() {
  const steps = ["Queued", "Building documents", "Filling the form",
                 "Submitting", "Verifying"];
  return (phase) => {
    const p = (phase || "Queued").toLowerCase();
    const i = steps.findIndex((st) => p.startsWith(st.toLowerCase().slice(0, 8)));
    return i === -1 ? 0 : i;
  };
}

const { PHASES, phaseStep } = helpers();
const CIRCULATING = phasesInCirculation();
const v1 = boardRule();

test('the census found the phases the device actually writes', () => {
  // The floor. Every check below passes on an empty list, and this file exists
  // because ten strings were missing from what the board could draw.
  assert.ok(CIRCULATING.length >= 10,
    `only ${CIRCULATING.length} phases found: ${CIRCULATING.join(', ')}`);
  assert.ok(CIRCULATING.includes('Building documents on Your Device'),
    'the document stage phase is missing from the census, and it is the one ' +
    'a run spends most of its time in');
  assert.deepEqual(PHASES, ["Queued", "Building documents", "Filling the form",
                            "Submitting", "Verifying"]);
});

test('no phase the device writes renders an unlit strip', () => {
  const dead = CIRCULATING.filter((p) => phaseStep(p).at < 0);
  assert.deepEqual(dead, [],
    `these render zero of five steps lit, so a reader watching a run sees ` +
    `nothing move: ${dead.join(', ')}`);
  assert.equal(phaseStep(null).at, 0,
    'a phase-less application must draw Queued with step one lit; 180 of 208 ' +
    'applications carry no phase and decide() inserts none');
  // **And the word, not just the step.** Dropping `|| "Queued"` leaves `at` at
  // 0 through the -1 fallback, so a check on the step alone passes while the
  // card draws an empty paragraph over a lit strip. Found by mutation.
  assert.equal(phaseStep(null).label, 'Queued',
    'a phase-less application draws no wording under the strip');
});

test('every phase resolves to the same step the board gives it', () => {
  // Not "resolves to something" -- resolves to the *same* thing. A port that
  // lights step one for everything would pass the check above.
  for (const p of [null, ...CIRCULATING]) {
    assert.equal(phaseStep(p).at, v1(p),
      `${JSON.stringify(p)}: v2 lights step ${phaseStep(p).at} and the board ` +
      `lights step ${v1(p)}`);
  }
});

test('every phase renders its own words', () => {
  for (const p of CIRCULATING) {
    assert.equal(phaseStep(p).label, p,
      `${JSON.stringify(p)} renders as ${JSON.stringify(phaseStep(p).label)}`);
  }
});

test('v2 prefix-matches on the reference rule\'s eight characters', () => {
  // **The claim this replaces was false, and the mutation is what said so.**
  // It asserted the eight characters were "what tells the two document steps
  // apart", and shortening the prefix to three passes every case here: no two
  // step labels collide at three characters in today's phase set. Eight is a
  // margin, not a discriminator.
  //
  // What is load-bearing is that v2 keeps the reference rule's margin. A
  // match on eight and a match on three agree on every string in circulation
  // now and can part on the next one added, and it would be silent, because
  // both answers look reasonable.
  const rows = readFileSync(join(REPO, 'apps/web/app/_ui/Rows.tsx'), 'utf8');
  const v2 = rows.match(/st\.toLowerCase\(\)\.slice\(0,\s*(\d+)\)/);
  assert.ok(v2, 'Rows.tsx no longer slices the step label');
  assert.equal(Number(v2[1]), 8,
    `v2 prefix-matches on ${v2[1]} characters and the reference rule on 8; ` +
    `they agree on every phase in circulation today and will part on the next ` +
    `one added`);
  // And the pair the margin exists for still resolves.
  assert.equal(phaseStep('Building documents').at, 1);
  assert.equal(phaseStep('Building documents on Your Device').at, 1);
});

test('a phase nobody told the board about falls to step one, on both', () => {
  // **A mutation that passes, explained rather than left standing.** Adding a
  // phase to `DOCUMENT_PHASES` does not fail this file, and that is correct:
  // the reference rule's fallback gives an unrecognised phase step one too, so
  // the card draws the new words over a lit strip. The property is agreement
  // with the rule, not recognition of every string.
  const invented = 'Zzz a phase nobody told the board about';
  assert.equal(phaseStep(invented).at, 0);
  assert.equal(phaseStep(invented).at, v1(invented));
  assert.equal(phaseStep(invented).label, invented,
    'an unrecognised phase must still render its own words, or a reader ' +
    'learns nothing from a state nobody anticipated');
});
