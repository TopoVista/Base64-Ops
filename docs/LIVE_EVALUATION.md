# Live Evaluation Framework & GitHub Delivery Architecture

## Overview

Base64 Ops includes an optional live evaluation framework (`python -m evals.runner --mode live`). The default evaluation mode remains strictly offline and deterministic (`--mode deterministic`), requiring zero cloud credentials or API tokens.

---

## Controlled Live Evaluation

Live evaluation requires explicit environment enablement:
```bash
export BASE64_ENABLE_LIVE_EVALS=true
python -m evals.runner --mode live --max-cases 5
```

If `BASE64_ENABLE_LIVE_EVALS` is omitted or set to `false`, the runner outputs a clear status message and exits without making paid model or network API calls.

---

## Live GitHub Delivery Test Architecture

Controlled end-to-end GitHub delivery testing targets allowlisted disposable repositories:
1. **Target Repository**: Configured via `BASE64_LIVE_TEST_REPOSITORY`.
2. **Allowlist Check**: Verifies `target_repo` is present in `BASE64_LIVE_EVAL_ALLOWED_REPOS`. Non-allowlisted repositories are aborted immediately with `status: "aborted"`.
3. **Execution Flow**:
   - Create ephemeral test branch (`eval/live-test-<timestamp>`).
   - Introduce target defect.
   - Run investigation and generate candidate patch.
   - Execute exact approved delivery plan.
   - Verify pull request diff.
   - Perform automated branch/PR cleanup.

---

## CI & Safety Policy

- **Normal Pull Request CI**: Credential-free offline execution only (`backend-unit-tests`, `ruff-check`, `deterministic-evals`, `frontend-build`).
- **Manual Live Workflow**: Configured in [.github/workflows/live-evals.yml](file:///c:/Users/KIIT0001/Desktop/base64/Base64-Ops/.github/workflows/live-evals.yml) triggered via `workflow_dispatch` only.
