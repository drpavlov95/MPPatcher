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

The GUI also accepts a local folder or ZIP containing headpart `.tri` files.
Headparts mode uses the current Physiques of Skyrim references
`FACE_FemaleHead.tri` and `FACE_MaleHead.tri` and transfers
`RLY_Heavy`, `RLY_Muscular` and `RLY_Thin` while preserving the source folder
layout in the output. The references are discovered from the active Data/VFS
tree (`SKSE/Plugins/MPF/morphs`). This is the runtime TRI transfer path; NIF
geometry generation for new hair meshes remains the separate FaceMorphsPatcher
authoring workflow.
