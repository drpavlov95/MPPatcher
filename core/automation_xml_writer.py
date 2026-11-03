import os
import re
from datetime import datetime
from xml.etree import ElementTree as ET
from xml.dom import minidom

from core.outfit_scanner import detect_gender_label


_MPP_REFERENCES = {
    ("cbbe", "body"):  ("MPP Female Body.osp", "MPP 3BA Body"),
    ("cbbe", "hands"): ("MPP Female Body.osp", "MPP 3BA Hands"),
    ("cbbe", "feet"):  ("MPP Female Body.osp", "MPP 3BA Feet"),
    ("himbo", "body"): ("MPP Male Body.osp",   "MPP HIMBO Body"),
    ("himbo", "hands"): ("MPP Male Body.osp",  "MPP HIMBO Hands"),
    ("himbo", "feet"): ("MPP Male Body.osp",   "MPP HIMBO Feet"),
}


def detect_gender(group_name: str) -> str:
    """Return the legacy internal key ``himbo`` or ``cbbe``.

    ``cbbe`` is intentionally kept here only because the CLI/reference API
    predates Physiques of Skyrim's user-facing ``3BA`` label.  UI and package
    names use 3BA; the mapping below resolves that key to the current MPP
    3BA reference project and set names.
    """
    detected = detect_gender_label(group_name)
    if detected == "HIMBO":
        return "himbo"
    if detected == "3BA":
        return "cbbe"
    return ""


def detect_mesh_type(group_name: str) -> str:
    """Returns 'hands', 'feet', or 'body'."""
    lower = group_name.lower()
    if "hand" in lower:
        return "hands"
    if "feet" in lower or "foot" in lower or "boot" in lower:
        return "feet"
    return "body"


def _osp_ref(gender: str, mesh_type: str, game_data_path: str) -> tuple[str, str]:
    """Returns (abs_osp_path, set_name) for the given gender/mesh_type."""
    key = (gender, mesh_type)
    if key not in _MPP_REFERENCES:
        raise ValueError(f"unsupported MPP reference selection: {gender}/{mesh_type}")
    osp_file, set_name = _MPP_REFERENCES[key]
    abs_path = game_data_path.rstrip("/\\") + f"/CalienteTools/BodySlide/SliderSets/{osp_file}"
    return abs_path.replace("\\", "/"), set_name


def generate(
    slider_set_names: list[str],
    gender: str,
    mesh_type: str,
    game_data_path: str,
    output_folder: str = "",
    output_prefix: str = "MPP_",
) -> str:
    """Returns the automation XML as a string."""
    root = ET.Element("AutomationScript")

    # Batch settings
    batch = ET.SubElement(root, "Batch")
    batch.set("mode", "SliderSets")
    folder_path = game_data_path.rstrip("/\\") + "/CalienteTools/BodySlide/SliderSets"
    ET.SubElement(batch, "Folder").text = folder_path.replace("\\", "/")

    if slider_set_names:
        escaped = [re.escape(n) for n in slider_set_names]
        ET.SubElement(batch, "SliderSetFilter").text = "^(" + "|".join(escaped) + ")$"
        ET.SubElement(batch, "SliderSetFilterRegex").text = "true"

    # Step 1: Clear old reference
    ET.SubElement(root, "Step").set("type", "ClearReference")

    # Step 2: Load MPP reference
    ref_osp, ref_set = _osp_ref(gender, mesh_type, game_data_path)
    load = ET.SubElement(root, "Step")
    load.set("type", "LoadReference")
    ET.SubElement(load, "SourceFile").text = ref_osp
    ET.SubElement(load, "Set").text = ref_set
    ET.SubElement(load, "MergeSliders").text = "true"

    # Step 3: Conform RM_* sliders only
    conform = ET.SubElement(root, "Step")
    conform.set("type", "ConformSliders")
    ET.SubElement(conform, "SliderNames").text = "RM_Heavy,RM_Muscular,RM_Thin"
    ET.SubElement(conform, "ProximityRadius").text = "10.0"
    ET.SubElement(conform, "MaxResults").text = "10"

    # Step 4: Save
    ET.SubElement(root, "Step").set("type", "SaveProject")

    return _pretty_xml(root)


def write_to_automations(
    slider_set_names: list[str],
    gender: str,
    mesh_type: str,
    game_data_path: str,
    automations_folder: str,
    output_folder: str = "",
    output_prefix: str = "MPP_",
) -> str:
    """Write the automation XML to the automations folder. Returns script name (no .xml).

    Avoids os.makedirs/os.path.exists (stat family is NOT hooked by MO2's usvfs).
    Writes directly via open() (CreateFileW IS hooked → redirected to Overwrite).
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    script_name = f"mppatcher_{ts}"
    file_path = os.path.join(automations_folder, script_name + ".xml")
    xml_str = generate(slider_set_names, gender, mesh_type, game_data_path,
                       output_folder, output_prefix)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(xml_str)
    except (FileNotFoundError, OSError):
        # Automations folder may not exist yet — create it (CreateDirectoryW is hooked)
        try:
            os.mkdir(automations_folder)
        except OSError:
            pass
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(xml_str)
    return script_name


def _pretty_xml(root: ET.Element) -> str:
    raw = ET.tostring(root, encoding="unicode")
    dom = minidom.parseString(raw)
    return dom.toprettyxml(indent="    ", encoding=None)
