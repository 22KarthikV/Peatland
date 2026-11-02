import os
from unittest.mock import MagicMock, patch

import pytest
from api.auth import hash_key, is_valid_key, load_key_hashes, require_api_key
from api.main import create_app
from api.settings import Settings
from fastapi import Depends
from fastapi.testclient import TestClient


def test_hash_is_sha256_hex() -> None:
    assert hash_key("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_is_valid_key() -> None:
    allowed = frozenset({hash_key("a"), hash_key("b")})
    assert is_valid_key("b", allowed)
    assert not is_valid_key("c", allowed)
    assert not is_valid_key("", allowed)
    assert not is_valid_key(None, allowed)


def test_env_hashes_only_when_no_secret() -> None:
    s = Settings(api_keys=hash_key("k").upper())
    assert load_key_hashes(s) == {hash_key("k")}


def test_secret_keys_are_hashed_and_merged_with_env() -> None:
    client = MagicMock()
    client.get_secret_value.return_value = {"SecretString": "dev-only-key, second"}
    with patch("boto3.client", return_value=client) as make:
        s = Settings(
            api_keys=hash_key("envkey"),
            api_key_secret_id="peatwatch/api-key",
            aws_endpoint_url="http://localhost:4566",
        )
        hashes = load_key_hashes(s)
    assert hashes == {hash_key("envkey"), hash_key("dev-only-key"), hash_key("second")}
    assert make.call_args.kwargs["endpoint_url"] == "http://localhost:4566"
    client.get_secret_value.assert_called_once_with(SecretId="peatwatch/api-key")


def test_secret_failure_falls_back_to_env() -> None:
    with patch("boto3.client", side_effect=RuntimeError("down")):
        s = Settings(api_keys=hash_key("envkey"), api_key_secret_id="x")
        assert load_key_hashes(s) == {hash_key("envkey")}


def test_app_accepts_secret_key_and_rejects_others() -> None:
    client = MagicMock()
    client.get_secret_value.return_value = {"SecretString": "dev-only-key"}
    with patch("boto3.client", return_value=client):
        app = create_app(Settings(api_key_secret_id="peatwatch/api-key"))

    @app.get("/protected", dependencies=[Depends(require_api_key)])
    async def protected() -> dict[str, bool]:
        return {"ok": True}

    c = TestClient(app)
    assert c.get("/health").status_code == 200
    r = c.get("/protected", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401
    assert set(r.json()) == {"code", "message", "detail"}
    assert c.get("/protected").status_code == 401
    assert c.get("/protected", headers={"X-API-Key": "dev-only-key"}).status_code == 200


@pytest.mark.localstack
@pytest.mark.skipif(os.environ.get("RUN_LOCALSTACK") != "1", reason="needs LocalStack")
def test_localstack_secret_dev_key() -> None:
    s = Settings(
        api_key_secret_id="peatwatch/api-key",
        aws_endpoint_url=os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566"),
    )
    assert hash_key("dev-only-key") in load_key_hashes(s)
