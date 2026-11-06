# Headparts from NIFs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MPPatcher's Headparts target generate a working MPF morph library from a mod's NIFs, so hair, brows, beards and jewellery that ship no `.tri` are patched like everything else.

**Architecture:** Five seams, each its own module under `core/`: source discovery, geometry via `nifdump`, sex/part classification, library generation, and a verification pass that replays the plugin's own matching. The existing in-tree TRI transfer survives behind `--mode tri`. `fmp/` is reused unchanged — it already holds the TRI codec, the surface wrap, the BSA reader, the `HDPT` parser and the `nifdump` dump reader.

**Tech Stack:** Python 3.14, numpy, scipy (`cKDTree`, via `fmp.wrap`), PySide6 for the GUI layer, `unittest` for tests, `nifdump.exe` (nifly) as an external geometry reader.

**Spec:** `docs/superpowers/specs/2026-11-06-headparts-nif-pipeline-design.md`

## Global Constraints

- Morph names are exactly `RLY_Heavy`, `RLY_Muscular`, `RLY_Thin`, in that order, and are selected **by name**, never by index.
- Plugin acceptance threshold `kSameMeshLimit = 3.0`; clustering limit default `1.5`.
- Head-frame translation: `0,-1.633,120.555` (MPF head frame → headpart frame).
- Wrap presets: `standoff` = full delta to 4.0, zero at 14.0; `onskin` = no falloff.
- Placeholder floor: a shape under 8 vertices is not a headpart.
- Output layout: `<output>/SKSE/Plugins/MPF/morphs/` holding `RLY_<TAG>_<F|M>_<vertexcount>.tri` and `PhysiquesOfSkyrim_<TAG>.toml`.
- Generated TRIs declare `polyTris = 0`: base vertices and morphs only.
- MO2's usvfs hooks directory **enumeration** but not the stat family. Discovery code uses `os.listdir` / `os.walk` (or `core.config.vfs_*`), never `os.path.exists` / `isfile` on a possibly-virtual path.
- Never write into the source tree. Output is always a separate directory.
- Tests are `unittest`, under `tests/test_<module>.py`, with the `ROOT` `sys.path` preamble the existing tests use. Suite command: `python -m unittest discover -s tests`.

## Review Focus

Input classes the spec implies but no obvious task test covers; each has its test assigned below.

1. **A reference head whose morph list is out of order or carries a stale entry.** Measured: the shipped `FACE_FemaleHead.tri` holds `['OLD_Heavy', 'RLY_Muscular', 'RLY_Thin', 'RLY_Heavy']`. Selecting by position would write `OLD_Heavy`'s deltas under `RLY_Heavy`'s name. → Task 5, Step 1.
2. **A NIF that dumps zero shapes.** Plenty of headpart NIFs are just a skin-partition wrapper; `nifdump` reports that as a valid empty dump. It must be counted and reported, not crash the run. → Task 1, Step 6.
3. **Two distinct geometries sharing a vertex count.** The plugin stores a vector per key and picks the closest, so both must ship — with distinct filenames, because the manifest dedupes by filename. → Task 7, Step 1.
4. **A source tree with no plugin at all.** A loose-NIF drop with no ESP must still run on the path and filename rules and say how many shapes no plugin declared. → Task 3, Step 6.
5. **Non-finite vertices in a dumped shape.** A `NaN` in one vertex poisons `CentredMeanDistance` for its whole key, silently preventing every shape in that bucket from matching. Such a shape is rejected with a reason. → Task 4, Step 6.

---

### Task 1: nifdump resolution and dump reading

**Files:**
- Create: `core/nif_geometry.py`
- Test: `tests/test_nif_geometry.py`

**Interfaces:**
- Consumes: `fmp.nifdump.read_dump` (already present).
- Produces:
  - `class NifdumpMissing(RuntimeError)` — carries the searched paths in `str()`.
  - `find_nifdump(app_dir: str) -> str` — `""` when not found.
  - `@dataclass DumpedGeometry: relpath: str; shape: str; verts: np.ndarray; tris: np.ndarray`
  - `@dataclass DumpRun: dump_root: str; index_path: str; nifs: int; shapes: int; empty: list[str]; failures: list[str]`
  - `dump_tree(exe: str, source_root: str, work_dir: str, *, runner=None) -> DumpRun` — writes the dumps under `<work_dir>/dumps` and the index at `<work_dir>/nifdump-index.tsv`. `runner(argv) -> (returncode, stdout)`, defaulting to `subprocess.run`.
  - `read_geometries(run: DumpRun) -> list[DumpedGeometry]` — reads `run.index_path` plus each `.bin`, skipping index rows whose dump is unreadable and recording them in `run.failures`.

- [ ] **Step 1: Write the failing resolution tests**

```python
def test_prefers_the_binary_beside_the_exe_over_the_parent(self):
    # app_dir/nifdump.exe wins over dirname(app_dir)/nifdump.exe
def test_environment_override_wins_over_both(self):
    # MPP_NIFDUMP points somewhere else entirely
def test_returns_empty_string_when_absent(self):
    self.assertEqual(find_nifdump(empty_dir), "")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest tests.test_nif_geometry -v`
Expected: FAIL, `cannot import name 'find_nifdump'`

- [ ] **Step 3: Implement `find_nifdump(app_dir: str) -> str` in `core/nif_geometry.py`**

Mirror `Config.mppatcher_cli_exe`: `core.config.vfs_find` over `app_dir` then `os.path.dirname(app_dir)`, looking for `nifdump.exe` (and `nifdump` for a non-Windows dev box). `MPP_NIFDUMP` is checked first and returned as given.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m unittest tests.test_nif_geometry -v`
Expected: PASS

- [ ] **Step 5: Write the failing dump-reading tests**

Build a `NIFD0002` binary in the test with `struct` (the format is documented at the top of `fmp/nifdump.py`) and a two-row TSV index.

```python
def test_reads_vertices_and_triangles_for_each_indexed_shape(self):
    # two shapes across two relpaths; verts round-trip to the written floats
def test_dump_tree_raises_nifdump_missing_for_an_empty_exe_path(self):
    with self.assertRaises(NifdumpMissing):
        dump_tree("", source, work)
def test_dump_tree_reports_a_nonzero_exit_as_failure(self):
    # injected runner returns (2, "[FAIL] x.nif: cannot load")
```

- [ ] **Step 6: Write the Review Focus test — a NIF with no shapes**

```python
def test_a_dump_with_zero_shapes_is_reported_not_raised(self):
    # NIFD0002 + shapeCount 0, with no index row for it
    # read_geometries returns [] and the relpath appears in run.empty
```

- [ ] **Step 7: Run to verify both fail, then implement `dump_tree` and `read_geometries`**

`dump_tree` invokes `[exe, "--dir", source_root, "--out", dump_root, "--index", index_path, "--quiet"]`, parses the `nifdump: N NIFs, M shapes[, K with no usable geometry][, F FAILED]` tally from stdout, and keeps `[FAIL] ` lines as `failures`. `read_geometries` walks the index rows, skips `verts == 0`, and reads each `<dump_root>/<relpath>.bin` with `fmp.nifdump.read_dump`.

- [ ] **Step 8: Run the module's tests**

Run: `python -m unittest tests.test_nif_geometry -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add core/nif_geometry.py tests/test_nif_geometry.py
git commit -m "feat(headparts): resolve nifdump and read its geometry dumps"
```

---

### Task 2: Source discovery and BSA staging

**Files:**
- Create: `core/headpart_sources.py`
- Test: `tests/test_headpart_sources.py`

**Interfaces:**
- Consumes: `fmp.bsa.extract(archives: list[str], out_root: str, *, folder_contains="", suffix="") -> (int, list[str])`.
- Produces:
  - `@dataclass HeadpartSource: root: str; loose_nifs: list[str]; archives: list[str]; plugins: list[str]`
  - `discover(root: str) -> HeadpartSource` — relative paths for `loose_nifs`, absolute for `archives` and `plugins`.
  - `stage_archive_nifs(archives: list[str], work_dir: str, *, extract=None) -> tuple[list[str], list[str]]` — `(staged_roots, failures)`, one subfolder per archive named after its stem.

- [ ] **Step 1: Write the failing discovery tests**

```python
def test_finds_nifs_archives_and_plugins_case_insensitively(self):
    # Meshes/A.NIF, Thing.Bsa, Mod.ESP, Mod.esl, Mod.esm
def test_ignores_backup_and_generated_morph_folders(self):
    # backup/, SKSE/Plugins/MPF/morphs/ contribute nothing
def test_an_empty_tree_discovers_nothing_without_raising(self):
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest tests.test_headpart_sources -v`
Expected: FAIL, `No module named 'core.headpart_sources'`

- [ ] **Step 3: Implement `discover(root)` in `core/headpart_sources.py`**

`os.walk` only — no stat calls. Exclude any path containing `/backup` or `/mpf/morphs` after lowercasing and normalising separators, matching the existing exclusion in `core.headpart_patcher.scan_headparts`.

- [ ] **Step 4: Run to verify it passes, then write the failing staging tests**

```python
def test_each_archive_is_staged_into_its_own_subfolder(self):
    # injected extract records its out_root per archive; two archives -> two roots
def test_an_extract_failure_is_collected_not_raised(self):
    # injected extract raises for one archive; the other still stages
```

- [ ] **Step 5: Implement `stage_archive_nifs` and run the module's tests**

Default `extract` is `fmp.bsa.extract` with `suffix=".nif"`.

Run: `python -m unittest tests.test_headpart_sources -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add core/headpart_sources.py tests/test_headpart_sources.py
git commit -m "feat(headparts): discover loose NIFs, BSAs and plugins in a source tree"
```

---

### Task 3: Sex and part classification

**Files:**
- Create: `core/headpart_classify.py`
- Test: `tests/test_headpart_classify.py`

**Interfaces:**
- Consumes: `fmp.esp.head_parts_by_model(paths) -> dict[str, list[HeadPart]]`, `fmp.esp.model_key`.
- Produces:
  - `@dataclass Classification: sex: str | None; sex_source: str; part: str; part_source: str`
  - `SEX_SOURCES = ("plugin", "path", "filename", "paired", "default", "unresolved")`
  - `collect_stems(relpaths: list[str]) -> frozenset[str]` — lowercased basenames without extension.
  - `mesh_key(relpath: str) -> str` — tail after the last `meshes/`, lowercased, forward slashes.
  - `classify(relpath: str, *, hdpt=None, stems=frozenset(), default_sex=None, default_part="hair") -> Classification`

- [ ] **Step 1: Write the failing plugin-authority tests**

```python
def test_a_single_sex_hdpt_record_decides_and_is_credited(self):
    # HeadPart(female=True) for this model -> ("female", "plugin")
def test_a_record_declared_for_both_sexes_is_unresolved_not_guessed(self):
    # male=True and female=True -> (None, "unresolved")
def test_the_part_type_comes_from_the_record(self):
    # PNAM 6 -> part "eyebrows", part_source "plugin"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest tests.test_headpart_classify -v`
Expected: FAIL, `No module named 'core.headpart_classify'`

- [ ] **Step 3: Write the failing path and filename tests**

```python
def test_female_in_the_path_beats_the_male_substring_it_contains(self):
    # "hair/female/x.nif" -> "female", and "femaledarkelfhair01.nif" -> "female"
def test_a_space_separated_pair_resolves_when_the_opposite_file_exists(self):
    # "gortash_hair F.nif" with stem "gortash_hair m" present -> ("female", "paired")
def test_a_lone_trailing_letter_is_not_evidence(self):
    # "gortash_hair F.nif" with no M counterpart -> (None, "unresolved")
def test_default_sex_applies_only_when_nothing_else_answered(self):
    # default_sex="male" -> ("male", "default"); never overrides a path hit
```

- [ ] **Step 4: Implement `classify`, `collect_stems` and `mesh_key`**

Port `classify_sex`, `classify_sex_paired` and `mesh_key` from `FaceMorphsPatcher/tools/gen_headpart_morphs.py`, extending the trailing-letter rule to accept a space or underscore before the letter so `gortash_hair F` pairs with `gortash_hair M`. Order of rules is the Global Constraints order; each returns its own `sex_source`.

- [ ] **Step 5: Run to verify it passes**

Run: `python -m unittest tests.test_headpart_classify -v`
Expected: PASS

- [ ] **Step 6: Write the Review Focus test — no plugin at all**

```python
def test_classification_works_with_no_hdpt_index(self):
    # hdpt=None, path rules still answer, sex_source is never "plugin"
```

- [ ] **Step 7: Run the module's tests and commit**

```bash
git add core/headpart_classify.py tests/test_headpart_classify.py
git commit -m "feat(headparts): resolve sex and part type, plugin records first"
```

---

### Task 4: The plugin's metric, clustering and verification

**Files:**
- Create: `core/headpart_library.py`
- Test: `tests/test_headpart_library.py`

**Interfaces:**
- Produces:
  - `MORPHS = ("RLY_Heavy", "RLY_Muscular", "RLY_Thin")`
  - `SAME_MESH_LIMIT = 3.0`, `DEFAULT_CLUSTER_LIMIT = 1.5`, `MIN_VERTS = 8`
  - `@dataclass ShapeInput: relpath: str; shape: str; sex: str | None; sex_source: str; part: str; verts: np.ndarray; tris: np.ndarray` — `sex` is `None` for an unresolved shape, which `build_library` skips or duplicates per `LibraryOptions.unclassified_both`. Only resolved shapes reach `cluster_geometries`.
  - `@dataclass GeometryCluster: sex: str; part: str; verts: np.ndarray; tris: np.ndarray; members: list[str]; representative: str`
  - `centred_mean_distance(a: np.ndarray, b: np.ndarray) -> float`
  - `cluster_geometries(shapes: list[ShapeInput], limit: float = DEFAULT_CLUSTER_LIMIT) -> list[GeometryCluster]`
  - `@dataclass VerifyResult: matched: int; unmatched: list[str]; worst_distance: float`
  - `verify_against_clusters(shapes, clusters, accept: float = SAME_MESH_LIMIT) -> VerifyResult`
  - `reject_reason(shape: ShapeInput) -> str | None` — `"placeholder"` under `MIN_VERTS`, `"non-finite vertices"`, else `None`.

- [ ] **Step 1: Write the failing metric and clustering tests**

```python
def test_the_metric_ignores_a_pure_translation(self):
    self.assertAlmostEqual(centred_mean_distance(v, v + 50.0), 0.0)
def test_identical_shapes_collapse_to_one_cluster_listing_both_members(self):
def test_shapes_further_apart_than_the_limit_stay_separate(self):
def test_shapes_of_different_vertex_counts_never_cluster(self):
def test_clusters_never_mix_sexes(self):
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest tests.test_headpart_library -v`
Expected: FAIL, `No module named 'core.headpart_library'`

- [ ] **Step 3: Implement the metric and `cluster_geometries`**

Bucket by `(sex, len(verts))`, then greedy representatives as in `gen_headpart_morphs.cluster_geometries`. A cluster inherits the representative's `part`.

- [ ] **Step 4: Run to verify it passes, then write the failing verification tests**

```python
def test_every_input_shape_matches_a_cluster_it_produced(self):
    # matched == len(shapes), unmatched == []
def test_a_shape_whose_cluster_was_dropped_is_reported_unmatched(self):
    # verify against clusters[1:] -> that shape's relpath in unmatched
def test_the_worst_distance_is_reported_even_when_everything_matched(self):
```

- [ ] **Step 5: Implement `verify_against_clusters`**

Replay the plugin: index clusters by `(sex, vertex count)`, take the minimum `centred_mean_distance` within the key, accept under `accept`.

- [ ] **Step 6: Write the Review Focus test — non-finite vertices**

```python
def test_a_shape_with_a_nan_vertex_is_rejected_with_a_reason(self):
    self.assertEqual(reject_reason(shape_with_nan), "non-finite vertices")
def test_a_shape_under_the_vertex_floor_is_rejected_as_a_placeholder(self):
```

- [ ] **Step 7: Run the module's tests and commit**

```bash
git add core/headpart_library.py tests/test_headpart_library.py
git commit -m "feat(headparts): cluster geometries and verify the way the plugin matches"
```

---

### Task 5: Head references and frame choice

**Files:**
- Modify: `core/headpart_library.py`
- Test: `tests/test_headpart_library.py`

**Interfaces:**
- Consumes: `fmp.tri.read_tri`, `fmp.morphs.find_morph`, `fmp.wrap.closest_surface_samples`.
- Produces:
  - `HEAD_OFFSET = np.array([0.0, -1.633, 120.555])`
  - `@dataclass HeadReference: sex: str; verts: np.ndarray; faces: np.ndarray; deltas: dict[str, np.ndarray]; surface_head: np.ndarray; surface_origin: np.ndarray`
  - `load_head_reference(path: str, sex: str) -> HeadReference` — raises `ValueError` naming the missing morphs, or naming the file when it carries zero faces.
  - `@dataclass FrameChoice: surface: np.ndarray; name: str; median_distance: float`
  - `pick_frame(ref: HeadReference, verts: np.ndarray, *, sample: int = 256) -> FrameChoice`

- [ ] **Step 1: Write the failing reference tests, including Review Focus 1**

```python
def test_morphs_are_selected_by_name_not_by_position(self):
    # reference TRI written with morphs ordered
    # ["OLD_Heavy", "RLY_Muscular", "RLY_Thin", "RLY_Heavy"], where OLD_Heavy's
    # deltas are all 9.0 and RLY_Heavy's are all 1.0
    self.assertEqual(ref.deltas["RLY_Heavy"].max(), 1.0)
def test_a_reference_missing_a_morph_raises_naming_it(self):
    with self.assertRaisesRegex(ValueError, "RLY_Muscular"):
def test_a_reference_with_no_faces_raises(self):
    # polyTris 0 -> a surface wrap has nothing to sample
```

- [ ] **Step 2: Run to verify it fails, then implement `load_head_reference`**

Run: `python -m unittest tests.test_headpart_library -v`
Expected: FAIL, then PASS after implementing.

- [ ] **Step 3: Write the failing frame tests**

```python
def test_a_head_height_shape_picks_the_head_frame(self):
    # verts near the reference as authored -> name "head", median under 1.0
def test_an_origin_centred_shape_picks_the_origin_frame(self):
    # the same verts minus HEAD_OFFSET -> name "origin"
def test_the_sample_cap_does_not_change_the_choice(self):
    # sample=4 on a 500-vertex shape picks the same frame as sample=256
```

- [ ] **Step 4: Implement `pick_frame` and run the module's tests**

Subsample with `np.linspace` over the vertex indices, query both surfaces with `closest_surface_samples(..., k=4)`, take the lower median distance.

Run: `python -m unittest tests.test_headpart_library -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/headpart_library.py tests/test_headpart_library.py
git commit -m "feat(headparts): load head references by morph name and measure the frame"
```

---

### Task 6: Wrap presets and set building

**Files:**
- Modify: `core/headpart_library.py`
- Test: `tests/test_headpart_library.py`

**Interfaces:**
- Consumes: `fmp.wrap.wrap_deltas`, `fmp.morphs.make_morph`, `fmp.tri.TriFile`, `fmp.tri.write_tri`, `fmp.tri.read_tri`.
- Produces:
  - `@dataclass WrapPreset: name: str; falloff: float | None; falloff_inner: float; smooth: int`
  - `DEFAULT_PRESET = WrapPreset("default", falloff=14.0, falloff_inner=4.0, smooth=0)` — one preset for every part. See the Task 3/6 ruling in the ledger: measurement showed a second on-skin preset makes a bit-identical difference for brows and 0.0093 units for hair, and selecting it from the `HDPT` part type is unsafe on real data.
  - `minimal_tri(verts: np.ndarray) -> TriFile`
  - `@dataclass BuiltSet: data: bytes; metrics: dict[str, float]`
  - `build_set(ref: HeadReference, cluster: GeometryCluster, *, preset: WrapPreset, frame: FrameChoice) -> BuiltSet` — raises `ValueError` if the written bytes do not read back with the right vertex count and exactly `MORPHS` in order.

- [ ] **Step 1: Write the failing preset and codec tests**

```python
def test_the_default_preset_carries_the_measured_falloff_numbers(self):
    # falloff 14.0, falloff_inner 4.0, smooth 0
def test_a_minimal_tri_round_trips_with_no_faces(self):
    # write_tri -> read_tri; vertex_count preserved, len(faces) == 0
```

- [ ] **Step 2: Run to verify it fails, then implement `DEFAULT_PRESET` and `minimal_tri`**

- [ ] **Step 3: Write the failing `build_set` tests**

```python
def test_a_built_set_carries_exactly_the_three_morphs_in_order(self):
    self.assertEqual(read_tri(built.data).morph_names(), list(MORPHS))
def test_a_vertex_lying_on_the_surface_keeps_the_full_delta(self):
    # vertex 0.03 off a reference whose RLY_Heavy is a uniform 1.0 -> delta ~1.0
def test_a_vertex_beyond_the_falloff_is_zeroed(self):
    # vertex 20 units out -> delta magnitude 0.0
def test_a_vertex_count_mismatch_in_the_readback_raises(self):
```

- [ ] **Step 4: Implement `build_set` and run the module's tests**

One `wrap_deltas` call per morph against `frame.surface`; collect `wrap_dist_p50` / `wrap_dist_max` / `delta_max` from `RLY_Heavy` into `metrics`.

Run: `python -m unittest tests.test_headpart_library -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/headpart_library.py tests/test_headpart_library.py
git commit -m "feat(headparts): sample deltas by surface wrap with per-part presets"
```

---

### Task 7: Manifest, filenames and the run orchestrator

**Files:**
- Modify: `core/headpart_library.py`
- Test: `tests/test_headpart_library.py`

**Interfaces:**
- Consumes: `fmp.report.RunReport`, `fmp.report.FileResult`.
- Produces:
  - `MORPHS_SUBDIR = os.path.join("SKSE", "Plugins", "MPF", "morphs")`
  - `set_filename(tag: str, sex: str, vertex_count: int, used: set[str]) -> str`
  - `write_manifest(path: str, rows: list[tuple[str, str, str]], *, tag: str) -> None` — rows are `(file, sex, part)`.
  - `@dataclass LibraryOptions: tag: str; default_part: str = "hair"; default_sex: str | None = None; unclassified_both: bool = False; cluster_limit: float = DEFAULT_CLUSTER_LIMIT; dry_run: bool = False`
  - `@dataclass LibrarySummary: sets: int; shapes: int; verified: int; unmatched: list[str]; median_wrap_p50: float; worst_wrap_p50: float; frames: dict[str, int]; bytes_written: int; manifest: str | None`
  - `build_library(shapes: list[ShapeInput], refs: dict[str, HeadReference], output_dir: str, options: LibraryOptions) -> tuple[RunReport, LibrarySummary]` — report `tool="headparts-library"`, one `FileResult` per input shape (`relpath` is `"<relpath>#<shape>"`), every run-wide number on the summary.

- [ ] **Step 1: Write the failing filename and Review Focus 3 tests**

```python
def test_a_set_filename_encodes_tag_sex_and_vertex_count(self):
    self.assertEqual(set_filename("BG3", "female", 4014, set()), "RLY_BG3_F_4014.tri")
def test_two_geometries_sharing_a_vertex_count_both_ship_under_distinct_names(self):
    # second call with the first name in `used` -> "RLY_BG3_F_4014_b.tri"
    # and build_library writes both files and both manifest rows
```

- [ ] **Step 2: Run to verify it fails, then implement `set_filename` and `write_manifest`**

Manifest text: a comment block naming the tag and the generator, then one `[[sets]]` block per row with `file`, `sex` and `part` keys, matching the format in `SKSE/Plugins/MPF/morphs/README.txt`.

- [ ] **Step 3: Write the failing orchestration tests**

```python
def test_build_library_writes_tris_and_one_manifest_under_the_morphs_subdir(self):
def test_a_dry_run_writes_nothing_but_still_reports_the_set_count(self):
def test_an_unclassified_shape_is_skipped_with_a_warning_by_default(self):
def test_unclassified_both_ships_the_shape_for_both_sexes(self):
def test_a_run_whose_verification_leaves_a_shape_unmatched_fails_the_report(self):
    # status "failed" on that shape, not a warning
```

- [ ] **Step 4: Implement `build_library` and run the module's tests**

Order: reject (`reject_reason`), group by sex, cluster, pick frame per cluster, build each set, write, then `verify_against_clusters` over every accepted input shape. Verification failure marks the affected shapes `failed`.

Run: `python -m unittest tests.test_headpart_library -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/headpart_library.py tests/test_headpart_library.py
git commit -m "feat(headparts): write the MPF library and its manifest, verified"
```

---

### Task 8: CLI mode switch

**Files:**
- Modify: `mppatcher.py:6-43`
- Test: `tests/test_headparts_cli.py`

**Interfaces:**
- Consumes: Tasks 1-7.
- Produces:
  - `build_headparts_parser() -> argparse.ArgumentParser` in `mppatcher.py`, extracted so the arguments are testable without running a patch.
  - `_run_headparts(argv: list[str]) -> int` keeps its signature and its `[{STATUS:7}] {relpath}` output protocol.

- [ ] **Step 1: Write the failing parser tests**

```python
def test_library_is_the_default_mode(self):
    self.assertEqual(parse(["--headparts", "--source", "s", "--output-dir", "o"]).mode, "library")
def test_tri_mode_still_accepts_only_missing_and_filter_file(self):
def test_library_mode_accepts_tag_part_default_sex_and_cluster_limit(self):
def test_an_unknown_mode_is_rejected(self):
```

- [ ] **Step 2: Run to verify it fails, then extract `build_headparts_parser` and add the flags**

`--mode library|tri` (default `library`), `--tag`, `--part`, `--default-sex`, `--unclassified-both`, `--cluster-limit`, `--dry-run`, `--nifdump`. `--tag` defaults to the source folder's basename, sanitised to `[A-Za-z0-9]`.

- [ ] **Step 3: Write the failing wiring test**

```python
def test_library_mode_stops_with_an_actionable_error_when_nifdump_is_absent(self):
    # returns 2 and the message names the directories searched
```

- [ ] **Step 4: Implement the library branch of `_run_headparts` and run the tests**

Stage the source (`StagedSource`), `discover`, `stage_archive_nifs`, `dump_tree`, `read_geometries`, `collect_stems`, `classify` per shape, `load_head_reference` per sex, `build_library`. Print one `[{STATUS}] {relpath}` per `FileResult`, then the `LibrarySummary` numbers (sets, verified/shapes, median and worst wrap `p50`, frame split), then the same `Headparts: N patched, …` tally the GUI already parses. Return 1 when `summary.unmatched` is non-empty or any `FileResult` failed.

Run: `python -m unittest tests.test_headparts_cli -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mppatcher.py tests/test_headparts_cli.py
git commit -m "feat(headparts): add --mode library to the headparts CLI"
```

---

### Task 9: Fix sex detection in the surviving TRI mode

**Files:**
- Modify: `core/headpart_patcher.py:57-77`
- Test: `tests/test_headpart_patcher.py`

**Interfaces:**
- Consumes: `core.headpart_classify.classify`, `collect_stems`.
- Produces: `detect_headpart_sex(path, *, stems=frozenset())` keeps returning `"female"` / `"male"` / `"unknown"`; `patch_headparts` passes the stems of its own scan.

- [ ] **Step 1: Write the failing test**

```python
def test_a_space_separated_pair_is_no_longer_unknown(self):
    # "gortash_hair F.tri" with "gortash_hair m" in stems -> "female"
def test_a_lone_space_separated_letter_stays_unknown(self):
def test_beard_still_reads_as_male(self):
```

- [ ] **Step 2: Run to verify it fails, then delegate `detect_headpart_sex` to `classify`**

Keep the beard/`/beards/` rule, which `classify` does not have, as an explicit pre-check.

- [ ] **Step 3: Run the suite and commit**

Run: `python -m unittest discover -s tests`
Expected: PASS

```bash
git add core/headpart_patcher.py tests/test_headpart_patcher.py
git commit -m "fix(headparts): resolve space-separated sex pairs in TRI mode"
```

---

### Task 10: GUI wiring

**Files:**
- Modify: `ui/main_window_ui.py:3253-3280` (headparts branch of `_reload_groups`), `:4381-4438` (`_on_headparts_patch`), `:1217-1221` (target hint text)
- Test: `tests/test_state_model.py`

**Interfaces:**
- Consumes: Tasks 1-8.
- Produces: no new public API; the headparts branch builds its `OutfitInfo` rows from `core.headpart_sources` + `core.headpart_classify` instead of `scan_headparts`.

- [ ] **Step 1: Write the failing scan-model test**

```python
def test_headpart_rows_carry_the_resolved_sex_and_part_not_a_body_family(self):
    # gender is "Female"/"Male"/"Unknown", never "3BA" or "HIMBO"
def test_an_unresolved_headpart_row_carries_a_warning_before_the_run(self):
```

- [ ] **Step 2: Run to verify it fails, then rewrite the headparts branch of `_reload_groups`**

Group name `"Headparts (NIF)"`; one member per `relpath#shape`; `confidence` from `sex_source` (`plugin` → `High`, `path`/`filename`/`paired` → `Medium`, `default` → `Low`, `unresolved` → `Unknown`); `mesh_type` from the part.

- [ ] **Step 3: Update `_on_headparts_patch` to pass the library flags**

Add `--mode library`, `--tag` from the source basename, and keep `--filter-file` carrying the checked members.

- [ ] **Step 4: Update the target hint at `:1219` to say the NIF-based behaviour, run the suite, commit**

Run: `python -m unittest discover -s tests`
Expected: PASS

```bash
git add ui/main_window_ui.py tests/test_state_model.py
git commit -m "feat(headparts): scan NIF geometries in the GUI headparts target"
```

---

### Task 11: Declared dependencies, docs and deployment

**Files:**
- Modify: `requirements.txt`, `README.md:145-160`, `PUBLIC_PIPELINE.md:22-31`, `MPPatcher.spec`
- Create: `docs/headparts.md`

**Interfaces:** none.

- [ ] **Step 1: Declare the real dependencies**

`requirements.txt` gains `numpy>=2.0` and `scipy>=1.11`. Measure the frozen size first: build once with and once without `scipy`, and record both numbers in `docs/headparts.md`. If the delta is above 80 MB, replace `cKDTree` in `fmp/wrap.py` with a numpy spatial hash in a follow-up task rather than here.

- [ ] **Step 2: Rewrite the Headparts sections of `README.md` and `PUBLIC_PIPELINE.md`**

Both currently describe TRI-only behaviour. State: library mode is the default and produces a `SKSE/Plugins/MPF/morphs` tree; TRI mode is the `Backend=racemenu` fallback; `nifdump.exe` must sit beside `MPPatcher.exe`.

- [ ] **Step 3: Write `docs/headparts.md`**

The operator guide: what each reported number means (`headpart-to-scalp median p50` 1 to 3, above 15 means a wrong frame), the two thresholds and why they differ, and the verification invariant.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt README.md PUBLIC_PIPELINE.md MPPatcher.spec docs/headparts.md
git commit -m "docs(headparts): document library mode and declare numpy/scipy"
```

---

### Task 12: Real-data verification

**Files:**
- Create: `docs/superpowers/specs/2026-11-06-headparts-verification.md`

**Interfaces:** none. This task runs the shipped code and records what it did.

- [ ] **Step 1: Run library mode against the NIF-only mod**

```bash
python mppatcher.py --headparts --mode library --tag EMB \
  --source "E:/Wabbajack/InstallPacks/mods/Even More Brows - HPH - COtR" \
  --output-dir "<scratch>/emb" --part eyebrows
```

Expected: 2 sets (one per sex), 141 vertices each, wrap `p50` under 0.1, verification `2/2`.

- [ ] **Step 2: Run it against the mod the old path covered at 20%**

```bash
python mppatcher.py --headparts --mode library --tag BG3 \
  --source "E:/Wabbajack/InstallPacks/mods/BG3 hairs 1.1" --output-dir "<scratch>/bg3"
```

Expected: all 50 shapes classified from the plugin's 54 `HDPT` records, 27 female and 27 male, zero unresolved, verification covering every shape.

- [ ] **Step 3: Run it against the BSA-only mod**

```bash
python mppatcher.py --headparts --mode library --tag BOP --part facialhair \
  --default-sex male \
  --source "E:/Wabbajack/InstallPacks/mods/Beards of Power" --output-dir "<scratch>/bop"
```

Expected: NIFs staged out of the 911 MB archive, and the documented frame split (shapes at head height and at the origin both present).

- [ ] **Step 4: Record all three runs in the verification doc, with the actual numbers, and commit**

If any run leaves a shape unmatched, that is a bug in Tasks 4-7, not a number to write down.

```bash
git add docs/superpowers/specs/2026-11-06-headparts-verification.md
git commit -m "docs(headparts): record the measured verification runs"
```
