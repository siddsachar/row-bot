# Landing Page Overhaul Implementation Checklist

Branch: `feat/landing-page-overhaul`

Base: `46d888923a43d5e104054fe2353d591a50ef15ca`

This checklist is updated as the implementation proceeds. A checked item has
code and focused verification; live-capture and visual-review items are checked
only after the real run evidence exists.

## Capture system

- [x] Capture CLI exposes preflight, prepare, capture, process, validate, publish
- [x] Exact normal-profile authorization and profile quiescence are enforced
- [x] Loopback-only owned application launch and cleanup are enforced
- [x] Six-attempt generation budget and uncertain-outcome stop are enforced
- [x] Client-neutral semantic adapter and NiceGUI implementation are complete
- [x] Run receipts exclude secrets/private paths and contain hashes/provenance
- [x] Output containment and reviewed-publication gate are complete
- [x] WebP/poster/short-clip pipeline and contact sheet are complete

## Real story

- [x] Public-safe source allowlist reviewed
- [x] Canonical Qwen/Sol preparation run completed in the normal profile
- [x] Seven lived-in story records created through Row-Bot
- [x] Nine canonical desktop/mobile scenes captured in settled states
- [x] Full-resolution privacy and model-boundary review approved
- [x] Reviewed assets published into `docs/media/landing-story/`

## Landing experience

- [x] Statement-led hero and download/watch actions complete
- [x] Buddy six-state controller, movement, and decoded-frame handoff complete
- [x] Scroll, click/tap, keyboard, visibility, offscreen, reduced-motion complete
- [x] Research -> Create -> Automate -> Ship autonomous story complete
- [x] Sovereignty field, connected knowledge visual, and local-first proofs complete
- [x] Three privacy-enhanced demo facades preserved
- [x] Local-first statement and final CTA complete
- [x] Navigation, structured data, platform detection, analytics consent preserved
- [x] No-JavaScript and missing-media fallbacks complete

## Verification

- [x] Capture/adapter/safety/media deterministic tests pass
- [x] Landing runtime/accessibility/fallback tests pass
- [x] Existing landing-page tests pass
- [x] Changed-source matrix completed against `46d88892`
- [x] Desktop visual QA complete
- [x] Mobile visual QA complete
- [x] Console, focus, clipping, playback, privacy checks complete
- [x] JS/CSS/media performance budgets pass
- [x] Coherent milestone commits created

The changed-source matrix completed with 4,861 passed and 19 skipped. Four
Developer workspace-import recovery tests failed only in the aggregated test
order; the untouched 98-test file passed independently, and the adjacent
Designer + Developer sequence passed 793 tests with 14 skips.

Known reviewed limitation: a rare single-frame Buddy compositor/chroma flash can
still appear during a long cross-screen reposition. The user accepted the
current result after the deterministic single-still movement handoff removed
the more disruptive double-image and intermittent-static behavior.
