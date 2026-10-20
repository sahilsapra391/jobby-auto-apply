// The match rubric's component order and weights, the details panel's
// per-section cap, and the card's word rules. This file is the source: edit
// it directly.
//
// COMPONENTS and WEIGHTS describe the score the device computes
// (scripts/match_score.py WEIGHTS), so they are not free to change on their
// own: tests/test_v2_rubric_matches_the_score.py fails when the two disagree.

export const COMPONENTS = ["title", "experience", "location", "keywords", "freshness"] as const;

export const WEIGHTS: Record<string, number> = {"title": 35, "experience": 20, "location": 15, "keywords": 20, "freshness": 10};

export const SECTION_POINTS = 5;

// What a source calls itself on a chip. A source not in it is title-cased.
export const SOURCE_LABELS: Record<string, string> = {"adzuna": "Adzuna", "ashby": "Ashby", "breezy": "Breezy", "careerjet": "Careerjet", "greenhouse": "Greenhouse", "himalayas": "Himalayas", "jooble": "Jooble", "lever": "Lever", "linkedin": "LinkedIn", "personio": "Personio", "recruitee": "Recruitee", "reed": "Reed", "remoteok": "RemoteOK", "remotive": "Remotive", "rippling": "Rippling", "smartrecruiters": "SmartRecruiters", "themuse": "The Muse", "usajobs": "USAJobs", "weworkremotely": "We Work Remotely", "workable": "Workable", "workingnomads": "Working Nomads"};

// The tag tidier's two word lists and the card's caps: tags are kept to
// MAX_TAGS when compacted and the card draws four.
// `tests/card_words_read_like_the_board.test.mjs` runs both.
export const KEEP_UPPER: string[] = ["3D", "AI", "AML", "APAC", "API", "APIs", "AR", "AWS", "B2B", "B2C", "CEO", "CFO", "COO", "CRM", "CTO", "CX", "DevOps", "EMEA", "ERP", "EU", "F", "GCP", "GTM", "HR", "IT", "IoT", "K", "KYC", "LLM", "M", "M&A", "ML", "NLP", "P&L", "PM", "PPC", "QA", "R&D", "RX", "SDK", "SEM", "SEO", "SQL", "SRE", "SaaS", "UAE", "UI", "UK", "US", "USA", "UX", "VP", "VR", "iOS"];

export const SMALL_WORDS: string[] = ["a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "vs", "with"];

export const MAX_TAGS = 6;

// The rubric persisted "posted 1 days ago", and the board mends it
// wherever it draws a reason.
export const DAY_WORDS: [string, string][] = [[" 0 days ago", " today"], [" 1 days ago", " yesterday"]];

// The Submitted Status chip per history event: [chip modifier, label].
export const OUTCOME_CHIPS: Record<string, [string, string]> = {"completed": ["", "submitted"], "interview": ["warm", "interview"], "offer": ["ready", "offer"], "rejected": ["", "rejected"], "response": ["warm", "response"]};
