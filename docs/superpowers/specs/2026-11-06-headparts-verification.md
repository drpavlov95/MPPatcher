# Headparts library mode — measured verification

**Date:** 2026-11-06
**Binary:** `dist/MPPatcher.exe` (PyInstaller) and the source tree at the same commit
**Geometry reader:** `nifdump.exe` from
`BodySlide-and-Outfit-Studio/mppatcher/build/Release`
**References:** `FACE_FemaleHead.tri` / `FACE_MaleHead.tri` from the installed
Physiques of Skyrim (3127 vertices, 5952 faces each)

Every number here is from a run, not an estimate. The invariant each run has to
state is **Verified: N of N** — the library replayed through the plugin's own
matching rules, with every shipped shape finding its set.

## The three mod shapes this had to handle

| | NIF-only | loose NIFs + TRIs | BSA-only |
|---|---|---|---|
| mod | Even More Brows - HPH - COtR | BG3 hairs 1.1 | Beards of Power |
| loose NIFs | 2 | 50 | 0 |
| TRIs shipped | 0 | 38 (all zero morphs) | 0 |
| archives | 0 | 0 | 1 × 911 MB, v105/LZ4 |
| **covered before** | **0** | **10 of 50 shapes (20%)** | **0** |

## Run 1 — Even More Brows (ships no `.tri` at all)

```
Geometry: 2 shapes from 2 inputs
Plugins:  1 read, 164 HDPT records covering 2 meshes
[PATCHED] meshes\actors\character\EMB\FemaleBrows001.nif#FemaleBrows
[PATCHED] meshes\actors\character\EMB\MaleBrows001.nif#MaleBrows
Sets:     2 written, 0.0 MB
Verified: 2 of 2 shipped shapes match a set the plugin would find
Frames:   {'head': 2}
Distance: headpart-to-head p50, median 0.02, worst 0.03
```

Output tree, enableable as a mod directly:

```text
SKSE/Plugins/MPF/morphs/PhysiquesOfSkyrim_EMB.toml
SKSE/Plugins/MPF/morphs/RLY_EMB_F_141.tri
SKSE/Plugins/MPF/morphs/RLY_EMB_M_141.tri
```

141 vertices each, 4350 bytes, 30.9 bytes per vertex — the documented floor.
Part type `eyebrows` came from the plugin, not from the flag.

## Run 2 — BG3 hairs 1.1 (the 20% case)

```
Geometry: 50 shapes from 50 inputs
Plugins:  1 read, 54 HDPT records covering 50 meshes
Sets:     25 written, 10.9 MB
Verified: 44 of 44 shipped shapes match a set the plugin would find
Frames:   {'head': 25}
Distance: headpart-to-head p50, median 1.11, worst 3.22
Skipped:  6 x detached from the head surface
Headparts: 44 patched, 6 skipped
```

All 50 meshes classified from the plugin: 25 female, 25 male, zero unresolved —
against 28 of 38 TRIs that the old path skipped as unknown.

The six skips are Orin's braid and hanging accessories, with their measured
contact distances in the log:

```
orin_hair_braid F.nif#orin_braid  — closest vertices 19.2 units away
orin_hair_braid M.nif#orin_braid  — closest vertices 20.9 units away
orin_misc01 F.nif#misc01          — closest vertices 31.4 units away
orin_misc01 M.nif#orin_misc_01    — closest vertices 30.5 units away
orin_misc02 F.nif#misc02          — closest vertices 17.1 units away
orin_misc02 M.nif#orin_misc_02    — closest vertices 17.3 units away
```

Past the 14-unit falloff radius every delta is zero by construction, so a set
for them would be a file of zeros. Pass `--falloff 0` to ship them anyway.

## Run 3 — Beards of Power (nothing loose)

```
Archives: 1 opened, 1 yielded NIFs
Geometry: 182 shapes from 1 inputs
Plugins:  1 read, 388 HDPT records covering 198 meshes
Sets:     114 written, 14.2 MB
Verified: 166 of 166 shipped shapes match a set the plugin would find
Frames:   {'head': 54, 'origin': 60}
Distance: headpart-to-head p50, median 2.56, worst 17.31
Skipped:  16 x detached from the head surface
```

The frame split is the point: **54 head height against 60 origin-centred, in one
mod**. That is why the frame is measured per geometry rather than declared, and
it independently reproduces what the FaceMorphsPatcher notes recorded for this
mod (83 / 94 before clustering).

## Run 4 — ApachiiSkyHair (deduplication)

```
190 shapes -> 72 sets
Verified 190/190, worst accepted match distance 0.750 of 3.0
Frames: {'head': 69, 'origin': 3}
Distance: median 1.69, worst 7.00
```

72 geometries from 190 shapes. The FaceMorphsPatcher notes record 74 from 194
for this mod, so the clustering reproduces the known result.

## Independent check of the artifact on disk

Not the run's own report: the written files were read back the way the plugin
reads them — every `.toml` in the folder, then each `.tri` it names, indexed by
`(sex, vertex count)`, best `CentredMeanDistance`, accepted under 3.0.

| mod | sets on disk | `(sex, count)` keys | shapes matched | worst accepted |
|---|---|---|---|---|
| BG3 hairs | 25 | 22 | 44 of 44 shipped | 1.372 / 3.0 |
| ApachiiSkyHair | 72 | 71 | 190 of 190 | 0.750 / 3.0 |

Fewer keys than sets means several geometries share a vertex count — BG3 has 3
such collisions — which is safe because the plugin stores a vector per key and
picks the closest. Every set read back with exactly
`['RLY_Heavy', 'RLY_Muscular', 'RLY_Thin']`, in order.

## Classification across the installed mod list

| mod | meshes | decided by plugin | unresolved |
|---|---|---|---|
| BG3 hairs 1.1 | 50 | 50 | 0 |
| Even More Brows | 2 | 2 | 0 |
| ApachiiSkyHair SSE | 190 | 190 | 0 |
| Cyberpunk 2077 Hairs | 69 | 68 | 1 |

The single holdout is `rogueHaircap.nif`, which carries no `HDPT` record at all
— no headpart references it, so there is nothing to decide from. It is reported
rather than guessed.

## Frozen-exe confirmation

The packaged `dist/MPPatcher.exe` (107.7 MB) was run against Even More Brows
with `nifdump.exe` beside it. It reproduced Run 1 exactly, and the morph sets it
wrote are **byte-identical** to the ones the source tree produced:

```text
RLY_EMB_F_141.tri   sha256:769be9fe07027299   = frozen output
RLY_EMB_M_141.tri   sha256:538ce4731fdbf179   = frozen output
```

This step was worth doing twice, because it found two bugs that the 221-test
suite passed straight through — both invisible from source and fatal in release:

1. **`dirname(__file__)` inside a PyInstaller bundle is the temporary extraction
   directory.** The first build reported `nifdump` missing while it sat right
   next to the exe, and used the same wrong base to look for the reference
   heads. Fixed with `core.vfs.app_directory()`; pinned by
   `test_a_frozen_build_searches_beside_its_own_executable`.
2. **scipy's array-API shim imports its backends by name**, which PyInstaller's
   static analysis never sees. The build was clean and the exe died at the first
   surface wrap with `No module named
   'scipy._external.array_api_compat.numpy.fft'`. Fixed with a
   `collect_submodules` entry in `MPPatcher.spec`.

The lesson is the one already recorded for the Drape bridge: confirm which
binary ran and that it ran, rather than that the tests passed.

## Still open

* **The body weld.** Long hair that lies on the neck and shoulders has no
  surface to follow there, because the head reference stops at the neck — and
  the body fattens roughly fifteen times as much as the head does. The authoring
  tool takes `--body-dump` / `--body-osd` for this; the plumbing is not in this
  change. Until it lands, the falloff is what keeps such hair from being
  dragged by a field that does not describe it.
* **scipy's 45.3 MB.** See `docs/headparts.md`.
