# Row-Bot Docs Site

This Docusaurus site is a review-only public documentation build. It is not wired into the current public website and is not published by CI.

## Local Preview

```powershell
cd docs-site
npm ci
npm run start
```

## Validation

```powershell
python scripts\docs\collect_inventory.py --out docs-build\inventory
python scripts\docs\capture_real_ui_screenshots.py --validate-only
python scripts\docs\validate_public_docs.py
cd docs-site
npm run build:ci
```

Full screenshot recapture is local-only for now:

```powershell
python scripts\docs\seed_real_app_demo_data.py --scenario full --data-dir docs-build\demo-data
python scripts\docs\capture_real_ui_screenshots.py --scenario full
```
