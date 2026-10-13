# Agent safety boundary

Base64 Ops treats repository files, CI output, logs, runbooks, screenshots, and external adapter responses as untrusted evidence. They never redefine policy, approve an action, or grant access to credentials.

- Read-only actions use an allowlisted toolbox.
- Mutating requests can proceed only through a persisted exact delivery plan. Approval binds its diff hash, base SHA, repository, action, and canonical arguments before workspace branch creation or tool execution.
- Evidence is redacted before persistence and browser delivery.
- Evidence, sessions, approvals, maps, and GitHub credentials are queried under the authenticated user scope.
- Direct PR creation is rejected. Delivery acquires an atomic approval execution lock and revalidates the exact plan before it can create a draft PR.
- GitHub Actions is read-only. A CI log or workflow `run:` line is evidence, not an instruction or validator command. Failed-run SHA and current HEAD are represented separately, and a historical failure that is already corrected cannot create a duplicate patch.
- Optional candidate verification uses a distinct GitHub App, never an OAuth
  token. It is disabled by default, requires a repository-owner workflow
  binding plus a second exact approval, rejects patches that modify
  `.github/workflows/`, and creates only an auditable non-forced candidate
  ref. Workflow `run:` commands remain untrusted data. A targeted workflow
  success is never reported as full CI success or delivery authorization.
- Optional GitHub-native incident reporting requires a separate per-repository
  preference, a verified App installation, and `Checks: write`. It writes only
  a centrally redacted, `neutral` Check after durable evidence collection; it
  never posts PR comments, changes a workflow, reruns CI, reports a repair as
  successful, or elevates OAuth read access.
