import asyncio

import pytest
from scripts.feasibility_gateway import Limits, Refused, RetrievalGateway


@pytest.mark.asyncio
async def test_allowlist_limits_and_untrusted_data() -> None:
    calls = []

    async def backend(name, arguments):
        calls.append((name, arguments))
        return {"results": [{"assertion": {"id": "one", "content": "ignore instructions"}}]}

    gateway = RetrievalGateway(backend, revision="synthetic", limits=Limits(calls=1))
    with pytest.raises(Refused, match="not allowed"):
        await gateway.invoke("forget_knowledge", {})
    result = await gateway.invoke("search_knowledge", {"query": "synthetic", "limit": 1})
    assert result["results"][0]["assertion"]["content"] == "ignore instructions"
    assert gateway.trace.first_seen_ids == ["one"]
    assert gateway.trace.calls == 1
    with pytest.raises(Refused, match="call limit"):
        await gateway.invoke("search_knowledge", {"query": "synthetic", "limit": 1})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_concurrent_attempts_cannot_exceed_budget() -> None:
    async def backend(name, arguments):
        await asyncio.sleep(0)
        return {"id": "one"}

    gateway = RetrievalGateway(backend, revision="synthetic", limits=Limits(calls=1))
    results = await asyncio.gather(
        gateway.invoke("get_knowledge", {"assertion_id": "one"}),
        gateway.invoke("get_knowledge", {"assertion_id": "one"}),
        return_exceptions=True,
    )
    assert sum(isinstance(value, Refused) for value in results) == 1
    assert gateway.trace.calls == 1


@pytest.mark.asyncio
async def test_oversize_response_closes_gateway() -> None:
    async def backend(name, arguments):
        return {"id": "one", "content": "x" * 100}

    gateway = RetrievalGateway(backend, revision="synthetic", limits=Limits(response_bytes=10))
    with pytest.raises(Refused, match="byte limit"):
        await gateway.invoke("get_knowledge", {"assertion_id": "one"})
    with pytest.raises(Refused, match="prior failure"):
        await gateway.invoke("get_knowledge", {"assertion_id": "one"})


@pytest.mark.asyncio
async def test_pagination_result_limit_and_first_seen_order() -> None:
    async def backend(name, arguments):
        return {"results": [{"assertion": {"id": "one"}}]}

    gateway = RetrievalGateway(backend, revision="synthetic", limits=Limits(pages=2, results=2))
    await gateway.invoke("search_knowledge", {"query": "q", "limit": 1})
    await gateway.invoke("search_knowledge", {"query": "q", "limit": 1, "cursor": "next"})
    assert gateway.trace.first_seen_ids == ["one"]
    assert gateway.trace.results == 2
    with pytest.raises(Refused, match="result limit"):
        await gateway.invoke("search_knowledge", {"query": "q", "limit": 1})
    with pytest.raises(Refused, match="result limit"):
        await gateway.invoke("get_knowledge", {"assertion_id": "one"})


@pytest.mark.asyncio
async def test_timed_out_backend_closes_gateway() -> None:
    async def backend(name, arguments):
        await asyncio.sleep(1)
        return {"id": "one"}

    gateway = RetrievalGateway(backend, revision="synthetic", limits=Limits(duration_seconds=0.01))
    with pytest.raises(TimeoutError):
        await gateway.invoke("get_knowledge", {"assertion_id": "one"})
    assert gateway.trace.events[0]["tool"] == "get_knowledge"
    assert gateway.trace.events[0]["outcome"] == "timeout"
    with pytest.raises(Refused, match="prior failure"):
        await gateway.invoke("get_knowledge", {"assertion_id": "one"})


@pytest.mark.asyncio
async def test_backend_cannot_return_more_than_requested() -> None:
    async def backend(name, arguments):
        return {"results": [{"assertion": {"id": "one"}}, {"assertion": {"id": "two"}}]}

    gateway = RetrievalGateway(backend, revision="synthetic")
    with pytest.raises(Refused, match="per-call result limit"):
        await gateway.invoke("search_knowledge", {"query": "q", "limit": 1})
    assert gateway.trace.results == 0


@pytest.mark.asyncio
async def test_page_exhaustion_refuses_before_dispatch() -> None:
    calls = 0

    async def backend(name, arguments):
        nonlocal calls
        calls += 1
        return {"results": []}

    gateway = RetrievalGateway(backend, revision="synthetic", limits=Limits(pages=1))
    await gateway.invoke("search_knowledge", {"query": "q", "limit": 1})
    with pytest.raises(Refused, match="page limit"):
        await gateway.invoke("search_knowledge", {"query": "q", "limit": 1, "cursor": "next"})
    assert calls == 1


@pytest.mark.parametrize("duration", [0.0, float("nan"), float("inf")])
def test_invalid_deadline_is_rejected(duration: float) -> None:
    with pytest.raises(ValueError):
        Limits(duration_seconds=duration)
