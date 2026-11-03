# MPPatcher

MPPatcher is an open-source helper for Skyrim BodySlide/Outfit Studio
workflows. It provides a Qt desktop interface for scanning outfits, selecting
patch targets, creating safe output folders or ZIP archives, and transferring
Physiques of Skyrim morphs to TRI headparts.

The Python application is the user interface and orchestration layer. The
native BodySlide patching CLI and optional Drape runtime are separate
components and are discovered from the BodySlide installation at runtime.

## Requirements

- Windows 10 or newer
- Python 3.11+
- PySide6
- BodySlide/Outfit Studio and Mod Organizer 2 for the integrated workflow

Install the Python dependency with:

```powershell
python -m pip install -r requirements.txt
```

Run the application from the repository root:

```powershell
python mppatcher.py
```

Run the test suite:

```powershell
python -m unittest discover -s tests -v
```

## Packaging

The included `MPPatcher.spec` can be used with PyInstaller. Build artifacts
are intentionally ignored by Git; releases should be published separately
from the source repository.

## Scope and safety

MPPatcher writes to a user-selected output location and keeps source projects
read-only during the normal patch workflow. Always test generated output in a
separate MO2 profile before deploying it to a live setup.

## License

MPPatcher is released under the MIT License. See [LICENSE](LICENSE).
