// The lane subtitles and empty states. This file is the source: edit it
// directly. Each empty state names the control that fills its lane, so a
// blank panel never reads as broken.

export const LANE_HINTS: Record<string, string> = {
  "act": "Each opens straight to where it is blocked.",
  "done": "Already out. Check here before applying again.",
  "watch": "Newest first. Search or filter to narrow."
};

export const LANE_EMPTY: Record<string, [string, string]> = {
  "act": [
    "Nothing needs you.",
    "Every application is either done or running. Press Apply on something in Watching if you want more here."
  ],
  "flight": [
    "Nothing in flight.",
    "What are you doing? Go apply for more jobs."
  ],
  "watch": [
    "Nothing on the watch list.",
    "Press Scan now and it will go looking."
  ],
  "done": [
    "Nothing submitted yet.",
    "The first one has to start somewhere."
  ],
  "dropped": [
    "Nothing dropped.",
    "Roles you say no to end up here, with how far they had got."
  ]
};

// The board's own placeholder, so the two searches read alike.
export const LANE_SEARCH_PLACEHOLDER = "Search company, role, skill, location...";
