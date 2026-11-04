import ipaddress
import json
import re
import ssl
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import ValidationError

from xiaohe.contracts import ServiceError, VerifiedCompletion

_PREFIX = "/internal-api/system/sso-bridge"
_PATH = re.compile(
    r"/transactions(?:/[0-9a-f-]{36}/(?:captcha(?:/check)?|sms|verify|agreement|claim|cancel))?\Z"
)


class SsoBridgeClient:
    def __init__(self, settings, *, transport=None):
        self.settings, self.transport = settings, transport

    async def request(
        self, method, path, *, body=None, key=None, client_ip, user_agent, params=None
    ):
        settings = self.settings
        if (
            not settings.sso_bridge_base_url
            or not settings.sso_bridge_application_id
            or not settings.sso_bridge_secret
            or not _PATH.fullmatch(path)
            or method not in ("POST", "GET")
        ):
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503)
        try:
            client_ip = str(ipaddress.ip_address(client_ip))
            if len(user_agent) > 512 or any(
                ord(char) < 32 or ord(char) == 127 for char in user_agent
            ):
                raise ValueError("invalid bound user agent")
            verify = ssl.create_default_context(cafile=settings.sso_bridge_ca_file)
            headers = {
                "Authorization": "Bearer " + settings.sso_bridge_secret.get_secret_value(),
                "X-SSO-Bridge-Application-Id": settings.sso_bridge_application_id,
                "X-Bridge-Client-IP": client_ip,
                "X-Bridge-User-Agent": user_agent,
            }
            if key:
                headers["Idempotency-Key"] = key
            async with httpx.AsyncClient(
                base_url=settings.sso_bridge_base_url,
                transport=self.transport,
                verify=verify,
                trust_env=False,
                follow_redirects=False,
                timeout=10,
            ) as client:
                async with client.stream(
                    method, _PREFIX + path, json=body, params=params, headers=headers
                ) as response:
                    raw = bytearray()
                    async for block in response.aiter_bytes():
                        raw.extend(block)
                        if len(raw) > 1024 * 1024:
                            raise ValueError("oversized bridge response")
                    value = json.loads(raw)
                    if not isinstance(value, dict) or type(value.get("code")) is not int:
                        raise ValueError("invalid bridge response")
                    if response.status_code == 200 and value["code"] == 0:
                        return value.get("data")
                    code = value["code"]
                    if response.status_code == 401 and code == 1002029008:
                        raise ServiceError("AUTH_REQUIRED", 401)
                    if response.status_code == 409 and code == 1002029027:
                        raise ServiceError("AUTH_NOT_READY", 409, True, retry_after=1)
                    if response.status_code == 429 and code in (1002029005, 1002029010):
                        delay = response.headers.get("Retry-After", "")
                        retry = int(delay) if re.fullmatch(r"[1-9][0-9]{0,5}", delay) else 60
                        raise ServiceError("AUTH_RATE_LIMITED", 429, True, retry_after=retry)
                    if response.status_code == 410 and code == 1002029025:
                        raise ServiceError("AUTH_OPERATION_EXPIRED", 410)
                    if response.status_code == 409 and code == 1002029022:
                        raise ServiceError("IDEMPOTENCY_CONFLICT", 409)
                    if response.status_code == 409 and code == 1002029026:
                        raise ServiceError("AUTH_REPLAY", 409)
                    if response.status_code == 400 and code in (
                        1002029004,
                        1002029007,
                        1002029012,
                        1002029013,
                    ):
                        raise ServiceError("INPUT_INVALID", 422)
                    raise ServiceError("UPSTREAM_UNAVAILABLE", 503, response.status_code >= 500)
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True) from None

    async def claim(self, system, transaction_id, *, client_ip, user_agent):
        data = await self.request(
            "POST",
            f"/transactions/{transaction_id}/claim",
            body={},
            key=transaction_id,
            client_ip=client_ip,
            user_agent=user_agent,
        )
        try:
            result = VerifiedCompletion.model_validate(data)
        except ValidationError:
            raise ServiceError("UPSTREAM_UNAVAILABLE", 503) from None
        if result.issuer != self.settings.sso_bridge_base_url:
            raise ServiceError("IDENTITY_MISMATCH", 409)
        if (
            result.portal_client_id != system.portal_client_id
            or result.transaction_id != transaction_id
            or result.authenticated_at > datetime.now(UTC) + timedelta(seconds=30)
        ):
            raise ServiceError("IDENTITY_MISMATCH", 409)
        return result
