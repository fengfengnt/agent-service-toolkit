import json
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from xiaohe.contracts import ServiceError, ToolkitExecution
from xiaohe.settings import Settings
from xiaohe.toolkit_client import ToolkitClient

RUN = "00000000-0000-4000-8000-000000000001"
MESSAGE = "00000000-0000-4000-8000-000000000002"


def command(**changes):
    return ToolkitExecution(
        run_id=UUID(RUN),
        user_id="trusted-ai-user",
        thread_id="trusted-thread",
        attempt=1,
        ownership_epoch=0,
        mode="start",
        **changes,
    )


def settings():
    return Settings(
        toolkit_base_url="http://127.0.0.1:18090", auth_secret="fixture-internal-secret"
    )


class Chunks(httpx.AsyncByteStream):
    def __init__(self, content):
        self.content = content

    async def __aiter__(self):
        for byte in self.content:
            yield bytes([byte])


@pytest.mark.asyncio
async def test_internal_auth_uses_shared_secret_and_server_identity(caplog):
    events = [
        {"kind": "delta", "message_id": MESSAGE, "delta": "模拟😀"},
        {
            "kind": "outcome",
            "outcome": {
                "parts": [{"type": "text", "text": "模拟😀"}],
                "wait_for_input": False,
                "artifact_version_ids": [],
            },
        },
    ]

    def handler(request):
        assert request.method == "POST"
        assert str(request.url) == f"http://127.0.0.1:18090/internal/xiaohe/runs/{RUN}/stream"
        assert request.headers["authorization"] == "Bearer fixture-internal-secret"
        assert json.loads(request.content) == {
            "run_id": RUN,
            "user_id": "trusted-ai-user",
            "thread_id": "trusted-thread",
            "attempt": 1,
            "ownership_epoch": 0,
            "mode": "start",
        }
        return httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            stream=Chunks(
                ("\r\n".join(json.dumps(x, ensure_ascii=False) for x in events) + "\r\n").encode()
            ),
        )

    client = ToolkitClient(settings(), transport=httpx.MockTransport(handler))
    result = [event async for event in client.stream_execution(command())]
    assert [event.kind for event in result] == ["delta", "outcome"]
    assert result[0].delta == "模拟😀"
    assert "fixture-internal-secret" not in caplog.text
    assert "fixture-internal-secret" not in repr(client)


@pytest.mark.parametrize(
    "extra",
    [
        {"checkpoint_id": "foreign"},
        {"checkpoint_ns": "foreign"},
        {"agent_config": {"user_id": "attacker"}},
        {"configurable": {"thread_id": "foreign"}},
    ],
)
def test_external_execution_overrides_are_rejected(extra):
    with pytest.raises(ValidationError):
        command(**extra)


@pytest.mark.parametrize("mode", ["start", "recover", "resume"])
def test_execution_modes(mode):
    value = command().model_dump()
    assert ToolkitExecution.model_validate({**value, "mode": mode}).mode == mode


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 403, 500, 503])
async def test_http_errors_are_safe_and_never_follow_redirects(status, caplog):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            headers={"location": "https://untrusted.example/"},
            text="fixture-upstream-secret fixture-internal-secret",
        )

    client = ToolkitClient(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(ServiceError) as exc:
        _ = [event async for event in client.stream_execution(command())]
    assert exc.value.code == "UPSTREAM_UNAVAILABLE"
    assert exc.value.retryable is (status >= 500)
    assert len(requests) == 1
    assert "fixture-" not in str(exc.value) + repr(exc.value) + caplog.text
    assert exc.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,media",
    [
        (b"not json\n", "application/x-ndjson"),
        (b'{"kind":"unknown"}\n', "application/x-ndjson"),
        (b'{"kind":"delta","message_id":"bad","delta":"private"}\n', "application/x-ndjson"),
        (b"", "application/x-ndjson"),
        (b"{}\n", "text/html"),
        (b"x" * (1024 * 1024 + 1), "application/x-ndjson"),
        (
            b'{"kind":"error","code":"UPSTREAM_UNAVAILABLE","retryable":true}',
            "application/x-ndjson",
        ),
    ],
    ids=["json", "kind", "payload", "empty", "media", "oversized", "unterminated"],
)
async def test_invalid_truncated_or_oversized_stream_is_not_success(body, media):
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": media}, content=body)
    )
    client = ToolkitClient(settings(), transport=transport)
    with pytest.raises(ServiceError) as exc:
        _ = [event async for event in client.stream_execution(command())]
    assert exc.value.code == "UPSTREAM_PROTOCOL_ERROR"
    assert exc.value.__context__ is None


@pytest.mark.asyncio
async def test_transport_failure_is_safe():
    def handler(request):
        raise httpx.ConnectError("fixture-internal-secret", request=request)

    client = ToolkitClient(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(ServiceError) as exc:
        _ = [event async for event in client.stream_execution(command())]
    assert exc.value.retryable and "fixture-" not in str(exc.value)
    assert exc.value.__context__ is None


@pytest.mark.asyncio
async def test_explicit_error_is_terminal_event():
    content = b'{"kind":"error","code":"EXECUTION_EXPIRED","retryable":false}\n'
    client = ToolkitClient(
        settings(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "application/x-ndjson"}, content=content
            )
        ),
    )
    events = [event async for event in client.stream_execution(command())]
    assert len(events) == 1 and events[0].code == "EXECUTION_EXPIRED"


@pytest.mark.asyncio
async def test_small_delta_is_delivered_before_next_network_read():
    class LiveStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (
                json.dumps({"kind": "delta", "message_id": MESSAGE, "delta": "小块"}) + "\n"
            ).encode()
            raise AssertionError("client read ahead before delivering delta")

    client = ToolkitClient(
        settings(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "application/x-ndjson"}, stream=LiveStream()
            )
        ),
    )
    stream = client.stream_execution(command())
    try:
        assert (await anext(stream)).delta == "小块"
    finally:
        await stream.aclose()
