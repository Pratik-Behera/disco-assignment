import json
import re

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.main import create_app


def _sse_token_text(body: str) -> str:
    """Join SSE token payloads only (done repeats the same text)."""
    parts: list[str] = []
    for block in body.split("\n\n"):
        if not block.startswith("event: token"):
            continue
        _, _, rest = block.partition("\n")
        if not rest.startswith("data: "):
            continue
        try:
            payload = json.loads(rest[len("data: ") :])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "text" in payload:
            parts.append(str(payload["text"]))
    return "".join(parts)


def test_health() -> None:
    client = TestClient(create_app())
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["publishers"] == 20


def test_root_json_without_ui(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCO_UI_DIR", str(tmp_path))
    body = TestClient(create_app()).get("/").json()
    assert body["service"] == "disco"


def test_root_serves_ui_and_keeps_api(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>ui</title>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("DISCO_UI_DIR", str(tmp_path))
    client = TestClient(create_app())
    home = client.get("/")
    assert home.status_code == 200
    assert "ui" in home.text
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/assets/app.js").text == "console.log(1)"


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
        json={
            "raw_input": (
                "We sell premium organic dog food for health-conscious owners. "
                "$10,000 over 30 days to drive purchases with a $25 CPA."
            )
        },
    ) as res:
        assert res.status_code == 200
        body = res.read().decode()
        assert "event: done" in body
        payload = json.loads(body.split("event: done")[-1].split("data: ", 1)[1].split("\n", 1)[0])
        assert isinstance(payload["chosen"], list)
        assert isinstance(payload["personas"], list)
        assert isinstance(payload["creatives"], list)
        assert "followup" not in payload
        assert '"kind": "campaign"' in body


def test_iter_run_exhausts_after_clarify() -> None:
    from app.agents import iter_run

    nodes = [name for name, _ in iter_run("We help people feel better.")]
    assert nodes[-1] == "halt_required"
    malt = [name for name, _ in iter_run("We sell a wide range of single malts.")]
    assert malt[-1] == "validate_creatives"
    assert "creative_generation" in malt


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
        "no synthetic fragrances. Mostly bought as gifts. "
        "$10,000 over 30 days to drive purchases with a $25 CPA."
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
    assert '"kind": "ads"' in body
    assert '"allow_skip": true' in body
    assert '"field": "target_audience"' in body
    thread_id = body.split('"thread_id": "')[-1].split('"')[0]
    with client.stream(
        "POST",
        "/api/run/stream",
        json={"raw_input": "", "thread_id": thread_id, "skip": True},
    ) as res:
        again = res.read().decode()
    assert "event: done" not in again
    assert "event: clarify" in again
    assert '"kind": "ads"' not in again
    assert '"kind": "publishers"' not in again
    assert '"field": "campaign_objective"' in again


def test_stream_useful_followup_then_chip_pick() -> None:
    """Shopper chip (not Skip) re-runs ads, then campaign — no publisher re-rank."""
    client = TestClient(create_app())
    with client.stream(
        "POST",
        "/api/run/stream",
        json={"raw_input": "We sell a wide range of single malts."},
    ) as res:
        body = res.read().decode()
    assert "event: clarify" in body
    assert "event: done" not in body
    assert '"field": "target_audience"' in body
    assert '"kind": "ads"' in body
    clarify = json.loads(body.split("event: clarify")[-1].split("data: ", 1)[1].split("\n", 1)[0])
    thread_id = clarify["thread_id"]
    chip = (clarify.get("quick_replies") or [""])[0]
    assert chip
    assert chip.lower() != "skip"
    with client.stream(
        "POST",
        "/api/run/stream",
        json={"raw_input": "", "thread_id": thread_id, "resume": chip},
    ) as res:
        again = res.read().decode()
    assert "event: error" not in again
    assert '"kind": "ads"' in again
    assert '"kind": "publishers"' not in again
    assert "event: clarify" in again
    assert '"field": "campaign_objective"' in again
    assert _sse_token_text(again)


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
        assert '"kind": "ads"' in body
        assert "event: clarify" in body
        assert '"field": "campaign_objective"' in body


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


def test_stream_exhausts_iter_run_after_campaign_clarify(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSE must drain iter_run after campaign_input_analysis clarify (no GeneratorExit)."""
    from app import agents

    exhausted: list[str] = []
    real = agents.iter_run

    def tracking(*args: object, **kwargs: object):
        for item in real(*args, **kwargs):
            yield item
        exhausted.append("done")

    monkeypatch.setattr(main, "iter_run", tracking)
    client = TestClient(create_app())
    with client.stream(
        "POST",
        "/api/run/stream",
        json={"raw_input": "premium senior dog food"},
    ) as res:
        body = res.read().decode()
    assert exhausted == ["done"]
    assert "event: error" not in body
    assert "GeneratorExit" not in body
    assert "event: clarify" in body
    assert '"field": "campaign_objective"' in body


def test_stream_skip_performance_goal_then_campaign_done() -> None:
    client = TestClient(create_app())
    with client.stream(
        "POST",
        "/api/run/stream",
        json={
            "raw_input": (
                "premium senior dog food. $10,000 over 30 days to drive purchases."
            )
        },
    ) as res:
        body = res.read().decode()
    assert "event: clarify" in body
    assert "event: done" not in body
    assert '"field": "performance_goal"' in body
    assert '"allow_skip": true' in body
    assert '"kind": "ads"' in body
    thread_id = body.split('"thread_id": "')[-1].split('"')[0]
    with client.stream(
        "POST",
        "/api/run/stream",
        json={"raw_input": "", "thread_id": thread_id, "skip": True},
    ) as res:
        again = res.read().decode()
    assert "event: error" not in again
    assert "event: done" in again
    assert '"kind": "campaign"' in again
    assert '"kind": "publishers"' not in again


def test_chained_revisions_do_not_resurrect_the_first_message() -> None:
    """Each edit layers on the last plan: the chip budget and the opening "30 days" stay gone."""
    client = TestClient(create_app())
    with client.stream(
        "POST",
        "/api/run/stream",
        json={"raw_input": "premium senior dog food. Drive purchases for 30 days."},
    ) as res:
        body = res.read().decode()
    assert '"field": "total_budget_usd"' in body

    def resume(prev: str, **payload: object) -> str:
        thread_id = prev.split('"thread_id": "')[-1].split('"')[0]
        with client.stream(
            "POST", "/api/run/stream", json={"thread_id": thread_id, **payload}
        ) as stream:
            return stream.read().decode()

    picked = resume(body, raw_input="", resume="$500")
    plan = resume(picked, raw_input="", skip=True)
    assert "$500 over 30 days" in _sse_token_text(plan)
    for edit, expected in (
        ("$2,000", "$2,000 over 30 days"),
        ("60 days", "$2,000 over 60 days"),
        # A later edit must not let the first message's "30 days" back in.
        ("Build awareness", "$2,000 over 60 days"),
        ("$100", "$100 over 60 days"),
    ):
        plan = resume(plan, raw_input=edit)
        assert "event: error" not in plan
        assert expected in _sse_token_text(plan)


def test_stream_revision_updates_budget_without_rerank() -> None:
    client = TestClient(create_app())
    complete = (
        "premium senior dog food. $10,000 over 30 days to drive purchases with a $25 CPA."
    )
    with client.stream("POST", "/api/run/stream", json={"raw_input": complete}) as res:
        first = res.read().decode()
    assert "event: done" in first
    assert '"kind": "campaign"' in first
    assert '"kind": "publishers"' in first
    thread_id = first.split('"thread_id": "')[-1].split('"')[0]
    first_pcts = re.findall(r"(\d+)%\s+\$", _sse_token_text(first))
    with client.stream(
        "POST",
        "/api/run/stream",
        json={"raw_input": "Actually, let's make it $15k.", "thread_id": thread_id},
    ) as res:
        again = res.read().decode()
    assert "event: error" not in again
    assert "event: done" in again
    assert '"kind": "campaign"' in again
    assert '"kind": "publishers"' not in again
    assert '"kind": "ads"' not in again
    joined = _sse_token_text(again)
    assert "$15,000" in joined
    assert "$10,000" not in joined
    assert re.findall(r"(\d+)%\s+\$", joined) == first_pcts
    assert first_pcts
