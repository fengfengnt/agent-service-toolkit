import json
from datetime import timedelta

import pytest
from deep_runtime_support import make_script_model
from langchain_core.messages import AIMessage, HumanMessage, messages_to_dict
from test_deep_factory import final_response
from test_deep_runtime import invoke
from test_deep_tools import tool_context

from xiaohe.models import Command, Message, Run

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_native_model_excludes_auth_fields_and_sanitizes_bounded_history(
    pg_factory, job_app, tmp_path
):
    context, frame = await tool_context(pg_factory, tmp_path, text="介绍服务")
    async with pg_factory.begin() as db:
        command = await db.get(Command, context.command_id)
        command.payload = command.payload | {
            "otp": "867530",
            "authorization": "Bearer fixture-secret",
            "sourceTenantId": 777,
            "id_token": "private-idtoken",
            "browserVerifier": "private-verifier",
        }
        message = await db.get(Message, command.message_id)
        message.parts = message.parts + [{"type": "auth_request", "intent": "login"}]
        run = await db.get(Run, frame.fence.run_id)
        run.pending_request = {"type": "input_request", "prompt": "补充需求", "otp": "867530"}
        for index in range(25):
            db.add(
                Message(
                    conversation_id=context.conversation_id,
                    system_id="portal",
                    tenant_id=1,
                    role="user",
                    parts=[{"type": "text", "text": "短信验证码867530，手机号13800138000"}],
                    created_at=message.created_at - timedelta(seconds=index + 1),
                )
            )
    model = make_script_model([final_response(wait_for_input=False)])
    await invoke(context, model=model)
    serialized = json.dumps(messages_to_dict(model.requests[0]), ensure_ascii=False)
    for value in (
        "867530",
        "13800138000",
        "fixture-secret",
        "private-idtoken",
        "private-verifier",
        "sourceTenantId",
    ):
        assert value not in serialized
    history = [
        m
        for m in model.requests[0]
        if isinstance(m, HumanMessage) and m.content == "[认证信息已省略，请使用认证卡片]"
    ]
    assert 0 < len(history) <= 20
    assert sum(isinstance(m, (HumanMessage, AIMessage)) for m in model.requests[0]) <= 21
