// Every control in v2 reaches something, and "enabled and inert" is its own state.
//
// **Five controls have now rendered correctly and reached nothing**: Confirm
// submitted, Open & finish, Undrop, Apply-from-Watching, and Connect. Each was
// found by a person pressing it, not by a check.
//
// `lane_actions_are_driven.test.mjs` was written after the second and it could
// not have caught the fifth, for two reasons worth stating because they are the
// general shape rather than an oversight:
//
//   it walked one file      Rows.tsx, so Settings was never its subject
//   it equated inert with   Connect carried no `disabled` at all. It rendered
//   `disabled`              fully pressable and posted nothing, which is the
//                           worse state and the one it had no concept of.
//
// So this walks every `.tsx` under `apps/web/app` and asks a different
// question: does anything drive this control. Three ways it can be driven, and
// the third is the one a JSX-only reader cannot see:
//
//   a prop        onClick, type="submit", or an <Act body={...}>
//   a script      a client module or generated script that names its class and
//                 attaches a handler
//   a declaration an entry here, with the reason it asks for nothing
//
// **Without the second, this check cries wolf.** `Dropped.tsx`'s close button
// has no handler in its own JSX and is driven by `DroppedOpen.tsx`, which
// queries `.dialog-close`. A guard that called that stranded would be the
// 27-problems line again: a check whose subject is wider than the thing it is
// named for, reporting 26 healthy channels as faults.

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
 * Controls that reach nothing on purpose, each with the reason.
 *
 * **A reason naming a future phase is not one.** "Phase 4 moves the runner and
 * this control with it" kept Open & finish dark while `watch.py` was already
 * claiming its queue, and "nothing here connects anything" kept Connect dark
 * while `providerLocal/` was being claimed every two seconds. Both sentences
 * were true about where something lives and neither was a reason for a button
 * to send nothing. Say what the control would order and why nothing can carry
 * it today.
 */
const INERT_ALLOWED = {
  // Correct rather than unbuilt: this branch renders only when nothing is
  // filtered, so there is nothing to clear. The other branch is an `<a>` to
  // `/?lane=found`, which does clear, and a `type="reset"` would restore the
  // form's defaults while leaving the URL filtered so the count would not
  // move. Declared rather than exempted by shape, because a hard `disabled` is
  // exactly what Open & finish carried for a bad reason.
  "Reset to default": "nothing is filtered, so there is nothing to reset; the "
       + "filtered branch renders a link to /?lane=found which does",
};

function sources(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) sources(path, out);
    else if (path.endsWith('.tsx') || path.endsWith('.ts')) out.push(path);
  }
  return out;
}

const files = sources(APP);
const text = new Map(files.map((f) => [f, readFileSync(f, 'utf8')]));

/**
 * Class names any script attaches a handler to.
 *
 * Derived by finding every `addEventListener` in the tree and collecting the
 * class literals in the same module, rather than by listing them: a listed set
 * is the copy that goes stale, which is what `GATE_PATHS` and the aggregator
 * host list both cost.
 */
function wiredClasses() {
  const wired = new Set();
  for (const [file, src] of text) {
    if (!src.includes('addEventListener')) continue;
    for (const m of src.matchAll(/["'`.]([a-z][a-z0-9-]{2,})["'`\s)]/gi)) {
      if (m[1].includes('-')) wired.add(m[1]);
    }
    void file;
  }
  return wired;
}
const WIRED = wiredClasses();

function classOf(open, tsm) {
  const attr = open.attributes.properties.find(
    (a) => tsm.isJsxAttribute(a) && a.name.getText() === 'className');
  if (!attr || !attr.initializer) return [];
  return [...attr.initializer.getText().matchAll(/[a-z][a-z0-9-]{2,}/gi)]
    .map((m) => m[0]);
}

function controls(node, file, out) {
  const open = ts.isJsxElement(node) ? node.openingElement
    : ts.isJsxSelfClosingElement(node) ? node : null;
  if (open) {
    const tag = open.tagName.getText();
    if (tag === 'button' || tag === 'Act') {
      const attrs = open.attributes.properties;
      const has = (n) => attrs.find(
        (a) => ts.isJsxAttribute(a) && a.name.getText() === n);
      const typeAttr = has('type');
      const submits = Boolean(typeAttr)
        && /submit/.test(typeAttr.initializer?.getText() ?? '');
      const label = (ts.isJsxElement(node)
        ? node.children.map((c) => c.getText()).join('')
        : tag).replace(/\s+/g, ' ').trim().slice(0, 44) || tag;
      const classes = classOf(open, ts);
      const disabled = has('disabled');
      out.push({
        file: relative(REPO, file),
        label,
        byProp: (tag === 'Act' && Boolean(has('body')))
                || Boolean(has('onClick')) || submits,
        byScript: classes.some((c) => WIRED.has(c)),
        // A literal `disabled` cannot be pressed at all. `disabled={expr}` is
        // a working control greying itself while a write is gated, so it is
        // not this.
        hardDisabled: tag === 'button' && Boolean(disabled) && !disabled.initializer,
      });
    }
  }
  node.forEachChild((child) => controls(child, file, out));
}

const found = [];
for (const [file, src] of text) {
  if (!file.endsWith('.tsx')) continue;
  controls(ts.createSourceFile(file, src, ts.ScriptTarget.ES2022, true), file, found);
}

test('the walk reaches the whole app, not one file', () => {
  assert.ok(found.length >= 30,
    `only ${found.length} controls found across apps/web/app; the walker is ` +
    `not reaching the JSX and every check below passes on a short list`);
  const seen = new Set(found.map((c) => c.file));
  assert.ok(seen.size >= 4,
    `controls found in only ${seen.size} file(s): ${[...seen].join(', ')}. ` +
    `The predecessor to this check walked one file and that is why Connect ` +
    `sat unwired inside its subject.`);
  assert.ok(found.some((c) => c.byProp), 'no control is driven by a prop');
  assert.ok(found.some((c) => c.byScript),
    'no control is driven by a script, so that detection is not working and ' +
    'every delegated control is about to be reported stranded');
});

test('no control renders enabled and reaches nothing', () => {
  const stranded = found
    .filter((c) => !c.byProp && !c.byScript && !c.hardDisabled)
    .filter((c) => !(c.label in INERT_ALLOWED));
  assert.deepEqual(stranded.map((c) => `${c.file}: ${c.label}`), [],
    `these render pressable and send nothing, which is the state a person ` +
    `finds by pressing them: ${stranded.map((c) => `${c.file}: ${c.label}`)
      .join('; ')}`);
});

test('no control is rendered permanently inert without a reason', () => {
  const inert = found.filter((c) => c.hardDisabled)
                     .filter((c) => !(c.label in INERT_ALLOWED));
  assert.deepEqual(inert.map((c) => `${c.file}: ${c.label}`), [],
    `hard-disabled and undeclared, so they read as finished work: ` +
    `${inert.map((c) => c.label).join(', ')}`);
});

test('an inert control says what it would have ordered', () => {
  for (const [label, why] of Object.entries(INERT_ALLOWED)) {
    assert.ok(why && why.length > 60,
      `${label} is inert with no reason worth checking`);
  }
});

test('Connect is driven, by name', () => {
  // The fifth one, pinned so a reader deleting the wiring meets the incident
  // rather than a list. Settings renders `<ProviderAction>` now, whose own
  // button this walk finds in `_ui/ProviderAction.tsx`, so the assertion is
  // about that button rather than about the settings page carrying a raw one.
  const settings = readFileSync(join(APP, 'settings/page.tsx'), 'utf8');
  assert.ok(settings.includes('<ProviderAction'),
    "Settings no longer renders ProviderAction; if the control moved, point " +
    "this at whatever replaced it rather than deleting the case");
  assert.ok(!/<button[^>]*provider-action/.test(settings),
    "Settings is back to a raw provider-action button, which is the state it " +
    "was in when Connect sent nothing");

  const control = found.filter((c) => c.file.includes('ProviderAction.tsx'));
  assert.ok(control.length >= 1, 'ProviderAction renders no button');
  for (const c of control) {
    assert.ok(c.byProp, `ProviderAction's ${c.label} reaches nothing again`);
  }
});

// **Every file this walk reads has to be a file the build accepts, and for one
// push it was not.** `ts.createSourceFile` recovers from syntax errors rather
// than throwing: it returns a tree with error nodes and files the complaints in
// `parseDiagnostics`, which nothing read. So a `{/* comment */}` written into a
// ternary branch, where JSX children are not expected and the braces are an
// object literal, was walked by five separate guards -- this one,
// `form_controls_have_names`, `lane_actions_are_driven`,
// `status_writes_reach_the_daemon` and `profile_route_writes_nothing` -- and
// every one of them found its controls and passed. `next build` refused it.
//
// It is the same shape as a check pointed at a stale server, one step earlier:
// the guards were reading a real file and answering correctly about it, and the
// thing that decides whether the board exists was never asked. A guard that can
// only report "this control reaches nothing" is blind to "this file is not a
// file", and the second failure is the one that takes the board down.
//
// This is not a substitute for the build. It is the cheap half that runs in
// `node --test` in a second, so the expensive half is not the first thing to
// notice.
test('every file the walk reads is one the compiler can parse', () => {
  const broken = [];
  for (const [file, src] of text) {
    const tree = ts.createSourceFile(file, src, ts.ScriptTarget.ES2022, true,
                                     file.endsWith('.tsx') ? ts.ScriptKind.TSX
                                                           : ts.ScriptKind.TS);
    for (const d of tree.parseDiagnostics ?? []) {
      const { line } = tree.getLineAndCharacterOfPosition(d.start ?? 0);
      broken.push(`${relative(APP, file)}:${line + 1} ` +
                  ts.flattenDiagnosticMessageText(d.messageText, ' '));
    }
  }
  assert.deepEqual(broken, [],
    'these files do not parse, so every AST guard walking them is answering ' +
    'about a tree the build will refuse:\n  ' + broken.join('\n  '));
});

test('the parse check has files to check, and it fails on a real break', () => {
  // Anti-vacuity, and the mutation in one. A walk that has stopped walking
  // reports full compliance, and a diagnostics read that never fires is the
  // eleven other checks in CLAUDE.md that measured nothing.
  assert.ok(text.size >= 10,
            `only ${text.size} sources found; the walk has stopped walking`);
  const bad = ts.createSourceFile(
    'x.tsx', 'export const A = () => (cond ? ({/* c */}<p/>) : null);',
    ts.ScriptTarget.ES2022, true, ts.ScriptKind.TSX);
  assert.ok((bad.parseDiagnostics ?? []).length > 0,
            'the exact construct that broke the build parsed clean, so this ' +
            'check would not have caught it');
});
