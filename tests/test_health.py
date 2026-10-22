import json
import logging
import sys

from fastapi.testclient import TestClient

from app.core.logging import JsonFormatter, request_id_var


def test_health_reports_database_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_request_id_is_echoed_or_generated(client: TestClient) -> None:
    echoed = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["X-Request-ID"] == "abc-123"

    generated = client.get("/health", headers={"X-Request-ID": "not valid!"})
    assert generated.headers["X-Request-ID"] != "not valid!"
    assert len(generated.headers["X-Request-ID"]) == 32


def test_json_formatter_emits_structured_fields() -> None:
    token = request_id_var.set("req-1")
    try:
        record = logging.makeLogRecord(
            {"name": "app", "levelname": "INFO", "msg": "hello %s", "args": ("world",)}
        )
        record.tenant_id = 7
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert payload["message"] == "hello world"
    assert payload["request_id"] == "req-1"
    assert payload["tenant_id"] == 7
    assert payload["level"] == "INFO"


def test_json_formatter_includes_exception() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.makeLogRecord({"msg": "failed", "exc_info": sys.exc_info()})
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exc_info"]
