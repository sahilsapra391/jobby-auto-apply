// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.
//
// The lane SQL lives in scripts/v2_lanes.py and is emitted here so
// there is one spelling of each predicate. Parameters are positional
// for node-postgres, in the order [account, afterAt, afterId, limit].
export type Lane = "found" | "watch" | "flight" | "act" | "done" | "dropped";

export type Sort = "new" | "score";

// The date windows the board's radio row offers, from
// found_lane.DATE_WINDOWS. The value is the interval the SQL casts.
export const WINDOWS: Record<string, string> = {"6h": "6 hours", "24h": "24 hours", "7d": "7 days", "14d": "14 days", "30d": "30 days"};

// What each sort is called in the control, taken from the
// board's own found_lane.SORT_OPTIONS.
export const SORT_LABELS: Record<Sort, string> = {"new": "Newest", "score": "Match %"};

// Each query carries the parameters it takes, in order. Numbering is
// contiguous per query: a shared map leaves gaps Postgres cannot type.
export type LaneQuery = { sql: string; params: string[] };

export const LANES: Record<Lane, { label: string; page: string; pageParams: string[]; count: string; countParams: string[]; row: string; rowParams: string[]; sorts: Record<string, LaneQuery> }> =
{
  "found": {
    "label": "Found",
    "page": "select p.id, p.legacy_id, p.title, p.location_raw, p.remote,\n             p.workplace, p.posted_at, p.apply_url, p.years_min, p.years_max,\n             p.summary, p.source, p.salary_raw, e.canonical_name as employer,\n             e.domain as employer_domain, e.logo_url as employer_logo,\n             s.total as score, s.parts as score_parts, s.gates as score_gates,\n             s.unknowns as score_unknowns,\n             p.logo_src, p.logo_domain, p.logo_alt\n  from postings p\n  left join job_decisions d\n    on d.posting_id = p.id and d.account_id = $1\n  left join employers e on e.id = p.employer_id\n  left join job_scores s\n    on s.posting_id = p.id and s.account_id = $1\n where p.closed_at is null and d.posting_id is null\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            p.posted_at < $2::timestamptz\n         or p.posted_at is null\n         or (p.posted_at = $2::timestamptz\n             and p.id < $3::uuid)))\n   or ($2::timestamptz is null and p.posted_at is null\n       and p.id < $3::uuid)\n)\n   and ($5::text is null\n        or p.search_tsv @@ websearch_to_tsquery('english', $5::text))\n   and ($6::text is null\n        or (p.posted_at is not null\n            and p.posted_at >= now() - $6::interval))\n   and (not coalesce($7::boolean, false)\n        or p.sources @> '[{\"sourceClass\":\"ats\"}]'::jsonb)\n order by p.posted_at desc nulls last, p.id desc\n limit $4",
    "pageParams": [
      "account",
      "after_at",
      "after_id",
      "limit",
      "q",
      "within",
      "direct"
    ],
    "count": "select count(*)\n  from postings p\n  left join job_decisions d\n    on d.posting_id = p.id and d.account_id = $1\n where p.closed_at is null and d.posting_id is null\n   and ($2::text is null\n        or p.search_tsv @@ websearch_to_tsquery('english', $2::text))\n   and ($3::text is null\n        or (p.posted_at is not null\n            and p.posted_at >= now() - $3::interval))\n   and (not coalesce($4::boolean, false)\n        or p.sources @> '[{\"sourceClass\":\"ats\"}]'::jsonb)",
    "countParams": [
      "account",
      "q",
      "within",
      "direct"
    ],
    "row": "select p.id, p.legacy_id, p.title, p.location_raw, p.remote,\n             p.workplace, p.posted_at, p.apply_url, p.years_min, p.years_max,\n             p.summary, p.source, p.salary_raw, e.canonical_name as employer,\n             e.domain as employer_domain, e.logo_url as employer_logo,\n             s.total as score, s.parts as score_parts, s.gates as score_gates,\n             s.unknowns as score_unknowns,\n             p.logo_src, p.logo_domain, p.logo_alt\n  from postings p\n  left join job_decisions d\n    on d.posting_id = p.id and d.account_id = $1\n  left join employers e on e.id = p.employer_id\n  left join job_scores s\n    on s.posting_id = p.id and s.account_id = $1\n where p.closed_at is null and d.posting_id is null\n   and p.id = $2::uuid\n limit 1",
    "rowParams": [
      "account",
      "row_id"
    ],
    "sorts": {
      "new": {
        "sql": "select p.id, p.legacy_id, p.title, p.location_raw, p.remote,\n             p.workplace, p.posted_at, p.apply_url, p.years_min, p.years_max,\n             p.summary, p.source, p.salary_raw, e.canonical_name as employer,\n             e.domain as employer_domain, e.logo_url as employer_logo,\n             s.total as score, s.parts as score_parts, s.gates as score_gates,\n             s.unknowns as score_unknowns,\n             p.logo_src, p.logo_domain, p.logo_alt\n  from postings p\n  left join job_decisions d\n    on d.posting_id = p.id and d.account_id = $1\n  left join employers e on e.id = p.employer_id\n  left join job_scores s\n    on s.posting_id = p.id and s.account_id = $1\n where p.closed_at is null and d.posting_id is null\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            p.posted_at < $2::timestamptz\n         or p.posted_at is null\n         or (p.posted_at = $2::timestamptz\n             and p.id < $3::uuid)))\n   or ($2::timestamptz is null and p.posted_at is null\n       and p.id < $3::uuid)\n)\n   and ($5::text is null\n        or p.search_tsv @@ websearch_to_tsquery('english', $5::text))\n   and ($6::text is null\n        or (p.posted_at is not null\n            and p.posted_at >= now() - $6::interval))\n   and (not coalesce($7::boolean, false)\n        or p.sources @> '[{\"sourceClass\":\"ats\"}]'::jsonb)\n order by p.posted_at desc nulls last, p.id desc\n limit $4",
        "params": [
          "account",
          "after_at",
          "after_id",
          "limit",
          "q",
          "within",
          "direct"
        ]
      },
      "score": {
        "sql": "select p.id, p.legacy_id, p.title, p.location_raw, p.remote,\n             p.workplace, p.posted_at, p.apply_url, p.years_min, p.years_max,\n             p.summary, p.source, p.salary_raw, e.canonical_name as employer,\n             e.domain as employer_domain, e.logo_url as employer_logo,\n             s.total as score, s.parts as score_parts, s.gates as score_gates,\n             s.unknowns as score_unknowns,\n             p.logo_src, p.logo_domain, p.logo_alt\n  from postings p\n  left join job_decisions d\n    on d.posting_id = p.id and d.account_id = $1\n  left join employers e on e.id = p.employer_id\n  left join job_scores s\n    on s.posting_id = p.id and s.account_id = $1\n where p.closed_at is null and d.posting_id is null\n   and (\n      $3::uuid is null\n   or ($2::int is not null and (\n            s.total < $2::int\n         or s.total is null\n         or (s.total = $2::int\n             and p.id < $3::uuid)))\n   or ($2::int is null and s.total is null\n       and p.id < $3::uuid)\n)\n   and ($5::text is null\n        or p.search_tsv @@ websearch_to_tsquery('english', $5::text))\n   and ($6::text is null\n        or (p.posted_at is not null\n            and p.posted_at >= now() - $6::interval))\n   and (not coalesce($7::boolean, false)\n        or p.sources @> '[{\"sourceClass\":\"ats\"}]'::jsonb)\n order by s.total desc nulls last, p.id desc\n limit $4",
        "params": [
          "account",
          "after_at",
          "after_id",
          "limit",
          "q",
          "within",
          "direct"
        ]
      }
    }
  },
  "watch": {
    "label": "Watching",
    "page": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             a.posting_id is not null as linked,\n             coalesce(p.title, a.role) as role,\n             coalesce(p.apply_url, a.apply_url) as apply_url,\n             p.location_raw,\n             coalesce(p.posted_at, a.posted_at) as posted_at,\n             p.years_min, p.years_max,\n             coalesce(e.canonical_name, a.company) as company,\n             e.domain as employer_domain,\n             p.logo_src, p.logo_domain, p.logo_alt,\n             a.blockers, a.core_skills, a.fit_score, a.alignment,\n             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,\n             (select h.at from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_at,\n             (select h.payload from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_payload\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status = 'watch' and not exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            a.updated_at < $2::timestamptz\n         or a.updated_at is null\n         or (a.updated_at = $2::timestamptz\n             and a.id < $3::uuid)))\n   or ($2::timestamptz is null and a.updated_at is null\n       and a.id < $3::uuid)\n)\n   and ($5::text is null\n        or concat_ws(' ', coalesce(e.canonical_name, a.company),\n                          coalesce(p.title, a.role),\n                          p.location_raw, a.region,\n                          array_to_string(a.core_skills, ' '))\n            ilike '%%' || $5::text || '%%')\n order by a.updated_at desc nulls last, a.id desc\n limit $4",
    "pageParams": [
      "account",
      "after_at",
      "after_id",
      "limit",
      "q"
    ],
    "count": "select count(*)\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1 and a.status = 'watch' and not exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and ($2::text is null\n        or concat_ws(' ', coalesce(e.canonical_name, a.company),\n                          coalesce(p.title, a.role),\n                          p.location_raw, a.region,\n                          array_to_string(a.core_skills, ' '))\n            ilike '%%' || $2::text || '%%')",
    "countParams": [
      "account",
      "q"
    ],
    "row": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             a.posting_id is not null as linked,\n             coalesce(p.title, a.role) as role,\n             coalesce(p.apply_url, a.apply_url) as apply_url,\n             p.location_raw,\n             coalesce(p.posted_at, a.posted_at) as posted_at,\n             p.years_min, p.years_max,\n             coalesce(e.canonical_name, a.company) as company,\n             e.domain as employer_domain,\n             p.logo_src, p.logo_domain, p.logo_alt,\n             a.blockers, a.core_skills, a.fit_score, a.alignment,\n             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,\n             (select h.at from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_at,\n             (select h.payload from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_payload\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status = 'watch' and not exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and a.id = $2::uuid\n limit 1",
    "rowParams": [
      "account",
      "row_id"
    ],
    "sorts": {
      "new": {
        "sql": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             a.posting_id is not null as linked,\n             coalesce(p.title, a.role) as role,\n             coalesce(p.apply_url, a.apply_url) as apply_url,\n             p.location_raw,\n             coalesce(p.posted_at, a.posted_at) as posted_at,\n             p.years_min, p.years_max,\n             coalesce(e.canonical_name, a.company) as company,\n             e.domain as employer_domain,\n             p.logo_src, p.logo_domain, p.logo_alt,\n             a.blockers, a.core_skills, a.fit_score, a.alignment,\n             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,\n             (select h.at from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_at,\n             (select h.payload from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_payload\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status = 'watch' and not exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            a.updated_at < $2::timestamptz\n         or a.updated_at is null\n         or (a.updated_at = $2::timestamptz\n             and a.id < $3::uuid)))\n   or ($2::timestamptz is null and a.updated_at is null\n       and a.id < $3::uuid)\n)\n   and ($5::text is null\n        or concat_ws(' ', coalesce(e.canonical_name, a.company),\n                          coalesce(p.title, a.role),\n                          p.location_raw, a.region,\n                          array_to_string(a.core_skills, ' '))\n            ilike '%%' || $5::text || '%%')\n order by a.updated_at desc nulls last, a.id desc\n limit $4",
        "params": [
          "account",
          "after_at",
          "after_id",
          "limit",
          "q"
        ]
      }
    }
  },
  "flight": {
    "label": "In flight",
    "page": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             a.posting_id is not null as linked,\n             coalesce(p.title, a.role) as role,\n             coalesce(p.apply_url, a.apply_url) as apply_url,\n             p.location_raw,\n             coalesce(p.posted_at, a.posted_at) as posted_at,\n             p.years_min, p.years_max,\n             coalesce(e.canonical_name, a.company) as company,\n             e.domain as employer_domain,\n             p.logo_src, p.logo_domain, p.logo_alt,\n             a.blockers, a.core_skills, a.fit_score, a.alignment,\n             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,\n             (select h.at from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_at,\n             (select h.payload from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_payload\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status = 'inflight' and not exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            a.updated_at < $2::timestamptz\n         or a.updated_at is null\n         or (a.updated_at = $2::timestamptz\n             and a.id < $3::uuid)))\n   or ($2::timestamptz is null and a.updated_at is null\n       and a.id < $3::uuid)\n)\n   and ($5::text is null\n        or concat_ws(' ', coalesce(e.canonical_name, a.company),\n                          coalesce(p.title, a.role),\n                          p.location_raw, a.region,\n                          array_to_string(a.core_skills, ' '))\n            ilike '%%' || $5::text || '%%')\n order by a.updated_at desc nulls last, a.id desc\n limit $4",
    "pageParams": [
      "account",
      "after_at",
      "after_id",
      "limit",
      "q"
    ],
    "count": "select count(*)\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1 and a.status = 'inflight' and not exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and ($2::text is null\n        or concat_ws(' ', coalesce(e.canonical_name, a.company),\n                          coalesce(p.title, a.role),\n                          p.location_raw, a.region,\n                          array_to_string(a.core_skills, ' '))\n            ilike '%%' || $2::text || '%%')",
    "countParams": [
      "account",
      "q"
    ],
    "row": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             a.posting_id is not null as linked,\n             coalesce(p.title, a.role) as role,\n             coalesce(p.apply_url, a.apply_url) as apply_url,\n             p.location_raw,\n             coalesce(p.posted_at, a.posted_at) as posted_at,\n             p.years_min, p.years_max,\n             coalesce(e.canonical_name, a.company) as company,\n             e.domain as employer_domain,\n             p.logo_src, p.logo_domain, p.logo_alt,\n             a.blockers, a.core_skills, a.fit_score, a.alignment,\n             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,\n             (select h.at from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_at,\n             (select h.payload from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_payload\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status = 'inflight' and not exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and a.id = $2::uuid\n limit 1",
    "rowParams": [
      "account",
      "row_id"
    ],
    "sorts": {
      "new": {
        "sql": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             a.posting_id is not null as linked,\n             coalesce(p.title, a.role) as role,\n             coalesce(p.apply_url, a.apply_url) as apply_url,\n             p.location_raw,\n             coalesce(p.posted_at, a.posted_at) as posted_at,\n             p.years_min, p.years_max,\n             coalesce(e.canonical_name, a.company) as company,\n             e.domain as employer_domain,\n             p.logo_src, p.logo_domain, p.logo_alt,\n             a.blockers, a.core_skills, a.fit_score, a.alignment,\n             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,\n             (select h.at from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_at,\n             (select h.payload from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_payload\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status = 'inflight' and not exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            a.updated_at < $2::timestamptz\n         or a.updated_at is null\n         or (a.updated_at = $2::timestamptz\n             and a.id < $3::uuid)))\n   or ($2::timestamptz is null and a.updated_at is null\n       and a.id < $3::uuid)\n)\n   and ($5::text is null\n        or concat_ws(' ', coalesce(e.canonical_name, a.company),\n                          coalesce(p.title, a.role),\n                          p.location_raw, a.region,\n                          array_to_string(a.core_skills, ' '))\n            ilike '%%' || $5::text || '%%')\n order by a.updated_at desc nulls last, a.id desc\n limit $4",
        "params": [
          "account",
          "after_at",
          "after_id",
          "limit",
          "q"
        ]
      }
    }
  },
  "act": {
    "label": "Needs you",
    "page": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             a.posting_id is not null as linked,\n             coalesce(p.title, a.role) as role,\n             coalesce(p.apply_url, a.apply_url) as apply_url,\n             p.location_raw,\n             coalesce(p.posted_at, a.posted_at) as posted_at,\n             p.years_min, p.years_max,\n             coalesce(e.canonical_name, a.company) as company,\n             e.domain as employer_domain,\n             p.logo_src, p.logo_domain, p.logo_alt,\n             a.blockers, a.core_skills, a.fit_score, a.alignment,\n             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,\n             (select h.at from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_at,\n             (select h.payload from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_payload\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status in ('review', 'active') and not exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            a.updated_at < $2::timestamptz\n         or a.updated_at is null\n         or (a.updated_at = $2::timestamptz\n             and a.id < $3::uuid)))\n   or ($2::timestamptz is null and a.updated_at is null\n       and a.id < $3::uuid)\n)\n   and ($5::text is null\n        or concat_ws(' ', coalesce(e.canonical_name, a.company),\n                          coalesce(p.title, a.role),\n                          p.location_raw, a.region,\n                          array_to_string(a.core_skills, ' '))\n            ilike '%%' || $5::text || '%%')\n order by a.updated_at desc nulls last, a.id desc\n limit $4",
    "pageParams": [
      "account",
      "after_at",
      "after_id",
      "limit",
      "q"
    ],
    "count": "select count(*)\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1 and a.status in ('review', 'active') and not exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and ($2::text is null\n        or concat_ws(' ', coalesce(e.canonical_name, a.company),\n                          coalesce(p.title, a.role),\n                          p.location_raw, a.region,\n                          array_to_string(a.core_skills, ' '))\n            ilike '%%' || $2::text || '%%')",
    "countParams": [
      "account",
      "q"
    ],
    "row": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             a.posting_id is not null as linked,\n             coalesce(p.title, a.role) as role,\n             coalesce(p.apply_url, a.apply_url) as apply_url,\n             p.location_raw,\n             coalesce(p.posted_at, a.posted_at) as posted_at,\n             p.years_min, p.years_max,\n             coalesce(e.canonical_name, a.company) as company,\n             e.domain as employer_domain,\n             p.logo_src, p.logo_domain, p.logo_alt,\n             a.blockers, a.core_skills, a.fit_score, a.alignment,\n             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,\n             (select h.at from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_at,\n             (select h.payload from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_payload\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status in ('review', 'active') and not exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and a.id = $2::uuid\n limit 1",
    "rowParams": [
      "account",
      "row_id"
    ],
    "sorts": {
      "new": {
        "sql": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             a.posting_id is not null as linked,\n             coalesce(p.title, a.role) as role,\n             coalesce(p.apply_url, a.apply_url) as apply_url,\n             p.location_raw,\n             coalesce(p.posted_at, a.posted_at) as posted_at,\n             p.years_min, p.years_max,\n             coalesce(e.canonical_name, a.company) as company,\n             e.domain as employer_domain,\n             p.logo_src, p.logo_domain, p.logo_alt,\n             a.blockers, a.core_skills, a.fit_score, a.alignment,\n             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,\n             (select h.at from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_at,\n             (select h.payload from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_payload\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status in ('review', 'active') and not exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            a.updated_at < $2::timestamptz\n         or a.updated_at is null\n         or (a.updated_at = $2::timestamptz\n             and a.id < $3::uuid)))\n   or ($2::timestamptz is null and a.updated_at is null\n       and a.id < $3::uuid)\n)\n   and ($5::text is null\n        or concat_ws(' ', coalesce(e.canonical_name, a.company),\n                          coalesce(p.title, a.role),\n                          p.location_raw, a.region,\n                          array_to_string(a.core_skills, ' '))\n            ilike '%%' || $5::text || '%%')\n order by a.updated_at desc nulls last, a.id desc\n limit $4",
        "params": [
          "account",
          "after_at",
          "after_id",
          "limit",
          "q"
        ]
      }
    }
  },
  "done": {
    "label": "Submitted",
    "page": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             a.posting_id is not null as linked,\n             coalesce(p.title, a.role) as role,\n             coalesce(p.apply_url, a.apply_url) as apply_url,\n             p.location_raw,\n             coalesce(p.posted_at, a.posted_at) as posted_at,\n             p.years_min, p.years_max,\n             coalesce(e.canonical_name, a.company) as company,\n             e.domain as employer_domain,\n             p.logo_src, p.logo_domain, p.logo_alt,\n             a.blockers, a.core_skills, a.fit_score, a.alignment,\n             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,\n             (select h.at from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_at,\n             (select h.payload from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_payload\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            a.updated_at < $2::timestamptz\n         or a.updated_at is null\n         or (a.updated_at = $2::timestamptz\n             and a.id < $3::uuid)))\n   or ($2::timestamptz is null and a.updated_at is null\n       and a.id < $3::uuid)\n)\n   and ($5::text is null\n        or concat_ws(' ', coalesce(e.canonical_name, a.company),\n                          coalesce(p.title, a.role),\n                          p.location_raw, a.region,\n                          array_to_string(a.core_skills, ' '))\n            ilike '%%' || $5::text || '%%')\n order by a.updated_at desc nulls last, a.id desc\n limit $4",
    "pageParams": [
      "account",
      "after_at",
      "after_id",
      "limit",
      "q"
    ],
    "count": "select count(*)\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1 and exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and ($2::text is null\n        or concat_ws(' ', coalesce(e.canonical_name, a.company),\n                          coalesce(p.title, a.role),\n                          p.location_raw, a.region,\n                          array_to_string(a.core_skills, ' '))\n            ilike '%%' || $2::text || '%%')",
    "countParams": [
      "account",
      "q"
    ],
    "row": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             a.posting_id is not null as linked,\n             coalesce(p.title, a.role) as role,\n             coalesce(p.apply_url, a.apply_url) as apply_url,\n             p.location_raw,\n             coalesce(p.posted_at, a.posted_at) as posted_at,\n             p.years_min, p.years_max,\n             coalesce(e.canonical_name, a.company) as company,\n             e.domain as employer_domain,\n             p.logo_src, p.logo_domain, p.logo_alt,\n             a.blockers, a.core_skills, a.fit_score, a.alignment,\n             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,\n             (select h.at from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_at,\n             (select h.payload from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_payload\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and a.id = $2::uuid\n limit 1",
    "rowParams": [
      "account",
      "row_id"
    ],
    "sorts": {
      "new": {
        "sql": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             a.posting_id is not null as linked,\n             coalesce(p.title, a.role) as role,\n             coalesce(p.apply_url, a.apply_url) as apply_url,\n             p.location_raw,\n             coalesce(p.posted_at, a.posted_at) as posted_at,\n             p.years_min, p.years_max,\n             coalesce(e.canonical_name, a.company) as company,\n             e.domain as employer_domain,\n             p.logo_src, p.logo_domain, p.logo_alt,\n             a.blockers, a.core_skills, a.fit_score, a.alignment,\n             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,\n             (select h.at from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_at,\n             (select h.payload from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_payload\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            a.updated_at < $2::timestamptz\n         or a.updated_at is null\n         or (a.updated_at = $2::timestamptz\n             and a.id < $3::uuid)))\n   or ($2::timestamptz is null and a.updated_at is null\n       and a.id < $3::uuid)\n)\n   and ($5::text is null\n        or concat_ws(' ', coalesce(e.canonical_name, a.company),\n                          coalesce(p.title, a.role),\n                          p.location_raw, a.region,\n                          array_to_string(a.core_skills, ' '))\n            ilike '%%' || $5::text || '%%')\n order by a.updated_at desc nulls last, a.id desc\n limit $4",
        "params": [
          "account",
          "after_at",
          "after_id",
          "limit",
          "q"
        ]
      }
    }
  },
  "dropped": {
    "label": "Dropped",
    "page": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             a.posting_id is not null as linked,\n             coalesce(p.title, a.role) as role,\n             coalesce(p.apply_url, a.apply_url) as apply_url,\n             p.location_raw,\n             coalesce(p.posted_at, a.posted_at) as posted_at,\n             p.years_min, p.years_max,\n             coalesce(e.canonical_name, a.company) as company,\n             e.domain as employer_domain,\n             p.logo_src, p.logo_domain, p.logo_alt,\n             a.blockers, a.core_skills, a.fit_score, a.alignment,\n             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,\n             (select h.at from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_at,\n             (select h.payload from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_payload\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status = 'dropped'\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            a.updated_at < $2::timestamptz\n         or a.updated_at is null\n         or (a.updated_at = $2::timestamptz\n             and a.id < $3::uuid)))\n   or ($2::timestamptz is null and a.updated_at is null\n       and a.id < $3::uuid)\n)\n   and ($5::text is null\n        or concat_ws(' ', coalesce(e.canonical_name, a.company),\n                          coalesce(p.title, a.role),\n                          p.location_raw, a.region,\n                          array_to_string(a.core_skills, ' '))\n            ilike '%%' || $5::text || '%%')\n order by a.updated_at desc nulls last, a.id desc\n limit $4",
    "pageParams": [
      "account",
      "after_at",
      "after_id",
      "limit",
      "q"
    ],
    "count": "select count(*)\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1 and a.status = 'dropped'\n   and ($2::text is null\n        or concat_ws(' ', coalesce(e.canonical_name, a.company),\n                          coalesce(p.title, a.role),\n                          p.location_raw, a.region,\n                          array_to_string(a.core_skills, ' '))\n            ilike '%%' || $2::text || '%%')",
    "countParams": [
      "account",
      "q"
    ],
    "row": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             a.posting_id is not null as linked,\n             coalesce(p.title, a.role) as role,\n             coalesce(p.apply_url, a.apply_url) as apply_url,\n             p.location_raw,\n             coalesce(p.posted_at, a.posted_at) as posted_at,\n             p.years_min, p.years_max,\n             coalesce(e.canonical_name, a.company) as company,\n             e.domain as employer_domain,\n             p.logo_src, p.logo_domain, p.logo_alt,\n             a.blockers, a.core_skills, a.fit_score, a.alignment,\n             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,\n             (select h.at from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_at,\n             (select h.payload from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_payload\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status = 'dropped'\n   and a.id = $2::uuid\n limit 1",
    "rowParams": [
      "account",
      "row_id"
    ],
    "sorts": {
      "new": {
        "sql": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             a.posting_id is not null as linked,\n             coalesce(p.title, a.role) as role,\n             coalesce(p.apply_url, a.apply_url) as apply_url,\n             p.location_raw,\n             coalesce(p.posted_at, a.posted_at) as posted_at,\n             p.years_min, p.years_max,\n             coalesce(e.canonical_name, a.company) as company,\n             e.domain as employer_domain,\n             p.logo_src, p.logo_domain, p.logo_alt,\n             a.blockers, a.core_skills, a.fit_score, a.alignment,\n             a.dropped_from, a.dropped_stage, a.seen_on_boards, a.ats,\n             (select h.at from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_at,\n             (select h.payload from application_events h\n               where h.application_id = a.id and h.kind = 'completed'\n               order by h.at desc limit 1) as completed_payload\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status = 'dropped'\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            a.updated_at < $2::timestamptz\n         or a.updated_at is null\n         or (a.updated_at = $2::timestamptz\n             and a.id < $3::uuid)))\n   or ($2::timestamptz is null and a.updated_at is null\n       and a.id < $3::uuid)\n)\n   and ($5::text is null\n        or concat_ws(' ', coalesce(e.canonical_name, a.company),\n                          coalesce(p.title, a.role),\n                          p.location_raw, a.region,\n                          array_to_string(a.core_skills, ' '))\n            ilike '%%' || $5::text || '%%')\n order by a.updated_at desc nulls last, a.id desc\n limit $4",
        "params": [
          "account",
          "after_at",
          "after_id",
          "limit",
          "q"
        ]
      }
    }
  }
} as const;
