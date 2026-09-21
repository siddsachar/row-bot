# Unified workspace contributor cookbook

The default client at `/app-v2/` presents conversations, saved Designer artifacts
and registered Developer workspaces through shared application services.
NiceGUI remains mounted at `/` as the explicit `--legacy-ui` fallback. Selecting
either client does not migrate user data or replace the shared host.

The checked-in [v1 contracts](../../contracts/client-platform/v1/schema/openapi.json)
describe the public wire format. Python
[schemas](../../src/row_bot/api/v1/schemas.py) are its source;
[the generator](../../scripts/generate_client_platform_contracts.py) produces the
JSON schemas and TypeScript transport. Import generated types through
`frontend/src/api/types.ts`; do not maintain a second wire model in a component.

## Route, store and panel interfaces

`/app-v2/` is Home in the persistent application shell. Home reads bounded
recent conversations, saved designs and saved workspaces through the existing
controller. Recent conversations do not inherit the sidebar's group filter;
Search and the saved-resource picker retain access to the complete libraries.
Designer and Developer sections are resource entry points into the same
conversation route, not separate chat modes. `/tasks` presents saved workflow
summaries; Settings presents saved provider/model/tool and Knowledge/Documents
read views. Their mutation controls and Monitor still use the current app while
the remaining Phase 4 packets are implemented.

Sidebar Home is navigation only. Every New chat entry delegates to the single
`useNewChat` owner mounted by `Workspace`; it reserves a command identity before
creation and checks a retained receipt after an uncertain response. Returning
Home preserves the selected conversation's draft and producer. Late creation
completion cannot override a subsequent route, selection or session change.
The composer consumes the new conversation's focus request after its initial
read completes; it contains no second creation implementation.

`/app-v2/conversations/:conversationId` selects a durable conversation. Route state
identifies the conversation; a panel's open, close, move or focus operation only
changes presentation. Navigation never creates a generation or changes its write
target. Application and resource queries remain behind the authenticated
`/api/v1` interface and existing access middleware.

`ClientController` in `frontend/src/api/controller.ts` owns connection lifecycle,
conversation selection, transcript reconciliation, commands and drafts.
`RuntimeContext` supplies this controller and the platform adapter.
`useClientState()` observes the shared client state. Components should call the
controller rather than open independent event streams or copy domain state into
another store. Small local query pages and view state are appropriate inside a
panel; they are not another resource authority.

`GET /api/v1/conversations/:id/open` combines the existing metadata, bounded
latest history window, workspace and draft readers into one revision-fenced
response. Each component retains its own revision and continuation semantics;
this is not a new store or a global transaction over legacy writers. The client
accepts a combined response of at most 2 MiB, including the separately bounded
100-row/256-KiB history window and the existing draft. The handshake's
`limits.json_bytes` bounds ordinary inbound JSON request bodies.
The client
starts observation from its signed subscription snapshot after opening. A
visibility return or snapshot reset refreshes metadata and resources. Bounded
view, observation and acknowledgement rate limits are separate from command and
Stop/approval reserves, so navigation cannot exhaust those controls.

Panels register bundled renderers in `features/panels/model.ts`. A
`PanelDescriptor` contains `panel_kind`, `title`, and optional `resource_ref`,
`resource_kind`, `resource_revision`, `subresource_key` and
`required_capabilities`. These are client presentation descriptors, not accepted
v1 wire events or executable registrations. A descriptor cannot register code,
create a route, grant a capability or authorize a write.

`ResourcePanel` resolves the descriptor against the current conversation's
server-provided workspace view. It binds typed query callbacks to the
conversation and binding IDs, then passes resource ID, resource revision and
visibility to the renderer. Reuse this wrapper and the existing UI primitives.
Cancel obsolete requests and fence late responses by identity/revision when a
panel hides, switches resource or unmounts. An observer's cancellation must not
cancel a shared backend producer. Layout persistence stores view descriptors and
geometry, never source files, secrets or accepted execution state.

`features/panels/presentation.ts` centralizes automatic presentation. A committed
Designer binding opens `artifact.preview`; a workspace binding opens
`workspace.inspector`. Saved layout is restored first, and a conversation with
no saved view can discover its available resources in stable binding order.
Resource revisions update an existing instance. Close and collapse choices
survive ordinary updates, route revisits and reload; an explicit Open or new
setup action can reveal the panel again. Neither a visible panel nor assistant
text can select an execution write target.

Compact background discovery registers panels and announces their availability
without replacing the active composer or opening a sheet over typing. Explicit
resource actions use the compact panel view. Persisted layout version 2 scopes
geometry and presentation records by backend instance, conversation and width
class. Binding/resource identity distinguishes a replaced binding from a
revision update. Legacy global layout migration retains geometry and matching
resource preferences while excluding another conversation's resource panels.

## Bindings, revisions and explicit setup

A resource ID identifies the saved domain object. A binding ID identifies its
relationship to one conversation. `resource_ref` combines conversation and
binding IDs; it is not a filesystem path. A project workspace and its execution
worktree may have different IDs. Global Open preserves an existing legacy
conversation's execution binding rather than appending its project as another
execution candidate.

Keep these versions distinct:

| Version | Purpose |
| --- | --- |
| Conversation revision | Compare-and-set admission for conversation changes |
| Binding revision | Confirm the accepted relationship still exists |
| Resource revision | Confirm the saved object has not changed |
| Inspector snapshot revision | Pin changes and ledger pages to one collected view |
| Directory/file/diff revision | Prevent continuation across changed content |
| Draft revision | Compare-and-set the retained text and attachment metadata |

Send mutations through the generated command transport with a stable command
UUID, an idempotency key, client session identity and expected conversation
revision. New chat is `conversation.create`; it needs no configured model.
`resource.setup` uses `kind: artifact | workspace` and `intent: create | open |
add | repair | new_conversation`. Global setup uses `/api/v1/resources/commands`; setup against an
existing conversation uses its commands route.

For a design, read its typed setup options for Deck, Document, Landing, App
mockup or Storyboard and submit the supported template/canvas, name and brief.
Creation saves the artifact, associates a conversation and binds
it without invoking generation. For a workspace, request
`POST /api/v1/resources/folder-selection` from an authenticated local owner. The
host picker returns a short-lived session-scoped grant; submit that grant rather
than a renderer-supplied absolute path. Registration accepts an existing folder
and saves registry metadata. It performs no Git initialization, cloning,
worktree creation, dependency installation, process launch or source write.
Browser-only hosts without a native picker report capability unavailable.

New empty workspace is a distinct `empty_folder` setup action. Select an
authorized existing parent and a valid new folder name; opening setup remains
inert. The explicit action reserves and creates one directory and registry
record. Collisions never overwrite or merge existing files. Continuation after
a partial outcome revalidates the parent grant and original resource identity;
it does not create another directory or initialize Git.

Global Open resumes the saved origin, or the supported legacy association. A
missing origin requires explicit repair with its expected origin ID. Add binds
a saved object to the current conversation without changing its canonical
origin. Unbinding removes a relationship; closing a panel changes only layout.
Neither operation deletes the saved object.

Global saved-workspace setup also offers **New conversation with this workspace**.
Its `new_conversation` intent requires an existing workspace ID and expected
resource revision on the global resources route. It creates a separate
conversation and binds that workspace without changing its original association,
including an absent or missing origin. Ordinary Open still follows the original
history or requires explicit repair. The setup command reserves the separate
conversation's deterministic identity; receipt recovery and explicit continuation
reuse it after partial failure. This action neither creates a workspace nor
allocates an execution worktree.

`SetupSessions` in `features/shell/setup-state.ts` retains bounded presentation
drafts in session storage, scoped to the backend instance and initiating
conversation (or global entry point). It stores inputs and command/receipt
identities before dispatch. Reopening or reloading checks receipts; it does not
replay a mutation. Folder grants remain volatile and expire on rehandshake.
Completed setup offers an explicit **Start another resource** action; unresolved
submission retains its identity until reconciliation. This presentation store
does not own resources or authentication.

Home starters initialize this same setup owner and never overwrite an unresolved
setup or generation receipt. A saved Home resource is checked against current
library identity, availability and revision before enabling Open. Setup captures
the initiating route, selection version and session for automatic presentation;
a late result remains recoverable without changing the current view. The
receipt's explicit Open conversation action remains available afterward.

## Readiness, generation and partial recovery

The conversation workspace view returns action readiness separately from model,
profile, runtime and approval controls. Setup and browsing can work while Send
or Generate reports missing model configuration. Setup must never silently
generate content. An explicit submit supplies the selected provider-qualified
model and accepted write targets, including binding and resource revisions.
The server revalidates targets before producer dispatch; the visible panel is
not a fallback target. Preserve existing approvals, sandbox policy and write
locks when adding a domain adapter.

The rounded composer keeps the current model, supported Thinking and approval
mode visible in compact menus. Runtime and profile remain distinct controls in
the secondary menu. Thinking choices come from the workspace's exact-model
`reasoning` view, including provider default, supported efforts or modes, and
budget bounds. An unsupported or mismatched model has no active Thinking
selector. `conversation.controls` carries an explicit `reasoning` selection
with its qualified model reference and capability revision. The server validates
and stores it through the retained per-model reasoning owner; changing a label
or browser storage alone is not a selection. Switching models must not forward
the previous model's reasoning control. Generation admission and queued dispatch
retain the accepted reasoning snapshot alongside model, profile and targets.

Context usage is a saved-response snapshot with its model identity, estimated
input, usable capacity and last provider-confirmed input when available. Display
unknown capacity and stale snapshots explicitly; the visible transcript window
is not a measure of the full request context.

Admission freezes the selected profile's instructions, skill policy and tool
restriction from the existing profile registry. Queued dispatch retains that
snapshot even if the profile changes later. An explicit empty profile remains
empty; existing explicit tool restrictions are intersected with the profile
restriction and never widened. Missing or disabled newly selected profiles
fail admission before a user message or producer is created.

The optional first-draft checkbox leads to a separate review after creation.
**Review generation controls** reads the created conversation's current model,
profile, runtime, approval mode and resource target. **Generate first draft** is
the explicit provider action and has its own retained command receipt. Opening
settings, closing setup or a generation failure preserves the created resource
and setup draft.

Commands can return `partial` with a resource ID, conversation ID when confirmed,
resource revision and `confirmed_stages`. Preserve that receipt. Read
`GET /api/v1/commands/{command_id}` after an uncertain response; do not create
another object to hide the failure. `resource.continue` explicitly names the
original setup command and current expected resource revision, plus expected
origin when repair still requires it. Continue on the receipt's conversation
when present, otherwise the global resources route. Completed stages are reused.

Receipts recover a registered object or exact saved origin across a process-loss
boundary. Blank chat reserves a deterministic identity before creation and only
reports recovered completion after verifying saved metadata. A reserved identity
without a saved object remains uncertain. Receipt reads never create resources
or invoke a provider.

## Bounded reads, search, drafts and steering

Workspace queries use
`/api/v1/conversations/{conversation}/workspaces/{binding}/...`. The server checks
the binding before and after reads. Inspector collection reuses the Developer
snapshot scheduler and coalescing. `changes`, `change-sets` and per-set files
provide continuation cursors for the complete result. Directory queries inspect
only the requested directory; file and diff queries return bounded text with
byte offsets and revisions. Follow continuations rather than treating the first
page as the full result. Reject traversal, `.git`, symlinks and reparse points;
render preview text literally. Custom Git filters or filesystem-monitor hooks
make read-only inspection unavailable rather than executing those hooks.
Detected checks say **Not run** until there is actual execution evidence.

Artifact preview uses the conversation's artifact binding, stable page IDs and
resource revisions. Use the existing sanitized preview service and sandboxed
renderer. Do not inject saved HTML into the application document or introduce a
second artifact cache.

All five Designer modes use this preview. Landing, App mockup and Storyboard
permit only the exact packaged declarative interaction runtime in an opaque
sandbox; authored scripts stay blocked. Zoom changes presentation without
reloading the iframe. A current resource revision updates the existing panel
instance; unavailable or replaced bindings still remove its content.

`MediaPreview` supplies one authenticated retrieval/retry/object-URL owner for
generated images, native audio/video presentation and download fallback. Only
explicit MIME types are previewed; HTML/PDF/SVG and MIME disagreements download
without active embedding. Native players do not autoplay and release playback
on removal. The present attachment transport exposes PNG/JPEG/MP4/PDF and opaque
downloads; audio transport/capture parity remains a separate implementation
packet. Do not infer captions or accessibility from the existence of controls.

Saved task/model/tool/knowledge/document queries apply server-side filtering
before bounded paging. Follow every cursor for complete discovery, but retain
at most 200 rendered entries in these client lists. Reload returns to the first
page. A changed snapshot disables continuation until explicit reload. Saved
statuses and unknown runtime/index readiness must not be presented as a live
capability check. These reads do not start models, discover external tools,
ingest documents or run workflows.

Conversation search and history use bounded server queries and continuation
cursors. Search is not limited to the loaded sidebar or transcript window.
Selecting a hit opens its conversation and message context; lazy content reads
provide large message content without expanding the entire history in memory.
Restart a query when a cursor expires or its revision no longer matches.
`GET /api/v1/conversations/{conversation}/text/{message_id}` pages public text
through the existing checkpoint reader. Its `LazyContent` response carries
base64 UTF-8 with `media_type: text/plain` and a continuation pinned to the
message and checkpoint. Decode and display each bounded page as text; never
parse it as checkpoint JSON or expose non-text blocks and private metadata.
Library grouping (`all`, `pinned`, `artifact`, `workspace`) is server-side and
continues beyond the loaded sidebar page.

`GET` and `PUT /api/v1/conversations/{conversation}/draft` use the retained thread
draft owner shared with NiceGUI. Save text and conversation-owned attachment
references with `expected_revision`. An identical retry is idempotent; a stale
different edit returns `draft_revision_conflict`. Keep unsaved local text visible
and offer recovery rather than silently overwriting another client's draft.
Foreign attachment references are denied before attachment inspection, and
deletion or session revocation must fence writes.
Returning to a conversation refreshes a clean draft from the server. A dirty
local draft stays visible; resolving a conflict requires reviewing the saved
draft revision and explicitly keeping local text or adopting the saved draft.
The client retains the selected draft and at most 31 other clean drafts;
unsaved, conflicted and in-flight drafts remain protected. Autosaves, ordinary
commands and upload setup/completion share client pacing below the unchanged
server mutation budget. Waiting requests are bounded and cancellable, retain
their captured payload, and never replay automatically. Stop, approval and
upload cancellation retain their separate control capacity.

`conversation.steer` accepts a stable steering ID and text. The parent's steering
query and `steering.queued`/`steering.consumed` events distinguish queued input
from confirmed consumption. Queue display must reflect these durable states;
acknowledging a request is not proof the agent has consumed it. Preserve current
voice cancellation and parent-generation ownership when handing off voice input.

For an ordinary generation, the same steering command stages pending user input
in the existing checkpoint owner's queue namespace. The paged `queue` query
returns stable submission IDs, item revisions, and `queued`, `dispatching`,
`consumed`, `cancelled` or `paused` states. `conversation.queue.edit`,
`conversation.queue.remove` and `conversation.queue.dispatch` require the
accepted item revision. `queue.changed` prompts a fresh read. Dispatch uses the
captured model, profile, approval and resource targets and revalidates them;
staged messages are not future input to the currently running graph. Stop or
process loss preserves pending inputs as paused. A receipt or dispatch alone
does not prove consumption.

The bounded `delegated` query and `delegated/{run_id}` detail query read existing
agent-run relationships. They expose only public task identity, name, status,
summary and available parent/child conversation IDs. The shared detail overlay
offers **Back to parent** and an explicit child-history link when that retained
conversation still exists. Child conversations offer their actual parent link.
Navigation uses the same conversation route and controller; it never creates a
second composer or transfers resource write authority. Deleted/deleting parents
and foreign run references are denied; private child prompts, context, errors,
result payloads and workspace paths are excluded.

## Validation and extension boundaries

Fresh devices use dark appearance, the blue accent and compact desktop density.
Explicit saved System/Light/Dark, accent, density and transparency preferences
remain authoritative. Shared semantic tokens and bundled Row-Bot artwork supply
the NiceGUI visual family; coarse-pointer targets and readable transcript text
remain accessibility requirements. No external font or icon fetch is required.

Add deterministic tests under the existing domain and client-platform test
owners; use isolated data, fake providers and synthetic folders. Update
`tests/helpers/source_test_map.py` for cross-subsystem changes. Generated
contracts and frontend changes select the matrix's explicit Node lane. The
browser fixture runner is `tests/browser/client_workspace/run_browser.py` and
requires its documented local browser/toolchain setup; Python ownership tests
do not substitute for browser checks.

Keep NiceGUI compatibility checks, generated-contract checks, client unit tests,
browser accessibility/lifecycle checks and the appropriate Python matrix lanes
in the handoff. Report platform or manual checks that remain unperformed.

This pilot does not supply a new native bridge or browser filesystem authority,
replace Developer execution controls, migrate later application surfaces, or
complete a desktop cutover. New artifact types, editor/write operations, native
hosting and broader feature migrations require their own scoped contracts and
validation before becoming available.

Browser dictation uses the existing voice coordinator. Starting reserves a
conversation- and session-bound lease before browser microphone permission;
finishing sends one bounded utterance and appends the result to the current
draft without submitting it. Cancellation, navigation, disconnect and shutdown
stop browser capture and revoke the exact lease. An admitted transcription or
model-readiness operation remains busy until its worker actually returns.
Dictation requires HTTPS or direct loopback and local transcription readiness.
Talk, realtime voice and Buddy parity are separate Phase 4 work.

MCP wrappers capture the current server configuration and tool descriptor at
binding. Execution rechecks that authority after waiting for the stateful server
lock; changed approval, schema, effects or transport require a fresh binding.
Known read operations retain their existing default behavior. Unclassified
effects require explicit enablement and approval; enabling a tool alone does
not remove that approval requirement. Reviewed browser interaction remains
distinct from read-only operations. Annotations and process separation do not
constitute an operating-system sandbox.
