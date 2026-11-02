import api


def test_package_imports() -> None:
    assert api.__name__ == "api"
