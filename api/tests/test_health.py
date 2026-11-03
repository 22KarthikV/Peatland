import json
from pathlib import Path

import yaml
from api.main import create_app
from api.settings import Settings
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


def _client() -> TestClient:
    return TestClient(create_app(Settings(api_keys="")))


def test_health_matches_fixture_and_schema() -> None:
    spec = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
    fixture = json.loads((CONTRACTS / "fixtures" / "health.200.json").read_text(encoding="utf-8"))
    body = _client().get("/health").json()
    assert body == fixture
    schema = {**spec["components"]["schemas"]["Health"]}
    Draft202012Validator(schema).validate(body)


def test_health_needs_no_api_key() -> None:
    assert _client().get("/health").status_code == 200


def test_generated_paths_are_subset_of_contract() -> None:
    spec = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
    generated = create_app(Settings(api_keys="")).openapi()["paths"]
    assert set(generated) <= set(spec["paths"])
    for path, ops in generated.items():
        assert set(ops) <= set(spec["paths"][path])


def test_cors_preflight_allows_web_origin() -> None:
    from api.main import create_app
    from api.settings import Settings
    from fastapi.testclient import TestClient

    client = TestClient(create_app(Settings(cors_origins="http://localhost:5173")))
    r = client.options(
        "/sites",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-api-key",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
