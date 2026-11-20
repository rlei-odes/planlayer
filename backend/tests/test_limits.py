"""Bounds on what a request carries (planlayer.api.limits): every text and list a request body
or query takes has a maximum, a request's body has a size cap, and a number too large for its
column is a 422."""

import re
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from planlayer.api.bodylimit import BodyLimit, body_limit_for
from planlayer.api.typescript import app_schema
from planlayer.sheet import EditError, fits, parse_amount


def _unbounded(spec: dict[str, Any]) -> list[str]:
    """Every string without maxLength and every array or object without a size limit, in what
    the requests take. Enums and formats such as dates bound themselves; a Decimal field (a
    number or a string) is read by pydantic as a number."""
    schemas = spec["components"]["schemas"]
    found: list[str] = []
    seen: set[str] = set()

    def walk(s: dict[str, Any], where: str) -> None:
        if "$ref" in s:
            name = s["$ref"].split("/")[-1]
            if name in seen:
                return
            seen.add(name)
            s, where = schemas[name], name
        for key in ("anyOf", "oneOf", "allOf"):
            subs = s.get(key, [])
            if {"number", "string"} <= {sub.get("type") for sub in subs}:
                continue  # a Decimal
            for sub in subs:
                walk(sub, where)
        kind = s.get("type")
        bounded = {"enum", "format", "maxLength"} & s.keys()
        if kind == "string" and not bounded:
            found.append(where)
        if kind == "array":
            if "maxItems" not in s:
                found.append(f"{where}[]")
            walk(s.get("items", {}), where)
        if kind == "object":
            if "additionalProperties" in s and "maxProperties" not in s:
                found.append(f"{where}{{}}")
            if isinstance(s.get("additionalProperties"), dict):
                walk(s["additionalProperties"], where)
            for prop, sub in s.get("properties", {}).items():
                walk(sub, f"{where}.{prop}")

    for path, item in spec["paths"].items():
        for method, op in item.items():
            for media, content in op.get("requestBody", {}).get("content", {}).items():
                if media == "application/octet-stream":
                    continue  # an uploaded file: bounded by the body size cap
                walk(content["schema"], f"{method.upper()} {path}")
            for param in op.get("parameters", []):
                if param["in"] == "query":
                    walk(param["schema"], f"{method.upper()} {path} ?{param['name']}")
    return sorted(set(found))


def test_every_request_text_and_list_is_bounded() -> None:
    assert _unbounded(app_schema()) == [], "use the types of planlayer.api.limits"


def _echo() -> TestClient:
    app = FastAPI()

    @app.post("/api/echo")
    def echo(body: dict[str, str]) -> int:
        return len(body)

    app.add_middleware(BodyLimit)
    return TestClient(app)


def test_a_body_over_the_cap_is_refused() -> None:
    client = _echo()
    assert client.post("/api/echo", json={"a": "b"}).status_code == 200
    big = {"a": "x" * (body_limit_for("/api/echo") + 1)}
    assert client.post("/api/echo", json=big).status_code == 413


def test_a_streamed_body_over_the_cap_is_refused() -> None:
    def chunks() -> Any:
        for _ in range(3):
            yield b"x" * body_limit_for("/api/echo")

    response = _echo().post("/api/echo", content=chunks())
    assert response.status_code == 413


def test_the_salary_upload_takes_larger_files() -> None:
    upload = "/api/rounds/B2026/personnel/salaries/check"
    assert body_limit_for(upload) > 20 * 1024 * 1024 > body_limit_for("/api/echo")


def test_amounts_fit_their_column() -> None:
    assert fits(Decimal("9999999999999999.99"))  # numeric(18, 2)
    assert not fits(Decimal("-10000000000000000"))
    assert fits(Decimal("999.99"), 5, 2) and not fits(Decimal("1000"), 5, 2)
    assert parse_amount("9999999999999999.99") == Decimal("9999999999999999.99")
    with pytest.raises(EditError, match="too large"):
        parse_amount("1e20")


def test_the_frontend_has_the_same_bounds() -> None:
    """frontend/src/limits.ts stops typing where these refuse."""
    from pathlib import Path

    from planlayer.api import limits

    ts = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "limits.ts").read_text()
    frontend = {k: int(v.replace("_", "")) for k, v in re.findall(r"(\w+): ([\d_]+),", ts)}
    types = {
        "code": limits.Code,
        "name": limits.Name,
        "email": limits.Email,
        "longText": limits.LongText,
        "number": limits.Number,
        "query": limits.SqlQuery,
        "password": limits.Password,
        "pem": limits.Pem,
    }
    backend = {name: TypeAdapter(t).json_schema()["maxLength"] for name, t in types.items()}
    assert frontend == backend
