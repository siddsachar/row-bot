# Built-in Buddies

Each folder is a living Buddy pack: `art.png` (the cut-out character), `rig.json` (its bones, eyes and
expression patches, hand-tuned), `patches.png` (painted expressions, when present) and `manifest.json`.
Row-Bot animates them locally; nothing here needs a model call at runtime.

`scripts/buddy/rebuild_builtins.py` rebuilds the art from `scripts/buddy/sources/` and paints the
expression patches with an image model (a one-off, by hand).

The fourteen added in 5.3.4 (tile, trace, oar, halo, servo, hover, woolly, facet, glaze, mosaic, tide, brew,
cog, drake) were painted by the same steps as New look (the family pose guide, then six expression edits,
with ChatGPT's GPT Image 2), rigged by `living_hatch.build_pack` and cleaned of the magenta key by hand.
Their rigs are fitted, not hand-tuned. Tile is a new install's first Buddy; a profile set up before keeps Glyph.
