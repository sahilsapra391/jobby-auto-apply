# Claude handoff: application worker repair, 2026-08-24

Two production applications exposed failures at the document-to-form seam.
IFS showed `Documents ready` indefinitely even though four application slots
were free. Versant repeatedly returned `result_invalid` after Retry.

## Root causes

IFS had a valid role-specific resume and cover letter installed by the document
worker. Its company folder also contained an older generic IFS resume. The
brief selector treated every folder with multiple resumes as ambiguous, ignored
the exact generated filename recorded by the document contract, and handed the
form runner no resume. The runner refused immediately, but its preflight refusal
did not restore the prior lane or release the durable browser affinity. The UI
therefore said it was waiting for a slot even though the slot held no runner.

Versant passed provider generation, response parsing, content validation, and
PDF rendering. ATS provenance rejected a resume override containing a term not
supported by the base resume or story facts. The worker scrubbed plaintext as
designed, but Retry sent the same inputs through the same all-or-nothing path.

The full suite also exposed an intermittent submission bug. `token_urlsafe`
may begin with `-`; when passed after `--nonce`, argparse can interpret the
value as another option and reject a legitimate completion.

## Repairs

`brief.find_documents` accepts the exact names from
`document_contract.output_names`. A complete exact pair wins over older generic
or unrelated files. If no exact pair exists, the conservative prior behavior
remains: one pair is reusable, while multiple uncertain pairs are refused.

`document_worker.remove_unsupported_overrides` removes only top-level resume
override groups containing unsupported matched terms. The verified base wording
then supplies those sections. The worker renders and validates again, including
ATS provenance, layout, cover-letter completeness, and file safety. Unsupported
terms never enter the persistent log. A second failure still returns
`result_invalid`; no safety gate is weakened.

`watch.clear_document_failure` removes a resolved Document generation blocker
before form handoff and preserves every unrelated blocker. A preflight refusal
in `run_apply.py` restores the prior lane and clears its empty browser slot.

`finish.py` prefixes generated submission nonces with `jobby_`.

## Evidence

Focused worker, brief, watcher, and provider-runner suite: 202 passed.
Full repository suite: 1,147 passed and one pre-existing environment skip.
Tests that pin the production failures:

- `DocumentOwnershipTests.test_an_exact_generated_pair_wins_over_an_older_generic_pair`
- `WorkerPipelineTests.test_unsupported_ats_override_is_removed_and_validated_again`
- `ProviderNeutralApplyTests.test_resolved_document_failure_is_removed_before_form_handoff`
- `ApplyArtifactTests.test_preflight_refusal_restores_lane_and_releases_empty_slot`
- `CompletedTests.test_generated_nonce_can_never_look_like_an_option`

Do not delete old company documents to solve ownership. The selector must remain
correct with several roles in one folder. Do not log unsupported terms or copy
them into queue error metadata. Do not automatically submit either affected
application while verifying this repair. Retry may generate documents and start
form filling, but submission still requires the normal guarded confirmation.
