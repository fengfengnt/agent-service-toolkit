from uuid import uuid5

from sqlalchemy import select

from xiaohe.agents.contracts import AgentFrame
from xiaohe.agents.nodes import (
    ROLE_DIRECTIONS,
    _activate_pending,
    _checked,
    _command,
    _save_stage,
    _stage,
    build_agent_frame,
    make_node_executors,
)
from xiaohe.contracts import AgentOutcome, ServiceError
from xiaohe.models import Conversation, Run
from xiaohe.reception.models import ReceptionDraft
from xiaohe.reception.store import locked_flow
from xiaohe.workflows.compiler import compile_workflow
from xiaohe.workflows.definitions import baseline_bundle
from xiaohe.workflows.loader import load_bundle
from xiaohe.workflows.models import AGENT_IDS, WorkflowValidationError
from xiaohe.workflows.store import load_persisted_bundle, persist_bundle


class AgentRegistry(dict):
    def __init__(self, bundle, factory, model, business_client):
        self.bundle = bundle
        executors = make_node_executors(
            factory, model=model, business_client=business_client, bundle=bundle
        )
        super().__init__(
            {
                key: compile_workflow(definition, executors)
                for key, definition in bundle.definitions.items()
            }
        )


async def load_agent_registry(factory, resource_root, model, business_client):
    bundle = load_bundle(resource_root)
    registry = AgentRegistry(bundle, factory, model, business_client)
    async with factory.begin() as db:
        await baseline_bundle(db)
        await persist_bundle(db, bundle)
        # 验证仍活动的旧定义，失败时保留任务并阻止启动，不换用最新流程。
        ids = (
            await db.scalars(
                select(Run.workflow_bundle_id)
                .where(
                    Run.runtime_version == "dsl-v1",
                    Run.status.not_in(["completed", "failed", "cancelled", "expired"]),
                )
                .distinct()
            )
        ).all()
        for bundle_id in ids:
            previous = await load_persisted_bundle(db, bundle_id)
            AgentRegistry(previous, factory, model, business_client)
    factory.configure(info=factory.kw.get("info", {}) | {"workflow_bundle_id": bundle.bundle_id})
    return registry


async def _select_direction(db, conversation, flow, agent_id):
    direction = ROLE_DIRECTIONS[agent_id]
    conversation.direction = direction
    rows = (
        await db.scalars(
            select(ReceptionDraft).where(
                ReceptionDraft.conversation_id == conversation.id,
                ReceptionDraft.direction == direction,
                ReceptionDraft.state.in_(["collecting", "review", "submitting"]),
            )
        )
    ).all()
    flow.active_draft_id = rows[0].id if len(rows) == 1 else None


async def run_agent_turn(
    factory, fence, *, bundle, entry_agent=None, model=None, business_client=None, registry=None
):
    if entry_agent is not None and entry_agent not in AGENT_IDS:
        raise ServiceError("FORBIDDEN", 403)
    async with factory.begin() as db:
        run, command = await _command(db, fence)
        if run.runtime_version != "dsl-v1" or run.workflow_bundle_id != bundle.bundle_id:
            raise WorkflowValidationError("run definition mismatch")
        if cached := command.payload.get("agentOutcome"):
            return AgentOutcome.model_validate(cached)
        conversation = await db.get(Conversation, run.conversation_id)
        flow = await locked_flow(db, conversation)
        binding = command.payload.get("agentExecution")
        if binding is None:
            selected = (
                entry_agent
                or command.payload.get("entryAgent")
                or (
                    "xiaohe-reception"
                    if conversation.direction == "unknown"
                    else "xiaohe-" + conversation.direction
                )
            )
            if selected not in AGENT_IDS:
                raise ServiceError("FORBIDDEN", 403)
            binding = {
                "bundleId": bundle.bundle_id,
                "entry": selected,
                "active": selected,
                "handoffs": 0,
                "threadId": str(uuid5(command.id, bundle.bundle_id + selected)),
                "pending": ROLE_DIRECTIONS[selected] != conversation.direction,
                "fromDirection": conversation.direction,
            }
            command.payload = command.payload | {"agentExecution": binding}
        elif binding["bundleId"] != bundle.bundle_id or (
            entry_agent and binding["entry"] != entry_agent
        ):
            raise ServiceError("ACTION_STALE", 409)
    if registry is None or registry.bundle.bundle_id != bundle.bundle_id:
        registry = AgentRegistry(bundle, factory, model, business_client)
    while True:
        active = binding["active"]
        async with factory.begin() as db:
            _, command = await _command(db, fence)
            cached_frame = _stage(command, active).get("frame")
        if cached_frame:
            frame = AgentFrame.model_validate(cached_frame).model_copy(update={"fence": fence})
        else:
            frame = await build_agent_frame(factory, fence, active, bundle)
        async with factory.begin() as db:
            _, command, _, _ = await _checked(db, frame)
            _save_stage(command, active, frame=frame.model_dump(mode="json"))
        # 子图由 command 阶段回执恢复；不继承外壳的 checkpoint/thread 命名空间。
        try:
            state = await registry[active].ainvoke(
                {"frame": frame.model_dump(mode="json")},
                config={
                    "configurable": {"thread_id": binding["threadId"], "checkpoint_ns": active},
                    "tags": ["xiaohe_internal"],
                },
            )
        except ServiceError as error:
            if error.code != "SUBMISSION_UNKNOWN":
                raise
            outcome = AgentOutcome(
                parts=[
                    {
                        "type": "text",
                        "text": "正式受理结果尚未确认，请稍后查询；系统将沿原提交核对，不重复创建。",
                    }
                ],
                wait_for_input=False,
                artifact_version_ids=[],
            )
            break
        target = state.get("handoff")
        if not target:
            outcome = (
                AgentOutcome.model_validate(state["outcome"])
                if state.get("outcome")
                else AgentOutcome(
                    parts=[
                        {"type": "text", "text": "请明确需要采购、社群组织还是供应商服务。"},
                        {
                            "type": "input_request",
                            "requestId": str(uuid5(frame.command_id, "agent.clarify")),
                            "prompt": "您希望咨询哪个服务方向？",
                        },
                    ],
                    wait_for_input=True,
                    artifact_version_ids=[],
                )
            )
            break
        if binding["handoffs"] >= 1:
            outcome = AgentOutcome(
                parts=[
                    {"type": "text", "text": "本轮已完成一次交接，请下一轮再明确需要切换的方向。"},
                    {
                        "type": "input_request",
                        "requestId": str(uuid5(frame.command_id, "agent.handoff-limit")),
                        "prompt": "您希望继续咨询还是切换方向？",
                    },
                ],
                wait_for_input=True,
                artifact_version_ids=[],
            )
            break
        if target not in AGENT_IDS or target == "xiaohe-reception":
            raise ServiceError("INPUT_INVALID", 422)
        async with factory.begin() as db:
            _, command, conversation, flow = await _checked(db, frame)
            binding = binding | {
                "active": target,
                "handoffs": 1,
                "threadId": str(uuid5(command.id, bundle.bundle_id + target)),
                "pending": True,
                "fromDirection": conversation.direction,
            }
            command.payload = command.payload | {"agentExecution": binding}
    async with factory.begin() as db:
        _, command, conversation, flow = await _checked(db, frame)
        await _activate_pending(db, frame, command, conversation, flow)
        outcome_value = outcome.model_dump(mode="json", by_alias=True)
        command.payload = command.payload | {"agentOutcome": outcome_value}
        requests = [part for part in outcome_value["parts"] if part["type"] == "input_request"]
        from uuid import UUID

        flow.pending_request_id = UUID(requests[0]["requestId"]) if requests else None
    return outcome
