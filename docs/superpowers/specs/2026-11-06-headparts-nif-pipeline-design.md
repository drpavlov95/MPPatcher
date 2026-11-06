# Headparts from NIFs — design

**Date:** 2026-11-06
**Status:** approved, ready to plan

## Intent

MPPatcher's Headparts target must patch any headpart mod the user actually has,
including the ones that ship no `.tri` at all. Today it patches only `.tri`
files, which excludes hair, earrings, brows and jewellery that ship as a bare
NIF — and silently under-covers the mods that do ship TRIs.

### Why now

The headparts path was bolted onto the BodySlide/`CalienteTools` scan model: the
GUI loads a `SliderGroup` called "Headparts (TRI)", reports each TRI's sex as
`3BA`/`HIMBO`, and the backend walks for `*.tri`. The real headpart world is
NIF-shaped, and the facts the patcher needs about a headpart — its sex and its
part type — live in the mod's plugin, not in its filenames.

The authoring side of this already exists and works: `FaceMorphsPatcher`'s
`tools/gen_headpart_morphs.py` has generated the shipped libraries for vanilla
hair, ApachiiSkyHair, KS Hairdos, Beards of Power and others. It is a 530-line
script driven by eleven flags and two external commands, run by hand per mod.
This design brings that capability into MPPatcher as a first-class mode.

## Measured starting point

Numbers taken 2026-11-06 against the installed mod list, not estimated.

| Mod | NIFs | TRIs | Covered today |
|---|---|---|---|
| BG3 hairs 1.1 | 50 | 38 | 10 of 50 shapes (20%) |
| Even More Brows - HPH - COtR | 2 | 0 | 0 |
| Beards of Power | 0 loose (911 MB BSA) | 0 loose | 0 |
| Apachii Sky Hair SSE | 190 | 190 | partial |
| Cyberpunk 2077 Hairs | 69 | 58 | partial |

Two separate failures produce the BG3 number:

* **12 NIFs have no TRI**, so `scan_headparts` never sees them.
* **28 of the 38 TRIs are skipped as `unknown` sex.** `detect_headpart_sex`
  tests for `_f` / `f_` / `female`; this mod names its meshes
  `gortash_hair F.nif`, with a space. The function returns `unknown` and
  `patch_headparts` skips the file "for safety".

The mod's own plugin resolves all 50 meshes with no ambiguity: 54 `HDPT` records
covering 50 meshes, 27 female and 27 male, each with a part type. Sex and part
type are plugin facts, and reading them removes the guessing entirely.

The `.tri` files a mod ships are not a usable geometry source. In BG3 hairs all
38 happen to match their NIF vertex counts but carry **zero morphs**; Vanilla
Hair Remake ships a 212-byte, 4-vertex `hair01.tri` next to a 4014-vertex
`hair01.nif`. A morph set built from the stub matches no live mesh, and nothing
reports an error — the hair simply never moves.

## The runtime contract

`ModernPhysiqueSystem.dll` runs `Backend=shared` by default and, per its own
INI, "reads `Data\SKSE\Plugins\MPF\morphs` (TRI + TOML) directly and applies
`RLY_*` deltas". It indexes that folder by `(sex, vertex count)`, confirms a
candidate against the live mesh with `CentredMeanDistance`, and accepts it
within `kSameMeshLimit = 3.0`.

So the artifact that makes a headpart morph is **a morph-library TRI plus a
`[[sets]]` manifest entry in that folder** — not a modified copy of the mod's
own files. This is the only artifact that can serve a NIF-only mod, and it never
writes into the user's mod.

The in-tree TRI transfer that exists today remains meaningful for
`Backend=racemenu`, the documented compatibility fallback for old asset
installations. It stays, as a second mode, no longer the default.

## Goals

1. Point the Headparts target at a mod folder or ZIP and get a working morph
   library out, whether that mod ships NIFs only, NIFs plus stub TRIs, or NIFs
   plus real TRIs.
2. Take sex and part type from the mod's `HDPT` records; fall back to path and
   filename only where no plugin declares the mesh, and report which source
   decided each one.
3. Reach NIFs inside BSAs, because a mod like Beards of Power ships nothing
   loose.
4. Verify the output the way the plugin will read it, and fail loudly rather
   than write a library that silently matches nothing.
5. Keep the existing in-tree TRI transfer available, with the sex-detection
   defect above corrected so it stops skipping three quarters of a mod. Its
   output format and flags do not change.

## Non-goals

* Writing NIFs. Nothing in this pipeline modifies geometry.
* Patching plugins. No `HDPT` record is created or edited; the library matches
  geometry, so no plugin edit is needed.
* A general NIF library in Python. Geometry comes from `nifdump`.
* Generating chargen TRIs for headparts that lack one. That would also require
  an `HDPT` edit, and the shared backend makes it unnecessary.

## Anti-goals

Things that would make this a failure even if it ran:

* **A silent fallback between two geometry readers.** Every bug found while
  building the authoring tool was a silent failure that the happy path reported
  as success. If `nifdump` cannot be found, the run stops with an actionable
  message; it does not quietly read the stub TRIs instead.
* **Guessing a sex.** A wrong sex files a morph set under a key no NPC looks up.
  Unresolved is reported, and shipped for both sexes only on an explicit
  request.
* **Writing into the source mod.** Output is always a separate tree.

## How it works

Five stages, each independently testable.

### 1. Source discovery — `core/headpart_sources.py`

Walk the staged source (folder or extracted ZIP, via the existing
`StagedSource`) and collect: loose `*.nif`, `*.bsa`, and `*.esp` / `*.esm` /
`*.esl`. BSA entries ending in `.nif` are extracted to a work directory with
`fmp.bsa.extract`, one subfolder per archive — if the same hair exists in two
archives with different geometry, covering both beats guessing which the load
order picks; stage 4 merges them when identical.

### 2. Geometry — `core/nif_geometry.py`

`nifdump.exe` walks the tree and writes one flat binary per NIF plus a TSV
index; `fmp.nifdump` reads them back. The binary is resolved beside the GUI
exactly as `Config.mppatcher_cli_exe` resolves the native CLI, with
`MPP_NIFDUMP` as an operator override. Absent, the run fails with the paths it
looked in.

`nifdump` is chosen over a Python reader because it is nifly-backed: it handles
`NiTriShape` (Skyrim LE format, which plenty of old hair mods still ship) and
every `BSTriShape` variant, and it returns triangles, which the target mesh
needs for the optional smoothing pass. It is already built by the same CMake
project that produces `mppatcher-cli.exe`, and ships into the same folder.

### 3. Classification — `core/headpart_classify.py`

Per shape, in this order, recording which rule answered:

1. The `HDPT` record for that mesh path (`fmp.esp`), when exactly one sex is
   declared. Declared for both sexes is treated as unresolved, not as a guess.
2. `/female/` or `/male/` in the path, then `female` / `male` in the filename —
   female tested first, because it contains "male".
3. The vanilla trailing-letter pair (`HairShortHumanF` / `...M`), and the
   space-separated form this mod list actually uses (`gortash_hair F`), only
   when the opposite file exists. On its own, a trailing letter is no evidence.
4. An explicitly stated `--default-sex`, for a mod that is wholly one sex.

Part type comes from the `HDPT` record's `PNAM`, defaulting to the user's choice
for meshes no plugin declares. The plugin never compares `part`, so it is
descriptive; it still goes in the manifest because the key exists.

### 4. Library generation — `core/headpart_library.py`

Per sex, load the reference head TRI, confirm it carries all three `RLY_*`
morphs and real faces, and keep two candidate frames: as authored (head height)
and translated by `0,-1.633,120.555` (origin-centred). A mod is not
self-consistent — Beards of Power ships 83 shapes at head height and 94 at the
origin — and since the frames are ~120 units apart, the choice is measured per
shape on a 256-vertex subsample rather than declared. Measured today: the Even
More Brows meshes sit 0.03 units from the head-height surface and 113 units from
the origin-centred one, so the test is not close.

Shapes are grouped by `(sex, vertex count)` and then clustered by
`CentredMeanDistance < 1.5`. That is deliberately tighter than the plugin's own
3.0 acceptance: clustering up to the threshold leaves a member sitting 2.95 from
the representative that must match it at runtime, with no headroom for a live
mesh that differs from the file. One TRI is written per cluster.

Deltas are sampled by surface wrap (`fmp.wrap`), not by nearest vertex: a hair
mesh stands 1.5 to 1.8 units off the skull and neighbouring hair vertices can
land on unrelated head vertices, which shears the mesh along the boundary.
Wrapping interpolates across the containing triangle, so the field is
continuous.

One kernel, two parameter presets, chosen from the part type:

| preset | parts | falloff | why |
|---|---|---|---|
| `standoff` | hair, facialhair | full to 4, zero at 14 | a strand hanging in the air has nothing to follow; carrying the skin's full displacement out there distorts the silhouette |
| `onskin` | eyebrows, scar, eyes, misc | none | measured at 0.03 units off the surface; these inherit the skin's displacement in full |

Each written TRI is read straight back before the run is called a success — a
file MPF cannot parse is worse than no file. Output TRIs are minimal: base
vertices and morphs, `polyTris` declared 0 so the face and UV blocks are absent.
MPF keeps only the base vertices (to match) and the deltas (to apply) and skips
every other block by its declared size, so carrying faces would add megabytes
per download for data no reader looks at. Cost is ~30 bytes per vertex.

Output layout, which is a mod folder the user can enable directly:

```
<output>/SKSE/Plugins/MPF/morphs/RLY_<TAG>_<F|M>_<vertexcount>.tri
<output>/SKSE/Plugins/MPF/morphs/PhysiquesOfSkyrim_<TAG>.toml
```

### 5. Verification — same module, always runs

Rebuild the library the way the plugin does: key on `(sex, vertex count)`, pick
the best `CentredMeanDistance`, accept within 3.0. Then assert that **every
input shape matches a written set**. This is the invariant; a run that cannot
state it is a failed run, not a warning. The report also carries the median of
per-mesh wrap `p50` — 1 to 3 for hair, and above 15 means the reference head is
in the wrong frame and the morphs are nonsense.

## Surfaces

**CLI.** `--headparts` gains `--mode library|tri`, defaulting to `library`:

```
MPPatcher.exe --headparts --mode library --source <folder|zip> --output-dir <dir>
              --tag <SHORTNAME> [--part hair] [--default-sex male]
              [--unclassified-both] [--female-ref <tri>] [--male-ref <tri>]
              [--cluster-limit 1.5] [--dry-run]
```

`--mode tri` is today's behaviour, unchanged, including `--only-missing` and
`--filter-file`.

**GUI.** The Headparts target scans geometries rather than TRI paths. The
existing tree gains, per row, the shape's vertex count, its resolved sex, its
part type, and which rule resolved it — so an unresolved row is visible before
the run instead of appearing as a skip in the log. The misappropriated
`3BA` / `HIMBO` gender labels go away for this target. The per-line
`[STATUS] relpath` protocol the runner already parses stays as it is.

## Constraints

* Python 3.14, PySide6, numpy — all already in use. `fmp.wrap` additionally
  needs `scipy` for `cKDTree`; `requirements.txt` currently declares neither
  numpy nor scipy and must be corrected either way.
* MO2's usvfs hooks directory enumeration but not the stat family, so source
  and reference discovery must enumerate, never `os.path.exists` — the rule
  `core/config.py` already documents.
* The reference heads come from the active Data/VFS tree at
  `SKSE/Plugins/MPF/morphs`, as `discover_references` already finds them.
* `nifdump.exe` must be deployed beside `MPPatcher.exe` in `tools/MPPatcher/`,
  which is the copy MO2 actually launches.

## Decisions left to the builder

* Whether to keep `fmp.wrap`'s `scipy` dependency or replace `cKDTree` with a
  numpy spatial hash. Measure the PyInstaller size cost first; correctness is
  not in question either way.
* Module split inside `core/` — the five stages above are the seams that matter,
  the file names are not load-bearing.
* Whether the body weld (`--body-dump` / `--body-osd` in the authoring tool, for
  long hair lying on the neck and shoulders, where the head TRI has no surface)
  lands in this change or the next. It is plumbing, not a new idea, and the
  option is off by default.

## My calls, not the user's

Recorded so they are reviewable rather than invisible:

* Library mode as the default, TRI mode demoted to a flag.
* One wrap kernel with two presets, rather than porting the separate
  proximity-weighted kernel used for the Satisfactory Piercings patch. The
  measured standoff for on-skin parts is 0.03 units, where surface wrap already
  delivers the full, continuous field; the second kernel was written against
  nearest-vertex sampling, not against wrap.
* Hard failure when `nifdump` is missing, rather than degrading to TRI mode.
