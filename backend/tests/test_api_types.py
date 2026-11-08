"""The frontend's generated API types (F-04)."""

from pathlib import Path

import pytest

from planlayer.api.typescript import app_schema, ts_type, typescript

SCHEMA_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "schema.ts"


def test_frontend_types_are_current() -> None:
    assert SCHEMA_TS.read_text(encoding="utf-8") == typescript(app_schema()), (
        "frontend/src/schema.ts is out of date: run `npm run gen:api` in frontend/"
    )


def test_models_become_types() -> None:
    openapi = {
        "components": {
            "schemas": {
                "Row-Output": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["group", "account"]},
                        "value": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        "cells": {"type": "array", "items": {"$ref": "#/components/schemas/Cell"}},
                        "counts": {"type": "object", "additionalProperties": {"type": "integer"}},
                        "note": {"type": "string", "default": ""},
                    },
                    "required": ["kind", "value", "cells", "counts"],
                }
            }
        }
    }
    assert typescript(openapi).endswith(
        "export type RowOutput = {\n"
        '  kind: "group" | "account"\n'
        "  value: string | null\n"
        "  cells: Cell[]\n"
        "  counts: Record<string, number>\n"
        "  note?: string\n"
        "}\n"
    )


def test_nullable_arrays_keep_their_parentheses() -> None:
    nullable = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert ts_type({"type": "array", "items": nullable}) == "(string | null)[]"


def test_unknown_keywords_fail_loudly() -> None:
    with pytest.raises(ValueError, match="not"):
        ts_type({"not": {"type": "string"}})
