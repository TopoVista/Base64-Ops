# Threat model

| Threat | Existing defense | Remaining limitation |
| --- | --- | --- |
| Malicious repository, workflow, or CI-log instructions | All are explicitly untrusted Evidence; policy and command registry are separate | Semantic social-engineering quality still benefits from review |
| Secret exfiltration | Central redaction before evidence, model context, UI, and traces | Regex redaction cannot guarantee recognition of every secret format |
| Path traversal / Git internals | `RepositoryPathPolicy` validates repository-relative paths | Read access depends on local workspace availability |
| Shell injection / workflow-command confusion | Fixed argument subprocesses and validator registry; workflow `run:` is never executed | Authorized validators still require maintenance |
| Approval replay / stale plan | Exact plan, SHA, diff, and argument checks with atomic execution | User must review the approved diff |
| Tenant or repository crossover | User- and repository-scoped queries and Actions resolution | Live provider authorization remains externally dependent |
| Historical CI state confused with current state | Separate SHA provenance and deterministic applicability | Missing history produces a fail-closed limitation |
| Memory poisoning | Evidence-backed write policy, freshness, and lower precedence than current evidence | Live merged-PR verification remains unexercised |
| Network exfiltration during validation | Docker sandbox disables network when available; UI reports restricted-local fallback honestly | Local fallback cannot claim network isolation |
