# Exact-diff approval model

Base64 approval is not conversational permission. A pending approval is created only after a persisted delivery plan contains the exact file diffs, base branch, base commit, validation results, risk classification, and evidence IDs.

## Approval hash

The service canonicalizes this JSON with sorted keys and compact separators, then calculates SHA-256:

```json
{
  "action_type": "create_draft_pull_request",
  "base_branch": "main",
  "base_sha": "…",
  "canonical_arguments": {},
  "delivery_plan_id": "dpl_…",
  "diff_hash": "…",
  "repository_id": "…"
}
```

No secret is included in this payload. Before execution, Base64 recomputes the diff and approval hashes and checks the repository HEAD. A changed plan, action, arguments, repository, branch, or HEAD invalidates the approval and prevents mutation.

## Optional candidate-verification approval

Candidate verification has its own action type,
`dispatch_candidate_verification`, and its own exact approval hash. It binds
the same plan/base/diff fields plus the server-verified GitHub App installation,
reviewed workflow path, candidate branch, and candidate patch hash. Approving
it can create only an auditable disposable candidate branch and dispatch the
configured workflow; it cannot create a PR, push the delivery branch, merge,
or change the normal delivery approval. Any changed settings, candidate hash,
workflow path, plan, branch, or base SHA invalidates the request. See
[CANDIDATE_VERIFICATION.md](CANDIDATE_VERIFICATION.md).

Execution uses an atomic `approved → executing` Mongo transition. Only the request that wins this transition can create a branch, commit, push, or draft PR. Supported delivery is limited to the exact network-binding remediation plan; deployment, rollback, workflow dispatch, and arbitrary shell execution remain unsupported.
