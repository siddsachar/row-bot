# Landing-page refinement handoff

Status: planning only. Implement every unchecked item in this isolated worktree.

## Workspace and boundaries

- Worktree: `C:\Users\v_2si\.codex\worktrees\landing-page-refinement\row-bot`
- Branch: `feat/landing-page-refinement`
- Starting point: merged `origin/main` at `a593c99077432d7c518b851a7ef48179292c3ad3`.
- Do not edit, switch, stash, clean, or otherwise change `D:\Code\row-bot` or the concurrent React work. Do not push, deploy, or open a PR unless requested.
- Read `AGENTS.md` and `docs-content/LANDING_PAGE_OVERHAUL_PLAN.md` before editing. Keep the existing NiceGUI capture boundary, real-app provenance, download routing, three demos, accessibility and reduced-motion behavior.
- This refinement uses existing reviewed media. No live provider, image-generation, or video-generation calls are needed or authorized by this plan. If a new source asset becomes necessary, ask first.

## Visual references and acceptance checklist

The attached desktop references are, in order, `codex-clipboard-c588f30f-5107-48cb-8005-13f3a34ad794.png`, `codex-clipboard-f64dd6b0-13d1-4252-9467-3a4640e3f559.png`, `codex-clipboard-d975a976-4040-43e5-96b5-0d7c0ed1e695.png`, `codex-clipboard-1858d4b3-7747-46b7-92a5-f3a19ae8fd40.png`, `codex-clipboard-abc9461c-f8cb-42d2-be69-dbba017e85e2.png`, and `codex-clipboard-93f66ee1-4b4d-427f-b80a-c4cc3d942996.png` in `C:\Users\v_2si\AppData\Local\Temp\`.

The attached mobile references are `1000077649.jpg`, `1000077651.jpg`, `1000077653.jpg`, `1000077655.jpg`, `1000077657.jpg`, `1000077659.jpg`, and `1000077661.jpg` in `C:\Users\v_2si\Downloads\`.

All 13 references are also preserved locally at `C:\Users\v_2si\.codex\worktrees\landing-page-refinement\row-bot\.tmp\landing-page-refinement\references\`. This directory is ignored by Git; the screenshots are for visual QA, not public assets.

### Desktop

- [x] D1: Hero Buddy is truly animated with the existing Create/working motion; keep the immediate still/poster for LCP and no-JavaScript/reduced-motion fallback. Keep the large introductory size and title legibility.
- [x] D2: Scale only the four in-story Buddy placements to roughly 0.85-0.9 of their present size. Do not shrink the hero Buddy or sovereignty illustration by accident.
- [x] D3: Tighten the stage-to-demos, demos-to-sovereignty, sovereignty-to-FAQ, and FAQ-to-install/footer vertical gaps shown in screenshots 2, 3, 5, and 6. Preserve intentional breathing room, but remove empty bands that consume most of a viewport.
- [x] D4: Replace the current two-column, one-point `ownership-compare` with the full six-topic comparison table previously in the landing page. Put it inside an accessible `<details>` collapsed by default, with an obvious summary and the honest hosted-provider caveat. Keep the `#comparison` anchor and footer link working.

### Mobile

- [x] M1: Show a small Create/working Buddy animation beside or subtly behind the hero title without covering text or CTAs. Reuse the reviewed motion and avoid decoding two simultaneously playing Buddy videos.
- [x] M2: Shrink in-story Buddy to about 0.85-0.9 of current size and move scene placements inward so no pose is cropped at the right edge. The app stage itself must remain fully visible.
- [x] M3: Remove the large empty band below the stage and other inter-section gaps, including the space before/after the demos, in the sovereignty section, and before the footer.
- [x] M4: Show all three demo thumbnails in compact, consistently sized cards. Preserve click-to-load, privacy-enhanced YouTube facades, keyboard activation, and poster fallbacks. The current mobile CSS explicitly hides thumbnails 2 and 3.
- [x] M5: Make the four sovereignty proof items denser and visually coherent, without cramped text or small touch targets. Ensure the sticky navigation does not obscure section headings while scrolling.
- [x] M6: Use the same collapsed-by-default comparison table on mobile, with an obvious expand affordance. Preserve semantic table relationships and make narrow-width overflow discoverable and usable.
- [x] M7: Eliminate the detached Buddy and tall blank area at the end of sovereignty. Integrate a smaller Buddy beside the heading, into the artwork, or into a restrained background composition.
- [x] M8: Give `Direct desktop downloads` a clear disclosure affordance (chevron/plus and open state), keyboard focus styling, and an accessible label. Keep existing cross-platform links and mobile handoff behavior.

### Both viewports and motion

- [x] A1: Make real-app stage video noticeably sharper in the rendered page. Check whether softness comes from encoding, source resolution, CSS scaling, or the 3D transform; do not simply increase file sizes without measuring.
- [x] A2: Shorten each real-app clip by a few seconds so the four-scene cycle feels quicker. Preserve the meaningful action, natural playback speed, the approval state, and the review-gated story. Prefer precise edits of the reviewed source clips over global speed-up. Keep WebM/MP4 fallbacks, posters, aspect ratio, and honest provenance.
- [x] A3: Align Buddy motion and scene timing: Research/thinking, Create/working, Automate/idle or appropriate work motion, Ship/approval then success after the actual approval moment. Do not show celebration while the UI is still awaiting approval.
- [x] A4: Remove double-imaging during Buddy travel and magenta flashes at every transition, including 4→1 and approval→success. Test autoplay and rapid manual scene changes on desktop and mobile. Do not accept a transition that only looks clean when manually clicked.
- [x] A5: Preserve infinite autoplay 1→2→3→4→1, manual stage buttons, offscreen/page-hidden pause, Save-Data and reduced-motion poster behavior, keyboard input, focus, and no-JavaScript fallback.

## Implementation strategy

1. **Baseline and isolate.** Confirm branch/worktree and clean status. Inspect `docs/index.html`, `docs/landing-story.css`, `docs/landing-story.js`, `docs/site.css`, `docs/site.js`, `tests/docs/`, and `docs/media/landing-story/{manifest.json,recording-receipt.json}`. Do not recapture real profile data.
2. **Spacing and responsive composition.** Tune section padding/min-height and scene positioning at desktop and mobile breakpoints, not global type scales. The present `.journey-sticky`, `.sovereignty-section` (1160px desktop / 1410px mobile), mobile sovereignty `padding-block: 112px 500px`, and section padding are primary sources of empty space. Recheck 390px, 412px, tablet, and 1440px+; do not let fixed sticky nav hide titles.
3. **Buddy media architecture.** Use the existing reviewed `media/landing-story/buddy/working.webm` for intro motion on desktop and mobile, with the current static image as first-paint/reduced-motion fallback. Consider one active Buddy decoder at a time. Size scene Buddy independently from intro. Move mobile scene positions fully inside the viewport. Retain a clickable reaction control and semantic status.
4. **Transition repair.** Treat this as a state-machine/race fix, not a CSS polish pass. The current `setStoryBeat` adds `is-repositioning` for ~980ms; `crossfadeBuddy` queues a second image swap at ~1060ms; panels overlap through `.is-leaving`; video readiness is asynchronous. Use a monotonic transition token to cancel stale timers and `play()` completions, freeze/hide Buddy video before travel, paint only **one** Buddy representation during travel, then reveal the correctly decoded next state after repositioning. Avoid simultaneous opacity of old/new robot silhouettes. If encoded keyed video itself contains magenta frames, identify exact frames and repair/trim the existing media, then update hashes. Give the app panels a single-surface transition (poster/frame gate, mask/wipe, or brief dark interstitial) rather than ghosting two full UI frames. Ensure every beat actually plays after automatic transitions and failures fall back cleanly.
5. **Real-app clip edit and provenance.** Inspect all four current clips frame-by-frame and, if available, the original user recordings `C:\Users\v_2si\Downloads\1.mp4` through `4.mp4`. Shorten each clip with content-aware cuts while keeping key actions legible. Encode visually sharp WebM and MP4 at measured reasonable size; avoid a startup download of every clip. Update video dimensions/durations/SHA-256 and editorial notes in `recording-receipt.json` and matching asset hashes in `manifest.json`. Review all frames for secrets/private data before committing. If originals are unavailable, edit only the already-reviewed public clips and record that limitation honestly.
6. **Comparison and demos.** Restore the previous six-topic comparison (system of record, model choice, tools/actions, background work, offline operation, control/portability) from pre-overhaul history, reviewing wording for current accuracy. Reuse existing `.comparison-disclosure` / `.comparison-table` styles as appropriate; collapsed by default on all viewports. Remove the mobile rule in `docs/site.css` hiding demo facades 2 and 3, then make all three compact and visually balanced. Keep no YouTube iframe until user action.
7. **Accessibility/performance.** Use `muted playsinline`, pause videos out of view, load noncritical sources only as needed, keep first-paint poster discoverable, never autoplay audio. Maintain visible focus, sensible DOM order, touch targets, accessible `<details>`, alt text, Save-Data/reduced motion, and static media fallback. Measure bytes, filmstrip/LCP, cumulative layout shift, and console/network errors on desktop and mobile; improvements must not materially slow initial load.
8. **Tests and visual review.** Extend `tests/docs/test_landing_story.py`, `tests/docs/landing_story_runtime_test.cjs`, and `tests/docs/test_landing_page.py` for the contracts below. Open the local page in a real browser at desktop and mobile sizes, capture before/after screenshots, review every scene transition and section gap, and show the preview to the user before doing broad matrix tests if iteration is needed. Run focused tests, then `uv run python scripts/run_test_matrix.py changed --base origin/main`; fix failures caused by this work. Commit coherent implementation and media milestones locally. Do not push/deploy.

## Regression contracts

- Transition controller: single visible Buddy layer during travel; no stale timer/media promise can replace a newer beat; automatic loops and manual jumps always start the target animation; 4→1 and ship approval→success are covered; failed media yields poster, never blank/magenta.
- Media: reviewed hashes and receipt match published files; clips contain the relevant actions and remain within size/duration budgets; first screen does not eagerly download all four app clips or all Buddy states.
- Responsive: no horizontal overflow or cropped Buddy at mobile widths; three demo posters are visible and focusable; comparison is closed initially and opens accessibly; mobile download disclosure advertises its state; nav does not obscure headings.
- Existing behavior: Windows/macOS/Linux download routing, demo privacy facades, metadata, consent behavior, analytics boundaries, no-JS fallback, reduced motion, Save-Data, and offscreen pause remain intact.

## Handoff outcome to report

Report branch and commits; changed source and media files; clip durations/bytes; exact tests with counts; desktop/mobile browser sizes and findings; performance/accessibility results; any skips or remaining limitations; and confirmation that `D:\Code\row-bot` was not modified.
