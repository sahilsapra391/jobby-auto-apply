"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * The board's card disclosure: hover previews it, a click pins it open.
 *
 * **Item 22 was closed as "already matching" because the stylesheet arrived.**
 * `.jb-det`, `.jb-caret`, `.jb-slot`, `.jb-panel`, `.jb-sections`,
 * `.jb-section` and seven more were all in `board.css` and none of them was
 * ever rendered, so the CSS was byte-identical to the board's and the feature
 * did not exist. That is the same shape as the settings page that shipped
 * correct markup and a correct stylesheet and rendered nothing: two correct
 * halves and no third thing to join them.
 *
 * The interaction is the board's, rule for rule, from `found_lane.py:1830`:
 *
 *   hover opens a preview, so a glance costs nothing
 *   a click pins it, so a read does not have to be undone
 *   **hover never closes what a click opened**
 *   the panel is built on first open, never for the fifty cards on the page
 *
 * The last one is why `everOpened` latches instead of tracking `open`: React
 * would otherwise unmount the panel on mouse-out and refetch on the next
 * hover, which is fifty requests for someone running their eye down the lane.
 */

/** Fetched sections, kept for the life of the page like the board's own cache. */
const cache = new Map<string, Sections>();

type Sections = { responsibilities: string[]; requirements: string[] };

/** The board's caps, so a v2 card and a board card cut a long line alike. */
const POINTS = 6;

export function CardDetails(
  { id, unknowns }: { id: string; unknowns?: unknown },
) {
  const [pinned, setPinned] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [everOpened, setEverOpened] = useState(false);
  const [sections, setSections] = useState<Sections | null>(
    () => cache.get(id) ?? null);
  const [failed, setFailed] = useState(false);
  const slot = useRef<HTMLDivElement | null>(null);
  const open = pinned || hovered;

  // The card is rendered by a server component, so the hover lives here and is
  // attached to the article this sits in rather than to a wrapper: an extra
  // element between the card and its grid would be a layout change dressed as
  // an interaction one.
  useEffect(() => {
    const el = slot.current?.closest<HTMLElement>(".jb-card");
    if (!el) return;
    const enter = () => setHovered(true);
    const leave = () => setHovered(false);
    el.addEventListener("mouseenter", enter);
    el.addEventListener("mouseleave", leave);
    return () => {
      el.removeEventListener("mouseenter", enter);
      el.removeEventListener("mouseleave", leave);
    };
  }, []);

  useEffect(() => {
    if (open) setEverOpened(true);
  }, [open]);

  // `.jb-card.open` is what rotates the caret, and the card is not this
  // component's element. The board sets the same class from the same place.
  useEffect(() => {
    const card = slot.current?.closest<HTMLElement>(".jb-card");
    card?.classList.toggle("open", open);
  }, [open]);

  /**
   * The board's own max-height dance, and each step is load-bearing.
   *
   * `.jb-slot` transitions `max-height`, which cannot animate from `none`, so
   * closing puts a real number back and forces layout before flipping to zero.
   * Once open the cap is released, because a card that reflows taller than the
   * height measured at open time has its last line clipped.
   *
   * **The release cannot hang off `transitionend` alone, and the first version
   * did.** Measured: the cap sat at 410px forever. Two ways that happens. The
   * fetch lands after the animation, the effect re-runs and assigns the same
   * pixel value, and assigning a property the value it already holds starts no
   * transition, so the event never comes. And under
   * `prefers-reduced-motion` the stylesheet sets `transition:none`, so it
   * never comes for anybody. The timer is the one that always fires; the event
   * is the fast path.
   */
  useEffect(() => {
    const el = slot.current;
    if (!el) return;
    if (open) {
      el.style.maxHeight = `${el.scrollHeight}px`;
      const release = () => { el.style.maxHeight = "none"; };
      const timer = window.setTimeout(release, 320);
      const done = (event: TransitionEvent) => {
        if (event.propertyName !== "max-height") return;
        window.clearTimeout(timer);
        release();
      };
      el.addEventListener("transitionend", done);
      return () => {
        window.clearTimeout(timer);
        el.removeEventListener("transitionend", done);
      };
    }
    if (el.style.maxHeight === "none") {
      el.style.maxHeight = `${el.scrollHeight}px`;
      void el.offsetHeight;
    }
    el.style.maxHeight = "0px";
  }, [open]);

  /**
   * Content arriving while the panel is open.
   *
   * Only while it is still capped. Once released to `none` the panel is its
   * own height and re-measuring would put a ceiling back on it, which is the
   * clipping this exists to prevent.
   */
  useEffect(() => {
    const el = slot.current;
    if (!el || !open) return;
    if (el.style.maxHeight !== "none") {
      el.style.maxHeight = `${el.scrollHeight}px`;
    }
  }, [sections, open]);

  useEffect(() => {
    if (!everOpened || sections || failed) return;
    let live = true;
    fetch(`/api/posting?id=${encodeURIComponent(id)}`,
          { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!live) return;
        if (!data?.ok) { setFailed(true); return; }
        const value: Sections = {
          responsibilities: data.responsibilities ?? [],
          requirements: data.requirements ?? [],
        };
        cache.set(id, value);
        setSections(value);
      })
      // Never silent, and never an error the reader has to act on: the rest of
      // the panel still stands, and a posting whose row has been pruned is a
      // 404 rather than a fault.
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [everOpened, sections, failed, id]);

  const toggle = useCallback(() => setPinned((was) => !was), []);

  const missing = Array.isArray(unknowns)
    ? unknowns.map((u) => String(u)).filter(Boolean) : [];
  const rows: [string, string[]][] = sections
    ? [["Key responsibilities", sections.responsibilities.slice(0, POINTS)],
       ["Requirements", sections.requirements.slice(0, POINTS)]]
    : [];
  const anySection = rows.some(([, lines]) => lines.length > 0);

  return (
    <>
      <button className="jb-det" type="button" aria-expanded={open}
              onClick={toggle}
              title="Why this percentage, and what the posting asks for">
        <span className="jb-caret" />
        <span>details</span>
      </button>
      <div className="jb-slot" ref={slot}>
        {everOpened ? (
          <div className="jb-panel">
            <div className="jb-sections">
              {!sections && !failed
                ? <p className="jb-loading">Reading the posting…</p> : null}
              {rows.map(([heading, lines]) => lines.length ? (
                <div className="jb-section" key={heading}>
                  <h4>{heading}</h4>
                  <ul>{lines.map((line, i) => <li key={i}>{line}</li>)}</ul>
                </div>
              ) : null)}
              {(sections && !anySection) || failed ? (
                <p className="jb-loading">
                  This posting does not use headings this can read.
                </p>
              ) : null}
            </div>
            {/* Gates are not repeated here. They are already chips on the card
                face, and the board's own note about the excerpt applies: an
                expansion that opens on something already read is an expansion
                that was not worth opening. Unknowns are the opposite -- 87% of
                scored postings carry at least one and nothing draws them. */}
            {missing.length ? (
              <p className="jb-excerpt">
                Scored neutral because the posting does not say:{" "}
                {missing.join(", ")}.
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
    </>
  );
}
