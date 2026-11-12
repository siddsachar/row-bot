# Built-in Buddies

Each folder is a living Buddy pack: `art.png` (the cut-out character), `rig.json` (its bones, eyes and
expression patches, hand-tuned), `patches.webp` (painted expressions, when present; lossy colour,
lossless alpha) and `manifest.json`.
Row-Bot animates them locally; nothing here needs a model call at runtime.

`scripts/buddy/rebuild_builtins.py` rebuilds the art from `scripts/buddy/sources/` and paints the
expression patches with an image model (a one-off, by hand).
