import json

import pytest
from langchain_core.messages import messages_to_dict
from test_reception_graph import context, model

from xiaohe.contracts import AuthRequestPart
from xiaohe.graph import generate_reception_decision


@pytest.mark.asyncio
async def test_model_never_receives_auth_fields():
    data = context("介绍服务")
    data.confirmed_facts = {
        "otp": "867530",
        "mobile": "13800138000",
        "browserVerifier": "private-verifier",
        "authorization": "Bearer fixture-secret",
        "id_token": "private-idtoken",
        "sourceTenantId": 777,
    }
    data.recent_messages[0].parts.append(AuthRequestPart(type="auth_request", intent="login"))
    data.pending_request = {"type": "input_request", "prompt": "补充需求", "otp": "867530"}
    ai = model()
    await generate_reception_decision(data, ai)
    serialized = json.dumps(messages_to_dict(ai.requests[0]), ensure_ascii=False)
    for forbidden in (
        "867530",
        "13800138000",
        "private-verifier",
        "fixture-secret",
        "private-idtoken",
        "sourceTenantId",
        "auth_request",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_recent_messages_are_bounded_and_history_is_sanitized():
    data = context("短信验证码867530，手机号13800138000")
    data.recent_messages = [
        data.recent_messages[0].model_copy(update={"id": str(i)}) for i in range(25)
    ]
    ai = model()
    await generate_reception_decision(data, ai)
    serialized = json.dumps(messages_to_dict(ai.requests[0]), ensure_ascii=False)
    assert "867530" not in serialized and "13800138000" not in serialized
    assert len(ai.requests[0]) <= 21
