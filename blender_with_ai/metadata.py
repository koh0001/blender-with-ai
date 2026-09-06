"""Strict, Blender-independent metadata rules for a digital-twin scene.

Values describe provenance and object classification only. They must never be
interpreted as verified evacuation or safety properties.
"""

import csv
import io
import json
import re


OBJECT_TYPES = (
    "UNKNOWN", "SPACE", "CORRIDOR", "DOOR", "STAIR", "RAMP", "WALL", "EQUIPMENT"
)
REVIEW_STATUSES = ("UNREVIEWED", "NEEDS_REVIEW", "VERIFIED")
RULES = {
    "schema_version": 1,
    "fields": {
        "object_type": {"type": "enum", "values": list(OBJECT_TYPES)},
        "floor": {"type": "string", "max_length": 256},
        "zone": {"type": "string", "max_length": 256},
        "source": {"type": "string", "max_length": 256},
        "review_status": {"type": "enum", "values": list(REVIEW_STATUSES)},
    },
    "ai_review_status": "NEEDS_REVIEW",
    "identity_field": "object_id",
}
_FIELDS = frozenset(RULES["fields"])


def _text(value, label, *, nonempty=False):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if len(value) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{label} must be at most 256 characters without control characters")
    if nonempty and not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value


def validate_fields(fields, from_ai=False):
    """Validate partial fields without coercion; AI values always need review."""
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    if set(fields) - _FIELDS:
        raise ValueError("Unknown metadata field")
    result = {}
    for key, value in fields.items():
        result[key] = _text(value, key)
        if key == "object_type" and value not in OBJECT_TYPES:
            raise ValueError("Invalid object_type")
        if key == "review_status" and value not in REVIEW_STATUSES:
            raise ValueError("Invalid review_status")
    if from_ai:
        if result.get("review_status") == "VERIFIED":
            raise ValueError("AI cannot mark metadata VERIFIED")
        result["review_status"] = "NEEDS_REVIEW"
    return result


def validate_proposal(raw, allowed_names):
    """Return validated AI updates from a list or its JSON representation.

    Each item must contain exactly object_name and fields. Names are checked
    against the request's original selection; no executable content is evaluated.
    """
    if isinstance(raw, str):
        try:
            def unique_keys(pairs):
                output = {}
                for key, value in pairs:
                    if key in output:
                        raise ValueError("Duplicate JSON key")
                    output[key] = value
                return output
            raw = json.loads(raw, object_pairs_hook=unique_keys)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("Proposal must be valid JSON") from exc
    if not isinstance(raw, list):
        raise ValueError("Proposal must be a list")
    if isinstance(allowed_names, (str, bytes)):
        raise ValueError("allowed_names must be a collection of names")
    try:
        allowed = {_text(name, "object_name", nonempty=True) for name in allowed_names}
    except TypeError as exc:
        raise ValueError("allowed_names must be a collection of names") from exc
    result, seen = [], set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"object_name", "fields"}:
            raise ValueError("Each proposal needs exactly object_name and fields")
        name = _text(item["object_name"], "object_name", nonempty=True)
        if name not in allowed or name in seen:
            raise ValueError("Unknown or duplicate proposal target")
        seen.add(name)
        result.append({"object_name": name, "fields": validate_fields(item["fields"], from_ai=True)})
    return result


def build_export(records):
    """Validate flat records and preserve existing IDs, names, and field values."""
    if not isinstance(records, (list, tuple)):
        raise ValueError("records must be a list")
    objects, seen = [], set()
    for record in records:
        if not isinstance(record, dict) or not {"object_id", "object_name"} <= set(record):
            raise ValueError("Each record needs object_id and object_name")
        object_id = _text(record["object_id"], "object_id", nonempty=True)
        if object_id in seen:
            raise ValueError("Duplicate object_id")
        seen.add(object_id)
        fields = validate_fields({key: value for key, value in record.items()
                                  if key not in {"object_id", "object_name"}})
        objects.append({"object_id": object_id,
                        "object_name": _text(record["object_name"], "object_name", nonempty=True),
                        **fields})
    return {"schema_version": 1, "objects": objects}


def parse_csv(text):
    """Read ID-matched partial updates. Blank cells mean no update.

    Only object_id and schema-field columns are accepted; IDs are never inferred
    from names. A UTF-8 BOM is accepted at the beginning of the input.
    """
    if not isinstance(text, str):
        raise ValueError("CSV input must be text")
    try:
        reader = csv.DictReader(io.StringIO(text.removeprefix("\ufeff")), strict=True)
        headers = reader.fieldnames
        if (not headers or "object_id" not in headers or len(set(headers)) != len(headers)
                or set(headers) - (_FIELDS | {"object_id"})):
            raise ValueError("CSV needs unique headers: object_id and optional schema fields")
        rows, seen = [], set()
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("CSV row has incorrect column count")
            object_id = _text(row.pop("object_id"), "object_id", nonempty=True)
            if object_id in seen:
                raise ValueError("Duplicate object_id in CSV")
            seen.add(object_id)
            fields = validate_fields({key: value for key, value in row.items() if value != ""})
            rows.append({"object_id": object_id, **fields})
        return rows
    except csv.Error as exc:
        raise ValueError("Malformed CSV") from exc


def suggest_by_name(name):
    """Propose conservative labels; ambiguous classifications remain UNKNOWN."""
    name = _text(name, "object_name", nonempty=True)
    tokens = set(re.split(r"[\s_.\-/]+", name.upper()))
    labels = {
        "SPACE": {"SPACE", "ROOM", "공간", "실"},
        "CORRIDOR": {"CORRIDOR", "복도"},
        "DOOR": {"DOOR", "문", "출입문", "출입구"},
        "STAIR": {"STAIR", "STAIRS", "계단"},
        "RAMP": {"RAMP", "경사로"},
        "WALL": {"WALL", "벽", "벽체"},
        "EQUIPMENT": {"EQUIPMENT", "설비"},
    }
    matches = [kind for kind, aliases in labels.items() if tokens & aliases]
    result = {"object_type": matches[0] if len(matches) == 1 else "UNKNOWN",
              "source": "name_rule", "review_status": "NEEDS_REVIEW"}
    floors = set()
    for token in tokens:
        if re.fullmatch(r"B[1-9]\d?", token) or re.fullmatch(r"[1-9]\d?F", token):
            floors.add(token)
        elif re.fullmatch(r"지하[1-9]\d?층", token):
            floors.add("B" + token[2:-1])
        elif re.fullmatch(r"[1-9]\d?층", token):
            floors.add(token[:-1] + "F")
    if len(floors) == 1:
        result["floor"] = floors.pop()
    return result
