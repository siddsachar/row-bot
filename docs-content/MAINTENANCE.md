# Public documentation maintenance

`metadata/ui_surfaces.yml` is the authoritative coverage map. When a user-facing
surface changes, update its documentation route and either its `screenshot_id` or
its short `no_image_reason`. Capture instructions live only in
`metadata/screenshots.yml`.

Use an isolated profile for screenshots. The capture command refuses the normal
Row-Bot data directory, seeds neutral demonstration data, disables background
autostart and network status checks, and uses display-only provider, channel,
plugin, and MCP states where live access would be unsafe.

```powershell
uv run python scripts/docs/collect_inventory.py --out docs-build/inventory
uv run python scripts/docs/generate_mdx.py
uv run python scripts/docs/generate_llms_txt.py
uv run python scripts/docs/capture_real_ui_screenshots.py --timeout 120
uv run python scripts/docs/validate_public_docs.py
uv run python scripts/docs/build_review_report.py
cd docs-site
npm run build:ci
cd ..
uv run python scripts/docs/sync_github_pages.py
uv run python scripts/docs/sync_github_pages.py --check
```

GitHub Pages serves `main:/docs`. The synchronization command refreshes only
the built documentation directories (`assets`, `docs`, `img`, `pagefind`, and
`search`) and the selected machine-readable documentation files. It preserves the
marketing homepage, feature pages, analytics, contact form, and their assets.
Commit the synchronized artifact with its documentation source; merging the
reviewed pull request is the publication step.

For a smaller screenshot update, pass `--scenario first-run`, `--scenario
configured`, `--scenario mobile`, or `--ids` followed by stable screenshot IDs.
Desktop frames are 3840×2160; Android frames are 390×844. Review every changed
image for private data, misleading state, clipping, and legibility before moving
its metadata from `needs-review` to `approved`.

Never capture from a real profile, call a live provider, start a real channel or
MCP transport, or publish as part of routine generation. Native and live surfaces
remain in the manual checklist until a reviewer supplies or approves current
evidence.
