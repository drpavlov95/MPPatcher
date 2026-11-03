"""Opt-in diagnostics that avoid leaking environment values or writing probes."""
import os
import sys
from datetime import datetime


def _safe_listdir(path: str, limit: int = 40) -> list[str]:
    try:
        return os.listdir(path)[:limit]
    except Exception as exc:
        return [f"<error: {exc}>"]


def write_diagnostics(config, *, run_write_test: bool = False) -> tuple[str, str]:
    """Write a diagnostics report and return ``(path, report)``.

    Environment values are always redacted. The optional VFS write probe is
    disabled by default because diagnostics should not mutate the installation.
    """
    out_path = os.path.join(os.path.expanduser("~"), "mppatcher_debug.txt")
    app_dir = config.app_dir
    sg_folder = config.slider_groups_folder
    lines = [
        f"=== MPPatcher diagnostics {datetime.now().isoformat()} ===",
        f"sys.frozen          : {getattr(sys, 'frozen', False)}",
        f"sys.executable      : {sys.executable}",
        f"_MEIPASS            : {getattr(sys, '_MEIPASS', '(none)')}",
        f"os.getcwd()         : {os.getcwd()}",
        f"config.app_dir      : {app_dir}",
        f"  isdir             : {os.path.isdir(app_dir)}",
        f"config.slidergroups : {sg_folder}",
        f"  isdir             : {os.path.isdir(sg_folder) if sg_folder else 'N/A'}",
        f"config.os_exe       : {config.outfit_studio_exe or '(not found)'}",
        "",
        f"--- os.listdir(app_dir) [{app_dir}] ---",
    ]
    lines += [f"  {entry}" for entry in _safe_listdir(app_dir)]
    lines += ["", f"--- os.listdir(SliderGroups) [{sg_folder}] ---"]
    lines += [f"  {entry}" for entry in _safe_listdir(sg_folder)]
    lines += ["", "--- os.listdir(SliderSets) ---"]
    lines += [f"  {entry}" for entry in _safe_listdir(os.path.join(app_dir, "SliderSets"))]
    lines += ["", "--- environment variable names (values redacted) ---"]
    keywords = ("mo2", "mod", "organizer", "usvfs", "vfs", "instance", "game", "skyrim")
    lines += [f"  {key} = <redacted>" for key in sorted(os.environ)
              if any(word in key.lower() for word in keywords)]
    lines += ["", "--- ALL environment variable NAMES ---",
              "  " + ", ".join(sorted(os.environ)), ""]
    lines.append("--- VFS write test: not run (explicit opt-in required) ---")
    if run_write_test:
        auto = os.path.join(app_dir, "Automations")
        test_file = os.path.join(auto, "_mpp_write_test.xml")
        try:
            with open(test_file, "w", encoding="utf-8") as handle:
                handle.write("<test/>")
            lines.append(f"  WRITE OK -> {test_file}")
        except Exception as exc:
            lines.append(f"  WRITE FAILED: {exc}")
    lines += ["", "=== end ==="]
    report = "\n".join(lines)
    try:
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(report)
    except Exception as exc:
        report += f"\n\n<failed to write file: {exc}>"
    return out_path, report
