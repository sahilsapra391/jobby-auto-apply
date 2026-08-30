/**
 * The numbers the surface displays, until (d) reads them from Postgres.
 *
 * A fixture, and named one: every value here is invented and none of it comes
 * from anyone's store. The shapes match what the board's own state carries, so
 * (d) replaces this module and touches nothing else.
 */
export const SCAN = {
  version: 23,
  cadence: "Every 4 hours, 6 times a day",
  nextRun: "19:47 EDT",
  lastScan: "15:51 EDT",
  seen: 18674, fresh: 783, merged: 8211, filtered: 9680, closed: 36,
};

export const PROBLEMS = [
  { source: "ashby", times: 2, detail: "Nutanix (nutanix): 404, the board or token does not exist" },
  { source: "greenhouse", times: 4, detail: "Boston (boston): 404, the board or token does not exist" },
  { source: "jooble", times: 1, detail: "jooble any '': HTTP 400" },
  { source: "lever", times: 1, detail: "every board failed (1)" },
  { source: "recruitee", times: 3, detail: "Hetzner (hetzner): HTTP 302" },
];

export const LANES = [
  { key: "found", label: "Found", count: 14169 },
  { key: "watch", label: "Watching", count: 16 },
  { key: "flight", label: "In flight", count: 0 },
  { key: "act", label: "Needs you", count: 16 },
  { key: "done", label: "Submitted", count: 103 },
] as const;

export const DROPPED_COUNT = 57;

export const PROVIDERS = [
  { id: "openai", name: "OpenAI", agent: "Codex", model: "gpt-5.6-luna",
    connected: true, detail: "Signed in with a ChatGPT subscription." },
  { id: "anthropic", name: "Anthropic", agent: "Claude Code", model: "claude-sonnet-5",
    connected: false, detail: "Add an API key from the Anthropic Console." },
  { id: "gemini", name: "Gemini", agent: "agy", model: "gemini-3.7-flash-medium",
    connected: false, detail: "Add an API key from Google AI Studio." },
];
