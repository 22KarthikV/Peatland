"""Contract C4: fixtures validate against openapi.yaml response schemas and queue.schema.json."""

import copy
import csv
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
SPEC: dict[str, Any] = yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
QUEUE_SCHEMA: dict[str, Any] = json.loads((ROOT / "queue.schema.json").read_text(encoding="utf-8"))
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

# fixture file -> (method, path, status) for success responses.
SUCCESS: dict[str, tuple[str, str, str]] = {
    "health.200.json": ("get", "/health", "200"),
    "sites.create.201.json": ("post", "/sites", "201"),
    "sites.list.200.json": ("get", "/sites", "200"),
    "sites.get.200.json": ("get", "/sites/{site_id}", "200"),
    "runs.create.202.json": ("post", "/sites/{site_id}/runs", "202"),
    "runs.create.200.json": ("post", "/sites/{site_id}/runs", "200"),
    "runs.get.200.json": ("get", "/runs/{run_id}", "200"),
    "runs.get.failed.200.json": ("get", "/runs/{run_id}", "200"),
    "classifications.list.200.json": ("get", "/sites/{site_id}/classifications", "200"),
    "changes.create.200.json": ("post", "/sites/{site_id}/changes", "200"),
    "changes.create.202.json": ("post", "/sites/{site_id}/changes", "202"),
    "changes.stats.200.json": ("get", "/changes/{change_id}/stats", "200"),
}
# Error fixtures are checked against every operation that declares the status.
ERRORS: dict[str, list[str]] = {
    "401": ["error.401.json"],
    "404": ["error.404.json"],
    "406": ["error.406.json"],
    "409": ["error.409.json"],
    "422": [
        "error.422.validation_error.json",
        "error.422.boundary_too_large.json",
        "error.422.classification_missing.json",
    ],
    "429": ["error.429.json"],
}


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def resolve(node: dict[str, Any]) -> dict[str, Any]:
    """Follow local `$ref`s (used for components/responses)."""
    while "$ref" in node:
        target: Any = SPEC
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def operations() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, op)
        for path, item in SPEC["paths"].items()
        if not any(op.get("x-external") for m, op in item.items() if m in HTTP_METHODS)
        for method, op in item.items()
        if method in HTTP_METHODS
    ]


def response_schema(
    op: dict[str, Any], status: str, media: str = "application/json"
) -> dict[str, Any]:
    """The response schema as a standalone 2020-12 document with `components` attached."""
    response = resolve(op["responses"][status])
    schema: dict[str, Any] = copy.deepcopy(response["content"][media]["schema"])
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["components"] = SPEC["components"]
    return schema


def validator_for(schema: dict[str, Any]) -> Draft202012Validator:
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def find_op(method: str, path: str) -> dict[str, Any]:
    op: dict[str, Any] = SPEC["paths"][path][method]
    return op


@pytest.mark.parametrize("name", sorted(SUCCESS))
def test_success_fixture_matches_response_schema(name: str) -> None:
    method, path, status = SUCCESS[name]
    validator = validator_for(response_schema(find_op(method, path), status))
    errors = sorted(validator.iter_errors(load(name)), key=lambda e: list(e.path))
    assert not errors, [f"{list(e.path)}: {e.message}" for e in errors]


ERROR_CASES = [
    (name, method, path, status)
    for status, names in ERRORS.items()
    for name in names
    for method, path, op in operations()
    if status in op["responses"]
]


@pytest.mark.parametrize(("name", "method", "path", "status"), ERROR_CASES)
def test_error_fixture_matches_every_declaring_operation(
    name: str, method: str, path: str, status: str
) -> None:
    validator = validator_for(response_schema(find_op(method, path), status))
    errors = sorted(validator.iter_errors(load(name)), key=lambda e: list(e.path))
    assert not errors, [f"{list(e.path)}: {e.message}" for e in errors]


def test_every_json_response_has_a_fixture() -> None:
    """Each (operation, status) with a JSON body has at least one fixture."""
    covered = {(m, p, s) for m, p, s in SUCCESS.values()}
    covered |= {(m, p, s) for s in ERRORS for m, p, op in operations() if s in op["responses"]}
    missing = [
        (method, path, status)
        for method, path, op in operations()
        for status, response in op["responses"].items()
        if "application/json" in resolve(response).get("content", {})
        and (method, path, status) not in covered
    ]
    assert not missing


def test_every_fixture_file_is_registered() -> None:
    known = set(SUCCESS) | {n for names in ERRORS.values() for n in names}
    known |= {"queue.pipeline.json", "queue.change.json", "changes.stats.200.csv"}
    found = {p.name for p in FIXTURES.iterdir() if p.suffix in {".json", ".csv"}}
    assert found == known


def test_error_fixture_codes_are_declared_in_spec() -> None:
    codes = set(SPEC["components"]["schemas"]["Error"]["properties"]["code"]["enum"])
    for names in ERRORS.values():
        for name in names:
            assert load(name)["code"] in codes


def test_fixtures_use_demo_site_and_dates() -> None:
    site = load("sites.get.200.json")
    assert site["slug"] == "glen-trool"
    assert [w["start"] for w in site["available_windows"]] == ["2025-03-19", "2025-04-08"]
    assert load("changes.create.200.json")["site_id"] == site["id"]


def test_ids_are_consistent_across_fixtures() -> None:
    classifications = load("classifications.list.200.json")["items"]
    change = load("changes.create.200.json")
    assert change["from_classification_id"] == classifications[0]["id"]
    assert change["to_classification_id"] == classifications[1]["id"]
    run = load("runs.get.200.json")
    assert run["outputs"]["classification_ids"] == [c["id"] for c in classifications]
    assert load("changes.stats.200.json")["change"] == change
    assert load("changes.create.202.json")["outputs"]["change_id"] is None


def test_stats_are_arithmetically_consistent() -> None:
    stats = load("changes.stats.200.json")
    total = stats["total_ha"]
    for key in ("from_class_areas", "to_class_areas"):
        assert sum(c["area_ha"] for c in stats[key]) == pytest.approx(total, abs=0.01)
    assert sum(t["area_ha"] for t in stats["transitions"]) == pytest.approx(total, abs=0.01)
    for t in stats["transitions"]:
        assert t["percent"] == pytest.approx(t["area_ha"] / total * 100, abs=0.01)


def test_stats_csv_matches_json() -> None:
    stats = load("changes.stats.200.json")
    text = (FIXTURES / "changes.stats.200.csv").read_text(encoding="utf-8")
    rows = list(csv.DictReader(text.splitlines()))
    assert list(rows[0]) == [
        "from_class_id",
        "from_class",
        "to_class_id",
        "to_class",
        "area_ha",
        "percent",
    ]
    assert len(rows) == len(stats["transitions"])
    for row, t in zip(rows, stats["transitions"], strict=True):
        assert int(row["from_class_id"]) == t["from_class"]["id"]
        assert row["to_class"] == t["to_class"]["name"]
        assert float(row["area_ha"]) == pytest.approx(t["area_ha"])


def test_class_refs_match_classes_yaml() -> None:
    classes = yaml.safe_load((ROOT / "classes.yaml").read_text(encoding="utf-8"))["classes"]
    by_id = {c["id"]: c for c in classes}
    stats = load("changes.stats.200.json")
    refs = [a["class"] for a in stats["from_class_areas"] + stats["to_class_areas"]]
    refs += [t["from_class"] for t in stats["transitions"]] + [
        t["to_class"] for t in stats["transitions"]
    ]
    for ref in refs:
        c = by_id[ref["id"]]
        assert (ref["name"], ref["label"], ref["hex"]) == (c["name"], c["label"], c["hex"])


# ---- queue messages ----


@pytest.fixture(scope="module")
def queue_validator() -> Draft202012Validator:
    return validator_for(QUEUE_SCHEMA)


@pytest.mark.parametrize("name", ["queue.pipeline.json", "queue.change.json"])
def test_queue_fixture_valid(queue_validator: Draft202012Validator, name: str) -> None:
    errors = list(queue_validator.iter_errors(load(name)))
    assert not errors, [e.message for e in errors]


def test_queue_messages_agree_with_run_fixtures() -> None:
    pipeline = load("queue.pipeline.json")
    run = load("runs.create.202.json")
    assert pipeline["run_id"] == run["id"]
    assert pipeline["site"]["id"] == run["site_id"]
    assert pipeline["params"] == run["params"]
    assert pipeline["model"] == {"name": run["model"]["name"], "version": run["model"]["version"]}
    change = load("queue.change.json")
    assert change["run_id"] == load("changes.create.202.json")["id"]


def mutate(path: list[str | int], value: Any) -> dict[str, Any]:
    msg = copy.deepcopy(load("queue.pipeline.json"))
    node: Any = msg
    for key in path[:-1]:
        node = node[key]
    if value is KeyError:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return msg


INVALID = [
    pytest.param(mutate(["contract_version"], 2), id="unknown-contract-version"),
    pytest.param(mutate(["contract_version"], KeyError), id="missing-contract-version"),
    pytest.param(mutate(["kind"], "export"), id="unknown-kind"),
    pytest.param(mutate(["run_id"], "run-0001"), id="run-id-not-uuid"),
    pytest.param(mutate(["site", "slug"], "Glen Trool"), id="bad-slug"),
    pytest.param(mutate(["windows"], []), id="no-windows"),
    pytest.param(mutate(["windows", 0, "season"], "monsoon"), id="bad-season"),
    pytest.param(mutate(["windows", 0, "start"], "19/03/2025"), id="bad-date"),
    pytest.param(mutate(["model", "version"], KeyError), id="model-without-version"),
    pytest.param(mutate(["idempotency_key"], "ABC"), id="bad-key"),
    pytest.param(mutate(["surprise"], 1), id="extra-property"),
    pytest.param(
        mutate(["kind"], "change") | {"windows": load("queue.pipeline.json")["windows"][:1]},
        id="change-needs-two-windows",
    ),
]


@pytest.mark.parametrize("message", INVALID)
def test_queue_schema_rejects_invalid(
    queue_validator: Draft202012Validator, message: dict[str, Any]
) -> None:
    assert not queue_validator.is_valid(message)
