import json
from pathlib import Path

from api.auth import hash_key
from api.main import create_app
from api.ratelimit import RateLimiter, enforce_rate_limit
from api.settings import Settings
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"
KEY = "test-key"


class Body(BaseModel):
    n: int


def _app(job_limit: int = 10, clock: list[float] | None = None) -> FastAPI:
    ticks = clock if clock is not None else [0.0]
    settings = Settings(
        api_keys=f"{hash_key(KEY)}, {hash_key('other')}",
        rate_limit_job_per_minute=job_limit,
        rate_limit_default_per_minute=3,
    )
    app = create_app(settings, RateLimiter(clock=lambda: ticks[0]))
    # Drop the included routers (real /sites): this test uses POST /sites as a stand-in job route.
    app.router.routes = [r for r in app.router.routes if "Included" not in type(r).__name__]

    @app.get("/protected", dependencies=[Depends(enforce_rate_limit)])
    async def protected() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/sites", dependencies=[Depends(enforce_rate_limit)])
    async def sites(body: Body) -> dict[str, int]:
        return {"n": body.n}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    return app


def _fixture(name: str) -> dict[str, object]:
    data: dict[str, object] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def test_missing_and_wrong_key_return_401_fixture() -> None:
    client = TestClient(_app())
    for headers in ({}, {"X-API-Key": "nope"}):
        r = client.get("/protected", headers=headers)
        assert r.status_code == 401
        assert r.json() == _fixture("error.401.json")


def test_valid_key_passes_with_ratelimit_headers() -> None:
    r = TestClient(_app()).get("/protected", headers={"X-API-Key": KEY})
    assert r.status_code == 200
    assert r.headers["RateLimit-Limit"] == "3"
    assert r.headers["RateLimit-Remaining"] == "2"


def test_rate_limit_429_with_retry_after_then_window_resets() -> None:
    clock = [0.0]
    client = TestClient(_app(clock=clock))
    h = {"X-API-Key": KEY}
    for _ in range(3):
        assert client.get("/protected", headers=h).status_code == 200
    clock[0] = 48.0
    r = client.get("/protected", headers=h)
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "12"
    assert r.json() == _fixture("error.429.json")
    # other keys are independent
    assert client.get("/protected", headers={"X-API-Key": "other"}).status_code == 200
    clock[0] = 60.0
    assert client.get("/protected", headers=h).status_code == 200


def test_job_routes_use_the_tighter_limit() -> None:
    client = TestClient(_app(job_limit=1))
    h = {"X-API-Key": KEY}
    assert client.post("/sites", json={"n": 1}, headers=h).status_code == 200
    r = client.post("/sites", json={"n": 1}, headers=h)
    assert r.status_code == 429
    assert r.json()["code"] == "rate_limited"
    # default group unaffected
    assert client.get("/protected", headers=h).status_code == 200


def test_validation_error_is_422_in_contract_format() -> None:
    r = TestClient(_app()).post("/sites", json={"n": "x"}, headers={"X-API-Key": KEY})
    assert r.status_code == 422
    body = r.json()
    assert set(body) == {"code", "message", "detail"}
    assert body["code"] == "validation_error"
    assert body["detail"][0]["loc"] == ["body", "n"]
    assert set(body["detail"][0]) == {"loc", "msg", "type"}


def test_unknown_route_is_404_error_format() -> None:
    r = TestClient(_app()).get("/nope")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"
    assert r.json()["detail"] is None


def test_unexpected_error_is_500_without_leaking() -> None:
    r = TestClient(_app(), raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500
    assert r.json()["code"] == "internal_error"
    assert "secret" not in r.text
    assert "RuntimeError" not in r.text
