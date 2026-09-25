# React workspace visual and UX review

The screenshots use the isolated browser fixture. They contain synthetic conversations and no live provider, channel, or user data. The baseline is `9c2876b5bf695ef8cde6c5c8ae66d288979532b5` (merged PR #370). The after captures show this change before commit.
The baseline Home capture caught Buddy while it was loading; compare the main workspace content for that pair.

| Journey | Before | After | Review observation |
| --- | --- | --- | --- |
| Empty chat, 390×844, dark | [Screenshot](before-conversation-phone-dark.png) | [Screenshot](after-conversation-phone-dark.png) | The composer and short starting hint remain visible at once. Context is a single action in the mobile header. |
| Home and workflows, 1440×900, dark | [Screenshot](before-home-wide.png) | [Screenshot](after-home-wide.png) | Start chat, recent work, and workflows have a clearer order; the setup reminder is compact. |
| Providers, 390×844, light | [Screenshot](before-settings-providers-phone.png) | [Screenshot](after-settings-providers-phone.png) | One section picker replaces the long Settings rail while retaining the provider content. |
| Active code workspace, 1440×900, dark | [Screenshot](before-code-detail-dark-desktop.png) | [Screenshot](after-code-detail-dark-desktop.png) | Files and changes appear before repository controls; the active detail panel displays the bound draft while preserving its identity. |

The complete local captures are under `.local/evidence/unified-client-platform/phase-3-visual-alignment/qa/`:

- Baseline: `browser-20260924-175834-0e2fe179`.
- After conversation and Home: `browser-20260924-190609-a6aa3a6b` (five viewports, light and dark).
- After Settings: `browser-20260924-190123-2f9a8757` (all 17 leaves, desktop and phone, light and dark). The Settings capture test passed; the same run recorded one transient Buddy media console error in a separate test. A clean isolated conversation and Home rerun is above.
- Developer and Designer: `browser-20260924-185223-63e19249`, `browser-20260924-190106-181fcb99`, `browser-20260924-190157-a90186bb`, and `browser-20260924-190627-87e0f071` (desktop, laptop, tablet, phone, narrow; reviewed Git and design lifecycle outcomes).
- Conversation actions: `browser-20260924-185545-7d55a42e` (five viewport outcomes).
- WebKit desktop: `browser-20260924-190552-4c2dcce3`.
- Setup Center: `browser-20260924-191420-5179c8b1` (five viewports, light and dark, accessible controls).
- Component gallery: `browser-20260924-191237-075b390d` (five viewports, light and dark, 10/10 tests).
- Repeated coding/design/output/Context journeys: `browser-20260924-192011-ba34c9b8` (8/8, two passes through each outcome).
- Home and explicit Deck close/reopen: `browser-20260924-192325-494db7c5` (2/2).
- Offline/reconnect: `browser-20260924-192534-c67c55dd` (1/1; draft retained without command replay).
- Combined 200% CSS zoom, forced colors, reduced motion, stop, approval reject, and reconnect: `browser-20260924-193848-ad0653ec` (1/1; approval controls remain reachable).
- Approval lifecycle and keyboard containment: `browser-20260924-203455-1f4bc829` (1/1; Details, Escape, and one approval decision).
- Light/dark recovery states on desktop and phone: `browser-20260924-203607-0d961aef` (2/2; unavailable model, preview and Inspector recovery, approval states, and lost accepted response receipt).
- System appearance at first frame and runtime switching: `browser-20260924-204532-0e619a8e` (2/2, desktop and phone).
- All four accents in light and dark with real synthetic tools and media: `browser-20260924-205127-ed4a41a3` (1/1; eight palette captures and accessibility scans, settled output identity and composer retention).
- Local appearance controls, storage recovery, and forced-colors/reduced-motion/200% CSS zoom: `browser-20260924-204934-8afd1d8a` (6/6, desktop and phone).

The fixture build blocks external HTTP requests. Its asset manifest and screenshots are generated from the local build, and browser observations contain sanitized paths. The browser tests assert no horizontal overflow, review accessible controls, preserve unsent drafts, and inspect output identities and approvals. The screenshot suite also captures 720px and 1152px zoom-equivalent reflow, forced colors, and reduced motion. Existing focused browser tests cover actual 200% browser zoom, keyboard navigation, modal focus, resource recovery, offline/reconnect, and the same fake service boundaries.

## Baseline failure disposition

| Finding | Disposition |
| --- | --- |
| CSP fixture expected no YouTube embed host | The backend security contract explicitly allows `https://www.youtube-nocookie.com` in `frame-src`; the exact fixture assertion now matches it. |
| Synthetic chat response missing from an earlier capture | A fresh run received it. The test now waits for the responsive panel to return before asserting conversation content. |
| Home Monitor locator failed | The visible section is named **System Monitor**; the test uses the actual accessible name. |
| Browser folder picker stalled Developer fixture | The browser cannot issue a desktop folder grant. The journey uses the supported local draft folder, and the isolated repository probe recognizes only that scoped draft path. |
| Buddy media load produced one `ERR_FILE_NOT_FOUND` during rapid route/theme capture | Object URLs are released after the media source changes on the next paint. The focused lifecycle test and isolated Edge walkthrough pass. |
| A completed design sometimes missed its first panel open | The panel now uses the freshly read binding and exact resource revision. Two repeated design journeys pass, while focus-safe suppression during typing remains intact. |
| Repeated media test saw two fixture calls | The fixture retains calls from other synthetic conversations. The assertion now checks the current conversation ID and still requires exactly one generation. |
| Managed browser fixture preview returned 503 | The panel shows its recoverable **Picture unavailable** state. The exact fixture response is annotated; control and draft retention still pass. |
| Firefox Playwright `browserContext.newPage` failed before navigation | Host browser/toolchain failure; no app page loaded. Edge/Chromium and WebKit walkthroughs passed. |
| Older PR matrix native crash | The earlier log did not retain a crash signature. The latest full PR matrix completed without a native crash. |
| First full matrix rerun reported 24 runtime-installer failures | The Windows directory ownership guard used the host `TEMP` path outside this workspace's sandbox. With `TEMP`, `TMP`, and `TMPDIR` set to a workspace-local directory, the affected focused modules passed (91 passed, 1 skipped). The integrated rerun uses the same environment. |
| Integrated PR matrix deterministic lane failed three tests | The Designer test's Node subprocess exceeded its 15-second startup deadline under full-suite load; its output assertions are unchanged with a 60-second allowance. Two media tests wrote to the shared synthetic `tools_config.json`, whose older Windows owner differed from a fresh test file. Both now use `tmp_path` data directories. No production security gate was relaxed. The latest full matrix passes with the older file still present. |
| Deterministic Custom Tool tests stalled near 83% | The builder path requested an AI proposal without a fake model and could wait on a configured provider. Its test helper now forces the documented offline fallback. The dedicated AI-generator case retains its fake model; the module passed 30/30 and the full matrix advanced through this point. |
| Follow-up full matrix stopped in frontend formatting | Prettier corrected one browser evidence assertion. `npm run check` then passed all 1,594 frontend tests, lint, formatting, typecheck, and build before the final full matrix rerun. |

## Verification scope and limits

All 17 Settings leaves and the Home Workflows, Knowledge, Monitor, and Insights tabs have desktop/phone screenshots. Conversation, Setup Center, and the component gallery use five sizes and both themes. The Developer repository journey checks a local Git branch, managed worktree preservation, and sandbox persistence on five sizes; Designer opens presentation, export, and sharing. Focused component tests cover the modified Context, navigation, Home, Settings, task editor, Inspector, PWA, and Buddy behavior. NiceGUI's `--server --legacy-ui` path responded successfully to the authenticated launcher ping and `GET /`.

### Integrated checks

| Command | Result |
| --- | --- |
| `npm run check` (`frontend/`) | Passed: 1,594 component/unit tests across 161 files, lint, import boundaries, formatting, typecheck, and production build. Vite reported the existing 500 kB chunk advisory. |
| `npm run build:verify` (`frontend/`) | Passed: 14 assets, 2,597,009 bytes, byte-identical verification. |
| `npm run typecheck` and `npm run lint` (`frontend/`) after final browser-spec updates | Both passed; import/network boundary check covered 182 source files with 0 violations. |
| `git diff --cached --check` | Passed with no whitespace errors. |
| `uv run python -m pytest tests/subsystem/installer tests/contracts/installers -q` with workspace-local `TEMP`, `TMP`, and `TMPDIR` | 91 passed, 1 skipped; one deprecation warning. |
| `uv run --no-sync python scripts/run_test_matrix.py pr` with workspace-local temp paths | Failed in the deterministic lane: contracts 140 passed (1 warning), subsystem 5,727 passed and 27 skipped (8 warnings), migrated coverage 5,867 passed and 27 skipped at 75.91% (1,660 warnings), then deterministic 8,322 passed, 32 skipped, 2 deselected, 3 failed (10 warnings, 3 subtests passed). All earlier non-test gates passed. |
| `uv run --no-sync python scripts/run_test_matrix.py deterministic` with the older synthetic config file preserved outside the active test path | Passed: 8,325 passed, 32 skipped, 2 deselected, 10 warnings, 3 subtests passed. The original file was restored afterward. |
| `uv run --no-sync python scripts/run_test_matrix.py installer-contracts` | 94 passed. |
| `uv run --no-sync python scripts/run_test_matrix.py app-smoke` | Passed: app process, launcher ping, `GET /` 200, and clean termination. |
| `uv run --no-sync python scripts/run_test_matrix.py legacy-inventory` | 76 passed. |
| `uv run --no-sync python -m pytest tests/test_provider_media.py tests/subsystem/designer/test_client_editing.py -q` | 51 passed with the older shared synthetic config still present. |
| `uv run --no-sync python -m pytest tests/test_developer_studio_phase8.py -q` | 30 passed; no live provider needed. |
| Final `uv run --no-sync python scripts/run_test_matrix.py pr` with workspace-local temp paths | **Passed, exit 0.** Contracts 140 passed (1 warning); subsystem 5,727 passed, 27 skipped (8 warnings); migrated coverage 5,867 passed, 27 skipped (75.91% against 55%, 1,660 warnings); deterministic 8,325 passed, 32 skipped, 2 deselected (10 warnings, 3 subtests passed); installer contracts 94 passed; app smoke passed process, ping, and `GET /` 200; legacy inventory 76 passed. |

The PR matrix and follow-up lane logs are retained in `.local/evidence/react-app-ux-polish/`; their results are also reported in the PR handoff. They run with workspace-local Windows temporary directories because the sandbox cannot write to the host temp directory. The earlier failures remain recorded above; the final full PR command exited successfully without moving the older synthetic config file.

The fixture cannot validate a real native folder picker, provider account, channel delivery, OS installer, signing, or notarization. A manual screen-reader session and clean-machine UX pass remain pending. Firefox requires a working Playwright launch on this Windows host. The detailed Settings forms remain available and some retain their existing density because security, provider cost, and approval copy is consequential.
