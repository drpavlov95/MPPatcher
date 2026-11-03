"""Generate the OSP scanner before/after regression CSV.

The legacy side intentionally mirrors the pre-context scanner: CBBE was
treated as 3BA and every keyword in every path contributed equally to mesh
classification.  The new side comes from ``core.outfit_scanner``.
"""
import csv
import os
import re
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.outfit_scanner import (
    scan_osp, SUPPORTED_BODY_FAMILIES, SUPPORTED_MESH_PARTS,
    SUPPORTED_REFERENCES,
)

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "osp")
OUTPUT = os.path.join(ROOT, "tests", "fixtures", "osp_regression_report.csv")


def _tokens(value):
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(value or ""))
    return re.findall(r"[a-z0-9]+", spaced.lower())


def _legacy_gender(texts):
    tokens = {token for text in texts for token in _tokens(text)}
    if "himbo" in tokens:
        return "HIMBO"
    if tokens & {"cbbe", "3ba", "3bbb"}:
        return "3BA"
    return "Unknown"


def _legacy_mesh(texts):
    hits = set()
    for text in texts:
        value = str(text or "").lower()
        if "hand" in value or "glove" in value:
            hits.add("Hands")
        if "feet" in value or "foot" in value or "boot" in value:
            hits.add("Feet")
        if any(token in value for token in ("body", "armor", "armour", "outfit", "dress", "robe")):
            hits.add("Body")
    if len(hits) == 1:
        return next(iter(hits))
    if len(hits) > 1:
        return "Mixed"
    return "Unknown"


def _legacy_rows(path):
    root = ET.parse(path).getroot()
    fname = os.path.splitext(os.path.basename(path))[0]
    for slider_set in root.findall("SliderSet"):
        name = slider_set.get("name", "").strip()
        group = slider_set.findtext("Group", "")
        data = slider_set.findtext("DataFolder", "")
        output = slider_set.findtext("OutputPath", "")
        source = slider_set.findtext("SourceFile", "")
        shapes = [shape.get("target", "") + " " + (shape.text or "")
                  for shape in slider_set.findall("Shape")]
        targets = [data_node.get("target", "")
                   for slider in slider_set.findall("Slider")
                   for data_node in slider.findall("Data")]
        metadata = [fname, group, name, data, output, source] + shapes + targets
        gender = _legacy_gender(metadata)
        yield {
            "Source OSP": fname + ".osp",
            "SliderSet name": name,
            "Old gender": gender,
            "Old family": gender if gender != "Unknown" else "Unknown",
            "Old mesh part": _legacy_mesh([output, group, name, data, source, fname] + shapes + targets),
        }


def main():
    rows = []
    for filename in sorted(name for name in os.listdir(FIXTURES) if name.lower().endswith(".osp")):
        path = os.path.join(FIXTURES, filename)
        new = {row.name: row for row in scan_osp(path)}
        old_rows = list(_legacy_rows(path))
        for old in old_rows:
            row = new[old["SliderSet name"]]
            compatible = row.is_patchable
            rows.append({
                **old,
                "New gender": row.gender,
                "New family": row.body_family,
                "New reference compatibility": row.reference_compatibility,
                "New mesh part": row.mesh_type,
                "Status": row.status_label,
                "Compatibility state": row.compatibility_status,
                "Confidence": row.confidence,
                "Evidence": " | ".join(row.evidence),
                "Issues": " | ".join(row.issues),
                "Warnings": " | ".join(row.warnings),
                "Patch compatibility": "Yes" if compatible else "Review required",
            })
    fields = ["Source OSP", "SliderSet name", "Old gender", "New gender",
              "Old family", "New family", "New reference compatibility",
              "Old mesh part", "New mesh part", "Status",
              "Compatibility state", "Confidence", "Evidence", "Issues",
              "Warnings", "Patch compatibility"]
    with open(OUTPUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUTPUT}")
    from collections import Counter
    by_fixture = {}
    for row in rows:
        by_fixture.setdefault(row["Source OSP"], Counter())[row["Status"]] += 1
    for fixture, counts in sorted(by_fixture.items()):
        print(f"{fixture}: " + ", ".join(
            f"{status}={counts.get(status, 0)}"
            for status in ("Ready", "Needs Confirmation", "Already Patched",
                           "Not Applicable",
                           "Missing project", "Conflict / Error", "Cannot Patch")
            if counts.get(status, 0)))


if __name__ == "__main__":
    main()
