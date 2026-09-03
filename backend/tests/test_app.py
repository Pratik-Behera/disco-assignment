import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.main import create_app


def test_health() -> None:
    client = TestClient(create_app())
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["publishers"] == 20


def test_examples() -> None:
    client = TestClient(create_app())
    examples = client.get("/api/examples").json()["examples"]
    assert examples
    assert all(isinstance(line, str) for line in examples)
    # The file's `---` rule and the `1. ` numbering are formatting, not advertiser copy.
    assert "---" not in examples
    assert not any(line[0].isdigit() for line in examples)


def test_stream_done() -> None:
    client = TestClient(create_app())
    with client.stream(
        "POST",
        "/api/run/stream",
        json={"raw_input": "We sell premium organic dog food for health-conscious owners"},
    ) as res:
        assert res.status_code == 200
        body = res.read().decode()
        assert "event: done" in body


def test_stream_clarify_then_resume() -> None:
    client = TestClient(create_app())
    with client.stream("POST", "/api/run/stream", json={"raw_input": "We help people"}) as res:
        chunk = res.read().decode()
        assert "event: clarify" in chunk
        thread_id = chunk.split('"thread_id": "')[1].split('"')[0]

    with client.stream(
        "POST",
        "/api/run/stream",
        json={"raw_input": "", "thread_id": thread_id, "resume": "premium dog food"},
    ) as res:
        body = res.read().decode()
        assert "event: done" in body


def test_resume_without_thread_id_is_400() -> None:
    client = TestClient(create_app())
    res = client.post("/api/run/stream", json={"raw_input": "", "resume": "dog food"})
    assert res.status_code == 400


def test_agent_error_does_not_leak_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider errors quote the failing request; the browser must not see the key."""

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("401 Incorrect API key provided: sk-secret-123")

    monkeypatch.setattr(main, "agent_run", boom)
    client = TestClient(create_app())
    with client.stream("POST", "/api/run/stream", json={"raw_input": "premium dog food"}) as res:
        body = res.read().decode()
    assert "event: error" in body
    assert "sk-secret-123" not in body


def test_resume_with_unknown_thread_id_is_400() -> None:
    """A reload drops _pending; resuming must fail loudly, not answer without the query."""
    client = TestClient(create_app())
    res = client.post(
        "/api/run/stream",
        json={"raw_input": "", "thread_id": "not-a-live-thread", "resume": "dog food"},
    )
    assert res.status_code == 400
