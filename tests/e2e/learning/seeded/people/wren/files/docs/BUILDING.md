# Building Puddlejump's tools (current)

Updated 2 September 2026. This page replaces `BUILDING-2025.md`.

- Python 3.12 or newer. The tools use `tomllib` and the 3.12 typing syntax.
- Install: `python -m pip install -e .[tools]`.
- Build a release bundle: `make dist`. The bundle lands in `dist/` and is about 180 MB.
- Run the unit tests with `python -m unittest discover tools/tests`.
- Level files live in `levels/` as JSON; each tile has `x`, `y` and `type`.
- The asset cache is rebuilt with `make assets` (about 4 minutes on the studio build machine).
