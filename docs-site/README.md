# Row-Bot Docs Site

This Docusaurus site is the draft public documentation for Row-Bot. It is not
published by this branch and is not linked from the current public website.

## Local Preview

From the repository root:

```powershell
python scripts\docs\collect_inventory.py --out docs-build\inventory
python scripts\docs\generate_mdx.py --inventory docs-build\inventory
python scripts\docs\generate_llms_txt.py --docs-root docs-site\docs --out-dir docs-site\static
python scripts\docs\validate_public_docs.py
cd docs-site
npm ci
npm run build:ci
```

For a live local preview:

```powershell
cd docs-site
npm run start
```

Screenshot capture is local and deterministic:

```powershell
python scripts\docs\capture_screenshots.py
python scripts\docs\capture_screenshots.py --validate-only
```

The generated review report is written to
`docs-build\reports\docs-v1-review.md`.
