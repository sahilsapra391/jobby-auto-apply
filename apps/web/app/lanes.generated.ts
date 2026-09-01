// GENERATED. Do not edit. `python3 scripts/sync_v2_styles.py`.
//
// The lane SQL lives in scripts/v2_lanes.py and is emitted here so
// there is one spelling of each predicate. Parameters are positional
// for node-postgres, in the order [account, afterAt, afterId, limit].
export type Lane = "found" | "watch" | "flight" | "act" | "done" | "dropped";

export const LANES: Record<Lane, { label: string; page: string; count: string }> =
{
  "found": {
    "label": "Found",
    "page": "select p.id, p.legacy_id, p.title, p.location_raw, p.remote,\n             p.workplace, p.posted_at, p.apply_url, p.years_min, p.years_max,\n             p.summary, p.source, p.salary_raw, e.canonical_name as employer,\n             e.domain as employer_domain, e.logo_url as employer_logo\n  from postings p\n  left join job_decisions d\n    on d.posting_id = p.id and d.account_id = $1\n  left join employers e on e.id = p.employer_id\n where p.closed_at is null and d.posting_id is null\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            p.posted_at < $2::timestamptz\n         or p.posted_at is null\n         or (p.posted_at = $2::timestamptz\n             and p.id < $3::uuid)))\n   or ($2::timestamptz is null and p.posted_at is null\n       and p.id < $3::uuid)\n)\n order by p.posted_at desc nulls last, p.id desc\n limit $4",
    "count": "select count(*)\n  from postings p\n  left join job_decisions d\n    on d.posting_id = p.id and d.account_id = $1\n where p.closed_at is null and d.posting_id is null"
  },
  "watch": {
    "label": "Watching",
    "page": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             p.title as role, p.apply_url, p.location_raw, p.posted_at,\n             p.years_min, p.years_max,\n             e.canonical_name as company, e.domain as employer_domain\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status = 'watch'\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            a.updated_at < $2::timestamptz\n         or a.updated_at is null\n         or (a.updated_at = $2::timestamptz\n             and a.id < $3::uuid)))\n   or ($2::timestamptz is null and a.updated_at is null\n       and a.id < $3::uuid)\n)\n order by a.updated_at desc nulls last, a.id desc\n limit $4",
    "count": "select count(*)\n  from applications a\n where a.account_id = $1 and a.status = 'watch'"
  },
  "flight": {
    "label": "In flight",
    "page": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             p.title as role, p.apply_url, p.location_raw, p.posted_at,\n             p.years_min, p.years_max,\n             e.canonical_name as company, e.domain as employer_domain\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status = 'inflight'\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            a.updated_at < $2::timestamptz\n         or a.updated_at is null\n         or (a.updated_at = $2::timestamptz\n             and a.id < $3::uuid)))\n   or ($2::timestamptz is null and a.updated_at is null\n       and a.id < $3::uuid)\n)\n order by a.updated_at desc nulls last, a.id desc\n limit $4",
    "count": "select count(*)\n  from applications a\n where a.account_id = $1 and a.status = 'inflight'"
  },
  "act": {
    "label": "Needs you",
    "page": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             p.title as role, p.apply_url, p.location_raw, p.posted_at,\n             p.years_min, p.years_max,\n             e.canonical_name as company, e.domain as employer_domain\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status in ('review', 'active')\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            a.updated_at < $2::timestamptz\n         or a.updated_at is null\n         or (a.updated_at = $2::timestamptz\n             and a.id < $3::uuid)))\n   or ($2::timestamptz is null and a.updated_at is null\n       and a.id < $3::uuid)\n)\n order by a.updated_at desc nulls last, a.id desc\n limit $4",
    "count": "select count(*)\n  from applications a\n where a.account_id = $1 and a.status in ('review', 'active')"
  },
  "done": {
    "label": "Submitted",
    "page": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             p.title as role, p.apply_url, p.location_raw, p.posted_at,\n             p.years_min, p.years_max,\n             e.canonical_name as company, e.domain as employer_domain\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            a.updated_at < $2::timestamptz\n         or a.updated_at is null\n         or (a.updated_at = $2::timestamptz\n             and a.id < $3::uuid)))\n   or ($2::timestamptz is null and a.updated_at is null\n       and a.id < $3::uuid)\n)\n order by a.updated_at desc nulls last, a.id desc\n limit $4",
    "count": "select count(*)\n  from applications a\n where a.account_id = $1 and exists (\n            select 1 from application_events h\n             where h.application_id = a.id and h.kind = 'completed'\n               and not exists (\n                 select 1 from application_events r\n                  where r.application_id = a.id\n                    and r.kind = 'abandoned' and r.at > h.at))"
  },
  "dropped": {
    "label": "Dropped",
    "page": "select a.id, a.legacy_id, a.status, a.phase, a.updated_at,\n             a.created_at, a.posting_id,\n             p.title as role, p.apply_url, p.location_raw, p.posted_at,\n             p.years_min, p.years_max,\n             e.canonical_name as company, e.domain as employer_domain\n  from applications a\n  left join postings p on p.id = a.posting_id\n  left join employers e on e.id = p.employer_id\n where a.account_id = $1\n   and a.status = 'dropped'\n   and (\n      $3::uuid is null\n   or ($2::timestamptz is not null and (\n            a.updated_at < $2::timestamptz\n         or a.updated_at is null\n         or (a.updated_at = $2::timestamptz\n             and a.id < $3::uuid)))\n   or ($2::timestamptz is null and a.updated_at is null\n       and a.id < $3::uuid)\n)\n order by a.updated_at desc nulls last, a.id desc\n limit $4",
    "count": "select count(*)\n  from applications a\n where a.account_id = $1 and a.status = 'dropped'"
  }
} as const;
