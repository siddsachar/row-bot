# Row-Bot Landing Page Overhaul

Status: implementation-ready plan

Initial product client: NiceGUI

Future product client: React, using the same story state and capture contract

## Outcome

Replace the current text-heavy homepage with a fast, cinematic product story that
proves three things within the first viewport:

1. Row-Bot can research, create, automate, and ship real work.
2. It can use local and hosted models deliberately, without hiding the boundary.
3. The user's conversations, knowledge, workflows, and projects remain under the
   user's control.

The page must feel alive, but the animation must support the message rather than
compete with it. Product imagery must come from a real Row-Bot profile, real UI
interactions, and real model outputs. Public assets must still contain only
public-safe content.

## Decisions

### Use both Qwen and GPT-5.6 Sol

The canonical story run uses an explicit mixed-model strategy:

| Role | Model | Why it appears in the story |
| --- | --- | --- |
| Local research, extraction, recurring workflow, and background history | `model:ollama:qwen3.8:27b` | Makes the local-first claim visible and authentic. |
| Final synthesis, creative direction, and one demanding review | `model:codex:gpt-5.6-sol` | Demonstrates deliberate access to a frontier hosted model when the user chooses it. |

The local model is the default visual protagonist. Sol is used at one clear
handoff point, never presented as local, and never used as an invisible fallback.
Each scene shows the provider/model label when it adds meaning.

The model references are configurable CLI inputs rather than irreversible values
inside the capture code. The canonical run records the exact provider-qualified
model references and reasoning settings in an internal provenance receipt.

### Separate generation from capture

Model calls happen only during a bounded `prepare` phase. The resulting threads,
knowledge, workflow runs, and design artifacts are persisted in the real Row-Bot
profile. Subsequent screenshot, video, site development, and React recapture runs
reopen those settled states without calling a model.

This makes the final assets real while keeping capture repeatable, affordable,
and independent of transient provider availability.

### Use the real Buddy as the hero character

Row-Bot already ships first-party Buddy motion packs with `idle`, `thinking`,
`working`, `approval`, `success`, and `error` loops. Use the prepackaged Orbit
robot as the canonical landing-page Buddy. Motion review found steadier
frame-to-frame movement than the Pixel gaming-console pack across most states.
Add a short visual crossfade at the loop boundary, where Pixel's generated clips
currently have the stronger seam. This keeps the more polished robot silhouette
without accepting a visible restart.

Buddy sits in a lightweight animated environment and hands attention to the
product UI as the visitor scrolls. The app remains the main proof.

### Keep the landing implementation framework-light

Use semantic HTML, CSS, SVG/canvas, and a small JavaScript scene controller.
Avoid a WebGL or animation-framework dependency for the first version.

The Buddy state controller and scene contract should be framework-neutral. A
future React page can embed the same custom element or wrap the same controller
instead of recreating the interaction logic.

## Narrative and Copy Architecture

The page moves through one story rather than listing every feature.

### 1. Hero: immediate value

Primary statement:

> Think it. Build it. Run it. Keep it yours.

Support line:

> One local-first AI workbench for agents, knowledge, automations, code, and
> design. Use local or hosted models. Keep the system of record with you.

Primary CTA: `Download Row-Bot`

Secondary CTA: `Watch it work`

Trust strip:

> Free. Open source. No Row-Bot account. Your models, your data, your machine.

The hero shows Buddy in `idle`, a live but restrained sovereignty field, and a
real app frame beginning to resolve behind it.

### 2. Pinned product story

One sticky product frame changes as four short statements enter the viewport:

| Beat | Statement | Real product state | Buddy state |
| --- | --- | --- | --- |
| Research | Ask across documents, tools, and the web. | Local Qwen research thread, sources, and tool activity | `thinking` |
| Create | Turn the answer into work. | Sol-assisted synthesis and a real Designer artifact | `working` |
| Automate | Make it happen again. | Real workflow definition and completed run | `working` |
| Control | Choose the model. Review the action. Keep the memory. | Model picker, approval, knowledge, and activity | `approval`, then `success` |

Each beat is one headline, one short support sentence, and one optional proof
label. No body paragraph should carry the feature inventory.

### 3. Capability constellation

A compact interactive cluster makes the breadth visible without long prose:

- Agents
- Models
- Knowledge
- Workflows
- Tools
- Developer
- Designer
- Channels
- Voice

Hover, focus, or tap highlights the relevant region in the app frame. It does not
navigate unexpectedly. The default state remains understandable without hover.

### 4. Real demonstrations

Keep the existing three demonstrations. Present the featured demo at full width
and the two supporting demos beneath it. Continue using privacy-enhanced YouTube
facades so no iframe or third-party request is made until the visitor activates a
video.

### 5. Sovereignty statement

Use one strong statement rather than a comparison essay:

> The power of an AI platform. The ownership of local software.

Support it with four concise proofs:

- Local is a real model route, not a privacy mode.
- Hosted providers receive only the requests the user sends to them.
- Consequential actions remain approval-gated.
- Conversations, memory, projects, and run history stay with the Row-Bot host.

Link to the full architecture and comparison pages for detail.

### 6. Final CTA

> Your next serious AI workspace can belong to you.

Repeat the detected-platform download action, with GitHub and documentation as
secondary exits.

## Motion System

### Buddy hero behavior

Buddy is a real interactive control with a text equivalent, not a decorative
autoplay layer.

- Idle: gentle loop and low-intensity halo.
- Pointer: a maximum three-degree parallax tilt; the surrounding field follows,
  not the raster content itself.
- Click, Enter, or Space: advance to the next capability beat and announce the
  selected beat through an accessible status region.
- Scroll: map story progress to Buddy states and the current product frame.
- Approval beat: Buddy surfaces the approval state instead of celebrating an
  unreviewed action.
- Completion: one success loop, then return to idle.
- Hidden or offscreen: pause video/canvas work.
- Reduced motion: show the Buddy poster and instant state changes with no loops,
  parallax, or smooth scrolling.

The current motion pack can be used directly for the first implementation. Copy
only the approved public assets into the landing media directory; do not serve
them from a user data directory.

### Sovereignty field

The background is a deterministic, low-density knowledge field rather than a
generic particle effect.

- A seeded set of nodes forms a local boundary around Buddy.
- Lines connect Research, Create, Automate, and Control as the story advances.
- Local scenes keep motion within the boundary.
- The hosted-model scene shows one deliberate, labelled outward route and return.
  This makes the provider boundary honest rather than implying that every model
  runs locally.
- Click/tap emits one short pulse through the active capability, not an endless
  fireworks effect.
- The field uses canvas or inline SVG with a strict node cap, pauses when hidden,
  and degrades to a static background.

### Real app animation

Use three levels of product media:

1. Crisp WebP stills for the primary story states.
2. CSS transitions, masks, highlights, and controlled pan/zoom between those
   stills for most of the scroll story.
3. Short real UI clips only where motion proves behavior: opening the model
   picker, expanding an activity/tool trace, moving through a knowledge graph,
   or showing an approval transition.

Do not make an animated GIF. It is larger, less controllable, and less accessible
than a poster-backed muted video or a still-driven DOM sequence.

All clips are muted, `playsinline`, poster-backed, lazy-started when visible, and
non-essential. The still is a complete fallback.

## The Real, Lived-In Story Workspace

The automation uses the normal Row-Bot profile only after an explicit live
capture opt-in. It creates a public-safe marketing workspace inside that profile
through the real UI and APIs. It does not copy the user's unrelated history into
public assets.

### Source material

Use only material that is already safe to publish:

- Row-Bot public website and public documentation.
- Public repository documentation from a clean committed revision.
- A small allowlist of public web sources recorded in the story manifest.

Do not ingest uncommitted repository content, credentials, private notes, real
messages, or unrelated conversations.

### Canonical task

> Research how local-first AI changes ownership and control of knowledge work.
> Use Row-Bot's public documentation and the approved public sources to create a
> concise campaign brief, a visual launch concept, and a recurring weekly
> intelligence workflow. Keep file writes and external delivery behind approval.

### Real records to create

Create these through normal Row-Bot surfaces so the profile looks inhabited but
the content remains publishable:

1. `Local-first AI landscape` — Qwen research thread with real source/tool
   activity.
2. `What should stay local?` — Qwen document synthesis and knowledge extraction.
3. `Campaign narrative` — Sol synthesis of the local research.
4. `Landing visual direction` — real Designer project and editable output.
5. `Weekly sovereignty watch` — disabled-delivery workflow, run successfully
   once with Qwen.
6. `Homepage implementation backlog` — a real goal with bounded agent activity.
7. `Release readiness` — public-safe checklist thread for recent-history depth.

The newest public-safe records should occupy the visible recent list before
capture. Unrelated profile content must be outside the crop, masked, or excluded
by a public-safe filter. A human review remains mandatory before publication even
when the machine is explicitly approved for capture.

### Call budget

The canonical preparation run has a hard ceiling of six generation attempts:

| Attempt | Model | Purpose |
| --- | --- | --- |
| 1 | Qwen | Research and source digest |
| 2 | Qwen | Document/knowledge synthesis |
| 3 | Sol | Campaign synthesis |
| 4 | Sol | Designer direction or critique |
| 5 | Qwen | Workflow execution |
| 6 | Reserved | One explicit recovery attempt after a known safe failure |

An uncertain provider outcome is not retried automatically. The run records the
conversation and operation identifiers and stops for review. Local-only preview
runs are allowed but cannot silently become the canonical mixed-model capture.

No channel delivery, real recipient, destructive tool, or unreviewed MCP server
is part of the story.

## Capture Architecture

### Directory layout

```text
scripts/marketing/
  capture_landing_story.py       # CLI and orchestration
  landing_story.yml              # client-neutral scenes and public prompts
  capture_contract.py            # typed manifest and validation
  media_pipeline.py              # crop, poster, WebP, and clip processing
  clients/
    base.py                       # semantic client adapter contract
    nicegui.py                    # first implementation
    react.py                      # added when the React client is canonical

docs-build/marketing-capture/    # ignored raw runs and review reports
  <run-id>/
    run.json
    raw/
    processed/
    review/

docs/media/landing-story/        # reviewed public assets only
tests/marketing/                 # manifest, adapter, media, and safety tests
```

Do not extend the deterministic public-documentation screenshot job to make live
provider calls. Reuse its hardened helpers where appropriate, but keep the live
marketing workflow separate and explicitly authorized.

### Client-neutral scene contract

The story manifest describes intent, not NiceGUI selectors. Example:

```yaml
story:
  id: sovereignty-workbench-v1
  prompt_version: 1
  max_generation_attempts: 6
models:
  local: model:ollama:qwen3.8:27b
  frontier: model:codex:gpt-5.6-sol
scenes:
  - id: research-local
    surface: conversation
    record: local-first-ai-landscape
    state: settled
    framing: app-window
    outputs: [webp]
  - id: choose-model
    surface: model-picker
    record: campaign-narrative
    state: open
    framing: detail
    outputs: [webp, webm]
  - id: knowledge-control
    surface: knowledge
    state: populated
    framing: app-window
    outputs: [webp]
```

Each client adapter implements semantic actions such as:

- `open_conversation(record_key)`
- `open_home_surface(name)`
- `open_model_picker()`
- `open_activity()`
- `open_workflow(record_key)`
- `open_designer_project(record_key)`
- `await_settled_state(scene)`

Only the adapter owns routes, DOM selectors, and client-specific waits. The same
persisted story records can therefore be reopened in NiceGUI now and React later.

### Run receipt

`run.json` is internal capture provenance, not a public application asset. It
records:

- story and prompt version;
- Row-Bot version and Git commit;
- client adapter and viewport;
- provider-qualified model references and reasoning settings;
- public source URLs or checked-in source identities;
- created thread, workflow, goal, and artifact identifiers;
- generation attempt count and terminal status;
- capture timestamps, dimensions, and hashes;
- masks, crops, and reviewer decision.

It never stores credentials, tokens, private paths, unrelated profile data, or
raw hidden UI text.

### CLI workflow

The target interface is:

```powershell
# Read-only preflight: profile, providers, models, browser runtime, ports, media.
uv run python scripts/marketing/capture_landing_story.py preflight --client nicegui

# Explicitly create/reuse the public-safe story in the normal profile.
uv run python scripts/marketing/capture_landing_story.py prepare --client nicegui --authorize-real-profile

# Reopen settled states and capture them with all generation/network disabled.
uv run python scripts/marketing/capture_landing_story.py capture --client nicegui --run-id <run-id>

# Build WebP posters and optional WebM micro-clips, then create an HTML contact sheet.
uv run python scripts/marketing/capture_landing_story.py process --run-id <run-id>

# Validate content contracts, hashes, dimensions, media playback, and site scenes.
uv run python scripts/marketing/capture_landing_story.py validate --run-id <run-id>

# Copy only a reviewed run into public assets.
uv run python scripts/marketing/capture_landing_story.py publish --run-id <run-id> --approve-reviewed-run
```

Later React recapture:

```powershell
uv run python scripts/marketing/capture_landing_story.py capture --client react --run-id <run-id>
```

This last command must not need new model calls. It reuses the same stored Row-Bot
records and changes only the client adapter.

### Capture behavior

The automation must:

1. Resolve the exact normal Row-Bot profile and require the explicit authorization
   flag for it.
2. Verify that no other Row-Bot process is mutating the profile.
3. Launch a loopback-only application process that it owns.
4. Use the configured provider records without logging their secrets.
5. Drive the real UI for story preparation; do not insert finished answers
   directly into a database.
6. Tag every created record with the story ID and persist its identifier in the
   run receipt.
7. Bound all model calls and public-network tools.
8. Disable background autostart, channels, unrelated MCP transports, and delivery.
9. Wait for semantic settled states rather than fixed sleeps.
10. Capture raw lossless frames before crop or compression.
11. Stop only the processes it started and leave unrelated profile records alone.
12. Produce a review contact sheet before any asset can enter `docs/`.

## Public Asset Set

The first release should target a small, strong set rather than a screenshot of
every feature.

| Asset | Content | Format |
| --- | --- | --- |
| `hero-app` | Settled main conversation with local model and Activity visible | WebP poster |
| `research-local` | Qwen research result, sources, and tool trace | WebP + optional short WebM |
| `synthesis-sol` | Explicit hosted-model handoff and campaign brief | WebP |
| `knowledge-control` | Populated knowledge graph from public-safe material | WebP + optional short WebM |
| `designer-output` | Real editable visual artifact | WebP |
| `workflow-repeat` | Workflow definition and successful run history | WebP |
| `approval-boundary` | Real approval UI before a reversible write | WebP + optional short WebM |
| `model-choice` | Local and hosted provider-qualified choices | WebP |
| `mobile-shell` | One representative mobile-width state | WebP |

Raw capture can be high resolution. Publish responsive derivatives sized for the
actual rendered slots rather than shipping the raw 4K frames to every visitor.

## Landing Page Implementation Structure

Suggested public files:

```text
docs/index.html
docs/site.css
docs/site.js
docs/landing-story.css
docs/landing-story.js
docs/media/landing-story/
  buddy/
  screenshots/
  clips/
  manifest.json
```

`landing-story.js` exposes a small deterministic API for tests and capture:

```text
RowBotLandingStory.setScene(sceneId)
RowBotLandingStory.getState()
RowBotLandingStory.freezeMotion()
```

Query parameters such as `?landing_scene=research-local&motion=freeze` allow the
browser tests and marketing capture to reproduce any page state without scrolling
or waiting for an animation timeline.

## Performance and Accessibility Budgets

- The hero is useful before Buddy video or app media loads.
- No autoplay audio and no sound effect on click.
- Critical new JavaScript target: at most 45 KB compressed.
- Critical new CSS target: at most 35 KB compressed.
- Critical hero imagery target: at most 500 KB before interaction.
- Each lazy micro-clip target: at most 1.5 MB, with no more than one playing.
- Desktop LCP target: under 2.5 seconds on a representative throttled run.
- Avoid layout shift by declaring every media aspect ratio.
- Keyboard behavior mirrors click behavior.
- Current scene and controls have accessible names and visible focus.
- Text remains readable with JavaScript disabled.
- `prefers-reduced-motion`, `Save-Data`, page visibility, and offscreen state stop
  non-essential animation.
- Mobile uses a shorter, non-pinned sequence if sticky storytelling reduces
  usability.

## Testing and Review

### Automated tests

Add focused coverage for:

- manifest schema, unique scene IDs, and public asset references;
- exact-profile authorization and quiescence checks;
- generation budget and uncertain-outcome behavior;
- client adapter contract shared by NiceGUI and React;
- output containment under the run directory;
- mask/crop validation and metadata stripping;
- publish refusal without an approved review receipt;
- landing scene controller, keyboard input, and reduced-motion behavior;
- no YouTube request before activation;
- no model or network call during `capture`, `process`, `validate`, or page tests;
- image dimensions, clip playback, poster fallback, and missing-asset failure;
- existing download, analytics-consent, navigation, and docs-link behavior.

Use the existing documentation/browser test conventions and then run the changed
source test matrix. Keep live preparation under the opt-in `live_provider`/`e2e`
lane; deterministic capture and landing tests must not depend on a live model.

### Human review gate

The generated contact sheet must make it easy to inspect every asset at full
resolution. Review for:

- private or unrelated profile content;
- local paths, usernames, tokens, account identifiers, and notification content;
- misleading model labels or claims;
- accidental incomplete/streaming states;
- unreadable crop, cursor, tooltip, or modal positioning;
- visual consistency between screenshots and the live app;
- compression artifacts and motion-loop seams.

## Implementation Phases

### Phase 0: baseline and content lock

- Archive current desktop/mobile screenshots and basic performance numbers.
- Approve the six-section narrative and final statement copy.
- Approve the public source allowlist and canonical task.
- Identify any in-progress UI work that would invalidate selectors or captures.

Exit gate: copy and story scene IDs are stable enough to automate.

### Phase 1: live story preparation

- Implement preflight and exact-profile authorization.
- Add the client-neutral manifest and NiceGUI semantic adapter.
- Create the public-safe threads, knowledge, workflow, goal, and Designer artifact
  through the real app.
- Record a successful bounded mixed-model run.

Exit gate: one reviewed `run.json` can reopen every required state without a model
call.

### Phase 2: deterministic capture and processing

- Capture all NiceGUI scenes at desktop and mobile dimensions.
- Add optional interaction recording for the few proof-worthy transitions.
- Generate WebP derivatives, posters, WebM clips, hashes, and the contact sheet.
- Review and publish the approved media set.

Exit gate: public assets reproduce the real story and contain no unrelated data.

### Phase 3: page skeleton and copy

- Reduce the homepage to the six narrative sections.
- Implement the final CTA hierarchy and concise statements.
- Preserve demos, navigation, platform detection, privacy-enhanced embeds,
  structured data, and existing accessibility behavior.
- Add still product states before adding motion.

Exit gate: the static/no-motion page already communicates the complete value.

### Phase 4: Buddy and interactive story

- Integrate the real Buddy state loops.
- Add the sovereignty field and framework-neutral scene controller.
- Connect scroll, click, keyboard, and deterministic test controls.
- Add app-frame transitions and only the approved micro-clips.

Exit gate: animation is smooth, meaningful, pausable, and non-essential.

### Phase 5: responsive polish and validation

- Tune mobile flow, crops, fonts, focus, and reduced-motion behavior.
- Run deterministic landing, docs, and capture tests.
- Run a throttled performance pass and enforce the media budgets.
- Complete the full-resolution human review and final content check.

Exit gate: download path, demos, and core value are clear on desktop and mobile;
no capture or media process needs a live provider after preparation.

### Phase 6: React client migration

- Implement only `clients/react.py` against the same semantic adapter contract.
- Point it at the existing run receipt and persisted story records.
- Recapture the same scene IDs and compare them side by side with NiceGUI.
- Update public assets after review; do not rewrite the narrative or preparation
  workflow merely because the product client changed.

Exit gate: the same one-command capture and review process works for React with no
new generation step.

## Definition of Done

- The first viewport communicates power, local-first ownership, and one primary
  action in under ten seconds.
- The page uses statements and proof, not feature paragraphs.
- The real application is visible in the hero and central story.
- Qwen and Sol are both used honestly and visibly, with no suggestion that Sol is
  local.
- Buddy responds to scroll, click/tap, and keyboard using real Row-Bot motion
  states.
- The page remains complete with motion disabled or media unavailable.
- Existing demos remain accessible without third-party loading before consent or
  activation.
- Every public screenshot or clip traces back to a reviewed real story run.
- Re-capturing settled NiceGUI states requires no model call.
- A future React capture requires a new adapter, not a new story or manual media
  process.
- Tests cover the safety boundary, deterministic scenes, accessibility behavior,
  media integrity, and existing landing-page contracts.
