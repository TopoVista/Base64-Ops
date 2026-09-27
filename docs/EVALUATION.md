# Evaluation

Run deterministic safety regressions from `backend`:

```powershell
python -m evals.runner
python -m evals.runner --case docker-binding
python -m evals.runner --category security
python -m evals.runner --format json
```

The suite has 28 deterministic cases: repository diagnostics and safety fixtures, plus 13 GitHub Actions CI cases covering stale workflow paths, dependency and test failures, historical fixes, bounded logs, permission degradation, prompt injection, command confusion, tenant/repository isolation, and redaction.

Metrics are deterministic: fixture evidence recall, fixture citation precision, expected/prohibited tool selection, approval-policy behavior, and structurally unsupported claims. It does **not** report LLM diagnosis accuracy, semantic citation quality, token cost, or live GitHub success because those are not reliably measured by these offline fixtures. Add a case by creating a fixture directory and JSON case under `backend/evals`.

GitHub Actions integration tests use deterministic adapter/service fakes. They verify redaction, bounded excerpts, repository-scoped run selection, failed-run/current-SHA separation, historical-failure applicability, changed-file correlation, and log-permission degradation. A live authenticated Actions run is deliberately not part of the deterministic suite.

Use `python -m evals.runner --mode deterministic --output ../artifacts/eval-summary.json --format json` from `backend` to generate a machine-readable report containing only measured case results and summary metrics.
