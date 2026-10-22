import pytest
from fastapi.testclient import TestClient

from app import cli
from app.main import app
from tests.conftest import login


def test_seed_demo_is_idempotent_and_usable(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["seed-demo"]) == 0
    assert "seeded tenants: [1, 2]" in capsys.readouterr().out
    assert cli.main(["seed-demo"]) == 0
    assert "already present" in capsys.readouterr().out

    client = TestClient(app)
    assert login(client, "beta-cashier", cli.DEMO_PASSWORD).status_code == 200
    skus = {p["sku"] for p in client.get("/products").json()}
    assert skus == {"TEA-GRN", "MUG-CER"}


def test_create_tenant_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD", "a-strong-password")
    args = ["create-tenant", "--name", "Gamma", "--slug", "gamma", "--admin-username", "g-admin"]
    assert cli.main(args) == 0
    assert "created tenant" in capsys.readouterr().out
    assert login(TestClient(app), "g-admin", "a-strong-password").status_code == 200

    monkeypatch.setenv("ADMIN_PASSWORD", "short")
    assert cli.main([*args[:-1], "other-admin"]) == 2
