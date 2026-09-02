// What an event does to a lane, and whether the map agrees with the SQL.
//
// **The lane list is derived from the generated SQL, not restated.** A lane
// moving between `postings` and `applications` while this map stays put is a
// lane that silently stops updating: every test still passes, the board still
// renders, and one column of it is simply frozen. That is the shape CLAUDE.md
// calls a check that measures nothing.

import { test } from "node:test";
import assert from "node:assert/strict";
import { actionFor, applyRow, POSTING_LANES } from "../apps/web/app/_ui/project.ts";
import { LANES } from "../apps/web/app/lanes.generated.ts";
import { KINDS } from "../apps/web/app/_ui/events.ts";

const at = (kind, payload = {}) =>
  ({ seq: 1, account_id: null, kind, at: "", payload });

/** Which table a lane actually reads, off its own generated SQL. */
function tableOf(lane) {
  const sql = LANES[lane].row;
  const posting = /^\s*from postings p$/m.test(sql);
  const application = /^\s*from applications a$/m.test(sql);
  assert.ok(posting !== application,
    `${lane} reads neither or both tables; the regex has stopped matching`);
  return posting ? "postings" : "applications";
}

/* ------------------------------------------------- against the real SQL */

test("the posting lanes are the lanes whose SQL reads postings", () => {
  const derived = Object.keys(LANES).filter((l) => tableOf(l) === "postings");
  assert.deepEqual(derived.sort(), [...POSTING_LANES].sort(),
    "this map is a second spelling of a fact the SQL already states, so it " +
    "is compared with it rather than trusted");
  assert.ok(derived.length > 0 && derived.length < Object.keys(LANES).length,
    "a regex matching everything or nothing would satisfy the line above");
});

test("every application lane asks about an application event", () => {
  for (const lane of Object.keys(LANES)) {
    const action = actionFor(lane, at("application.changed", { application_id: "a1" }));
    const expected = tableOf(lane) === "applications"
      ? { do: "ask", id: "a1" } : { do: "ignore" };
    assert.deepEqual(action, expected, `${lane} got the wrong action`);
  }
});

test("only the posting lanes care that a posting was decided", () => {
  for (const lane of Object.keys(LANES)) {
    const action = actionFor(lane, at("posting.decided", { posting_id: "p1" }));
    const expected = tableOf(lane) === "postings"
      ? { do: "ask", id: "p1" } : { do: "ignore" };
    assert.deepEqual(action, expected, `${lane} got the wrong action`);
  }
});

test("every kind the channel can carry has a decision here", () => {
  // Not "the ones this file thought of". A kind added to the channel and not
  // to the map falls through to ignore, which is the silent half of the
  // failure this file exists to catch.
  for (const kind of KINDS) {
    for (const lane of Object.keys(LANES)) {
      const action = actionFor(lane, at(kind, {
        application_id: "a1", posting_id: "p1",
      }));
      assert.ok(["ignore", "ask", "reload"].includes(action.do),
        `${kind} on ${lane} produced ${JSON.stringify(action)}`);
    }
  }
  const decided = [...KINDS].filter((kind) =>
    Object.keys(LANES).some((lane) => actionFor(lane, at(kind, {
      application_id: "a1", posting_id: "p1",
    })).do !== "ignore"));
  assert.deepEqual(decided.sort(), [
    "application.changed", "application.completed", "application.needs_you",
    "application.phase", "pool.delta", "posting.decided",
  ], "the kinds that move a lane, named, so adding one is a decision here");
});

/* ------------------------------------------------------- payload guards */

test("an event naming nothing moves no lane", () => {
  for (const lane of Object.keys(LANES)) {
    assert.deepEqual(actionFor(lane, at("application.changed", {})),
      { do: "ignore" }, `${lane} acted on an event with no subject`);
    assert.deepEqual(actionFor(lane, at("application.changed", { application_id: 7 })),
      { do: "ignore" }, `${lane} took a number as an id`);
  }
});

test("a pool sweep reloads Found and carries no number to add", () => {
  assert.deepEqual(actionFor("found", at("pool.delta", { added: 40 })),
    { do: "reload" },
    "reload, not add 40: a resync replays events on top of a fetch that may " +
    "already hold them, so anything accumulated would double");
  assert.deepEqual(actionFor("watch", at("pool.delta", { added: 40 })), { do: "ignore" });
});

/* ---------------------------------------------------------- applyRow */

test("the lane's empty answer takes the row out", () => {
  const rows = [{ id: "a" }, { id: "b" }, { id: "c" }];
  const out = applyRow(rows, "b", null);
  assert.deepEqual(out.rows.map((r) => r.id), ["a", "c"]);
  assert.equal(out.changed, true);
  assert.equal(out.arrived, false);
});

test("a row the lane still holds is replaced where it stands", () => {
  const rows = [{ id: "a" }, { id: "b", phase: "one" }, { id: "c" }];
  const out = applyRow(rows, "b", { id: "b", phase: "two" });
  assert.deepEqual(out.rows.map((r) => r.id), ["a", "b", "c"],
    "in place: a card that jumps while it is being read is worse than one a " +
    "moment out of order");
  assert.equal(out.rows[1].phase, "two");
  assert.equal(out.arrived, false);
});

test("a row the lane holds and the list does not is announced, not inserted", () => {
  const rows = [{ id: "a" }];
  const out = applyRow(rows, "z", { id: "z" });
  assert.deepEqual(out.rows.map((r) => r.id), ["a"],
    "inserting means sorting into an order the database chose, which is the " +
    "keyset comparator written a second time in another language");
  assert.equal(out.arrived, true);
  assert.equal(out.changed, false);
});

test("removing a row that was never shown changes nothing", () => {
  const rows = [{ id: "a" }];
  const out = applyRow(rows, "z", null);
  assert.equal(out.rows, rows, "the same array, so React does not re-render");
  assert.equal(out.changed, false);
  assert.equal(out.arrived, false);
});
