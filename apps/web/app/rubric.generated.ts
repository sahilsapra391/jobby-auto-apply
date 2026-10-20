// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.
// The rubric's own component order and weights, from
// found_lane.COMPONENTS and match_score.WEIGHTS, and the
// details panel's per-section cap, from found_lane.SECTION_POINTS.

export const COMPONENTS = ["title", "experience", "location", "keywords", "freshness"] as const;

export const WEIGHTS: Record<string, number> = {"title": 35, "experience": 20, "location": 15, "keywords": 20, "freshness": 10};

export const SECTION_POINTS = 5;

// What a board calls itself on a chip, from found_lane.SOURCE_LABELS.
// A source not in it is title-cased, which is the board's fallback.
export const SOURCE_LABELS: Record<string, string> = {"adzuna": "Adzuna", "ashby": "Ashby", "breezy": "Breezy", "careerjet": "Careerjet", "greenhouse": "Greenhouse", "himalayas": "Himalayas", "jooble": "Jooble", "lever": "Lever", "linkedin": "LinkedIn", "personio": "Personio", "recruitee": "Recruitee", "reed": "Reed", "remoteok": "RemoteOK", "remotive": "Remotive", "rippling": "Rippling", "smartrecruiters": "SmartRecruiters", "themuse": "The Muse", "usajobs": "USAJobs", "weworkremotely": "We Work Remotely", "workable": "Workable", "workingnomads": "Working Nomads"};

// found_lane.tidy_tag's two word lists and the card's caps: tags
// are kept to MAX_TAGS when compacted and the card draws four.
// `tests/card_words_read_like_the_board.test.mjs` runs both.
export const KEEP_UPPER: string[] = ["3D", "AI", "AML", "APAC", "API", "APIs", "AR", "AWS", "B2B", "B2C", "CEO", "CFO", "COO", "CRM", "CTO", "CX", "DevOps", "EMEA", "ERP", "EU", "F", "GCP", "GTM", "HR", "IT", "IoT", "K", "KYC", "LLM", "M", "M&A", "ML", "NLP", "P&L", "PM", "PPC", "QA", "R&D", "RX", "SDK", "SEM", "SEO", "SQL", "SRE", "SaaS", "UAE", "UI", "UK", "US", "USA", "UX", "VP", "VR", "iOS"];

export const SMALL_WORDS: string[] = ["a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "vs", "with"];

export const MAX_TAGS = 6;

// found_lane.DAY_WORDS: the rubric persisted "posted 1 days ago",
// and the board mends it wherever it draws a reason.
export const DAY_WORDS: [string, string][] = [[" 0 days ago", " today"], [" 1 days ago", " yesterday"]];

// The Submitted Status chip per history event, from dashboard.py's
// own `outcome_chip`: [chip modifier, label].
export const OUTCOME_CHIPS: Record<string, [string, string]> = {"completed": ["", "submitted"], "interview": ["warm", "interview"], "offer": ["ready", "offer"], "rejected": ["", "rejected"], "response": ["warm", "response"]};
