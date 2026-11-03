# MPPatcher

MPPatcher is an open-source helper for Skyrim BodySlide and Outfit Studio
workflows. It scans BodySlide projects, helps you review outfit compatibility,
and creates a separate mod-ready output with the selected morph patches.

It also includes a Headparts mode for transferring Physiques of Skyrim morphs
to `.tri` files used by hair, brows, beards, and other headparts.

> **Project status:** the Python GUI and orchestration layer are open source.
> The native BodySlide patching CLI and the optional Drape runtime are separate
> components and are discovered from the BodySlide installation at runtime.

## Features

- BodySlide outfit scanning with body-family, gender, and body-part detection.
- Reviewable selection flow with explicit confirmation for uncertain projects.
- Optional Auto-Zap clipping slider generation.
- Optional Drape Smart refit (ALPHA; slower and not guaranteed to improve every mesh).
- Headparts/TRI morph transfer for Physiques of Skyrim.
- Output as an isolated folder or a mod-ready ZIP archive.
- Backups and source-preserving output workflow.

## Requirements

- Windows 10 or newer
- Python 3.11 or newer
- [PySide6](https://pypi.org/project/PySide6/)
- BodySlide/Outfit Studio for outfit patching
- Mod Organizer 2 when using the virtual file system workflow
- The matching native `mppatcher-cli` component for the outfit pipeline

## Install from source

Clone the repository, create a virtual environment, and install the Python
dependency:

```powershell
git clone https://github.com/drpavlov95/MPPatcher.git
cd MPPatcher
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run the GUI from the repository root:

```powershell
python mppatcher.py
```

For normal outfit patching, install the native CLI where BodySlide can discover
it. For Smart refit, place the bundled `MPPatcher-Drape` runtime beside the
MPPatcher executable, or set `MPP_DRAPE_ROOT` and `MPP_DRAPE_PYTHON` for a
development checkout.

## Basic workflow

1. Start MPPatcher from the same BodySlide/MO2 environment used by the source
   projects.
2. Select **Outfits** and choose the BodySlide source tree.
3. Review the detected groups and outfit statuses.
4. Resolve any rows marked for confirmation, then select the outfits to patch.
5. Enable Auto-Zap, Revealing, or Smart refit only where needed.
6. Choose a new output folder or ZIP file and start the patch.
7. Install or test the generated output in a separate MO2 profile before using
   it in a live load order.

MPPatcher does not overwrite the source `.osp`, NIF, or OSD files during the
normal public workflow. The generated output is intended to be installed as a
separate mod.

## Understanding the controls

### Review columns

The review table is deliberately conservative. If MPPatcher cannot identify a
supported body reference, gender, project, or mesh part, it marks the outfit
for review instead of silently guessing. The supported body references are
currently **3BA** and **HIMBO**; the supported mesh parts are **Body**,
**Hands**, and **Feet**.

- **Body Reference** identifies which MPP reference should drive the morph
  generation. Check this when a project uses unusual naming or contains more
  than one body family.
- **Mesh Part** selects the reference geometry for the outfit. MPPatcher can
  detect Body, Hands, and Feet automatically, but you can correct an uncertain
  result during review.
- **Output Reference** controls the embedded reference in the generated
  project:
  - **Leave as Original** keeps the source project's reference state and is the
    normal choice.
  - **Remove Reference** creates output without embedded body geometry when the
    source contains a reference.
  - **Force Reference** embeds the matching MPP reference when the source is
    reference-less.
- **Revealing** chooses between two body morph styles for each outfit:
  - **Covered** is smoother and is recommended for clothing or armor that hides
    most of the body. It reduces the chance of visible conforming artifacts
    underneath the outfit.
  - **Revealing** uses the detailed body morph and preserves features such as
    skin folds, navel definition, and cellulite. Enable it for outfits with
    substantial exposed skin. It is independent of Auto-Zap.
- **Smart refit** replaces the normal conform for that outfit with Drape's
  distance-weighted refit, solving the outfit as a whole against the morphed
  body. It is most useful for loose or difficult garments such as long skirts,
  robes, and capes. It runs once per RM slider and can take roughly a minute
  per slider, so enable it only for the outfits that need it. Smart refit is
  **ALPHA** and can produce a worse result on some meshes; always inspect the
  output before deploying it.

### Patching options

- **Auto-Zap** generates a `Fix Clipping` Zap slider in the output. The slider
  is intended to hide body geometry that would intersect the outfit. Auto-Zap
  changes the generated output only; it does not edit the source project. It
  is useful as a first pass for clipping, but the result should still be
  checked in BodySlide or in-game.
- **Reduce clipping** runs BodySlide/Outfit Studio's native Fix Clipping pass
  on the generated RM sliders. It is a different operation from Auto-Zap:
  Auto-Zap hides selected body geometry, while Fix Clipping adjusts the
  generated morph data. It can reduce intersections but may deform the mesh.
- **Fix Clipping strength** controls the native pass from `0.00` to `1.00`.
  The default is `0.50`; higher values are more aggressive and have a greater
  chance of visible deformation. Leave Reduce clipping disabled when preserving
  the original outfit shape is more important than removing every intersection.

### Output mode

The **Output** control has two modes:

- **Loose files** writes a normal mod folder. Use this when you want to inspect
  the generated files, compare iterations, or point an MO2 mod directly at the
  output directory.
- **ZIP archive** creates a ready-to-install mod archive with the correct
  relative paths. Use this for sharing, archiving, or installing the result as
  a new MO2 mod.

Always choose an output location separate from the source BodySlide data. A
good first run is one outfit, **Loose files**, Auto-Zap enabled, Smart refit
disabled, and Reduce clipping disabled. After checking the result, enable the
more expensive or more aggressive options only for the outfits that benefit
from them.

## Headparts mode

The GUI can patch a folder or ZIP containing headpart `.tri` files. It finds
the female and male Physiques of Skyrim references from the active Data/VFS
tree and transfers `RLY_Heavy`, `RLY_Muscular`, and `RLY_Thin`.

The same operation is available from the command line:

```powershell
python mppatcher.py --headparts `
  --source "C:\path\to\headparts.zip" `
  --output-dir "C:\path\to\patched-headparts"
```

Useful options include `--only-missing`, `--female-ref`, `--male-ref`, and
`--filter-file`.

## Development

Run the test suite with:

```powershell
python -m unittest discover -s tests -v
```

The included `MPPatcher.spec` can be used with PyInstaller. Build artifacts
and local diagnostic data are intentionally ignored by Git; releases should
be distributed separately from the source checkout.

## Troubleshooting

- **No BodySlide projects found:** verify the BodySlide root and launch context,
  especially when using MO2's virtual file system.
- **Native CLI not found:** confirm that the matching `mppatcher-cli` executable
  is installed beside the BodySlide files visible to MPPatcher.
- **Smart refit unavailable:** install the `MPPatcher-Drape` runtime or set both
  `MPP_DRAPE_ROOT` and `MPP_DRAPE_PYTHON` in the development environment.
- **Unexpected output:** test one outfit first and inspect the generated folder
  before deploying the full batch.

## License

MPPatcher is released under the [MIT License](LICENSE).
