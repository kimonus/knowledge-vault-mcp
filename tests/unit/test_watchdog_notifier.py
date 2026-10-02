import httpx
import pytest
from pydantic import ValidationError

from knowledge_vault.config import Settings
from knowledge_vault.watchdog import webhook_notifier


def test_no_notifier_without_a_webhook_and_urls_are_validated() -> None:
    assert webhook_notifier(Settings(environment="test")) is None
    with pytest.raises(ValidationError, match="http or https"):
        Settings(environment="test", alert_webhook_url="file:///etc/passwd")
    assert "hooks.example" not in repr(
        Settings(environment="test", alert_webhook_url="https://hooks.example/secret-token")
    )


@pytest.mark.parametrize("payload_format", ["json", "text"])
async def test_notifier_posts_a_content_free_message(
    monkeypatch: pytest.MonkeyPatch, payload_format: str
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **options: real_client(transport=transport, **options)
    )
    notify = webhook_notifier(
        Settings(
            environment="test",
            alert_webhook_url="https://hooks.example/topic",
            alert_webhook_format=payload_format,  # type: ignore[arg-type]
        )
    )
    assert notify is not None
    await notify("Knowledge Vault: 1 problem(s)", "no backup has succeeded", ["backup_stale"])
    request = requests[0]
    assert str(request.url) == "https://hooks.example/topic"
    if payload_format == "json":
        assert request.read() == (
            b'{"title":"Knowledge Vault: 1 problem(s)","message":"no backup has succeeded",'
            b'"problems":["backup_stale"]}'
        )
    else:
        assert request.read() == b"no backup has succeeded"
        assert request.headers["title"] == "Knowledge Vault: 1 problem(s)"


async def test_notifier_raises_when_the_webhook_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **options: real_client(transport=transport, **options)
    )
    notify = webhook_notifier(
        Settings(environment="test", alert_webhook_url="https://hooks.example/topic")
    )
    assert notify is not None
    with pytest.raises(httpx.HTTPStatusError):
        await notify("t", "m", [])
