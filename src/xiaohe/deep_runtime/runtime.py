import asyncio
import json
import re
from typing import TypedDict
from uuid import uuid5

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select, text

from xiaohe.agents.semantic import _claims_business_success
from xiaohe.agents.state import _command, _select_direction
from xiaohe.contracts import Actor, AgentOutcome, ServiceError, TrustedSystem
from xiaohe.deep_runtime.bundles import load_runtime_bundle_record, select_binding, skill_files
from xiaohe.deep_runtime.factory import build_business_agent
from xiaohe.deep_runtime.models import DeepExecution, DeepTurnResult
from xiaohe.deep_runtime.tools import ToolContext, execute_effect, make_business_tools
from xiaohe.graph import _safe_model_text
from xiaohe.lead_skills.notices import render_lead_notice
from xiaohe.models import Command, Conversation, Message, Run, System
from xiaohe.reception.models import ReceptionDraft
from xiaohe.reception.store import locked_flow
from xiaohe.runs import TERMINAL


class BusinessState(TypedDict, total=False):
    candidate: dict | None
    outcome: dict


def business_graph_nodes():
    return ("prepare_context", "business_agent", "finalize")


def deep_thread_ids(command: Command) -> set[str]:
    execution = command.payload.get("deepExecution", {})
    return {reference["id"] for reference in execution.get("threads", {}).values()}


def _persist(command, execution, **updates):
    updated = execution.model_copy(update=updates)
    command.payload = command.payload | {"deepExecution": updated.model_dump(mode="json")}
    return updated


async def publish_runtime(factory, resource_root):
    from xiaohe.deep_runtime.bundles import load_runtime_bundle, persist_runtime_bundle

    bundle = load_runtime_bundle(resource_root)
    async with factory.begin() as db:
        await persist_runtime_bundle(db, bundle)
        for bundle_id in await db.scalars(
            select(Run.deep_runtime_bundle_id)
            .where(Run.deep_runtime_bundle_id.is_not(None), Run.status.not_in(TERMINAL))
            .distinct()
        ):
            await load_runtime_bundle_record(db, bundle_id)
        for command in await db.scalars(select(Command).where(Command.completed_at.is_(None))):
            if previous := command.payload.get("deepExecution"):
                await load_runtime_bundle_record(
                    db, DeepExecution.model_validate(previous).bundle_id
                )
    factory.configure(
        info=factory.kw.get("info", {})
        | {
            "deep_runtime_bundle_id": bundle.bundle_id,
            "lead_interaction_policy_id": bundle.interaction_policy.policy_id,
        }
    )


async def run_deep_turn(factory, fence, *, execution, model, business_client, checkpointer):
    if checkpointer is None:
        raise ServiceError("CHECKPOINT_NOT_READY", 503)
    async with factory.begin() as db:
        run, command = await _command(db, fence)
        persisted = DeepExecution.model_validate(command.payload.get("deepExecution", {}))
        if persisted.bundle_id != execution.bundle_id:
            raise ServiceError("ACTION_STALE", 409)
        if persisted.result:
            return AgentOutcome.model_validate(persisted.result)
        bundle = await load_runtime_bundle_record(db, persisted.bundle_id)
        command_id = command.id

    context = None
    initial = None
    thread_id = None
    action = None

    async def prepare_context(state):
        nonlocal context, initial, thread_id, action
        async with factory.begin() as db:
            run, command = await _command(db, fence)
            if command.id != command_id:
                raise ServiceError("ACTION_STALE", 409)
            current = DeepExecution.model_validate(command.payload["deepExecution"])
            conversation = await db.get(Conversation, run.conversation_id)
            flow = await locked_flow(db, conversation)
            active = (
                current.active
                or command.payload.get("entryAgent")
                or (
                    "xiaohe-reception"
                    if conversation.direction == "unknown"
                    else "xiaohe-" + conversation.direction
                )
            )
            system = await db.get(System, run.system_id)
            actor = Actor(
                system=TrustedSystem(
                    system_id=system.system_id,
                    tenant_id=system.tenant_id,
                    portal_client_id=system.portal_client_id,
                    allowed_origin=system.allowed_origin,
                ),
                session_id=run.session_id,
                user_id=run.user_id,
                guest_id=run.guest_id,
            )
            binding = select_binding(bundle, actor, active)
            if active != "xiaohe-reception" and conversation.direction != active.removeprefix(
                "xiaohe-"
            ):
                await _select_direction(db, conversation, flow, active)
            message = await db.get(Message, command.message_id)
            original = "\n".join(
                part["text"] for part in message.parts if part["type"] in ("text", "system_notice")
            )
            context = ToolContext(
                factory,
                actor,
                fence,
                command.id,
                conversation.id,
                binding,
                bundle,
                original,
                business_client,
            )
            action = command.payload.get("reception", {})
            action = action | {"control": command.payload.get("receptionControl")}
            reference_key = f"{active}:{fence.attempt}:{fence.ownership_epoch}"
            reference = current.threads.get(reference_key) or {
                "id": str(uuid5(command.id, bundle.bundle_id + reference_key)),
                "attempt": fence.attempt,
                "ownership_epoch": fence.ownership_epoch,
            }
            thread_id = reference["id"]
            _persist(
                command,
                current,
                active=active,
                threads=current.threads | {reference_key: reference},
            )
            row = (
                await db.get(ReceptionDraft, flow.active_draft_id) if flow.active_draft_id else None
            )
            facts = {
                "direction": conversation.direction,
                "authenticated": bool(actor.user_id),
                "invitation": flow.invitation_state,
                "draft": {
                    "id": str(row.id),
                    "revision": row.revision,
                    "fields": row.fields,
                    "state": row.state,
                    "guidance": row.guidance_progress,
                }
                if row
                else None,
                "effects": current.effects,
                "pending_request": command.question,
            }
            recent = list(
                await db.scalars(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation.id,
                        Message.system_id == run.system_id,
                        Message.tenant_id == run.tenant_id,
                        Message.id != message.id,
                        Message.created_at <= message.created_at,
                    )
                    .order_by(Message.created_at.desc(), Message.id.desc())
                    .limit(20)
                )
            )
            from xiaohe.graph import visible_text

            history = [
                (HumanMessage if item.role == "user" else AIMessage)(
                    id=str(item.id), content=_safe_model_text(visible_text(item.parts))
                )
                for item in reversed(recent)
                if visible_text(item.parts)
            ]
            initial = {
                "messages": [
                    SystemMessage(content=_safe_model_text(json.dumps(facts, ensure_ascii=False))),
                    *history,
                    HumanMessage(id=str(message.id), content=_safe_model_text(original)),
                ],
                "files": skill_files(bundle, binding),
            }
        return {"candidate": None}

    async def business_agent(state):
        from xiaohe.graph import _auth_intent

        if action.get("control") == "switch_direction":
            return {
                "candidate": DeepTurnResult(
                    answer={
                        "text": "已进入当前服务方向，您可以继续咨询或填写资料。",
                        "basis": "general_advice",
                        "source_ids": [],
                    }
                ).model_dump(mode="json")
            }

        if context.binding.agent_id == "xiaohe-reception" and (
            intent := _auth_intent(context.text)
        ):
            from xiaohe.deep_runtime.models import ToolResult
            from xiaohe.deep_runtime.tools import _save_execution

            async with factory.begin() as db:
                _, command = await _command(db, fence)
                current = DeepExecution.model_validate(command.payload["deepExecution"])
                _save_execution(
                    command,
                    current,
                    "request_auth",
                    ToolResult(
                        operation="request_auth", status="auth", data={"auth_intent": intent}
                    ),
                )
            return {"candidate": None}
        if (
            action.get("control") in ("accept_invitation", "start_collection")
            and context.binding.agent_id != "xiaohe-reception"
        ):
            prepared = await execute_effect(
                context,
                "prepare_lead",
                draft_id=None,
                expected_revision=None,
                payload={"mode": "resume"},
            )
            if prepared.status != "ok" or prepared.data.get("state") in (
                "review",
                "accepted",
                "submitting",
            ):
                return {"candidate": None}
            initial["messages"].insert(
                -1,
                SystemMessage(
                    content="用户选择了留下信息，请按本方向留资 Skill 引导。准备结果："
                    + prepared.model_dump_json()
                ),
            )
        if action.get("action") == "confirm":
            await execute_effect(
                context,
                "submit_lead",
                draft_id=action.get("draftId"),
                expected_revision=action.get("expectedRevision"),
                payload={"confirmation": {"confirmed": True, "evidence": context.text}},
            )
            return {"candidate": None}
        if action.get("action") == "resume":
            await execute_effect(
                context,
                "prepare_lead",
                draft_id=action.get("draftId"),
                expected_revision=None,
                payload={"mode": "resume"},
            )
            return {"candidate": None}
        if action.get("action") in ("save_round", "skip_optional", "show_summary"):
            await _form_action(context, action)
            return {"candidate": None}
        from xiaohe.graph import reception_model

        agent = build_business_agent(
            bundle,
            execution,
            context.binding,
            model=model or reception_model(),
            tools=make_business_tools(context),
            checkpointer=checkpointer,
        )
        config = {
            "configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
            "tags": ["xiaohe_internal"],
            "recursion_limit": 64,
        }
        # The outer driver owns the business thread; this lock also excludes duplicate internal drivers.
        async with factory.kw["bind"].connect() as guard, guard.begin():
            acquired = await guard.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": "xiaohe.deep:" + thread_id},
            )
            if not acquired:
                raise ServiceError("EXECUTION_ALREADY_STARTED", 409, True)
            checkpoint = await checkpointer.aget_tuple(config)
            value = None if checkpoint else initial
            try:
                async with asyncio.timeout(90):
                    result = await agent.ainvoke(value, config=config, durability="sync")
                snapshot = await agent.aget_state(config)
                if any(task.interrupts for task in snapshot.tasks):
                    raise ServiceError("INVALID_WAIT_STATE", 502)
                candidate = DeepTurnResult.model_validate(result.get("structured_response"))
            except ServiceError:
                raise
            except Exception:
                async with factory.begin() as db:
                    _, command = await _command(db, fence)
                    effects = DeepExecution.model_validate(command.payload["deepExecution"]).effects
                    if not any(
                        effect["operation"] in ("save_lead", "submit_lead")
                        for effect in effects.values()
                    ):
                        raise
                return {"candidate": None}
            return {"candidate": candidate.model_dump(mode="json")}

    async def finalize(state):
        candidate = (
            DeepTurnResult.model_validate(state["candidate"]) if state.get("candidate") else None
        )
        async with factory.begin() as db:
            run, command = await _command(db, fence)
            current = DeepExecution.model_validate(command.payload["deepExecution"])
            conversation = await db.get(Conversation, run.conversation_id)
            flow = await locked_flow(db, conversation)
            effects = list(current.effects.values())
            submitted = [effect for effect in effects if effect["operation"] == "submit_lead"]
            saved = [effect for effect in effects if effect["operation"] == "save_lead"]
            parts = []
            if candidate and candidate.answer:
                answer = candidate.answer
                source_ids = set(context.binding.skills)
                if _claims_business_success(answer.text) or set(answer.source_ids) - source_ids:
                    if not submitted and not saved:
                        raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
                    candidate = None
                else:
                    parts.append(
                        {
                            "type": "text",
                            "text": re.sub(r"</?br\s*/?\s*>", "\n", answer.text, flags=re.IGNORECASE),
                        }
                    )
            if submitted or saved:
                notice = (
                    "accepted"
                    if any(e["status"] == "ok" for e in submitted)
                    else "unknown"
                    if submitted
                    else "saved"
                )
                parts.append(
                    {
                        "type": "text",
                        "text": render_lead_notice(
                            bundle.interaction_policy, notice, direction=conversation.direction
                        ),
                    }
                )
            if any(e["operation"] == "query_progress" and e["status"] == "ok" for e in effects):
                parts.append({"type": "text", "text": "已查询真实进展，请在“我的进展”查看记录。"})
            for effect in effects:
                if effect["operation"] == "prepare_lead" and effect.get("data", {}).get(
                    "state"
                ) in ("accepted", "submitting"):
                    notice = "accepted" if effect["data"]["state"] == "accepted" else "unknown"
                    parts.append(
                        {
                            "type": "text",
                            "text": render_lead_notice(
                                bundle.interaction_policy, notice, direction=conversation.direction
                            ),
                        }
                    )
            for effect in effects:
                if effect["operation"] == "form_action":
                    parts.append({"type": "text", "text": effect["data"]["text"]})
            auth = any(e["status"] == "auth" for e in effects)
            if auth:
                intent = next(
                    (e["data"]["auth_intent"] for e in effects if e["operation"] == "request_auth"),
                    "lead",
                )
                parts.extend(
                    [
                        {
                            "type": "text",
                            "text": "请在认证卡片中继续，手机号和验证码无需发送到聊天。",
                        },
                        {"type": "auth_request", "intent": intent},
                    ]
                )
            if run.user_id and any(e.get("data", {}).get("collection") for e in effects):
                from xiaohe.reception.flow import COLLECTION_NOTICE

                parts.append({"type": "system_notice", "text": COLLECTION_NOTICE})
            if not parts:
                parts.append({"type": "text", "text": "请告诉我您希望了解的内容。"})
            wait = (candidate.wait_for_input if candidate else not submitted) and not auth
            if wait:
                flow.pending_request_id = uuid5(command.id, "deep.input")
                parts.append(
                    {
                        "type": "input_request",
                        "requestId": str(flow.pending_request_id),
                        "prompt": "您可以进行咨询或者留下您的信息",
                    }
                )
            else:
                flow.pending_request_id = None
            outcome = AgentOutcome(parts=parts, wait_for_input=wait, artifact_version_ids=[])
            targets = [
                e["data"]["handoff"]
                for e in effects
                if e["operation"] == "request_handoff"
                and e.get("data", {}).get("handoff") != current.active
            ]
            if targets and not current.handoffs:
                target = targets[-1]
                await _select_direction(db, conversation, flow, target)
                _persist(command, current, active=target, handoffs=1)
                return {"outcome": {}}
            _persist(command, current, result=outcome.model_dump(mode="json", by_alias=True))
            return {"outcome": outcome.model_dump(mode="json", by_alias=True)}

    builder = StateGraph(BusinessState)
    for name, node in zip(
        business_graph_nodes(), (prepare_context, business_agent, finalize), strict=True
    ):
        builder.add_node(name, node)
    builder.add_edge(START, "prepare_context")
    builder.add_edge("prepare_context", "business_agent")
    builder.add_edge("business_agent", "finalize")
    builder.add_edge("finalize", END)
    graph = builder.compile()
    for _ in range(2):
        result = await graph.ainvoke({})
        if result["outcome"]:
            return AgentOutcome.model_validate(result["outcome"])
    raise ServiceError("INVALID_WAIT_STATE", 502)


async def _form_action(context, action):
    from xiaohe.deep_runtime.models import ToolResult
    from xiaohe.deep_runtime.tools import _save_execution, active_draft, checked_context
    from xiaohe.lead_skills.store import resolve_draft_policy
    from xiaohe.reception.fields import missing_required

    async with context.factory.begin() as db:
        _, command, _, flow = await checked_context(db, context, "read_lead")
        execution = DeepExecution.model_validate(command.payload["deepExecution"])
        key = "form_action"
        if key in execution.effects:
            return
        row = await active_draft(db, context, flow, action["draftId"])
        if row.revision != action["expectedRevision"] or row.state not in ("collecting", "review"):
            raise ServiceError("DRAFT_CONFLICT", 409)
        policy = await resolve_draft_policy(db, row)
        if action["action"] == "save_round":
            steps = [group.id for group in policy.rounds]
            target = (
                steps[min(steps.index(row.step) + 1, len(steps) - 1)]
                if row.step in steps
                else row.step
            )
        else:
            target = "summary"
        if row.step != target:
            row.step, row.revision, row.summary_version = target, row.revision + 1, None
        notice = render_lead_notice(
            context.bundle.interaction_policy, "saved", direction=row.direction
        )
        if target == "summary":
            row.state, row.summary_version = "review", row.revision
            missing = missing_required(row.direction, row.fields, policy=policy)
            if missing:
                notice += "\n" + render_lead_notice(
                    context.bundle.interaction_policy,
                    "missing",
                    direction=row.direction,
                    missing_labels=[field.label for field in policy.fields if field.id in missing],
                )
            notice += "\n请核对当前汇总，确认无误后再提交。"
        _save_execution(
            command,
            execution,
            key,
            ToolResult(
                operation="form_action",
                revision=row.revision,
                data={"text": notice, "collection": True},
            ),
        )
