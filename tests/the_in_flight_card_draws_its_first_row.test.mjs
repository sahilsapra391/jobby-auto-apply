// The In flight card, on the first row the lane has ever held.
//
// **`PHASES.indexOf(null ?? "")` is -1, so zero of five steps lit and the
// wording under them was an empty element.** v1 does not do this:
// `dashboard.py:739` reads `phase = entry.get("phase") or "Queued"` and
// prefix-matches with `next(..., 0)`, so a phase-less application draws
// "Queued" with step one on and an unrecognised one falls to step one rather
// than to nothing.
//
// Measured 2026-09-09: 180 of 208 applications carry no phase, and `decide()`
// inserts `status: "inflight"` with none at all, so the first card this lane
// draws is guaranteed to hit it. The lane has never held a row, which is the
// only reason nobody has seen it. Two stored values are also outside the list,
// `Needs you` (4 rows) and `Failed` (1).
//
// **Run, not read.** Both helpers are compiled out of `Rows.tsx` and called.
// The `.ats` half is the exception and says so: it is a JSX element with no
// children, which is a question about the tree rather than about a value.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const ROWS = join(REPO, 'apps/web/app/_ui/Rows.tsx');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

const SRC = readFileSync(ROWS, 'utf8');
const TREE = ts.createSourceFile(ROWS, SRC, ts.ScriptTarget.ES2022, true);

test('Rows.tsx parses', () => {
  // A recovering parser answers about a tree the build would refuse.
  assert.equal(TREE.parseDiagnostics?.length ?? 0, 0,
    (TREE.parseDiagnostics ?? []).map((d) => String(d.messageText)).join('; '));
});

/**
 * `PHASES`, `phaseStep` and `clockLabel`, compiled out of Rows.tsx and run.
 *
 * The declarations come off the AST rather than out of a brace-counting slice.
 * A slice starting at `function phaseStep(` closes on the first balanced pair
 * it meets, which is the *return type* `: { label: string; at: number }`, and
 * it hands back a signature with no body. It looks like an extraction and it
 * is a truncation.
 */
function helpers() {
  const wanted = new Set(['PHASES', 'phaseStep', 'clockLabel']);
  const parts = [];
  const walk = (node) => {
    if (ts.isFunctionDeclaration(node) && node.name && wanted.has(node.name.getText())) {
      parts.push(node.getText());
    } else if (ts.isVariableStatement(node)) {
      for (const d of node.declarationList.declarations) {
        if (d.name && wanted.has(d.name.getText())) parts.push(node.getText());
      }
    }
    node.forEachChild(walk);
  };
  walk(TREE);
  assert.equal(parts.length, wanted.size,
    `extracted ${parts.length} of ${wanted.size} declarations from Rows.tsx; ` +
    `the checks below would be about whatever survived`);
  const js = ts.transpileModule(
    `${parts.join('\n')}\nmodule.exports = { PHASES, phaseStep, clockLabel };`,
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
      fileName: 'rows.ts' }).outputText;
  const mod = { exports: {} };
  // `s` is Rows.tsx's own null-to-null stringifier, supplied rather than
  // re-implemented: the helpers call it and its exact null behaviour is half
  // the property under test.
  new Function('module', 'exports', 's', js)(
    mod, mod.exports, (v) => (v == null ? null : String(v)));
  return mod.exports;
}

const { PHASES, phaseStep, clockLabel } = helpers();

test('the phase list parsed as the five v1 draws', () => {
  assert.deepEqual(PHASES, ["Queued", "Building documents", "Filling the form",
                            "Submitting", "Verifying"]);
});

test('a phase-less application is Queued with step one lit', () => {
  // The case 180 of 208 applications are in, and the one `decide()` creates.
  for (const nothing of [null, undefined, ""]) {
    const { label, at } = phaseStep(nothing);
    assert.equal(label, "Queued", `phase ${JSON.stringify(nothing)} drew ${label}`);
    assert.equal(at, 0);
    const lit = PHASES.filter((_, i) => i <= at).length;
    assert.equal(lit, 1, `${lit} of ${PHASES.length} steps lit for a null phase`);
  }
});

test('each of the five lights itself and everything before it', () => {
  PHASES.forEach((name, i) => {
    const { label, at } = phaseStep(name);
    assert.equal(label, name);
    assert.equal(at, i, `${name} lit ${at + 1} steps, expected ${i + 1}`);
  });
});

test('an agent wording that is not exact still matches', () => {
  // v1 matches on the first eight characters, lowercased, so a run reporting
  // its own wording does not have to agree letter for letter.
  assert.equal(phaseStep("Filling the form for Acme").at, 2);
  assert.equal(phaseStep("building documents").at, 1);
});

test('a phase outside the list renders its own word and lights something', () => {
  // The two the store actually holds. -1 is the defect: it draws an unlit
  // strip and an empty paragraph, so a reader learns nothing at all.
  for (const [phase, rows] of [["Needs you", 4], ["Failed", 1]]) {
    const { label, at } = phaseStep(phase);
    assert.equal(label, phase, `${phase} (${rows} rows) did not render its word`);
    assert.notEqual(at, -1, `${phase} lit no steps`);
    assert.ok(at >= 0 && at < PHASES.length);
  }
});

test('the clock label reads a stamp and refuses one it cannot', () => {
  // **v1 renders nothing here and that is a v1 bug, not a decision.**
  // `dashboard.py:774` hands `clock_label` the slice `phaseAt[11:19]`, a bare
  // "16:00:07" that `clock.parse` cannot read, so `local()` is None and the
  // label is "". Measured by running it.
  assert.equal(clockLabel("16:00:07"), "",
    'a bare time is not a stamp and must not be guessed at');
  assert.equal(clockLabel(null), "");
  assert.equal(clockLabel(""), "");
  assert.equal(clockLabel("not a date"), "");
  const label = clockLabel("2026-09-09T20:00:07Z");
  assert.match(label, /^\d{2}:\d{2}:\d{2} E[DS]T$/,
    `clock.clock_label's format is "16:00:07 EDT"; got ${JSON.stringify(label)}`);
  assert.equal(label.slice(0, 8), "16:00:07",
    'the stamp must render in America/New_York, not in the runner timezone');
});

/** Every `<span className="ats">` in the file, with whether it has children. */
function atsSpans() {
  const out = [];
  const walk = (node) => {
    if (ts.isJsxElement(node)
        && node.openingElement.tagName.getText() === 'span') {
      const cls = node.openingElement.attributes.properties.find(
        (a) => ts.isJsxAttribute(a) && a.name.getText() === 'className');
      if (cls && cls.initializer && ts.isStringLiteral(cls.initializer)
          && cls.initializer.text === 'ats') {
        out.push(node.children.filter(
          (c) => !(ts.isJsxText(c) && c.getText().trim() === '')).length);
      }
    }
    node.forEachChild(walk);
  };
  walk(TREE);
  return out;
}

test('neither ats span is empty', () => {
  const spans = atsSpans();
  // The floor: v1 puts two different facts in this one class, the ATS name on
  // Needs you and a clock label on In flight, so there are two of them and a
  // check that found one has found half the subject.
  assert.equal(spans.length, 2,
    `found ${spans.length} ats spans, expected the Needs you one and the In ` +
    `flight one`);
  assert.deepEqual(spans, [1, 1],
    'an ats span has no children, so it renders empty on every card, which ' +
    'is what both lanes did: 23 of 23 Needs You cards drew nothing there');
});
