# Failure handling

Failures are classified as recoverable, user configuration, permission, external service, policy denied, validation failed, approval invalidated, or fatal. Only recoverable/external service failures may be retried; authentication, authorization, policy, approval, argument, and validation failures are never retried automatically.

The delivery path stops at the first failed prerequisite. A changed repository branch, HEAD, plan, diff, action, or canonical arguments invalidates approval before mutation. User-facing stream messages remain concise; technical diagnostics are limited to secret-redacted trace summaries.
