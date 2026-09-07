// A lane's primary action is wired, not rendered.
//
// **"Open & finish" sat disabled on every Needs You card since cutover.** The
// lane exists to hand a blocked application back to the person; the Chainguard
// run parked on "enter the 8-character code emailed to you" and there was no
// way to reach the form. The card rendered perfectly. Every class was right,
// every guard about markup passed, and the lane could not do its one job.
//
// The reason it carried was wrong rather than merely stale: "not something v2
// can do... Phase 4 moves the runner and this control with it". `watch.py:2432`
// has always claimed the `focus` queue and relaunches the slot with a window
// before opening the page. Phase 4 is about where the runner lives, not about
// whether a board may ask for this today.
//
// **The detection method is the transferable half.** A rendered control and a
// working control are indistinguishable from the markup, so no check that
// reads markup can tell them apart. What separates them is whether anything
// drives them: a `<button disabled>` posts nothing, an `<Act body={...}>`
// posts something. So this asks which, and requires a hard-disabled control in
// a lane to be declared with a reason rather than left to look finished.
//
// Same family as the unnamed form controls: a visual port that carried the
// markup and not the action.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const ROWS = join(REPO, 'apps/web/app/_ui/Rows.tsx');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

/**
 * Controls a lane may render inert, each with the reason it asks the device
 * for nothing. **A reason naming a future phase is not enough** -- that is what
 * "Open & finish" carried while the queue behind it was already live. It has
 * to say what the control would order and why nothing can carry it today.
 */
const INERT_ALLOWED = {
  // Empty. Every control in a lane's action row is wired.
};

const tree = ts.createSourceFile(
  ROWS, readFileSync(ROWS, 'utf8'), ts.ScriptTarget.ES2022, true);

/**
 * Every control a lane renders, and whether anything drives it.
 *
 * Two kinds. `<Act>` takes a `body` and posts it, so it is driven by
 * construction. A raw `<button>` in a lane posts nothing on its own, and one
 * carrying a literal `disabled` cannot even be clicked -- that is what "Open &
 * finish" was. `disabled={expr}` is different and is how a working control
 * greys itself while a write is in flight.
 *
 * **The floor counts both**, because after the fix `Rows.tsx` contains no raw
 * `<button>` at all: a floor on buttons alone would have been satisfied by
 * zero and this check would have passed for ever while measuring nothing. It
 * was, on its first run, and the floor is what said so.
 */
function controls(node, out = []) {
  const open = ts.isJsxElement(node) ? node.openingElement
    : ts.isJsxSelfClosingElement(node) ? node : null;
  if (open) {
    const tag = open.tagName.getText();
    if (tag === 'button' || tag === 'Act') {
      const attrs = open.attributes.properties;
      const has = (n) => attrs.find(
        (a) => ts.isJsxAttribute(a) && a.name.getText() === n);
      const disabled = has('disabled');
      const label = ts.isJsxElement(node)
        ? node.children.map((c) => c.getText()).join('').replace(/\s+/g, ' ')
            .trim().slice(0, 40)
        : tag;
      out.push({
        tag,
        driven: tag === 'Act' && Boolean(has('body')),
        hard: tag === 'button' && Boolean(disabled) && !disabled.initializer,
        label: label || tag,
      });
    }
  }
  node.forEachChild((child) => { controls(child, out); });
  return out;
}

const found = controls(tree);

test('the scan reaches the lane controls', () => {
  assert.ok(found.length >= 8,
    `only ${found.length} controls found in Rows.tsx; the walker is not ` +
    `reaching the JSX, and every check below passes on an empty list`);
  assert.ok(found.some((c) => c.driven),
    'no driven control found at all, so "driven" is not being detected');
});

test('no lane control is rendered permanently inert', () => {
  const inert = found.filter((c) => c.hard)
                     .filter((c) => !(c.label in INERT_ALLOWED));
  assert.deepEqual(inert.map((c) => c.label), [],
    `these lane controls are hard-disabled and undeclared, so they render as ` +
    `finished work and post nothing. A lane whose primary action does not ` +
    `fire cannot do its job, and the markup cannot tell you which: ` +
    `${inert.map((c) => c.label).join(', ')}`);
});

test('every Act carries a body, so pressing it sends something', () => {
  const empty = found.filter((c) => c.tag === 'Act' && !c.driven);
  assert.deepEqual(empty.map((c) => c.label), [],
    `these controls look like actions and post nothing: ` +
    `${empty.map((c) => c.label).join(', ')}`);
});

test('an inert control says what it would have ordered', () => {
  for (const [label, why] of Object.entries(INERT_ALLOWED)) {
    assert.ok(why && why.length > 60,
      `${label} is inert with no reason worth checking. "the runner moves in ` +
      `a later phase" is the sentence that kept Open & finish dark while the ` +
      `queue behind it was already being claimed.`);
  }
});
