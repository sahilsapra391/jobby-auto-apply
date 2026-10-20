import {
  DAY_WORDS, KEEP_UPPER, MAX_TAGS, SMALL_WORDS, SOURCE_LABELS,
} from "../rubric.generated";

/**
 * The words a Found card prints that the store did not write that way.
 *
 * **One contract in two languages.** `found_lane.tidy_tag`, `tidy_days` and the
 * source label's title-case fallback run in Python on the board and here on
 * v2, and the word lists both read are generated from the Python
 * (`rubric.generated.ts`). `tests/card_words_read_like_the_board.test.mjs`
 * runs both implementations over the same inputs and compares the output,
 * because a port that restates the rule passes while it drifts.
 *
 * Plain module, no `"use client"`: the Found card is a server component and
 * calls these during render. CLAUDE.md records what a value imported across
 * that boundary turns into.
 */

const CANONICAL = new Map(KEEP_UPPER.map((w) => [w.toUpperCase(), w]));
const SMALL = new Set(SMALL_WORDS);

const hasUpper = (text: string) =>
  [...text].some((c) => c !== c.toLowerCase() && c === c.toUpperCase());

/** `found_lane.tidy_tag`: a chip reads as a label, not as a database value. */
export function tidyTag(tag: unknown): string {
  const text = String(tag ?? "").trim();
  if (!text) return "";
  const whole = CANONICAL.get(text.toUpperCase());
  if (whole) return whole;
  // The board already styled it.
  if (hasUpper(text)) return text;
  return text.split(/\s+/).map((word, i) => {
    const known = CANONICAL.get(word.toUpperCase());
    if (known) return known;
    if (i && SMALL.has(word)) return word;
    return word.slice(0, 1).toUpperCase() + word.slice(1);
  }).join(" ");
}

/**
 * The tags a card draws: `compact` keeps the first `MAX_TAGS` that tidy to
 * something, and the card draws four of those (`found_lane.py`'s
 * `(job.tg || []).slice(0, 4)`).
 */
export function cardTags(tags: unknown): string[] {
  if (!Array.isArray(tags)) return [];
  return tags.slice(0, MAX_TAGS).map(tidyTag).filter(Boolean).slice(0, 4);
}

/** `found_lane.tidy_days`: "posted 1 days ago" is already in the store. */
export function tidyDays(text: unknown): string {
  let out = String(text ?? "");
  for (const [wrong, right] of DAY_WORDS) {
    if (out.includes(wrong)) out = out.split(wrong).join(right);
  }
  return out;
}

/** Python's `str.title()`, for the one place the board falls back to it. */
function pythonTitle(text: string): string {
  return text.toLowerCase().replace(/(^|[^a-z])([a-z])/g,
    (_m, before: string, letter: string) => before + letter.toUpperCase());
}

/**
 * What a board calls itself on a chip: `SOURCE_LABELS.get(source,
 * source.title())`, which is how `compact` names both the card's source and
 * each sighting.
 */
export function sourceLabel(source: unknown): string {
  const slug = String(source ?? "");
  return SOURCE_LABELS[slug] ?? pythonTitle(slug);
}
