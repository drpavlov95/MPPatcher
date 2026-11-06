# Public pipeline

The shipped `mppatcher-cli.exe` uses the isolated public pipeline. It never
modifies the source `.osp`, NIF or OSD files. It writes a new BodySlide data
root containing:

1. The MPP body imported in place of the source 3BA (legacy CBBE/3BBB aliases) or HIMBO-style body.
2. Normal RM_* conform data for the outfit shapes.
3. Optional Drape Smart refit, Auto-Zap and native Fix Clipping passes.

Smart refit is an ALPHA option. It can fail or look worse on individual outfits
and significantly increases processing time.

The internal migration build and its LegacyMigrator remain separate and are not
part of the public binary. BodySlide and Outfit Studio data are required for
the reference projects, NIF/OSD files and native clipping pass.

Hands and feet use normal `RM_Heavy`, never `RM_HeavyOutfit`. Their Muscular
data can legitimately be empty. Cloak/Cape shapes are intentionally excluded
from `RM_Thin`.

## Headparts mode

The GUI also accepts a local folder or ZIP holding a headpart mod. Headparts
mode reads the mod's **NIFs** — loose, or out of its BSAs — and writes an MPF
morph library: one minimal TRI per distinct geometry plus a `[[sets]]` manifest,
under `SKSE/Plugins/MPF/morphs` in the output. That is what the plugin reads
under its default `Backend=shared`, and the only artifact that can serve a mod
shipping no `.tri`.

Sex and part type come from the mod's own `HDPT` records; the path and filename
rules only answer for meshes no plugin declares, and anything still undecided is
reported rather than guessed. Geometry is read through `nifdump.exe`, which must
ship beside `MPPatcher.exe`; its absence stops the run rather than falling back
to the stub TRIs mods commonly ship beside real meshes.

The reference heads `FACE_FemaleHead.tri` and `FACE_MaleHead.tri` are discovered
from the active Data/VFS tree (`SKSE/Plugins/MPF/morphs`). Deltas are sampled by
surface wrap, and every run ends by replaying the plugin's own matching over the
library it just wrote — a shape left unmatched fails the run.

`--mode tri` keeps the previous behaviour, transferring into the `.tri` files a
mod already ships, for installations configured with `Backend=racemenu`.
