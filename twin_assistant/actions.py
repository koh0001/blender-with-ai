"""Strict, bounded scene commands. This module deliberately has no Blender dependency."""
import json
import math

OPERATIONS = ("create", "move", "rotate", "scale", "rename")
PRIMITIVES = ("CUBE", "UV_SPHERE", "CYLINDER", "PLANE", "CONE")
FIELDS = {"operation", "target", "name", "primitive", "vector"}


def response_schema():
    return {
        "type": "object", "additionalProperties": False,
        "required": ["message", "actions"],
        "properties": {
            "message": {"type": "string"},
            "actions": {"type": "array", "maxItems": 20, "items": {
                "type": "object", "additionalProperties": False,
                "required": sorted(FIELDS),
                "properties": {
                    "operation": {"type": "string", "enum": list(OPERATIONS)},
                    "target": {"type": "string"}, "name": {"type": "string"},
                    "primitive": {"type": "string", "enum": list(PRIMITIVES)},
                    "vector": {"type": "array", "items": {"type": "number"},
                               "minItems": 3, "maxItems": 3},
                },
            }},
        },
    }


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def validate_response(data, objects):
    if isinstance(data, str):
        try:
            data = json.loads(data, object_pairs_hook=_unique_pairs)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid assistant JSON") from exc
    if not isinstance(data, dict) or set(data) != {"message", "actions"}:
        raise ValueError("Assistant response must contain only message and actions")
    message, commands = data["message"], data["actions"]
    if not isinstance(message, str) or len(message) > 16000 or "\x00" in message:
        raise ValueError("Invalid assistant message")
    if not isinstance(commands, list) or len(commands) > 20:
        raise ValueError("At most 20 scene actions are supported")
    names = {record["object_name"] for record in objects}
    validated = []
    for command in commands:
        if not isinstance(command, dict) or set(command) != FIELDS:
            raise ValueError("Unexpected scene action fields")
        op = command["operation"]
        if op not in OPERATIONS or command["primitive"] not in PRIMITIVES:
            raise ValueError("Unsupported scene operation or primitive")
        for key in ("target", "name"):
            value = command[key]
            if not isinstance(value, str) or len(value.encode("utf-8")) > 63 or any(ord(c) < 32 for c in value):
                raise ValueError("Invalid object name")
        vector = command["vector"]
        if not isinstance(vector, list) or len(vector) != 3 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            or abs(v) > 100000 for v in vector
        ):
            raise ValueError("Vector must contain three finite bounded numbers")
        if op == "create":
            if command["target"]:
                raise ValueError("Create must have an empty target")
        elif command["target"] not in names:
            raise ValueError("Action target must belong to the supplied selection")
        if op == "rename" and not command["name"].strip():
            raise ValueError("Rename requires a name")
        if op not in ("create", "rename") and command["name"]:
            raise ValueError("Unused name must be empty")
        if op == "rename" and vector != [0, 0, 0]:
            raise ValueError("Rename vector must be zero")
        if op != "create" and command["primitive"] != "CUBE":
            raise ValueError("Unused primitive must be CUBE")
        if op == "scale" and any(v <= 0 or v > 100 for v in vector):
            raise ValueError("Scale multipliers must be positive and at most 100")
        validated.append({**command, "vector": list(vector)})
    return message, validated
