# Evaluation modes and external verification

## Deterministic evaluation

The default mode is offline and deterministic:

```bash
cd backend
python -m evals.runner --mode deterministic
```

It requires no model, GitHub, payment, or database credentials. It exercises
repository fixtures and safety policies, including CI-log redaction,
prompt-injection boundaries, approval requirements, path safety, and
historical/current CI provenance.

## Manually enabled evaluation mode

`python -m evals.runner --mode live` is deliberately gated behind
`BASE64_ENABLE_LIVE_EVALS=true` and has a bounded `--max-cases` argument.

At present this mode **does not call OpenAI, GitHub, or Razorpay**. It reruns
bounded deterministic cases under an explicit operator action and records an
allowlisted GitHub target, when one is provided. Its GitHub delivery result is
reported as `not_exercised`; this is not a passing delivery verification.

The manual workflow in `.github/workflows/live-evals.yml`:

- has read-only repository permissions;
- receives no OpenAI or GitHub OAuth token;
- validates `max_cases` as an integer from 0 through 5 before using it;
- does not create a branch, pull request, workflow run, or payment event.

## What is required for a real external test

An authenticated provider test is a separate implementation and operating
procedure. It needs all of the following before it can be called verified:

1. A disposable, explicitly allowlisted GitHub repository.
2. A dedicated least-privilege credential and exact allowed write scope.
3. An isolated branch/PR lifecycle with cleanup verification.
4. A budget-capped provider account and a recorded model request/usage trace.
5. An independently observed provider result, rather than a runner status.
6. A review that no live test can target a customer repository or production
   payment account.

Until those controls and actual provider calls exist, the source of truth is
the deterministic suite plus the explicit `not_exercised` status.
