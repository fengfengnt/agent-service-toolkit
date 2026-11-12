import pytest
from native_support import Business
from test_deep_tools import tool_context, tools_module

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_same_effect_new_tool_call_id_returns_receipt(pg_factory, job_app, tmp_path):
    client = Business()
    context, frame = await tool_context(pg_factory, tmp_path, client=client)
    arguments = dict(
        draft_id=frame.draft_id,
        expected_revision=frame.draft_revision,
        payload={"confirmation": {"confirmed": True, "evidence": "好的"}},
    )
    first = await tools_module().execute_effect(context, "submit_lead", **arguments)
    repeated = await tools_module().execute_effect(context, "submit_lead", **arguments)
    assert first.receipt_id == repeated.receipt_id
    assert len(client.payloads) == 1


async def test_two_distinct_saves_in_one_command_both_apply(pg_factory, job_app, tmp_path):
    context, frame = await tool_context(pg_factory, tmp_path, text="城市苏州，行业制造")
    revision = frame.draft_revision
    actual = []
    for field, value in [("city", "苏州"), ("industry", "制造")]:
        result = await tools_module().execute_effect(
            context,
            "save_lead",
            draft_id=frame.draft_id,
            expected_revision=revision,
            payload={"candidates": {field: {"value": value, "evidence": value}}},
        )
        revision = result.revision
        actual.append(revision)
    assert actual == [frame.draft_revision + 1, frame.draft_revision + 2]


async def test_unknown_submission_reuses_original_request(pg_factory, job_app, tmp_path):
    client = Business(lose_reply=True)
    context, frame = await tool_context(pg_factory, tmp_path, client=client)
    arguments = dict(
        draft_id=frame.draft_id,
        expected_revision=frame.draft_revision,
        payload={"confirmation": {"confirmed": True, "evidence": "好的"}},
    )
    first = await tools_module().execute_effect(context, "submit_lead", **arguments)
    assert first.status == "unknown"
    key = client.payloads[0]["submissionKey"]
    second = await tools_module().execute_effect(context, "submit_lead", **arguments)
    assert second.status == "ok" and first.receipt_id == second.receipt_id
    assert len(client.payloads) == 1 and second.data["receipt"]["submissionKey"] == key
