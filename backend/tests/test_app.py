import json

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.main import create_app


def test_health() -> None:
    client = TestClient(create_app())
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["publishers"] == 20


def test_health_llm_matches_llm_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Health `llm` must match runtime: OPENAI_API_KEY is enough for llm_enabled()."""
    monkeypatch.delenv("DISCO_FORCE_HEURISTIC", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    from app.llm import llm_enabled

    assert llm_enabled() is True
    body = TestClient(create_app()).get("/api/health").json()
    assert body["llm"] is llm_enabled()


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
        payload = json.loads(body.split("event: done")[-1].split("data: ", 1)[1].split("\n", 1)[0])
        assert isinstance(payload["chosen"], list)
        assert isinstance(payload["personas"], list)
        assert isinstance(payload["creatives"], list)
        assert "followup" not in payload


def test_iter_run_exhausts_after_clarify() -> None:
    from app.agents import iter_run

    nodes = [name for name, _ in iter_run("We help people feel better.")]
    assert nodes[-1] == "halt_required"
    malt = [name for name, _ in iter_run("We sell a wide range of single malts.")]
    assert malt[-1] == "assemble_result"
    assert "creative_generation" not in malt


def test_iter_run_handles_generator_exit() -> None:
    """Closing the iterator early must not raise; LangSmith/SSE rely on this."""
    from app.agents import iter_run

    gen = iter_run("We help people feel better.")
    next(gen)
    gen.close()


def test_stream_exhausts_iter_run_after_clarify(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSE must consume iter_run fully after clarify so GeneratorExit never surfaces."""
    from app import agents

    exhausted: list[str] = []
    real = agents.iter_run

    def tracking(*args: object, **kwargs: object):
        for item in real(*args, **kwargs):
            yield item
        exhausted.append("done")

    monkeypatch.setattr(main, "iter_run", tracking)
    client = TestClient(create_app())
    with client.stream("POST", "/api/run/stream", json={"raw_input": "We help people"}) as res:
        res.read()
    assert exhausted == ["done"]
    exhausted.clear()
    with client.stream(
        "POST",
        "/api/run/stream",
        json={"raw_input": "We sell a wide range of single malts."},
    ) as res:
        res.read()
    assert exhausted == ["done"]


def test_stream_clarify_does_not_emit_error() -> None:
    """Clarify paths must not leak GeneratorExit or other internals as SSE errors."""
    client = TestClient(create_app())
    for payload in (
        {"raw_input": "We help people feel better."},
        {"raw_input": "We sell a wide range of single malts."},
    ):
        with client.stream("POST", "/api/run/stream", json=payload) as res:
            body = res.read().decode()
        assert "event: error" not in body
        assert "GeneratorExit" not in body


def test_stream_candle_gifts_completes_without_audience_clarify() -> None:
    """Gift candles skip the shopper question and stream straight to done."""
    client = TestClient(create_app())
    text = (
        "Small-batch candles poured by hand in Vermont. Natural soy wax, "
        "no synthetic fragrances. Mostly bought as gifts."
    )
    with client.stream("POST", "/api/run/stream", json={"raw_input": text}) as res:
        body = res.read().decode()
    assert "event: done" in body
    assert "event: error" not in body
    assert "event: clarify" not in body
    assert '"kind": "ads"' in body


def test_iter_run_emits_assemble_before_ads() -> None:
    from app.agents import iter_run

    nodes = [name for name, _ in iter_run("premium senior dog food")]
    assert "assemble_result" in nodes
    assert "creative_generation" in nodes
    assert nodes.index("assemble_result") < nodes.index("creative_generation")


def test_stream_useful_followup_then_skip() -> None:
    client = TestClient(create_app())
    with client.stream(
        "POST",
        "/api/run/stream",
        json={"raw_input": "We sell a wide range of single malts."},
    ) as res:
        body = res.read().decode()
    assert "event: clarify" in body
    assert "event: done" not in body
    assert '"kind": "publishers"' in body
    assert '"kind": "personas"' in body
    assert '"kind": "ads"' not in body
    assert '"allow_skip": true' in body
    assert '"field": "target_audience"' in body
    thread_id = body.split('"thread_id": "')[-1].split('"')[0]
    with client.stream(
        "POST",
        "/api/run/stream",
        json={"raw_input": "", "thread_id": thread_id, "skip": True},
    ) as res:
        again = res.read().decode()
    assert "event: done" in again
    assert "event: clarify" not in again
    assert '"kind": "ads"' in again
    assert '"kind": "publishers"' not in again


def test_stream_clarify_then_resume() -> None:
    client = TestClient(create_app())
    with client.stream("POST", "/api/run/stream", json={"raw_input": "We help people"}) as res:
        chunk = res.read().decode()
        assert "event: clarify" in chunk
        thread_id = chunk.split('"thread_id": "')[1].split('"')[0]

    with client.stream(
        "POST",
        "/api/run/stream",
            json={"raw_input": "", "thread_id": thread_id, "resume": "premium senior dog food"},
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

    monkeypatch.setattr(main, "iter_run", boom)
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
