import json

import httpx

from xiaohe.contracts import ServiceError
from xiaohe.reception.contracts import (
    ApplicationView,
    ReceptionActor,
    SubjectPage,
    Submission,
    SubmissionResult,
    SubmitReceipt,
)


class ReceptionBusinessClient:
    def __init__(self, settings, *, transport=None):
        self.settings, self.transport = settings, transport

    async def _post(self, path, actor, payload, response_model):
        settings = self.settings
        if not all(
            (
                settings.reception_base_url,
                settings.reception_application_id,
                settings.reception_secret,
            )
        ):
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
        try:
            body = (
                payload.model_dump(mode="json", by_alias=True, exclude_none=True)
                if hasattr(payload, "model_dump")
                else payload
            )
            actor = ReceptionActor.model_validate(actor)
            async with httpx.AsyncClient(
                base_url=settings.reception_base_url,
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                timeout=10,
            ) as client:
                async with client.stream(
                    "POST",
                    "/internal-api/reception/v1/" + path,
                    headers={
                        "X-Reception-Application": settings.reception_application_id,
                        "Authorization": "Bearer " + settings.reception_secret.get_secret_value(),
                    },
                    json={"actor": actor.model_dump(mode="json", by_alias=True), "payload": body},
                ) as response:
                    raw = bytearray()
                    async for block in response.aiter_bytes():
                        if len(raw) + len(block) > 1024 * 1024:
                            raise ValueError("oversized response")
                        raw.extend(block)
                    value = json.loads(raw)
                    if not isinstance(value, dict) or type(value.get("code")) is not int:
                        raise ValueError("invalid envelope")
                    if response.status_code in (200, 201) and value["code"] == 0:
                        return response_model.model_validate(value["data"])
                    error = {
                        1050000001: ("INPUT_INVALID", 422),
                        1050000002: ("DRAFT_CONFLICT", 409),
                        1050000003: ("SUBJECT_CONFLICT", 409),
                        1050000004: ("IDEMPOTENCY_CONFLICT", 409),
                        403: ("FORBIDDEN", 403),
                    }.get(value["code"])
                    if error:
                        raise ServiceError(*error)
                    raise ValueError("unconfirmed result")
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True) from None

    async def submit(self, actor: ReceptionActor, payload: Submission) -> SubmitReceipt:
        return await self._post("applications/submit", actor, payload, SubmitReceipt)

    async def result(self, actor: ReceptionActor, submission_key: str) -> SubmissionResult:
        return await self._post(
            "applications/result", actor, {"submissionKey": submission_key}, SubmissionResult
        )

    async def application(self, actor: ReceptionActor, application_id: str) -> ApplicationView:
        return await self._post(
            "applications/get", actor, {"applicationId": application_id}, ApplicationView
        )

    async def subjects(self, actor: ReceptionActor, query: dict) -> SubjectPage:
        return await self._post("subjects/query", actor, query, SubjectPage)
