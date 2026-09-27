# Diagnostic Dashboard

The diagnostic dashboard is available from a session's **Code** view. It is a
dark, motion-enhanced workspace for inspecting CI output, reviewing source
context, and displaying an exact proposed diff without creating a second agent
or a browser-side Git client.

The editable source pane uses `react-simple-code-editor`; the right-hand review
surface uses the application's responsive split diff viewer.

## Streaming contract

`POST /api/stream-diagnostic` requires the normal Clerk bearer token. It accepts
both camel-case browser fields and snake-case integration fields:

```json
{
  "sessionSlugId": "session-id",
  "failedLog": "... CI output ...",
  "gitDiff": "... optional unified diff ...",
  "originalContent": "... optional source context ...",
  "generateFix": false
}
```

The same payload may use `session_slug_id`, `failed_log`, and `git_diff`.
Responses use `text/event-stream` with `Cache-Control: no-cache`,
`Connection: keep-alive`, and `X-Accel-Buffering: no`.

The default stream emits, in order:

1. `state` — `Analyzing log frames...`
2. `log_analysis` — redacted, bounded diagnostic markdown chunks
3. `state` — `Synthesizing minimal code fix...`
4. `code_fix` — either an exact plan diff or an explicit no-proposal result
5. `complete` — structured status, categories, truncation, and mutation state

## Proposal generation

Setting `generateFix` to `true` is an explicit request to run the existing
repository investigation graph. The pasted log, editor text, and diff are
labelled **UNTRUSTED EVIDENCE** in the request sent to that graph. A proposal is
only shown when the normal graph produces a validated `DeliveryPlan`; the UI
then renders its unified diff in the existing side-by-side diff viewer.

The dashboard never constructs a patch directly from pasted CI output.

## Delivery safety

`POST /api/apply-patch` does not accept a patch body. It only accepts a session
and an already-pending approval ID, then delegates to the existing exact-plan
approval executor. Base SHA, diff hash, arguments, validation, risk policy, and
delivery controls remain authoritative. In particular, a dashboard click cannot
commit an arbitrary diff or execute a command copied from a workflow or log.

## Bounds and redaction

Logs, diffs, and editor source are bounded before model use. All three are
passed through the centralized `RedactionService` before stream output or the
repository proposal prompt. A partial or malformed diff is rendered as an empty
review state rather than throwing in the browser.

## Verification

The focused backend tests cover redaction, required SSE events, snake-case
payload compatibility, repository-plan mapping, and transport headers. The
client is verified with TypeScript compilation and a production Vite build.
