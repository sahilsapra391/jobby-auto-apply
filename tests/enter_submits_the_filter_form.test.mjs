// Enter in a filter text box applies the filter, the way the board's does.
//
// **It stopped working when the Filter button was removed, and nothing noticed
// because nothing reads the spec.** The HTML spec's implicit submission refuses
// Enter in a form with no submit button and more than one field that blocks
// it. This form has three -- keyword, "Hide jobs mentioning", company names --
// so Enter did nothing: "product manager" typed, Enter pressed, and the URL,
// the chips and the count stayed put. Found from a screen recording on
// 2026-09-10, on a real keyboard. The cause is the measured form structure, not
// a reproduction: see the next note for why the pane could not reproduce it.
//
// Two halves, and neither is enough alone:
//
//   the rule     a form with more than one blocking field and no submit button
//                must have an Enter handler. Written as the spec's own
//                condition rather than "SubmitOnChange has a keydown listener",
//                so adding a real submit button passes it honestly and removing
//                the handler without one fails it.
//
//   the action   what Enter does, compiled out of SubmitOnChange and run. The
//                board has two cases a plain submit gets wrong: an empty box
//                adds nothing, and a keyword already chipped is not added again
//                but the box still clears.
//
// **What neither half can see**, said rather than implied: whether a real
// keyboard's Enter reaches the handler. And the browser pane cannot answer it
// either -- its `type` fires no keydown and its `key` action delivers none, so a
// keyboard test there is unfalsifiable. It made the original bug look
// reproduced and then made this fix look broken, in one session. The pane can
// verify the handler with a synthetic keydown (defaultPrevented, form submits,
// 2,421 jobs to 312 on the deployed board); only a real keyboard verifies
// delivery.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createRequire } from 'node:module';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..');
const ts = createRequire(join(REPO, 'apps/web/package.json'))('typescript');

function tree(rel) {
  const file = join(REPO, rel);
  const src = readFileSync(file, 'utf8');
  const t = ts.createSourceFile(file, src, ts.ScriptTarget.ES2022, true,
                                ts.ScriptKind.TSX);
  assert.equal(t.parseDiagnostics?.length ?? 0, 0,
    `${rel} does not parse: ` +
    (t.parseDiagnostics ?? []).map((d) => String(d.messageText)).join('; '));
  return t;
}

const attr = (el, name) => {
  const a = el.attributes.properties.find(
    (p) => ts.isJsxAttribute(p) && p.name.getText() === name);
  if (!a) return undefined;
  if (!a.initializer) return true;
  return ts.isStringLiteral(a.initializer) ? a.initializer.text : '(expression)';
};

/** Every opening/self-closing tag under the `jb-filter-form` element. */
function filterFormTags() {
  const t = tree('apps/web/app/_ui/FoundSurface.tsx');
  let form = null;
  const find = (n) => {
    if (form) return;
    if (ts.isJsxElement(n) && n.openingElement.tagName.getText() === 'form'
        && attr(n.openingElement, 'className') === 'jb-filter-form') form = n;
    else n.forEachChild(find);
  };
  find(t);
  assert.ok(form, 'no <form className="jb-filter-form"> in FoundSurface');
  const tags = [];
  const walk = (n) => {
    if (ts.isJsxSelfClosingElement(n)) tags.push(n);
    else if (ts.isJsxElement(n)) tags.push(n.openingElement);
    n.forEachChild(walk);
  };
  form.children.forEach(walk);
  return tags;
}

/** The input types the spec says block implicit submission. */
const BLOCKING = new Set(['text', 'search', 'url', 'tel', 'email', 'password',
  'date', 'month', 'week', 'time', 'datetime-local', 'number']);

test('a form Enter cannot submit has an Enter handler', () => {
  const tags = filterFormTags();
  const blocking = tags.filter((el) => el.tagName.getText() === 'input'
    && BLOCKING.has(String(attr(el, 'type') ?? 'text')));
  const submits = tags.filter((el) => {
    const tag = el.tagName.getText();
    const type = attr(el, 'type');
    return (tag === 'button' && (type === undefined || type === 'submit'))
      || (tag === 'input' && (type === 'submit' || type === 'image'));
  });
  // The floor. With one blocking field Enter works natively and this whole
  // file would be about nothing.
  assert.ok(blocking.length >= 2,
    `found ${blocking.length} text fields in the filter form; the walk has ` +
    `stopped reaching them`);

  if (blocking.length > 1 && submits.length === 0) {
    const src = readFileSync(join(REPO, 'apps/web/app/_ui/SubmitOnChange.tsx'), 'utf8');
    const t = tree('apps/web/app/_ui/SubmitOnChange.tsx');
    let listens = false, decides = false;
    const walk = (n) => {
      if (ts.isCallExpression(n)) {
        const callee = n.expression.getText();
        const first = n.arguments[0];
        if (callee.endsWith('addEventListener') && first
            && ts.isStringLiteral(first) && first.text === 'keydown') listens = true;
        if (callee === 'enterAction') decides = true;
      }
      n.forEachChild(walk);
    };
    walk(t);
    assert.ok(listens,
      `the filter form has ${blocking.length} fields that block implicit ` +
      `submission (${blocking.map((b) => attr(b, 'name')).join(', ')}) and no ` +
      `submit button, so the browser ignores Enter -- and nothing listens for ` +
      `keydown to do it instead`);
    assert.ok(decides, 'a keydown listener exists and never consults enterAction');
    assert.match(src, /requestSubmit\(\)/, 'nothing ever submits the form');
  }
});

/** `enterAction`, compiled out of SubmitOnChange and run. */
function loadEnterAction() {
  const t = tree('apps/web/app/_ui/SubmitOnChange.tsx');
  let decl = null;
  const walk = (n) => {
    if (ts.isFunctionDeclaration(n) && n.name?.getText() === 'enterAction') decl = n.getText();
    n.forEachChild(walk);
  };
  walk(t);
  assert.ok(decl, 'SubmitOnChange declares no enterAction');
  const js = ts.transpileModule(`${decl}\nmodule.exports = { enterAction };`,
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
      fileName: 'e.ts' }).outputText;
  const mod = { exports: {} };
  new Function('module', 'exports', js)(mod, mod.exports);
  return mod.exports.enterAction;
}

const enterAction = loadEnterAction();

test('a new keyword submits', () => {
  assert.equal(enterAction('q', 'product manager', [], false), 'submit');
  assert.equal(enterAction('q', 'growth', ['product manager'], false), 'submit');
});

test('an empty keyword box does nothing, like the board', () => {
  // `if (word && keywords.indexOf(word) === -1)` -- no word, no chip, and a
  // reload that changes nothing is not a response to the key.
  assert.equal(enterAction('q', '', [], false), 'ignore');
  assert.equal(enterAction('q', '   ', ['growth'], false), 'ignore');
});

test('a keyword that is already a chip clears the box and does not reload', () => {
  assert.equal(enterAction('q', 'product manager', ['product manager'], false), 'clear');
  assert.equal(enterAction('q', '  product manager ', ['product manager'], false), 'clear',
    'the board trims before comparing, so surrounding spaces are the same word');
});

test('an emptied exclude or company box submits, because that removes the filter', () => {
  assert.equal(enterAction('not', '', [], false), 'submit');
  assert.equal(enterAction('conames', '', [], false), 'submit');
  assert.equal(enterAction('not', 'senior', [], false), 'submit');
});

test('Enter during IME composition is left to the IME', () => {
  // Enter confirms a character there. Treating it as a search would submit
  // half a word in Japanese, Chinese or Korean input.
  for (const name of ['q', 'not', 'conames']) {
    assert.equal(enterAction(name, 'x', [], true), 'ignore');
  }
});
