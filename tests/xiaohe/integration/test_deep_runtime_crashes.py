import pytest
from sqlalchemy import select, text
from test_deep_tools import tool_context, tools_module
from test_workflow_recovery import kill_at_committed_phase

from xiaohe.reception.contracts import SubmissionResult, SubmitReceipt
from xiaohe.reception.models import ReceptionDraft, ReceptionSubmission

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_hard_kill_after_native_backend_success_reuses_original_key(
    pg_factory, job_app, tmp_path
):
    context, frame = await tool_context(pg_factory, tmp_path)
    await kill_at_committed_phase(tmp_path, frame, "deep")
    async with pg_factory() as db:
        submission = await db.scalar(
            select(ReceptionSubmission).where(ReceptionSubmission.draft_id == frame.draft_id)
        )
        original_key = submission.submission_key
        assert submission.status == "pending"
        assert (
            await db.scalar(
                text("SELECT invocations FROM xiaohe_probe_effects WHERE run_id=:run"),
                {"run": frame.fence.run_id},
            )
            == 1
        )

    class RecoveryBackend:
        async def result(self, actor, key):
            assert key == original_key
            return SubmissionResult(
                status="accepted",
                receipt=SubmitReceipt(
                    submission_key=key,
                    kind="service",
                    application_id="one-native-application",
                    revision=1,
                    status="accepted",
                ),
            )

        async def submit(self, *args):
            raise AssertionError("must not repeat external submission")

    from dataclasses import replace

    recovered = await tools_module().execute_effect(
        replace(context, business_client=RecoveryBackend()),
        "submit_lead",
        draft_id=frame.draft_id,
        expected_revision=frame.draft_revision,
        payload={"confirmation": {"confirmed": True, "evidence": "好的"}},
    )
    assert recovered.status == "ok" and recovered.data["receipt"]["submissionKey"] == original_key
    async with pg_factory() as db:
        assert (await db.get(ReceptionDraft, frame.draft_id)).state == "accepted"
        assert len(list(await db.scalars(select(ReceptionSubmission)))) == 1
