# Base64 Ops demo script

This walkthrough is deterministic and fits in 5–8 minutes. It never claims that fixture data came from a live GitHub account.

## 1. Set the context

Introduce Base64 Ops as an evidence-first operations engineer: it investigates before it patches, keeps failed-run state separate from current state, and requires approval before draft-PR delivery.

Open `/demo`. This route renders the deterministic
fixture through the same command-center snapshot contract used by persisted
sessions. Every mutating control is disabled and the page never contacts
GitHub.

## 2. Current CI failure

Use the deterministic fixture where the frontend lives under `client/` while the failed workflow runs from `./server`.

The fixture is versioned at `backend/evals/fixtures/actions-path`; its deterministic CI behavior is covered by the `ci-stale-working-directory` evaluation case.

Ask: `Why did my PR checks fail?`

Show the CI summary, bounded log excerpt, failed workflow SHA, current HEAD, evidence cards, and the supported stale-working-directory hypothesis. Explain that the condition is **CURRENT** because both failed and current workflow still reference `./server`.

Ask for a fix. Show the exact workflow diff, validation, deterministic **HIGH** CI/CD risk, and the approval wording. Only approve in an explicitly authorized disposable environment; otherwise explain that the approval creates a draft PR through the normal delivery path.

## 3. Replay

Open the developer run inspector. Show persisted CI context, safe spans, evidence IDs, applicability, and limitations. Emphasize that replay does not re-fetch GitHub or re-run validation.

## 4. Historical CI failure

Use the alternate fixture where the failed SHA has `./server` but current HEAD has `./client`.

The `ci-historical-fixed` case is the reproducible historical-state smoke test for this behavior.

Ask: `Why did run #842 fail?`

Show distinct historical and current evidence. The status is **HISTORICAL_FIXED**. Point out that Base64 explains the prior root cause but produces no patch, DeliveryPlan, or approval record.

## 5. Close

Show Operational Memory separately from Evidence. Explain that current CI/repository evidence always outranks historical memory, and a delivered draft PR is not represented as a merged fix.

## Safety notes

The deterministic demo does not verify live GitHub access or delivery. Those remain `NOT EXERCISED` unless an authorized repository and credentials are explicitly configured.
