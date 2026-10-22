# Puddlejump code style

- Lines are at most 100 characters (we moved up from 88 in 2026).
- Functions and variables use snake_case; classes use PascalCase.
- Every tool module has a module docstring and unit tests in `tools/tests/`.
- Use `pathlib`, never string paths, in new code.
- Log with `logging`, never `print`, outside command-line entry points.
- Tile types are lower-case words: `grass`, `water`, `stone`, `sand`, `lava`.
