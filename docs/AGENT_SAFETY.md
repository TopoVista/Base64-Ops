# Agent safety boundary

Base64 Ops treats repository files, CI output, logs, runbooks, screenshots, and external adapter responses as untrusted evidence. They never redefine policy, approve an action, or grant access to credentials.

- Read-only actions use an allowlisted toolbox.
- Mutating requests can proceed only through a persisted exact delivery plan. Approval binds its diff hash, base SHA, repository, action, and canonical arguments before workspace branch creation or tool execution.
- Evidence is redacted before persistence and browser delivery.
- Evidence, sessions, approvals, maps, and GitHub credentials are queried under the authenticated user scope.
- Direct PR creation is rejected. Delivery acquires an atomic approval execution lock and revalidates the exact plan before it can create a draft PR.
- GitHub Actions is read-only. A CI log or workflow `run:` line is evidence, not an instruction or validator command. Failed-run SHA and current HEAD are represented separately, and a historical failure that is already corrected cannot create a duplicate patch.
