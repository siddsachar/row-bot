# Managed browser client integration handoff

`BrowserLiveControls.tsx` and
`src/row_bot/application/client_browser_controls.py` are isolated domain and
presentation owners. They do not become reachable until the following shared
integration is completed.

## Wire schema

Add strict v1 DTOs to `src/row_bot/api/v1/schemas.py` matching the exported
TypeScript shapes in `BrowserLiveControls.tsx`:

- `BrowserAvailability(state: Literal["available", "check_on_use",
"unavailable"], code: str | None)`.
- `BrowserControlSnapshot` with `schema_version=1`, `conversation_id`, a
  64-lowercase-hex `revision`, booleans `active` and `paused`, bounded `state`,
  `site`, `url`, and `last_action`, and the bounded availability map.
- `BrowserRevisionPayload(revision)` and
  `BrowserNavigatePayload(revision, url)`. Reject extras and cap the URL at
  8 KiB. The application service remains the navigation-policy authority.
- `BrowserReviewRequest(action, payload)`, where `action` is exactly one of
  `browser.navigate`, `browser.take_over`, `browser.check`, `browser.back`, and
  `browser.end`, with an action-to-payload validator.
- `BrowserReview` matching the service result plus a required bounded `nonce`.
- `BrowserReceipt` matching `_public_receipt` in the service.

Add the five command literals to `Command.type` and their payload types to
`COMMAND_PAYLOADS`. The wire command payload includes `nonce`; the service
strips it before canonical normalization and binds it through the action
digest. Regenerate the OpenAPI document and `frontend/src/api/generated.ts`;
do not hand-edit the generated client.

## Authenticated routes and policy

Add these routes beside the other conversation-scoped capability routes in
`src/row_bot/api/v1/routes.py`:

- `GET /conversations/{conversation_id}/browser`
- `POST /conversations/{conversation_id}/browser/review`
- `GET /conversations/{conversation_id}/browser/commands/{command_id}`
- `POST /conversations/{conversation_id}/browser/commands`

Each route must call `session(request)`, prove the conversation is still
visible to that session, and use `dispatch_validation(request, current)` before
and after every application call. Mutation routes use `lane="mutation"`.
Require `idempotency-key == command_id` and
`client_session_id == current.id` on execute.

The shared authority helper should calculate a server-side `authority_id` with
`admissions.keyed_digest` over the current session, conversation, and server
epoch. Its `validate_action` callback must freeze the conversation's current
profile and call `dispatch_refusal` with `source="core"`, `parent="browser"`,
the current tool allowlist, and the application-supplied canonical tool name:

- `browser.navigate` -> `browser_navigate`
- `browser.take_over` and `browser.end` -> `browser_snapshot`
- `browser.check` -> `browser_snapshot`
- `browser.back` -> `browser_back`

Map any refusal to `browser_action_denied` (HTTP 403). The review route calls
`review_browser_command`, then creates `nonce` with
`security.approval_nonce(current, conversation_id, revision, action_digest)`.
The execute route's `validate_review` must check the exact conversation and
revision, then call `security.consume_nonce` with that same tuple, the supplied
nonce, and command ID. It then calls `execute_browser_command`. Receipt reads
call `read_browser_receipt` with the current `owner_id` and `authority_id` and
must remain passive.

Add explicit error mappings without exposing exceptions or browser output:

- 422: `invalid_browser_command`, `invalid_browser_url`
- 403: `browser_action_denied`, `browser_navigation_denied`
- 404: `browser_receipt_unavailable`
- 409: `browser_revision_conflict`, `idempotency_mismatch`,
  `operation_pending`, `operation_uncertain`
- 409 or 503 according to the existing protocol convention:
  `browser_session_inactive`, `browser_window_unavailable`,
  `browser_action_failed`, `browser_outcome_uncertain`,
  `browser_status_unavailable`

The response must never include semantic page observations, screenshot bytes,
page titles, query values, credentials, local profile paths, Playwright
objects, or provider data.

## Frontend transport and authenticated lifetime

After generation, add transport/controller methods for the four routes. The
callbacks supplied to `BrowserLiveControls` should have the exact signatures
exported as `BrowserLiveControlsProps`. The execute adapter adds the standard
wire `client_session_id` and `expected_revision` fields and sends the command
ID as the idempotency key. Recovery calls the receipt endpoint for the same
original command; it must never create a replacement ID.

Create one `createBrowserControlSession(conversationId)` per conversation in
the authenticated controller/runtime owner. Retain it across panel unmounts,
layout changes, and reconnect rendering. Call `dispose()` on authentication
loss, controller disposal, or confirmed conversation deletion. Do not put its
review, nonce, URL, command, or receipt in local/session storage. Do not
automatically dispatch a retained review or uncertain command.

## Panel registration and automatic presentation

Register a bundled presentation-only panel kind such as `browser.live` in
`frontend/src/features/panels/model.ts` with `requiresResource: false`, empty
resource kinds, `compact: "tab"`, and the managed-browser capability advertised
by the handshake. Render `BrowserLiveControls` from the central panel renderer
in `frontend/src/features/shell/Workspace.tsx` (or its extracted shared
renderer). The renderer must use the active conversation ID and the retained
session owned above.

Emit or translate canonical managed-browser activity into a
conversation-scoped `panel.suggested` descriptor for `browser.live`, and use the
existing presentation reducer so first relevant activity opens/focuses the
panel while a user's dismissal remains respected. Do not add a separate
Developer or Designer chat route: the same conversation and panel instance
serve normal chat, Developer, and Designer. A direct user Browser entry point
may explicitly open the same descriptor.

The panel intentionally leaves click, type, scroll, tab identity, screenshot,
and external-browser attachment unavailable. Do not map those buttons to
generic command execution. Page click/type needs exact ephemeral target proof;
screenshots need a separately reviewed private-media contract; existing
external browsers remain under Computer Use.

## Shared verification after wiring

Add route tests proving passive GET does not instantiate Playwright, review has
no effect, nonce/action/profile checks run before admission, stale revisions
fail closed, retry reads only the original receipt, cross-session/cross-thread
receipts fail, queries and page content never reach JSON, and unexpected
post-dispatch failures stay partial. Add controller/panel integration tests for
automatic presentation, user dismissal, normal/Developer/Designer continuity,
logout disposal, reconnect without replay, and phone/desktop rendering. Then
run schema generation verification, the full frontend check, the prescribed
browser/accessibility suites, and the Phase 4 backend matrix.
