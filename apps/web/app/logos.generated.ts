// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.
// The hosts a feed logo may be fetched from, from
// company_logos.TRUSTED_SOURCE_HOSTS. A third hand-written copy
// of this set is an open redirect nobody is watching.

export const LOGO_HOSTS: ReadonlySet<string> =
  new Set([
    "cdn-images.himalayas.app",
    "remoteok.com",
    "remotive.com",
    "we-work-remotely.imgix.net",
    "wwr-pro.s3.amazonaws.com"
]);

// `alt=1` means try the other TLDs of the same label. A constant
// here rather than in each URL, which is what kept the Python
// board's Found payload inside its cap.
export const CANDIDATE_TLDS = [".com", ".io", ".ai", ".co"] as const;
