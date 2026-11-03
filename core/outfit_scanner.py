"""Context-aware BodySlide ``.osp`` scanner.

The scanner keeps four different questions separate: inferred sex, body
family, safe MPP reference compatibility, and mesh part.  An OSP is analysed
as a project before individual SliderSets so sparse sets can inherit evidence
without hiding genuinely conflicting families.
"""
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


RM_SLIDERS = {"RM_Heavy", "RM_Muscular", "RM_Thin"}
# These remain the user-facing/manual reference choices used by the patch
# pipeline.  Detected ``gender`` itself is now Female/Male/Unknown.
GENDER_CYCLE = ["3BA", "HIMBO", "Unknown"]
MESH_CYCLE = ["Body", "Hands", "Feet", "Mixed", "Unknown", "Not Applicable"]
BODY_FAMILY_CYCLE = ["3BA", "CBBE", "HIMBO", "BHUNP", "UUNP", "Other", "Unknown"]
SUPPORTED_BODY_FAMILIES = {"3BA", "HIMBO"}
SUPPORTED_REFERENCES = {"3BA", "HIMBO"}
SUPPORTED_MESH_PARTS = {"Body", "Hands", "Feet"}
NONPATCHABLE_MESH_PARTS = {"Not Applicable"}
COMPATIBILITY_STATES = {"Proven compatible", "Unverified", "Proven incompatible", "Conflict / Error"}


@dataclass
class OutfitInfo:
    name: str
    # Semantic sex. __post_init__ accepts old 3BA/HIMBO values for saved
    # state and older callers.
    gender: str = "Unknown"
    mesh_type: str = "Unknown"
    # Garment tags remain separate from ``mesh_type``.  The latter selects an
    # MPP reference (Body/Hands/Feet): a cape is still Body, while a hood is
    # deliberately outside that reference pipeline.
    garment_tags: list[str] = field(default_factory=list)
    body_family: str = "Unknown"
    reference_compatibility: str = "Unknown"
    compatibility_status: str = "Unverified"
    confidence: str = "Unknown"
    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    project_found: bool = True
    source_project: str = ""
    already_patched: bool = False
    partially_patched: bool = False
    has_legacy: bool = False
    revealing: bool = False
    # Per-outfit Drape Smart refit. Deliberately not a run-wide switch: the
    # pass costs roughly a minute per RM slider, so it is worth paying on the
    # few loose garments the plain conform handles badly and nowhere else.
    smart_refit: bool = False
    # Whether the original SliderSet declares an embedded/reference shape.
    has_reference: bool = False
    # Per-outfit output policy: original, remove, or force.
    output_reference_policy: str = "original"
    # Old UI/state name retained for a manual patch-reference override.
    gender_override: str | None = None
    mesh_override: str | None = None

    def __post_init__(self):
        # Older saved state used the reference name in ``gender``.  Keep all
        # three historical aliases readable without turning weak CBBE evidence
        # into a proven 3BA reference.
        if self.gender in {"3BA", "HIMBO"}:
            if self.reference_compatibility == "Unknown":
                self.reference_compatibility = self.gender
            if self.body_family == "Unknown":
                self.body_family = self.gender
            self.compatibility_status = "Proven compatible"
            self.gender = "Female" if self.gender == "3BA" else "Male"
        elif self.gender == "CBBE":
            if self.body_family == "Unknown":
                self.body_family = "CBBE"
            if self.compatibility_status == "Proven compatible":
                self.compatibility_status = "Unverified"
            self.gender = "Female"

    @property
    def eff_sex(self) -> str:
        # Manual UI choices select an MPP reference internally, but the
        # user-facing concept is sex rather than the implementation family.
        if self.gender_override == "3BA":
            return "Female"
        if self.gender_override == "HIMBO":
            return "Male"
        return self.gender

    @property
    def display_reference(self) -> str:
        """User-facing MPP reference label without exposing 3BA/HIMBO keys."""
        return {"3BA": "Female", "HIMBO": "Male"}.get(self.eff_reference,
                                                           self.eff_reference)

    @property
    def eff_reference(self) -> str:
        return self.gender_override or self.reference_compatibility

    @property
    def has_manual_reference_override(self) -> bool:
        return self.gender_override in SUPPORTED_REFERENCES

    @property
    def eff_gender(self) -> str:
        """Legacy alias: returns the selected MPP reference, not sex."""
        return self.eff_reference

    @property
    def eff_mesh(self) -> str:
        return self.mesh_override or self.mesh_type

    @property
    def eff_body_family(self) -> str:
        return self.body_family

    @property
    def display_mesh_label(self) -> str:
        """Human-facing garment label without changing patch-reference logic."""
        return " / ".join(self.garment_tags) if self.garment_tags else self.eff_mesh

    @property
    def excludes_thin(self) -> bool:
        """Capes and cloaks retain Body handling but must not receive RM_Thin."""
        return bool({"Cape", "Cloak"} & set(self.garment_tags))

    @property
    def patch_status(self) -> str:
        """Selection-stage status, independent of reference diagnostics."""
        if not self.project_found:
            return "Missing project"
        if self.eff_mesh in NONPATCHABLE_MESH_PARTS:
            return "Not applicable"
        if self.already_patched:
            return "Patched"
        if self.partially_patched:
            return "Partially patched"
        return "Not patched"

    @property
    def needs_attention(self) -> bool:
        return self.status_label not in {"Ready", "Already Patched", "Not Applicable"}

    @property
    def is_patchable(self) -> bool:
        return (self.project_found and self.eff_mesh in SUPPORTED_MESH_PARTS
                and self.eff_reference in SUPPORTED_REFERENCES
                and (self.compatibility_status == "Proven compatible"
                     or self.has_manual_reference_override)
                and not any("Conflicting" in issue for issue in self.issues))

    @property
    def status_label(self) -> str:
        if not self.project_found:
            return "Missing project"
        if self.eff_mesh in NONPATCHABLE_MESH_PARTS:
            return "Not Applicable"
        if any("Conflicting" in text for text in (*self.warnings, *self.issues)):
            return "Conflict / Error"
        if self.compatibility_status == "Proven incompatible":
            return "Cannot Patch"
        # A set which already contains all generated sliders is not a new
        # patch candidate.  Keep that state visible even if its reference is
        # merely unverified; the user can still inspect the compatibility
        # tooltip without it being presented as an actionable failure.
        if self.already_patched:
            return "Already Patched"
        if self.eff_mesh not in SUPPORTED_MESH_PARTS:
            return "Needs Confirmation"
        if (not self.has_manual_reference_override
                and self.compatibility_status == "Unverified"):
            return "Needs Confirmation"
        if (not self.has_manual_reference_override and (
                self.eff_sex == "Unknown"
                or self.eff_reference not in SUPPORTED_REFERENCES
                or self.confidence in {"Low", "Unknown"})):
            return "Needs Confirmation"
        if self.warnings:
            return "Conflict / Error"
        return "Ready"

    def cycle_gender(self):
        cur = self.gender_override or self.eff_reference
        idx = GENDER_CYCLE.index(cur) if cur in GENDER_CYCLE else 2
        self.gender_override = GENDER_CYCLE[(idx + 1) % len(GENDER_CYCLE)]

    def cycle_mesh(self):
        cur = self.mesh_override or self.mesh_type
        idx = MESH_CYCLE.index(cur) if cur in MESH_CYCLE else len(MESH_CYCLE) - 1
        self.mesh_override = MESH_CYCLE[(idx + 1) % len(MESH_CYCLE)]

    def cycle_body_family(self):
        cur = self.body_family
        idx = BODY_FAMILY_CYCLE.index(cur) if cur in BODY_FAMILY_CYCLE else len(BODY_FAMILY_CYCLE) - 1
        self.body_family = BODY_FAMILY_CYCLE[(idx + 1) % len(BODY_FAMILY_CYCLE)]


_LEGACY = {"Chubby Refit", "Rley's CBBE Outfit", "Rley CBBE Shoulder",
           "Rley CBBE Outfit", "Fat Morph", "Fat Morphs"}

# 3BA/3BBB and HIMBO are explicit MPP-compatible markers. CBBE is a weak
# female-family marker and never proves 3BA compatibility by itself.
_FAMILY_MARKERS = {
    "3BA": {"3ba", "3bbb"},
    "CBBE": {"cbbe"},
    "HIMBO": {"himbo"},
    "BHUNP": {"bhunp"},
    "UUNP": {"uunp", "unpb", "unp"},
}
_VARIANT_MARKERS = {"tng", "sos", "sam"}
# A Shape carrying one of these names is usually the embedded body reference
# even when the OSP has no ReferenceInfo node.  Keep this separate from the
# family classifier: HIMP/TNG identify a body reference, but do not by
# themselves prove an MPP-compatible family.
_REFERENCE_BODY_MARKERS = {
    marker for markers in _FAMILY_MARKERS.values() for marker in markers
} | {"himp", "tng"}
_STRONG_FAMILIES = {"3BA", "HIMBO", "BHUNP", "UUNP"}
_FEMALE_WORDS = {"female", "fem", "woman", "women", "girl", "girls"}
_MALE_WORDS = {"male", "masc", "masculine", "man", "men", "boy", "boys"}
_FEMALE_FAMILIES = {"3BA", "CBBE", "BHUNP", "UUNP"}
_MALE_FAMILIES = {"HIMBO", "Other"}


@dataclass
class _Classification:
    gender: str = "Unknown"
    body_family: str = "Unknown"
    reference: str = "Unknown"
    confidence: str = "Unknown"
    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    family_strength: str = "none"
    compatibility_status: str = "Unverified"
    variants: list[str] = field(default_factory=list)


def _gender_tokens(text: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(text or ""))
    return re.findall(r"[a-z0-9]+", spaced.lower())


def _family_scores(texts: list[str]) -> tuple[dict[str, int], list[str], dict[str, list[str]]]:
    scores = {family: 0 for family in _FAMILY_MARKERS}
    evidence: list[str] = []
    marker_hits = {family: [] for family in _FAMILY_MARKERS}
    for index, text in enumerate(texts):
        value = str(text or "")
        tokens = _gender_tokens(value)
        # Earlier fields are project/set identity and structured paths;
        # shape targets are useful but weaker evidence. CBBE is intentionally
        # weak because it does not prove 3BA.
        source_weight = 8 if index == 0 else 7 if index == 1 else 6 if index == 2 else 4 if index == 3 else 3
        if index >= 6:
            source_weight = 2
        for family, markers in _FAMILY_MARKERS.items():
            for marker in markers:
                if marker not in tokens:
                    continue
                marker_weight = 10 if family in _STRONG_FAMILIES else 2
                scores[family] += marker_weight * source_weight
                marker_hits[family].append(marker)
                evidence.append(f"{family} marker in {value}")
                break
    return scores, evidence, marker_hits


def _resolve_family(scores: dict[str, int], marker_hits: dict[str, list[str]]) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    strong = sorted(((f, s) for f, s in scores.items() if f in _STRONG_FAMILIES and s),
                    key=lambda pair: pair[1], reverse=True)
    if len(strong) > 1:
        # BHUNP projects can carry a 3BBB compatibility marker.  When the
        # 3BA score consists only of 3BBB, preserve the explicit BHUNP body
        # family instead of treating the compatibility marker as a conflict.
        if ({family for family, _ in strong} == {"BHUNP", "3BA"}
                and not marker_hits.get("3BA", []).count("3ba")
                and marker_hits.get("3BA", []).count("3bbb")):
            return "BHUNP", "strong", warnings
        # One explicit strong family is enough to make a claim. Two different
        # strong families in one project are a genuine conflict regardless of
        # which one happens to occur more often; majority voting would silently
        # propagate a wrong reference to sparse SliderSets.
        warnings.append("Conflicting body families detected in the OSP project.")
        return "Unknown", "conflict", warnings
    if strong:
        return strong[0][0], "strong", warnings
    if scores.get("CBBE", 0):
        return "CBBE", "weak", warnings
    return "Unknown", "none", warnings


def _classify_texts(texts: list[str]) -> _Classification:
    scores, evidence, marker_hits = _family_scores(texts)
    family, strength, warnings = _resolve_family(scores, marker_hits)
    tokens = {token for text in texts for token in _gender_tokens(text)}
    variants = sorted(tokens & _VARIANT_MARKERS)
    if family == "Unknown" and variants and strength != "conflict":
        family, strength = "Other", "variant"
        evidence.append("variant/framework marker: " + ", ".join(variants))
    generic_female = bool(tokens & _FEMALE_WORDS)
    generic_male = bool(tokens & _MALE_WORDS)
    if family in _FEMALE_FAMILIES:
        gender = "Female"
    elif family in _MALE_FAMILIES:
        gender = "Male"
    elif generic_female and not generic_male:
        gender = "Female"
    elif generic_male and not generic_female:
        gender = "Male"
    else:
        gender = "Unknown"
    if generic_female and generic_male:
        gender = "Unknown"
        warnings.append("Conflicting gender markers detected in project metadata.")

    reference = family if family in SUPPORTED_REFERENCES else "Unknown"
    issues: list[str] = []
    if family == "Unknown":
        confidence = "Low" if evidence or generic_female or generic_male else "Unknown"
        issues.append("Gender/body family could not be inferred from project metadata.")
    elif strength == "strong":
        confidence = "High"
    else:
        confidence = "Medium"
    if family not in {"Unknown", *SUPPORTED_BODY_FAMILIES}:
        if family != "CBBE":
            issues.append(f"Body family {family} is not supported by the MPP reference set.")
    if family in SUPPORTED_BODY_FAMILIES and reference == "Unknown":
        issues.append("No compatible MPP reference is proven by the project metadata.")
    if family in SUPPORTED_REFERENCES:
        compatibility_status = "Proven compatible"
    elif family in {"CBBE", "Unknown"}:
        compatibility_status = "Unverified"
    elif strength == "conflict":
        compatibility_status = "Conflict / Error"
    else:
        compatibility_status = "Proven incompatible"
    if strength == "conflict":
        compatibility_status = "Conflict / Error"
    return _Classification(gender, family, reference, confidence, evidence,
                          list(dict.fromkeys(warnings)), list(dict.fromkeys(issues)),
                          strength, compatibility_status, variants)


def _project_classification(records: list[dict], filename: str) -> _Classification:
    texts = [filename]
    for record in records:
        # Shapes are evidence only when they explicitly identify a reference
        # body. Arbitrary accessory shape names must not become project-wide
        # body-family claims.
        texts.extend(record["metadata"])
    result = _classify_texts(texts)
    if result.family_strength == "conflict":
        result.warnings.append("SliderSets were not given inherited family evidence because the project is conflicting.")
    elif result.body_family != "Unknown":
        result.evidence.insert(0, f"Project-wide evidence: {result.body_family}")
    return result


def classify_metadata_details(texts: list[str], project: _Classification | None = None) -> _Classification:
    local = _classify_texts(texts)
    if project is None:
        return local
    if local.family_strength == "none" and project.family_strength in {"strong", "weak"}:
        local.body_family = project.body_family
        local.gender = project.gender
        local.reference = project.reference
        local.compatibility_status = project.compatibility_status
        local.confidence = "Medium" if project.confidence == "High" else project.confidence
        local.evidence.extend([f"Inherited from OSP project: {entry}"
                                for entry in project.evidence[:2]])
        local.issues = [issue for issue in local.issues
                        if "could not be inferred" not in issue]
        local.issues.extend(issue for issue in project.issues
                            if issue not in local.issues)
    elif (local.family_strength == "weak" and project.family_strength == "strong"
          and project.body_family != "Unknown"):
        local.body_family = project.body_family
        local.gender = project.gender
        local.reference = project.reference
        local.compatibility_status = project.compatibility_status
        local.confidence = "Medium"
        local.evidence.append(f"Project-wide strong evidence overrides weak local marker: {project.body_family}")
    elif project.family_strength == "conflict":
        # Do not turn a project-level conflict into a warning on every child.
        # Local evidence remains authoritative; sparse children simply stay in
        # Needs Confirmation and record why inheritance was withheld.
        local.evidence.append("Project-wide conflict: inheritance withheld; local evidence retained")
    return local


def classify_metadata(texts: list[str]) -> tuple[str, str, str, list[str], list[str]]:
    """Backward-compatible tuple: reference, family, confidence, evidence, warnings."""
    result = _classify_texts(texts)
    return result.reference, result.body_family, result.confidence, result.evidence, result.warnings


def _detect_gender(texts: list[str]) -> str:
    return _classify_texts(texts).reference


def detect_gender_label(text: str | list[str]) -> str:
    """Legacy automation label: 3BA, HIMBO, or Unknown."""
    return _detect_gender(text if isinstance(text, list) else [text])


_PART_WORDS = {
    "Hands": {"hand", "hands", "glove", "gloves", "gauntlet", "gauntlets"},
    "Feet": {"feet", "foot", "boot", "boots", "shoe", "shoes"},
    "Body": {"body", "outfit", "dress", "robe", "armor", "armour", "cape", "cloak"},
}
_STRUCTURED_PARTS = {
    "body": "Body", "outfit": "Body", "armor": "Body", "armour": "Body",
    "hands": "Hands", "hand": "Hands", "gloves": "Hands", "glove": "Hands",
    "gauntlets": "Hands", "gauntlet": "Hands",
    "feet": "Feet", "foot": "Feet", "boots": "Feet", "boot": "Feet",
}

# Garment categories are intentionally not reference parts. Capes and cloaks
# are body-reference outfits, with RM_Thin excluded by the native conformer;
# hoods have no compatible MPP Body/Hands/Feet reference and remain N/A.
_GARMENT_MARKERS = {
    "Hood": {"hood", "hoods", "hooded", "cowl", "cowls"},
    "Cape": {"cape", "capes", "capelet", "capelets"},
    "Cloak": {"cloak", "cloaks"},
}


def _part_hits(text: str, *, structured: bool = False) -> set[str]:
    tokens = _gender_tokens(text)
    if structured:
        return {_STRUCTURED_PARTS[token] for token in tokens if token in _STRUCTURED_PARTS}
    return {part for part, markers in _PART_WORDS.items() if set(tokens) & markers}


def detect_garment_tags(*texts: str) -> list[str]:
    """Detect explicit Hood/Cape/Cloak set categories from identity metadata.

    Shape names are deliberately excluded. A normal armor can contain a small
    cape-shaped accessory, and that must not classify the complete SliderSet
    as a cape or make it skip RM_Thin globally.
    """
    tokens = {token for text in texts for token in _gender_tokens(text)}
    return [tag for tag, markers in _GARMENT_MARKERS.items() if tokens & markers]


def _reference_marker_hits(shape: str) -> set[str]:
    """Return body-reference markers carried by a Shape entry.

    BodySlide commonly stores a reference as ``target="BaseShape"`` with
    text ``CBBE`` (or as ``target="3BA"`` with text ``3BA``).  A plain
    accessory called ``HIMBO Belt`` must not be promoted to a reference just
    because its first word happens to be a body-family marker.
    """
    target, _, value = str(shape or "").partition(" ")
    target = target.strip().lower()
    value = value.strip().lower()
    hits: set[str] = set()
    for marker in _REFERENCE_BODY_MARKERS:
        target_is_marker = target == marker or target.startswith(marker + "_")
        value_is_marker = value == marker or value.startswith(marker + "_")
        if value_is_marker:
            hits.add(marker)
        elif target_is_marker and (not value or target.startswith(marker + "_")):
            hits.add(marker)
    return hits


def _is_reference_shape(shape: str) -> bool:
    """Whether a ``Shape`` entry explicitly names a body reference."""
    return bool(_reference_marker_hits(shape))


def _detect_mesh_context(*, name: str, data_folder: str, output_path: str,
                         source_file: str, shapes: list[str], project_name: str = "") -> tuple[str, list[str], list[str]]:
    """Infer part with structured and specific identity evidence first."""
    del output_path, project_name  # generic output paths are not part evidence
    evidence: list[str] = []
    warnings: list[str] = []
    structured_hits = _part_hits(data_folder, structured=True)
    identity_hits = _part_hits(f"{name} {source_file}")
    if len(structured_hits) == 1:
        part = next(iter(structured_hits))
        evidence.append(f"structured DataFolder identifies {part}")
        if identity_hits - {part}:
            evidence.append("specific identity conflict ignored in favour of DataFolder")
        return part, evidence, warnings
    if len(identity_hits) == 1:
        part = next(iter(identity_hits))
        evidence.append(f"SliderSet/SourceFile identity identifies {part}")
        return part, evidence, warnings
    explicit = structured_hits | identity_hits
    if len(explicit) > 1:
        warnings.append("Ambiguous mesh part: multiple specific part identities were found.")
        evidence.append("specific part identities: " + ", ".join(sorted(explicit)))
        return "Mixed", evidence, warnings

    if data_folder:
        final_segment = re.split(r"[\\/]", data_folder.rstrip("\\/"))[-1]
        if final_segment.lower() in {"head", "heads", "hood", "hoods"}:
            evidence.append(f"structured DataFolder identifies out-of-scope part: {final_segment}")
            return "Not Applicable", evidence, warnings

    # In older OSPs the embedded reference is often the only reliable part
    # signal.  Treat a named body reference as Body unless that reference (or
    # another shape) explicitly identifies hands/feet.  This is deliberately
    # below DataFolder/identity evidence so ``...\\Hands`` and ``... Boots``
    # remain authoritative.
    reference_shapes = [shape for shape in shapes if _is_reference_shape(shape)]
    if reference_shapes:
        reference_parts = set()
        for shape in reference_shapes:
            reference_parts |= _part_hits(shape)
        shape_parts = set()
        for shape in shapes:
            shape_parts |= _part_hits(shape)
        if not (reference_parts | shape_parts) & {"Hands", "Feet"}:
            markers = sorted({marker for shape in reference_shapes
                              for marker in _reference_marker_hits(shape)})
            evidence.append("reference shape identifies Body: " + ", ".join(markers))
            return "Body", evidence, warnings

    # Shape names are consulted only without stronger identity evidence. A
    # body set containing accessory shapes therefore remains Body, not Mixed.
    shape_hits = set()
    for shape in shapes:
        shape_hits |= _part_hits(shape)
    if len(shape_hits) == 1:
        part = next(iter(shape_hits))
        evidence.append(f"shape names identify {part}")
        return part, evidence, warnings
    if len(shape_hits) > 1:
        warnings.append("Ambiguous mesh part: shape names identify multiple parts.")
        evidence.append("shape part identities: " + ", ".join(sorted(shape_hits)))
        return "Mixed", evidence, warnings
    if data_folder:
        final_segment = re.split(r"[\\/]", data_folder.rstrip("\\/"))[-1]
        if final_segment.lower() in {"head", "heads", "hood", "hoods"}:
            evidence.append(f"structured DataFolder identifies out-of-scope part: {final_segment}")
            return "Not Applicable", evidence, warnings
    warnings.append("Mesh part could not be inferred from project metadata.")
    return "Unknown", evidence, warnings


def _detect_mesh(texts: list[str]) -> str:
    """Compatibility wrapper for callers that only have flat text metadata."""
    name = texts[0] if texts else ""
    mesh, _, _ = _detect_mesh_context(name=name, data_folder="", output_path="",
                                      source_file="", shapes=texts[1:])
    return mesh


def scan_osp(path: str) -> list[OutfitInfo]:
    """Parse one OSP using project-wide and per-SliderSet evidence."""
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except (ET.ParseError, OSError, UnicodeError):
        return []

    fname = os.path.splitext(os.path.basename(path))[0]
    records: list[dict] = []
    for slider_set in root.findall("SliderSet"):
        name = slider_set.get("name", "").strip()
        group = slider_set.findtext("Group", "")
        data_folder = slider_set.findtext("DataFolder", "")
        output_path = slider_set.findtext("OutputPath", "")
        source_file = slider_set.findtext("SourceFile", "")
        shapes = [shape.get("target", "") + " " + (shape.text or "")
                  for shape in slider_set.findall("Shape")]
        sliders = {s.get("name", "") for s in slider_set.findall("Slider")}
        target_names = {data.get("target", "") for slider in slider_set.findall("Slider")
                        for data in slider.findall("Data")}
        reference_info = slider_set.find("ReferenceInfo")
        reference_shape_name = (reference_info.get("ShapeName", "").strip()
                                if reference_info is not None else "")
        # Some newer Outfit Studio projects keep the reference only in
        # ReferenceInfo instead of repeating it as a Shape entry.  Feed that
        # name through the same strict marker parser used for Shape entries.
        if reference_shape_name:
            shapes_for_detection = shapes + ["ReferenceInfo " + reference_shape_name]
        else:
            shapes_for_detection = shapes
        reference_shapes = [shape for shape in shapes_for_detection
                            if _is_reference_shape(shape)]
        # Outfit Studio writes ReferenceInfo for newer projects, but older
        # projects and many hand-authored OSPs only identify the embedded body
        # through the Shape entry itself.  Treat either form as evidence.  The
        # shape classifier is intentionally strict, so accessory names such as
        # 3BCA_Arm or CBBE Belt do not masquerade as a body reference.
        has_reference = bool(
            reference_info is not None and
            reference_info.get("ShapeName", "").strip()
        ) or bool(reference_shapes)
        # OutputPath is deliberately excluded from body-family evidence.  It
        # is an output destination, not a declaration of the reference body;
        # folder names such as ``meshes\\3BA`` are common even for unrelated
        # accessories.  Mesh-part detection receives it separately but treats
        # it as non-authoritative as well.
        metadata = [group, name, data_folder, source_file] + reference_shapes + sorted(target_names)
        records.append({"name": name, "group": group, "data_folder": data_folder,
                        "output_path": output_path, "source_file": source_file,
                        "shapes": shapes_for_detection, "metadata": metadata, "sliders": sliders,
                        "has_reference": has_reference})

    project = _project_classification(records, fname)
    results: list[OutfitInfo] = []
    for record in records:
        local = classify_metadata_details([fname] + record["metadata"], project)
        mesh, mesh_evidence, mesh_warnings = _detect_mesh_context(
            name=record["name"], data_folder=record["data_folder"],
            output_path=record["output_path"], source_file=record["source_file"],
            shapes=record["shapes"], project_name=fname)
        garment_tags = detect_garment_tags(
            record["data_folder"], record["name"], record["source_file"])
        if garment_tags:
            mesh_evidence = mesh_evidence + [
                "garment category: " + ", ".join(garment_tags)]
        warnings = list(dict.fromkeys(local.warnings + mesh_warnings))
        evidence = list(dict.fromkeys(local.evidence + mesh_evidence))
        results.append(OutfitInfo(
            name=record["name"], gender=local.gender, mesh_type=mesh,
            garment_tags=garment_tags,
            body_family=local.body_family, reference_compatibility=local.reference,
            compatibility_status=local.compatibility_status,
            confidence=local.confidence, evidence=evidence, warnings=warnings,
            issues=list(dict.fromkeys(local.issues)),
            project_found=True, source_project=os.path.basename(path),
            has_reference=record["has_reference"],
            already_patched=RM_SLIDERS.issubset(record["sliders"]),
            partially_patched=bool(record["sliders"] & RM_SLIDERS)
                              and not RM_SLIDERS.issubset(record["sliders"]),
            has_legacy=bool(record["sliders"] & _LEGACY)))
    return results


def scan_all(slider_sets_dir: str, cancel=None) -> dict[str, OutfitInfo]:
    """Scan all .osp files in a directory, keyed by SliderSet name."""
    results: dict[str, OutfitInfo] = {}
    try:
        fnames = os.listdir(slider_sets_dir)
    except OSError:
        return results
    for fname in fnames:
        if cancel and cancel():
            break
        if not fname.lower().endswith(".osp"):
            continue
        source_path = os.path.join(slider_sets_dir, fname)
        for info in scan_osp(source_path):
            previous = results.get(info.name)
            if previous is not None:
                previous.warnings.append(f"Duplicate SliderSet name also found in {info.source_project or fname}.")
                previous.evidence.append(f"First project: {previous.source_project or 'unknown'}")
                continue
            results[info.name] = info
    return results
