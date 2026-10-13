# Candidate verification

Status: **Implemented and mock-tested; disabled by default; live GitHub App
verification not exercised in this repository.**

Candidate verification is a separate, optional safety boundary for checking an
exact `DeliveryPlan` before that plan is delivered as a draft pull request. It
does not reuse a user's GitHub OAuth token, and it does not change normal
read-only CI investigation or the existing delivery approval flow.

## What it does

After a repository owner explicitly opts in, Base64 Ops can create a temporary
candidate branch containing the exact approved diff and dispatch one reviewed,
repository-owned GitHub Actions workflow on that branch. The resulting
`verification-run-v1` record retains the exact plan ID, base SHA, candidate
patch hash, branch, workflow path, status, timestamps, and safe GitHub-run
link. It never persists an installation token, App private key, raw workflow
commands, or complete logs.

The status meanings are deliberately narrow:

| Status | Meaning | Does not mean |
| --- | --- | --- |
| `proposed` | Exact diff exists; static information was recorded. | A remote job ran. |
| `static_validation_passed` | Existing structural/registered validation passed. | Targeted or full CI passed. |
| `dispatched` / `pending` / `running` | The reviewed workflow was requested or observed. | The candidate is correct. |
| `targeted_verification_passed` | The configured workflow completed successfully for the candidate SHA. | All repository CI passed or a PR was delivered. |
| `targeted_verification_failed` | The configured workflow did not pass. | The diagnosis is disproved. |
| `inconclusive` / `stale` | Observation or base-SHA binding could not complete. | A patch may be delivered anyway. |

## Setup

1. Create a dedicated GitHub App; do not add verification authority to the
   OAuth app.
2. Install it only on repositories that explicitly need candidate verification.
3. Grant the minimum required repository permissions:
   - **Contents: Read & write** — to create the non-forced disposable candidate
     ref after a human approves an exact plan.
   - **Actions: Read & write** — to dispatch and then observe the reviewed
     workflow.
   - **Checks: Write** only when optionally enabling neutral incident Check
     reporting. It is not needed for candidate verification itself.
   - No administration, secrets, environments, deployments, pull-request
     review, or organization permissions are required by this implementation.
4. Set the server-only environment variables:

   ```text
   GITHUB_CANDIDATE_VERIFICATION_ENABLED=true
   GITHUB_APP_ID=<numeric GitHub App ID>
   GITHUB_APP_PRIVATE_KEY=<PEM stored as a host secret>
   GITHUB_CANDIDATE_VERIFICATION_MAX_RUNS_PER_DAY=5
   ```

   In production, startup fails closed when the feature is enabled without the
   App ID/private key. All API and `ci_observer` worker replicas need the same
   server-side App credentials. Never place the private key in Vercel or a
   browser environment variable.
5. Add and review a workflow in each opted-in repository. In **Incident Inbox
   → Repository automation → Candidate verification**, enter the installation
   ID and its path, for example
   `.github/workflows/base64-candidate-verify.yml`. Base64 confirms that the
   App installation can access the repository and that the file exists at the
   repository default branch before it saves the binding.

No workflow is installed or edited by Base64 Ops. The workflow's `run:` lines
remain untrusted repository data and never enter the command registry.

## Required workflow hardening

Repository owners must review their workflow themselves. A suitable workflow
uses only `workflow_dispatch`, checks out the supplied candidate ref, has the
least `permissions:` possible, has a bounded `timeout-minutes`, and runs only
known repository checks. Do **not** use `pull_request_target`, privileged
artifact-consumption chains, production credentials, or untrusted fork code.

Base64 sends no workflow inputs and never parses workflow YAML into executable
commands. The workflow controls which checks it runs; Base64 only records its
bounded completion metadata. The candidate feature rejects plans that touch
`.github/workflows/` so a candidate cannot alter the workflow that validates
it.

## Optional incident Check reporting

Set `GITHUB_INCIDENT_REPORTING_ENABLED=true` only after the dedicated App has
the optional **Checks: Write** permission. In **Incident Inbox → Repository
automation**, a repository owner may then enable *Publish a neutral GitHub
Check after evidence collection*. Reporting also requires that repository's
already verified App installation binding.

Base64 creates or updates one `Base64 Ops CI investigation` Check on the
failed SHA. Its conclusion is always `neutral`; it contains only a bounded,
centrally redacted investigation summary and an authenticated Base64 Ops link.
It never creates a PR comment, reruns/cancels Actions, changes a workflow,
marks a repair successful, or exposes evidence/log payloads. Missing
permissions or provider failures only add a limitation to the persisted
incident; they do not discard evidence or retry the investigation.

## Authorization and lifecycle

The user first creates an evidence-bound `DeliveryPlan`, then separately asks
for candidate verification from the Code workspace. This creates a new exact
approval with `action_type=dispatch_candidate_verification`; it is independent
of the approval used to deliver a draft PR. The approval hash binds:

- repository URL and base branch/SHA;
- immutable delivery-plan ID and diff hash;
- App installation ID and reviewed workflow path;
- generated candidate branch name and candidate patch hash.

On approval, Base64 rechecks this binding and the live base ref. It creates
only the exact blob/tree/commit described by the plan, creates a non-forced
`base64-verify/...` branch, and dispatches the configured workflow. It never
creates a pull request, pushes to the base branch, merges, reruns arbitrary
CI, or grants a delivery approval.

The optional durable `ci_observer` worker polls only the persisted workflow
path, candidate branch, and candidate SHA. A manual **Refresh status** action
uses the same bounded tenant-scoped observer. A daily per-repository cap limits
new verification attempts. Candidate branches are intentionally retained for
audit until a repository owner cleans them up; Base64 does not silently delete
remote refs.

## Safety and known limitations

- GitHub App tokens are minted ephemerally and are never persisted, traced, or
  returned to the browser.
- Browser-provided repository names are never used as App targets. The server
  proves the OAuth-owned session repository against the App installation.
- A stale base SHA, changed settings, altered exact diff, changed workflow
  path, or disabled opt-in fails closed.
- Candidate workflow job logs are not ingested by this feature. Its durable
  outcome is workflow metadata only, so it does not claim per-command results
  or full-CI success. Use the existing redacted read-only CI investigation for
  log diagnosis.
- Live App installation, permissions, and Actions dispatch have not been
  exercised in this checkout. Mocked deterministic tests cover binding,
  branch/tree creation, dispatch, observation, redaction allowlists, and
  workflow-modification rejection.
