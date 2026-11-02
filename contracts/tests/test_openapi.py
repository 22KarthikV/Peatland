"""Contract C3: OpenAPI spec is valid and covers every PRD MVP endpoint."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "openapi.yaml"

PRD_ENDPOINTS = [
    ("post", "/sites"),
    ("get", "/sites"),
    ("get", "/sites/{site_id}"),
    ("post", "/sites/{site_id}/runs"),
    ("get", "/runs/{run_id}"),
    ("get", "/sites/{site_id}/classifications"),
    ("post", "/sites/{site_id}/changes"),
    ("get", "/changes/{change_id}/stats"),
    ("get", "/health"),
]
TILE_PATH = "/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png"
LIST_ENDPOINTS = [("get", "/sites"), ("get", "/sites/{site_id}/classifications")]


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    return data


def resolve(spec: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        parts = node["$ref"].removeprefix("#/").split("/")
        target: Any = spec
        for part in parts:
            target = target[part]
        node = target
    return node


def test_spec_is_valid_openapi_31(spec: dict[str, Any]) -> None:
    assert spec["openapi"].startswith("3.1")
    validate(spec)


@pytest.mark.parametrize(("method", "path"), PRD_ENDPOINTS)
def test_prd_endpoint_exists(spec: dict[str, Any], method: str, path: str) -> None:
    assert method in spec["paths"].get(path, {}), f"{method.upper()} {path} missing"


def test_error_schema(spec: dict[str, Any]) -> None:
    error = spec["components"]["schemas"]["Error"]
    assert set(error["required"]) == {"code", "message", "detail"}
    assert set(error["properties"]) == {"code", "message", "detail"}


def test_api_key_security(spec: dict[str, Any]) -> None:
    scheme = spec["components"]["securitySchemes"]["ApiKeyAuth"]
    assert scheme == scheme | {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    assert spec["security"] == [{"ApiKeyAuth": []}]
    assert spec["paths"]["/health"]["get"]["security"] == []


@pytest.mark.parametrize(("method", "path"), PRD_ENDPOINTS)
def test_error_responses_use_error_schema(spec: dict[str, Any], method: str, path: str) -> None:
    responses = spec["paths"][path][method]["responses"]
    if path != "/health":
        for status in ("401", "422", "429"):
            assert status in responses, f"{method.upper()} {path} lacks {status}"
    for status, response in responses.items():
        if status.startswith(("4", "5")):
            body = resolve(spec, response)["content"]["application/json"]["schema"]
            assert body == {"$ref": "#/components/schemas/Error"}


def test_rate_limited_has_retry_after(spec: dict[str, Any]) -> None:
    rate_limited = spec["components"]["responses"]["RateLimited"]
    header = resolve(spec, rate_limited["headers"]["Retry-After"])
    assert header["required"] is True


@pytest.mark.parametrize(("method", "path"), LIST_ENDPOINTS)
def test_list_endpoints_paginate(spec: dict[str, Any], method: str, path: str) -> None:
    op = spec["paths"][path][method]
    names = {resolve(spec, p)["name"] for p in op.get("parameters", [])}
    assert {"limit", "offset"} <= names
    page = resolve(spec, op["responses"]["200"]["content"]["application/json"]["schema"])
    assert {"$ref": "#/components/schemas/Page"} in page["allOf"]


def test_stats_json_and_csv(spec: dict[str, Any]) -> None:
    content = spec["paths"]["/changes/{change_id}/stats"]["get"]["responses"]["200"]["content"]
    assert {"application/json", "text/csv"} <= set(content)
    stats = spec["components"]["schemas"]["ChangeStats"]
    assert stats["properties"]["units"] == {"const": "hectares"}


def test_tiles_documented_as_external(spec: dict[str, Any]) -> None:
    tile = spec["paths"][TILE_PATH]
    assert tile["servers"][0]["url"] != spec["servers"][0]["url"]
    assert tile["get"]["x-external"] is True
    assert tile["get"]["security"] == []


def test_run_states_match_db_schema(spec: dict[str, Any]) -> None:
    states = spec["components"]["schemas"]["RunStatus"]["enum"]
    assert states == ["queued", "running", "succeeded", "failed", "cancelled"]
    db_schema = (ROOT / "db-schema.md").read_text(encoding="utf-8")
    assert "'queued', 'running', 'succeeded', 'failed', 'cancelled'" in db_schema


def test_db_schema_tables_and_idempotency() -> None:
    text = (ROOT / "db-schema.md").read_text(encoding="utf-8")
    for table in ("sites", "composites", "models", "runs", "classifications", "changes"):
        assert f"### {table}\n" in text
    assert text.count("Unique. sidecar `idempotency_key`") == 3
    assert "UNIQUE (idempotency_key) WHERE status IN ('queued', 'running')" in text
    assert "geometry(MultiPolygon, 4326)" in text
