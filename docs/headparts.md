# Headparts: what the numbers mean

Headparts mode is measured rather than assumed. Every line it prints exists
because a wrong assumption in that spot used to be silent — the hair simply
never moved, and the log said success. Read these before trusting a run.

## The one line that matters

```
Verified: 190 of 190 shipped shapes match a set the plugin would find
```

This is the invariant. After writing the library, MPPatcher rebuilds it the way
the plugin will — index by `(sex, vertex count)`, pick the best
`CentredMeanDistance`, accept within 3.0 — and checks that every shape it
shipped a set for can find that set. The check is written from the plugin's
rules rather than from the clustering that produced the library, so a bug shared
by both cannot verify itself.

Anything less than `N of N` is a failure, not a warning, and the run exits
non-zero with the shapes named.

## Distance

```
Distance: headpart-to-head p50, median 1.69, worst 7.00
```

How far each geometry sits from the reference head surface, as the median of the
per-mesh medians. Hair hugs the skull: expect **1 to 3**. Beards run a little
higher — Beards of Power measures 2.56. A median above 15 means the reference
head is in the wrong frame and the morphs are nonsense; the run says so
explicitly.

## Frames

```
Frames:   {'head': 69, 'origin': 3}
```

Headparts are authored either origin-centred or at head height, and the two
frames are about 120 units apart. Mods are **not self-consistent** — Beards of
Power comes out 54 head / 60 origin — so the frame is measured per geometry on a
256-vertex subsample rather than declared. The test is not close: for a normal
headpart the two candidates differ by 100× to 600×.

## Skipped

```
Skipped:  16 x detached from the head surface
```

A shape whose closest vertices are further from the head than the falloff radius
(14 units by default) receives a delta of zero everywhere by construction, so a
set for it would be a file of zeros — about 30 bytes per vertex of nothing. The
game behaves identically without one, so it is not written, and the reason is
reported.

Measured across 240 real shapes, this is cleanly separated: 234 contact the head
within 1.0 unit, 6 sit at 14.5 to 31.4, and **nothing falls in between**. The
six are long braids and hanging accessories. Raise `--falloff` or pass
`--falloff 0` if you want them shipped anyway.

Other skip reasons:

- `placeholder` — under 8 vertices; not a headpart.
- `non-finite vertices` — a NaN or infinity in the mesh. Rejected rather than
  carried, because `CentredMeanDistance` over a NaN loses every comparison and
  one poisoned shape would quietly stop every other shape sharing its vertex
  count from matching.
- `sex could not be determined …` — see below.

## Sex, and why it is never guessed

MPF indexes its library by `(sex, vertex count)`. A set filed under the wrong
sex is filed under a key no NPC will ever look up, and nothing reports it. So
the rules run in this order, and the run says which one answered:

| source | confidence | what it is |
|---|---|---|
| `plugin` | High | an `HDPT` record in the mod's own plugin declared it |
| `path` | Medium | `hair/female/` or `hair/male/` in the path |
| `filename` | Medium | `female`/`male` in the name — `femaledarkelfhair01` |
| `paired` | Medium | a trailing `F`/`M` **and** the opposite file exists |
| `default` | Low | you stated `--default-sex` for the whole mod |
| `unresolved` | Unknown | skipped, unless `--unclassified-both` |

The plugin is authoritative because filenames often carry nothing: KS Hairdos
names 3096 meshes `911.nif` and `acerola.nif`, and 3065 of them hint at no sex
at all. Measured across four installed mods, `HDPT` records decide 310 of 311
meshes. The one holdout, Cyberpunk 2077 Hairs' `rogueHaircap.nif`, has no record
because no headpart references it.

A record declared for *both* sexes is treated as unresolved, not as licence to
pick one.

## Two thresholds, deliberately different

`--cluster-limit` (1.5) decides when two shapes are one geometry.
`kSameMeshLimit` (3.0) is what the plugin accepts. Clustering right up to the
plugin's own threshold would leave a cluster member sitting 2.95 away from the
representative that has to match it at runtime — no headroom at all if the live
mesh differs from the file by even a little.

The headroom is visible in the verification: BG3's worst accepted match is
1.372 and ApachiiSkyHair's is 0.750, both against the 3.0 ceiling.

## Sets, and why there are fewer than shapes

```
BG3 hairs:  50 shapes -> 25 sets
Apachii:   190 shapes -> 72 sets
```

The unit of work is a distinct geometry, not a file: one hairstyle shipped under
several race paths needs one set. Several geometries may legitimately share a
vertex count — BG3's 25 sets occupy only 22 `(sex, count)` keys — which is safe
because the plugin stores a vector per key and picks the closest. They only need
distinct filenames, which is what the `_b` / `_c` suffixes are for.

## Output size

About 30 bytes per vertex: 12 for the base position and 6 for each of the three
morphs. That is the floor without a format change, and it is what makes a very
high-poly mod expensive. The generated TRIs declare `polyTris = 0` so the face
and UV blocks are absent — MPF keeps only the base vertices, to match, and the
deltas, to apply, and skips every other block by its declared size.

## Geometry comes from the NIFs

Never from the `.tri` a mod ships. Vanilla Hair Remake has a 212-byte,
four-vertex `hair01.tri` next to a 4014-vertex `hair01.nif`. MPF matches against
the live mesh, so a set built from that stub matches nothing, and nothing reports
an error.

`nifdump.exe` does the reading. It must sit beside `MPPatcher.exe`, or be named
with `--nifdump`, or be pointed at by `MPP_NIFDUMP`. If it cannot be found the
run stops — it does not fall back to the TRIs, because that fallback is exactly
the silent failure above.

## Dependencies, and what they cost

`fmp.wrap` uses `scipy.spatial.cKDTree`, so `scipy` is a real runtime
requirement of library mode and is declared in `requirements.txt`. It is
imported lazily, inside the wrap call, so the GUI's scan and the classification
path do not pay for it at import time.

It does cost download size. Measured 2026-11-06 with the same spec file:

| build | size |
|---|---|
| before, scipy not reachable | 62.3 MB |
| after, scipy bundled | 107.6 MB |
| delta | **+45.3 MB** |

That is a 73% increase for one function. `cKDTree` is used only in
`closest_surface_samples`, for the k-nearest query over the ~3100 head
vertices, so a numpy spatial hash could replace it and drop scipy entirely —
worth doing if the download size matters more than the risk of rewriting the
hot path of the whole transfer.

scipy also needs a hidden import to survive freezing. Its array-API shim
reaches its backends by name at import time, which PyInstaller's static
analysis cannot see, so `MPPatcher.spec` collects
`scipy._external.array_api_compat` explicitly. Without it the build is clean and
the exe dies at the first wrap with
`No module named 'scipy._external.array_api_compat.numpy.fft'` — a failure that
only appears on the user's machine. If scipy is ever replaced, that spec entry
goes with it.

## Native binaries, and the path trap

`nifdump.exe` is resolved from the program's **own** directory
(`sys.executable` when frozen), then its parent, then `MPP_NIFDUMP` — never
from `dirname(__file__)`, which inside a PyInstaller bundle is a temporary
extraction folder. The first frozen build of this feature reported nifdump
missing while it sat right beside `MPPatcher.exe`, and every unit test passed
with that bug in place. Deploy both native binaries beside the exe that MO2
actually launches, and confirm by running the packaged exe, not the source.
