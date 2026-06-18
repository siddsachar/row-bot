# Public Docs V1 Review Status

Status: review-ready draft pending human review.

Complete:

- Curated V1 guide pages for install, first launch, models/providers, UI tour,
  workflows, Designer Studio, Developer Studio, skills/plugins/MCP,
  channels/voice, privacy/safety, and troubleshooting.
- Generated MDX reference pages under `docs-site/docs/reference/generated/`.
- Pagefind search UI and dedicated `/search` page.
- `llms.txt` and `llms-full.txt` generation.
- Docs mode, deterministic fake demo data, and Playwright screenshot capture.
- Validation for generated pages, screenshots, metadata routes, guardrails,
  secrets/path patterns, workflow deployment safety, Pagefind output, and LLM
  docs files.

Generated:

- Inventory JSON under `docs-build/inventory` during automation runs.
- Generated MDX reference pages committed under `docs-site/docs/reference/generated`.
- Screenshots committed under `docs-site/static/img/screenshots/generated`.
- Review and screenshot reports under `docs-build/reports`.

Manual review focus:

- Provider/model recommendation wording.
- Privacy and safety claims.
- Troubleshooting steps that alter data, credentials, channels, MCP, plugins,
  or Developer Studio workspaces.
- Screenshot accuracy against the real app after design review.
- Any source-derived generated table that exposes too much implementation detail.
