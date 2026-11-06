import pytest
from test_agent_operations import cache_decision, prepare

from xiaohe.agents.contracts import SemanticDecision
from xiaohe.agents.nodes import make_node_executors
from xiaohe.contracts import ServiceError
from xiaohe.reception.contracts import SubmissionResult, SubmitReceipt
from xiaohe.workflows.models import OperationNode

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_core_accepts_alternate_backend_and_trusted_actor(pg_factory, job_app, tmp_path):
    bundle, frame = await prepare(pg_factory, tmp_path, host="other")
    decision = SemanticDecision(action="confirm_current_summary")
    await cache_decision(pg_factory, frame, decision)

    class AlternateBackend:
        submitted = []

        async def result(self, actor, key):
            assert actor.system_id == frame.actor.system.system_id
            return SubmissionResult(status="not_found", receipt=None)

        async def submit(self, actor, payload):
            self.submitted.append(payload.submission_key)
            return SubmitReceipt(
                submission_key=payload.submission_key,
                kind="service",
                application_id="alternate-application",
                revision=1,
                status="accepted",
            )

    backend = AlternateBackend()
    executors = make_node_executors(pg_factory, model=None, business_client=backend, bundle=bundle)
    node = OperationNode(
        id="confirm", name="提交", type="operation", operation="confirm_submission", next="done"
    )
    state = {"frame": frame.model_dump(), "decision": decision.model_dump()}
    result = await executors["operation"](node, state, {})
    assert "需求已记录" in result["operation_result"]["text"]
    assert await executors["operation"](node, state, {}) == result
    assert len(backend.submitted) == 1


async def test_backend_success_lost_reply_reuses_original_submission(pg_factory, job_app, tmp_path):
    bundle, frame = await prepare(pg_factory, tmp_path)
    decision = SemanticDecision(action="confirm_current_summary")
    await cache_decision(pg_factory, frame, decision)

    class LostReply:
        receipt = None
        creates = 0

        async def result(self, actor, key):
            return SubmissionResult(
                status="accepted" if self.receipt else "not_found", receipt=self.receipt
            )

        async def submit(self, actor, payload):
            self.creates += 1
            self.receipt = SubmitReceipt(
                submission_key=payload.submission_key,
                kind="service",
                application_id="one-application",
                revision=1,
                status="accepted",
            )
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)

    backend = LostReply()
    nodes = make_node_executors(pg_factory, model=None, business_client=backend, bundle=bundle)
    node = OperationNode(
        id="confirm", name="提交", type="operation", operation="confirm_submission", next="done"
    )
    state = {"frame": frame.model_dump(), "decision": decision.model_dump()}
    with pytest.raises(ServiceError, match="SUBMISSION_UNKNOWN"):
        await nodes["operation"](node, state, {})
    result = await nodes["operation"](node, state, {})
    assert "需求已记录" in result["operation_result"]["text"]
    assert backend.creates == 1
