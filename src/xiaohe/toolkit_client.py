from collections.abc import AsyncIterator

import httpx
from pydantic import TypeAdapter, ValidationError

from xiaohe.contracts import ServiceError, ToolkitEvent, ToolkitExecution
from xiaohe.settings import Settings

_EVENT = TypeAdapter(ToolkitEvent)
_MAX_FRAME_BYTES = 1024 * 1024


def decode_event(frame: bytes) -> ToolkitEvent | None:
    event = None
    try:
        event = _EVENT.validate_json(frame)
    except ValidationError:
        pass
    return event


class ToolkitClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = transport

    async def stream_execution(self, command: ToolkitExecution) -> AsyncIterator[ToolkitEvent]:
        failure = None
        try:
            async with httpx.AsyncClient(
                base_url=self._settings.toolkit_base_url,
                headers={
                    "Authorization": f"Bearer {self._settings.auth_secret.get_secret_value()}",
                    "Accept": "application/x-ndjson",
                },
                timeout=httpx.Timeout(30, read=120),
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                async with client.stream(
                    "POST",
                    f"/internal/xiaohe/runs/{command.run_id}/stream",
                    json=command.model_dump(mode="json"),
                ) as response:
                    if response.status_code != 200:
                        raise ServiceError(
                            "UPSTREAM_UNAVAILABLE", 503, retryable=response.status_code >= 500
                        )
                    if (
                        response.headers.get("content-type", "").split(";")[0].strip().lower()
                        != "application/x-ndjson"
                    ):
                        raise ServiceError("UPSTREAM_PROTOCOL_ERROR", 502)
                    pending = bytearray()
                    terminal = False
                    async for chunk in response.aiter_bytes():
                        pending.extend(chunk)
                        while b"\n" in pending:
                            frame, _, rest = pending.partition(b"\n")
                            pending = bytearray(rest)
                            if len(frame) > _MAX_FRAME_BYTES:
                                raise ServiceError("UPSTREAM_PROTOCOL_ERROR", 502)
                            if not frame.strip():
                                continue
                            event = decode_event(bytes(frame))
                            if terminal or event is None:
                                raise ServiceError("UPSTREAM_PROTOCOL_ERROR", 502)
                            terminal = event.kind in ("outcome", "error")
                            yield event
                        if len(pending) > _MAX_FRAME_BYTES:
                            raise ServiceError("UPSTREAM_PROTOCOL_ERROR", 502)
                    if pending or not terminal:
                        raise ServiceError("UPSTREAM_PROTOCOL_ERROR", 502)
        except httpx.HTTPError:
            failure = ServiceError("UPSTREAM_UNAVAILABLE", 503, retryable=True)
        # Raise outside the handler so original request/headers are not exception context.
        if failure is not None:
            raise failure
