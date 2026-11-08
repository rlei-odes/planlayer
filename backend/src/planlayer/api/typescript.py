"""TypeScript types for the frontend, generated from the API's OpenAPI schema (F-04).

Every component schema becomes one exported type. Pydantic emits a small subset of JSON Schema;
a keyword outside it raises, so a model the generator can't express fails loudly instead of
turning into a loose type.
"""

import json
import re
from typing import Any

from pydantic import SecretStr

from planlayer.config import Settings

HEADER = (
    "// Generated from the API's OpenAPI schema by `planlayer api-types` (npm run gen:api).\n"
    "// Do not edit: change the Pydantic models in backend/src/planlayer/api instead.\n"
)

# Keywords that only constrain or document a value; they don't change its TypeScript type.
_IGNORED = {
    "title",
    "description",
    "default",
    "format",
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "propertyNames",
    "examples",
}
_SHAPE = {"$ref", "anyOf", "oneOf", "allOf", "enum", "const", "type", "items", "properties"}
_SHAPE |= {"required", "additionalProperties"}
_SCALAR = {"string": "string", "integer": "number", "number": "number", "boolean": "boolean"}
_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def app_schema() -> dict[str, Any]:
    """The OpenAPI schema of the app. Building the app opens no connection (engines connect on
    first use), so a placeholder database URL is enough."""
    from planlayer.api.app import create_app

    settings = Settings(
        environment="development",
        app_database_url=SecretStr("postgresql+psycopg://localhost/openapi"),
    )
    return create_app(settings).openapi()


def type_name(component: str) -> str:
    """``ProfileIO-Input`` → ``ProfileIOInput``: FastAPI's names for split input/output models."""
    return re.sub(r"[^A-Za-z0-9_]", "", component)


def _union(parts: list[str]) -> str:
    unique = list(dict.fromkeys(parts))
    return " | ".join(unique)


def _key(name: str) -> str:
    return name if _IDENTIFIER.match(name) else json.dumps(name)


def ts_type(schema: dict[str, Any] | bool, indent: str = "") -> str:
    """The TypeScript type of one JSON Schema."""
    if schema is True or schema == {}:
        return "unknown"
    if schema is False:
        return "never"
    unknown = set(schema) - _IGNORED - _SHAPE
    if unknown:
        raise ValueError(f"unsupported JSON Schema keyword(s): {', '.join(sorted(unknown))}")
    if not set(schema) & _SHAPE:  # e.g. a field typed `Any`
        return "unknown"
    if "$ref" in schema:
        return type_name(schema["$ref"].rsplit("/", 1)[-1])
    if "allOf" in schema:
        if len(schema["allOf"]) != 1:
            raise ValueError("allOf with more than one schema is not supported")
        return ts_type(schema["allOf"][0], indent)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return _union([ts_type(s, indent) for s in schema[key]])
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return _union([json.dumps(v) for v in schema["enum"]])
    match schema.get("type"):
        case "null":
            return "null"
        case "array":
            item = ts_type(schema.get("items", True), indent)
            return f"({item})[]" if " | " in item else f"{item}[]"
        case "object" | None if "properties" in schema:
            return _object(schema, indent)
        case "object":
            extra = schema.get("additionalProperties", True)
            return f"Record<string, {ts_type(extra, indent)}>"
        case str(kind) if kind in _SCALAR:
            return _SCALAR[kind]
        case other:
            raise ValueError(f"unsupported JSON Schema type: {other!r}")


def _object(schema: dict[str, Any], indent: str) -> str:
    if schema.get("additionalProperties") not in (None, False):
        raise ValueError("objects with both properties and additionalProperties are not supported")
    required = set(schema.get("required", []))
    inner = indent + "  "
    lines = [
        f"{inner}{_key(name)}{'' if name in required else '?'}: {ts_type(prop, inner)}"
        for name, prop in schema["properties"].items()
    ]
    return "{\n" + "\n".join(lines) + f"\n{indent}}}" if lines else "Record<string, never>"


def typescript(openapi: dict[str, Any]) -> str:
    """One exported type per component schema, sorted by name."""
    components = openapi.get("components", {}).get("schemas", {})
    blocks = [
        f"export type {type_name(name)} = {ts_type(schema)}\n"
        for name, schema in sorted(components.items(), key=lambda item: type_name(item[0]))
    ]
    return HEADER + "\n" + "\n".join(blocks)
