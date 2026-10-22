# Building Puddlejump's tools (2025, superseded)

Kept for the 1.x branch only. Use `BUILDING.md` for anything current.

- Python 3.10 or newer.
- Build a release bundle: `python build.py --release` (about 240 MB).
- Run the unit tests with `pytest tools`.
- The asset cache is rebuilt with `python build.py --assets` (about 11 minutes).
