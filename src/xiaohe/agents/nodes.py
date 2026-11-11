from typing import cast, get_args
from uuid import UUID, uuid5

from sqlalchemy import select

from xiaohe.agents.contracts import (
    AgentDirection,
    AgentFrame,
    AnswerDraft,
    ConfirmationEvidence,
    InvitationState,
    LeadConfirmationEvidence,
    ModelContext,
    SemanticDecision,
    VisibleTurn,
)
from xiaohe.agents.semantic import compose_answer, understand_turn
from xiaohe.contracts import Actor, AgentOutcome, ServiceError, TrustedSystem
from xiaohe.lead_skills.notices import render_lead_notice
from xiaohe.lead_skills.store import command_bundle_id, load_policy, resolve_draft_policy
from xiaohe.models import Command, Conversation, Message, System, User
from xiaohe.reception.contracts import CreateDraft
from xiaohe.reception.fields import FIELD_LABELS, missing_required
from xiaohe.reception.models import ReceptionDraft, ReceptionSubmission
from xiaohe.reception.skills import load_skill_materials
from xiaohe.reception.store import create_draft, locked_flow, patch_locked_draft
from xiaohe.reception.submission import (
    actor_from_user,
    confirm_locked_draft,
    create_draft_from_business,
    prepare_submission_payload,
    submit_confirmed,
)
from xiaohe.runs import require_write
from xiaohe.workflows.compiler import ORGANIZATION_OPERATIONS, NodeExecutor
from xiaohe.workflows.definitions import draft_definition
from xiaohe.workflows.loader import workflow_for
from xiaohe.workflows.models import AnswerNode, DecideNode, NodeType, OperationName, RouteEvent

ROLE_DIRECTIONS: dict[str, AgentDirection] = {
    "xiaohe-reception": "unknown",
    "xiaohe-buyer": "buyer",
    "xiaohe-organization": "organization",
    "xiaohe-supplier": "supplier",
}
BUTTON_EVENTS: dict[str, RouteEvent] = {
    "save_round": "save_round",
    "skip_optional": "skip_optional",
    "show_summary": "show_summary",
    "confirm": "confirm_current_summary",
    "resume": "resume_draft",
}
OPERATION_EVENTS = {
    "collect_details": {"provide_details", "consultation_with_details"},
    "save_round": {"save_round"},
    "show_summary": {"show_summary", "skip_optional"},
    "confirm_submission": {"confirm_current_summary"},
    "request_auth": {"accept_invitation"},
    "new_draft": {"new_need"},
    "resume_draft": {"resume_draft"},
    "query_progress": {"query_progress"},
    "record_organization_intent": {"record_intent"},
    "start_onboarding": {"start_onboarding"},
}


async def _command(db, fence):
    run = await require_write(db, fence)
    command = await db.scalar(
        select(Command).where(Command.run_id == run.id, Command.completed_at.is_(None))
    )
    if command is None:
        raise ServiceError("ACTION_STALE", 409)
    return run, command


async def build_agent_frame(factory, fence, agent_id, bundle):
    definition = workflow_for(bundle, agent_id)
    async with factory.begin() as db:
        run, command = await _command(db, fence)
        if command_bundle_id(run, command) != bundle.bundle_id:
            raise ServiceError("ACTION_STALE", 409)
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
        conversation = await db.get(Conversation, run.conversation_id)
        flow = await locked_flow(db, conversation)
        row = await db.get(ReceptionDraft, flow.active_draft_id) if flow.active_draft_id else None
        binding = command.payload.get("agentExecution", {})
        if binding.get("pending") and binding.get("active") == agent_id:
            rows = (
                await db.scalars(
                    select(ReceptionDraft).where(
                        ReceptionDraft.conversation_id == run.conversation_id,
                        ReceptionDraft.direction == ROLE_DIRECTIONS[agent_id],
                        ReceptionDraft.state.in_(["collecting", "review", "submitting"]),
                    )
                )
            ).all()
            row = rows[0] if len(rows) == 1 else None
        if row is not None and row.direction != ROLE_DIRECTIONS[agent_id]:
            row = None
        message = await db.get(Message, command.message_id)
        recent = (
            await db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == run.conversation_id,
                    Message.created_at <= message.created_at,
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(20)
            )
        ).all()
        from xiaohe.graph import _safe_model_text, visible_text

        draft_rules = await draft_definition(db, row) if row else definition
        field_policy = await resolve_draft_policy(db, row) if row else None
        if field_policy is None and definition.lead_skill_id:
            from xiaohe.lead_skills.loader import parse_lead_skills
            field_policy = parse_lead_skills(bundle.resources).field_policies[ROLE_DIRECTIONS[agent_id]]
        interaction_policy = await load_policy(db, command.interaction_policy_id) if command.interaction_policy_id else None
        allowed = [key for group in draft_rules.rounds for key in group.fields] if row else []
        return AgentFrame(
            fence=fence,
            command_id=command.id,
            message_id=message.id,
            conversation_id=run.conversation_id,
            actor=actor,
            agent_id=agent_id,
            bundle_id=bundle.bundle_id,
            field_policy=field_policy,
            interaction_policy=interaction_policy,
            draft_id=row.id if row else None,
            draft_revision=row.revision if row else None,
            summary_version=row.summary_version if row else None,
            pending_request_id=flow.pending_request_id,
            invitation_state=cast(InvitationState, flow.invitation_state),
            model_context=ModelContext(
                authenticated=actor.user_id is not None,
                required_fields=field_policy.required_fields if field_policy else draft_rules.required_fields,
                field_labels={item.id: item.label for item in field_policy.fields} if field_policy else {},
                text=_safe_model_text(visible_text(message.parts)),
                direction=ROLE_DIRECTIONS[agent_id],
                recent_turns=[
                    VisibleTurn(role=item.role, text=_safe_model_text(visible_text(item.parts)))
                    for item in reversed(recent)
                    if item.role in ("user", "assistant")
                ],
                draft_step=row.step if row else None,
                draft_state=row.state if row else None,
                handoff_summary=(
                    "本轮由 "
                    + binding.get("fromDirection", "unknown")
                    + " 方向交接；继续当前问题，仅使用目标方向的草稿。"
                )
                if binding.get("handoffs")
                else None,
                allowed_fields=allowed,
                field_options={
                    item.id: item.options for item in field_policy.fields if item.options
                }
                if field_policy
                else {},
                role_guide=interaction_policy.guides.get(ROLE_DIRECTIONS[agent_id]) if interaction_policy else None,
                saved_fields=row.fields if row else {},
                summary_visible=bool(
                    row and row.state == "review" and row.summary_version == row.revision
                ),
                eligible_topics=definition.skills,
                invitation_state=cast(InvitationState, flow.invitation_state),
                allowed_actions=list(
                    dict.fromkeys(
                        [cast(RouteEvent, "unclear")]
                        + [
                            event
                            for node in definition.nodes
                            if isinstance(node, DecideNode)
                            for event, target in node.routes.items()
                            if target != node.fallback
                        ]
                    )
                ),
            ),
        )


async def _checked(db, frame):
    run, command = await _command(db, frame.fence)
    if (
        command.id,
        command.message_id,
        run.conversation_id,
        run.session_id,
        run.user_id,
        run.guest_id,
        run.system_id,
        run.tenant_id,
        command_bundle_id(run, command),
    ) != (
        frame.command_id,
        frame.message_id,
        frame.conversation_id,
        frame.actor.session_id,
        frame.actor.user_id,
        frame.actor.guest_id,
        frame.actor.system.system_id,
        frame.actor.system.tenant_id,
        frame.bundle_id,
    ):
        raise ServiceError("FORBIDDEN", 403)
    conversation = await db.get(Conversation, run.conversation_id)
    if ROLE_DIRECTIONS[frame.agent_id] != conversation.direction:
        binding = command.payload.get("agentExecution", {})
        if not (
            binding.get("pending")
            and binding.get("active") == frame.agent_id
            and binding.get("fromDirection") == conversation.direction
        ):
            raise ServiceError("ACTION_STALE", 409)
    return run, command, conversation, await locked_flow(db, conversation)


async def _activate_pending(db, frame, command, conversation, flow):
    binding = command.payload.get("agentExecution", {})
    if not binding.get("pending"):
        return
    from xiaohe.agents.runtime import _select_direction

    await _select_direction(db, conversation, flow, frame.agent_id)
    row = await db.get(ReceptionDraft, flow.active_draft_id) if flow.active_draft_id else None
    if (flow.active_draft_id, row.revision if row else None) != (
        frame.draft_id,
        frame.draft_revision,
    ):
        raise ServiceError("DRAFT_CONFLICT", 409)
    command.payload = command.payload | {"agentExecution": binding | {"pending": False}}


def _stage(command, agent_id):
    return command.payload.get("agentStages", {}).get(agent_id, {})


def _save_stage(command, agent_id, **values):
    stages = command.payload.get("agentStages", {})
    command.payload = command.payload | {
        "agentStages": stages | {agent_id: stages.get(agent_id, {}) | values}
    }


def _safe_decision(decision):
    return not (
        decision.negated
        or decision.deferred
        or decision.conditional
        or decision.needs_clarification
    )


def validate_confirmation_evidence(evidence, row, command, run, message_id, user_id):
    if not isinstance(evidence, ConfirmationEvidence) or (
        evidence.command_id,
        evidence.run_id,
        evidence.message_id,
        evidence.user_id,
        evidence.draft_id,
        evidence.revision,
        evidence.summary_version,
        evidence.bundle_id,
    ) != (
        command.id,
        run.id,
        message_id,
        user_id,
        row.id,
        row.revision,
        row.summary_version,
        run.workflow_bundle_id,
    ):
        raise ServiceError("DRAFT_CONFLICT", 409)
    stage = _stage(command, evidence.agent_id)
    try:
        frame = AgentFrame.model_validate(stage["frame"])
        decision = SemanticDecision.model_validate(stage["decision"])
    except (KeyError, ValueError):
        raise ServiceError("INPUT_INVALID", 422) from None
    if (
        frame.draft_id,
        frame.draft_revision,
        frame.summary_version,
        frame.message_id,
        frame.actor.user_id,
        frame.command_id,
        frame.fence.run_id,
        frame.bundle_id,
    ) != (
        row.id,
        row.revision,
        row.summary_version,
        message_id,
        user_id,
        command.id,
        run.id,
        run.workflow_bundle_id,
    ) or not frame.model_context.summary_visible:
        raise ServiceError("DRAFT_CONFLICT", 409)
    if (
        decision.action != "confirm_current_summary"
        or not _safe_decision(decision)
        or decision.candidates
    ):
        raise ServiceError("INPUT_INVALID", 422)
    if evidence.source == "button":
        action = command.payload.get("reception", {})
        if (
            action.get("action"),
            action.get("draftId"),
            action.get("expectedRevision"),
            action.get("summaryVersion"),
        ) != ("confirm", str(row.id), row.revision, row.summary_version):
            raise ServiceError("ACTION_STALE", 409)


def make_node_executors(factory, *, model, business_client, bundle) -> dict[NodeType, NodeExecutor]:
    async def understand(node, state, config):
        frame = AgentFrame.model_validate(state["frame"])
        async with factory.begin() as db:
            _, command, _, _ = await _checked(db, frame)
            cached = _stage(command, frame.agent_id)
            if cached.get("decision"):
                return {"decision": cached["decision"]}
            action = command.payload.get("reception", {})
            control = command.payload.get("receptionControl")
        if action:
            if action.get("action") not in BUTTON_EVENTS or (
                action.get("action") != "resume"
                and (action.get("draftId"), action.get("expectedRevision"))
                != (str(frame.draft_id), frame.draft_revision)
            ):
                raise ServiceError("ACTION_STALE", 409)
            decision = SemanticDecision(action=BUTTON_EVENTS[action["action"]])
        elif control:
            if control not in ("accept_invitation", "start_collection", "switch_direction"):
                raise ServiceError("ACTION_STALE", 409)
            decision = SemanticDecision(
                action="consultation" if control == "switch_direction" else "accept_invitation"
            )
        else:
            from xiaohe.reception.flow import collection_shortcut

            topic = collection_shortcut(frame.model_context.direction, frame.model_context.text)
            if topic and topic in frame.model_context.eligible_topics and frame.draft_id is None:
                decision = SemanticDecision(action="accept_invitation", topics=[topic])
            else:
                if model is None:
                    from xiaohe.graph import reception_model

                    turn_model = reception_model()
                else:
                    turn_model = model
                decision = await understand_turn(
                    turn_model,
                    frame,
                    workflow_for(bundle, frame.agent_id),
                    bundle,
                    prompt_key=node.prompt,
                )
        async with factory.begin() as db:
            _, command, _, _ = await _checked(db, frame)
            _save_stage(
                command,
                frame.agent_id,
                frame=frame.model_dump(mode="json"),
                decision=decision.model_dump(mode="json"),
            )
        return {"decision": decision.model_dump()}

    async def decide(node, state, config):
        frame = AgentFrame.model_validate(state["frame"])
        decision = SemanticDecision.model_validate(state["decision"])
        event = decision.action
        if (
            decision.needs_clarification
            or decision.conditional
            or (not _safe_decision(decision) and event != "defer_invitation")
        ):
            event = "unclear"
        if event == "defer_invitation" and frame.invitation_state not in ("offered", "deferred"):
            event = "unclear"
        if event == "confirm_current_summary" and decision.candidates and not frame.interaction_policy:
            event = "consultation_with_details" if decision.topics else "provide_details"
        if event == "confirm_current_summary" and not frame.model_context.summary_visible and not frame.interaction_policy:
            event = "unclear"
        if event == "switch_direction" and (
            not decision.explicit_switch or decision.target_direction in (None, "unknown")
        ):
            event = "unclear"
        if (
            event == "switch_direction"
            and decision.target_direction == frame.model_context.direction
        ):
            event = "consultation"
        async with factory.begin() as db:
            _, command, _, _ = await _checked(db, frame)
            if _stage(command, frame.agent_id).get("decision") != decision.model_dump(mode="json"):
                raise ServiceError("INPUT_INVALID", 422)
            _save_stage(command, frame.agent_id, effective_event=event)
        return {"event": event}

    async def answer(node, state, config):
        frame = AgentFrame.model_validate(state["frame"])
        decision = SemanticDecision.model_validate(state["decision"])
        async with factory.begin() as db:
            _, command, _, _ = await _checked(db, frame)
            if cached := _stage(command, frame.agent_id).get("answer"):
                return {"answer": cached}
            if command.payload.get("receptionControl") in ("accept_invitation", "start_collection"):
                return {}
            partner_entry = command.payload.get("receptionControl") == "switch_direction"
        if partner_entry:
            from xiaohe.reception.flow import LABELS, OPENERS

            direction = ROLE_DIRECTIONS[frame.agent_id]
            text = "您好，我是小鹤" + LABELS[direction] + " Agent。"
            text += (
                "您在此方向的资料已保留，可以继续咨询或补充原资料。"
                if frame.draft_id
                else OPENERS[direction]
            )
            result = AnswerDraft(text=text, basis="needs_confirmation")
        else:
            materials = load_skill_materials(bundle, frame.agent_id, decision.topics)
            definition = workflow_for(bundle, frame.agent_id)
            if model is None:
                from xiaohe.graph import reception_model

                turn_model = reception_model()
            else:
                turn_model = model
            result = await compose_answer(
                turn_model,
                frame,
                decision,
                materials,
                bundle.resources["prompts/" + definition.prompts[node.prompt]],
            )
        async with factory.begin() as db:
            _, command, _, _ = await _checked(db, frame)
            _save_stage(command, frame.agent_id, answer=result.model_dump(mode="json"))
        return {"answer": result.model_dump()}

    async def operation(node, state, config):
        frame = AgentFrame.model_validate(state["frame"])
        decision = SemanticDecision.model_validate(state["decision"])
        if (
            node.operation not in get_args(OperationName)
            or frame.agent_id == "xiaohe-reception"
            or (
                node.operation in ORGANIZATION_OPERATIONS
                and frame.agent_id != "xiaohe-organization"
            )
        ):
            raise ServiceError("FORBIDDEN", 403)
        if not _safe_decision(decision) or (
            node.operation == "confirm_submission"
            and (decision.action != "confirm_current_summary" or (decision.candidates and not frame.interaction_policy))
        ):
            raise ServiceError("INPUT_INVALID", 422)
        editing_confirmation = node.operation == "collect_details" and (
            decision.action == "confirm_current_summary" and decision.candidates
        )
        if decision.action not in OPERATION_EVENTS[node.operation] and not editing_confirmation:
            raise ServiceError("INPUT_INVALID", 422)
        # 旧挂起流程仍可能直接走 auth；复用答疑回执，确保建草稿前已完成回答。
        prepared_answer = (
            await answer(
                AnswerNode(
                    id="conversion_answer", name="回答转化问题", type="answer",
                    prompt="answer", next=node.id,
                ),
                state,
                config,
            )
            if node.operation == "request_auth"
            else {}
        )
        submission_id = None
        if node.operation == "confirm_submission":
            async with factory.begin() as db:
                _, command, _, _ = await _checked(db, frame)
                stage = _stage(command, frame.agent_id)
                if stage.get("decision") != decision.model_dump(mode="json"):
                    raise ServiceError("INPUT_INVALID", 422)
                previous = stage.get("submission_id")
            if previous:
                return await finish_submission(frame, UUID(previous))
        async with factory.begin() as db:
            run, command, conversation, flow = await _checked(db, frame)
            stage = _stage(command, frame.agent_id)
            if stage.get("decision") != decision.model_dump(mode="json"):
                raise ServiceError("INPUT_INVALID", 422)
            if cached := stage.get("operation_result"):
                return prepared_answer | {"operation_result": cached}
            await _activate_pending(db, frame, command, conversation, flow)
            row = (
                await db.get(ReceptionDraft, flow.active_draft_id, with_for_update=True, populate_existing=True) if flow.active_draft_id else None
            )
            if (row.id if row else None, row.revision if row else None) != (
                frame.draft_id,
                frame.draft_revision,
            ):
                raise ServiceError("DRAFT_CONFLICT", 409)
            text = ""
            collection = False
            if node.operation == "request_auth":
                flow.invitation_state = "accepted"
                if run.user_id is None:
                    return prepared_answer | {
                        "operation_result": {
                            "text": "请通过认证卡片注册或登录。",
                            "auth": True,
                            "wait": False,
                        }
                    }
                if row is None:
                    drafts = (
                        await db.scalars(
                            select(ReceptionDraft).where(
                                ReceptionDraft.conversation_id == frame.conversation_id,
                                ReceptionDraft.direction == conversation.direction,
                                ReceptionDraft.state.in_(["collecting", "review", "submitting"]),
                            )
                        )
                    ).all()
                    if len(drafts) > 1:
                        result = {"text": "该方向已有多份草稿，请先选择要继续的一份。", "wait": True}
                        _save_stage(command, frame.agent_id, operation_result=result)
                        return prepared_answer | {"operation_result": result}
                    if drafts:
                        row = drafts[0]
                        flow.active_draft_id = row.id
                    else:
                        view = await create_draft(
                            db,
                            frame.actor,
                            frame.conversation_id,
                            CreateDraft(
                                client_draft_id="invitation:" + str(command.id),
                                kind="service",
                                direction=conversation.direction,
                            ),
                            definition_bundle_id=frame.bundle_id,
                        )
                        row = await db.get(ReceptionDraft, UUID(view.id))
                        row.definition_bundle_id = frame.bundle_id
                text = (
                    "已准备需求草稿，请填写当前资料；最后核对汇总后再确认提交。"
                    if row.state in ("collecting", "review")
                    else "该需求正在核实受理结果，请稍后查询进展。"
                    if row.state == "submitting"
                    else "该需求已有受理回执，请在“我的进展”查看；另一项需求请明确新建。"
                )
                if frame.interaction_policy:
                    text = render_lead_notice(frame.interaction_policy,
                        "prepare" if row.state in ("collecting", "review") else
                        "unknown" if row.state == "submitting" else "accepted",
                        direction=row.direction)
                collection = row.state in ("collecting", "review")
            elif node.operation == "new_draft":
                if run.user_id is None:
                    raise ServiceError("AUTH_REQUIRED", 401)
                view = await create_draft(
                    db,
                    frame.actor,
                    frame.conversation_id,
                    CreateDraft(
                        client_draft_id="new:" + str(command.id),
                        kind="service",
                        direction=conversation.direction,
                    ),
                    definition_bundle_id=frame.bundle_id,
                )
                row = await db.get(ReceptionDraft, UUID(view.id))
                row.definition_bundle_id = frame.bundle_id
                flow.invitation_state = "accepted"
                text = "已创建新的需求草稿。"
                collection = True
                if frame.interaction_policy:
                    text = render_lead_notice(frame.interaction_policy, "prepare", direction=row.direction)
            elif node.operation == "resume_draft":
                action = command.payload.get("reception", {})
                rows = (
                    await db.scalars(
                        select(ReceptionDraft).where(
                            ReceptionDraft.conversation_id == conversation.id,
                            ReceptionDraft.direction == conversation.direction,
                            ReceptionDraft.state.in_(["collecting", "review", "submitting"]),
                        )
                    )
                ).all()
                matches = [
                    item
                    for item in rows
                    if (
                        action.get("draftId") == str(item.id)
                        and action.get("expectedRevision") == item.revision
                    )
                    or (
                        decision.draft_name is not None
                        and decision.draft_name
                        in (item.fields.get("companyName"), item.fields.get("organizationName"))
                    )
                ]
                if len(matches) != 1:
                    text = "请在草稿列表选择要继续的一份。"
                else:
                    flow.active_draft_id = matches[0].id
                    row = matches[0]
                    collection = True
                    text = "已恢复所选草稿。"
            elif node.operation in (
                "collect_details",
                "save_round",
                "show_summary",
                "confirm_submission",
            ):
                if row is None:
                    text = "请先接受登记邀请并登录，再填写或确认资料。"
                elif (
                    row.state == "accepted"
                    and row.kind == "service"
                    and node.operation == "collect_details"
                ):
                    if decision.action == "consultation_with_details" and not stage.get("answer"):
                        raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
                    return await _external_prepared(
                        db, "edit_application", frame, decision, row, command, run
                    )
                elif row.state == "accepted":
                    text = "该需求已有受理回执；新需求请新建草稿。"
                elif node.operation == "confirm_submission":
                    if frame.interaction_policy:
                        if run.user_id is None:
                            flow.invitation_state = "accepted"
                            result = {"text": "请通过认证卡片注册或登录。", "auth": True, "wait": False}
                            _save_stage(command, frame.agent_id, operation_result=result)
                            return {"operation_result": result}
                        if decision.candidates:
                            await patch_locked_draft(db, row, frame.draft_revision,
                                {key: item.value for key, item in decision.candidates.items()})
                        rules = await draft_definition(db, row)
                        missing = missing_required(row.direction, row.fields, definition=rules)
                        if missing:
                            text = render_lead_notice(frame.interaction_policy, "missing", direction=row.direction,
                                missing_labels=[field.label for field in frame.field_policy.fields if field.id in missing])
                        else:
                            from xiaohe.agents.confirmation import payload_digest

                            source = "button" if command.payload.get("reception") else (
                                "semantic_reviewed" if frame.model_context.summary_visible and not decision.candidates
                                else "semantic_direct")
                            evidence = LeadConfirmationEvidence(source=source,
                                command_id=command.id, run_id=run.id, message_id=frame.message_id,
                                user_id=run.user_id, draft_id=row.id, revision=row.revision,
                                agent_id=frame.agent_id, summary_version=row.summary_version if source != "semantic_direct" else None,
                                field_policy_id=row.field_policy_id,
                                interaction_policy_id=command.interaction_policy_id,
                                payload_digest=payload_digest(await prepare_submission_payload(db, row)))
                            _save_stage(command, frame.agent_id, leadConfirmation=evidence.model_dump(mode="json"))
                            submission = await confirm_locked_draft(db, row, row.revision,
                                evidence.summary_version, frame.message_id, run.user_id, evidence=evidence)
                            submission_id = submission.id
                    else:
                        if not frame.model_context.summary_visible or frame.summary_version is None:
                            raise ServiceError("DRAFT_CONFLICT", 409)
                        evidence = ConfirmationEvidence.from_frame(
                            frame, source="button" if command.payload.get("reception") else "semantic")
                        submission = await confirm_locked_draft(db, row, frame.draft_revision,
                            frame.summary_version, frame.message_id, run.user_id, evidence=evidence)
                        submission_id = submission.id
                else:
                    if row.state == "submitting":
                        raise ServiceError("DRAFT_CONFLICT", 409)
                    if decision.candidates:
                        if decision.action == "consultation_with_details" and not stage.get(
                            "answer"
                        ):
                            raise ServiceError("UPSTREAM_UNAVAILABLE", 503, True)
                        await patch_locked_draft(
                            db,
                            row,
                            frame.draft_revision,
                            {key: item.value for key, item in decision.candidates.items()},
                        )
                    draft_rules = await draft_definition(db, row)
                    steps = [group.id for group in draft_rules.rounds]
                    if node.operation == "save_round":
                        target = steps[min(steps.index(row.step) + 1, len(steps) - 1)]
                    elif node.operation == "show_summary":
                        target = "summary"
                    else:
                        target = row.step
                    if row.step != target:
                        row.step, row.revision = target, row.revision + 1
                        row.summary_version = None
                    if row.step == "summary":
                        row.state, row.summary_version = "review", row.revision
                        missing = missing_required(
                            row.direction, row.fields, definition=await draft_definition(db, row)
                        )
                        text = (
                            "还缺少最低资料："
                            + "、".join(FIELD_LABELS[key] for key in missing)
                            + "。"
                            if missing
                            else ""
                        ) + "请核对当前汇总，确认无误后再提交。"
                    else:
                        fields = next(
                            (group.fields for group in draft_rules.rounds if group.id == row.step),
                            [],
                        )
                        fields = [key for key in fields if not row.fields.get(key)]
                        text = (
                            (
                                "请补充本轮资料："
                                + "、".join(FIELD_LABELS[key] for key in fields)
                                + "；也可先继续咨询。"
                            )
                            if fields
                            else "本轮资料已保存。您可以继续交流、补充其他资料，或查看汇总。"
                        )
                    if frame.interaction_policy:
                        policy = await resolve_draft_policy(db, row)
                        missing = missing_required(row.direction, row.fields, definition=draft_rules)
                        text = render_lead_notice(frame.interaction_policy, "saved", direction=row.direction)
                        if missing:
                            text += "\n" + render_lead_notice(frame.interaction_policy, "missing", direction=row.direction,
                                missing_labels=[field.label for field in policy.fields if field.id in missing])
            elif node.operation in (
                "record_organization_intent",
                "start_onboarding",
                "query_progress",
            ):
                # Network operations run after releasing the DB transaction and recheck the fence on return.
                return await _external_prepared(
                    db, node.operation, frame, decision, row, command, run
                )
            result = {"text": text, "wait": submission_id is None}
            if collection and row is not None and row.state in ("collecting", "review"):
                result["collection"] = True
            if submission_id is not None:
                _save_stage(command, frame.agent_id, submission_id=str(submission_id))
            else:
                _save_stage(command, frame.agent_id, operation_result=result)
        if submission_id is not None:
            return await finish_submission(frame, submission_id)
        return prepared_answer | {"operation_result": result}

    async def finish_submission(frame, submission_id):
        try:
            receipt = await submit_confirmed(factory, frame.fence, submission_id, business_client)
        except ServiceError as error:
            if not frame.interaction_policy or error.code not in (
                "INPUT_INVALID", "DRAFT_CONFLICT", "SUBJECT_CONFLICT", "IDEMPOTENCY_CONFLICT",
            ):
                raise
            result = {"text": render_lead_notice(frame.interaction_policy, "failed",
                direction=frame.model_context.direction, public_guidance="请核对资料或查询当前进展。"), "wait": True}
            async with factory.begin() as db:
                _, command, _, _ = await _checked(db, frame)
                _save_stage(command, frame.agent_id, operation_result=result)
            return {"operation_result": result}
        result = {
                "text": "入驻申请已记录，待后台审核。"
                if receipt.kind == "onboarding"
                else "需求已记录，请在进展中查看真实跟进状态。",
                "wait": False,
        }
        if frame.interaction_policy:
            result["text"] = render_lead_notice(frame.interaction_policy, "accepted", direction=frame.model_context.direction)
        async with factory.begin() as db:
            _, command, _, _ = await _checked(db, frame)
            _save_stage(command, frame.agent_id, operation_result=result)
        return {"operation_result": result}

    async def _external_prepared(db, operation_name, frame, decision, row, command, run):
        if run.user_id is None:
            raise ServiceError("AUTH_REQUIRED", 401)
        formal = (
            await db.scalar(
                select(ReceptionSubmission)
                .where(
                    ReceptionSubmission.draft_id == row.id, ReceptionSubmission.status == "accepted"
                )
                .order_by(ReceptionSubmission.draft_revision.desc())
                .limit(1)
            )
            if row
            else None
        )
        application_id = (
            formal.receipt["applicationId"]
            if formal
            else row.source_application_id
            if row
            else None
        )
        prepared = {"external": operation_name, "application_id": application_id}
        if operation_name == "query_progress" and row and row.state == "submitting":
            pending = await db.scalar(
                select(ReceptionSubmission)
                .where(
                    ReceptionSubmission.draft_id == row.id, ReceptionSubmission.status == "pending"
                )
                .order_by(ReceptionSubmission.draft_revision.desc())
                .limit(1)
            )
            if pending:
                prepared["submission_id"] = str(pending.id)
        if operation_name == "start_onboarding" and application_id:
            pending = (
                await db.scalars(
                    select(ReceptionDraft).where(
                        ReceptionDraft.conversation_id == frame.conversation_id,
                        ReceptionDraft.kind == "onboarding",
                        ReceptionDraft.source_application_id == application_id,
                        ReceptionDraft.state.in_(["collecting", "review"]),
                    )
                )
            ).all()
            if row and row.kind == "onboarding" and row.state in ("collecting", "review"):
                prepared["resume_id"] = str(row.id)
            elif len(pending) == 1:
                prepared["resume_id"] = str(pending[0].id)
            elif len(pending) > 1:
                prepared["choose"] = True
            if decision.onboarding_mode == "supplement":
                latest = await db.scalar(
                    select(ReceptionSubmission)
                    .where(
                        ReceptionSubmission.conversation_id == frame.conversation_id,
                        ReceptionSubmission.status == "accepted",
                        ReceptionSubmission.receipt["applicationId"].astext == application_id,
                        ReceptionSubmission.receipt["kind"].astext == "onboarding",
                    )
                    .order_by(ReceptionSubmission.draft_revision.desc())
                    .limit(1)
                )
                prepared["onboarding_id"] = latest.receipt.get("onboardingId") if latest else None
                if not prepared["onboarding_id"]:
                    prepared["missing_onboarding"] = True
        _save_stage(command, frame.agent_id, operation_result=prepared)
        return {"operation_result": prepared}

    async def handoff(node, state, config):
        decision = SemanticDecision.model_validate(state["decision"])
        target = "xiaohe-" + str(decision.target_direction)
        if target not in node.targets or not _safe_decision(decision):
            return {"handoff": None, "operation_result": {"text": "请明确需要哪个服务方向。"}}
        return {"handoff": target}

    async def result(node, state, config):
        frame = AgentFrame.model_validate(state["frame"])
        answer_value = AnswerDraft.model_validate(state["answer"]) if state.get("answer") else None
        operation_value = state.get("operation_result", {})
        if operation_value.get("external"):
            operation_value = await _external_operation(
                factory,
                frame,
                SemanticDecision.model_validate(state["decision"]),
                operation_value,
                business_client,
            )
        text = (
            (
                ("一般建议：" if answer_value and answer_value.basis == "general_advice" else "")
                + answer_value.text
            )
            if answer_value
            else ""
        )
        if operation_value.get("text"):
            text += ("\n\n" if text else "") + operation_value["text"]
        if not text:
            text = "请说明您想咨询、补充资料还是切换服务方向；含条件或不明确的表达不会直接提交。"
        wait = operation_value.get("wait", node.wait_for_input)
        async with factory.begin() as db:
            run, command, conversation, flow = await _checked(db, frame)
            if cached := _stage(command, frame.agent_id).get("outcome"):
                return {"outcome": cached}
            await _activate_pending(db, frame, command, conversation, flow)
            if state.get("event") == "defer_invitation":
                flow.invitation_state = "deferred"
                text += "\n\n好的，先继续咨询；需要登记时再告诉我。"
            offered = (
                frame.agent_id != "xiaohe-reception"
                and command.payload.get("receptionControl") != "switch_direction"
                and flow.active_draft_id is None
                and flow.invitation_state == "none"
                and run.user_id is None
                and answer_value
                and state.get("event") in ("consultation", "consultation_with_details")
            )
            if offered:
                flow.invitation_state = "offered"
                text += "\n\n是否愿意登录并登记需求，方便后续跟进？也可以继续咨询或暂缓。"
            parts = [{"type": "text", "text": text}]
            if operation_value.get("collection") and run.user_id is not None:
                from xiaohe.reception.flow import COLLECTION_NOTICE

                parts.append({"type": "system_notice", "text": COLLECTION_NOTICE})
            if operation_value.get("auth") or (offered and run.user_id is None):
                parts.append({"type": "auth_request", "intent": "lead"})
            if wait:
                flow.pending_request_id = uuid5(command.id, "agent.input")
                parts.append(
                    {
                        "type": "input_request",
                        "requestId": str(flow.pending_request_id),
                        "prompt": "您可以继续咨询或补充当前资料。",
                    }
                )
            else:
                flow.pending_request_id = None
            outcome = AgentOutcome(parts=parts, wait_for_input=wait, artifact_version_ids=[])
            _save_stage(
                command, frame.agent_id, outcome=outcome.model_dump(mode="json", by_alias=True)
            )
        return {"outcome": outcome.model_dump(mode="json", by_alias=True)}

    return {
        "understand": understand,
        "decide": decide,
        "answer": answer,
        "operation": operation,
        "handoff": handoff,
        "result": result,
    }


async def _external_operation(factory, frame, decision, prepared, client):
    name, application_id = prepared["external"], prepared.get("application_id")
    async with factory.begin() as db:
        run, command, _, _ = await _checked(db, frame)
        if cached := _stage(command, frame.agent_id).get("external_result"):
            return cached
        actor = actor_from_user(await db.get(User, run.user_id))
    if name == "query_progress":
        if client is None:
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
        if prepared.get("submission_id"):
            try:
                await submit_confirmed(
                    factory, frame.fence, UUID(prepared["submission_id"]), client
                )
                value = {"text": "已核实原提交的受理回执，请在进展中查看真实跟进。", "wait": False}
                if frame.interaction_policy:
                    value["text"] = render_lead_notice(frame.interaction_policy, "accepted", direction=frame.model_context.direction)
            except ServiceError as error:
                if error.code != "SUBMISSION_UNKNOWN":
                    raise
                value = {
                    "text": "正式受理结果仍待核实；稍后会沿原提交核对，不重复创建。",
                    "wait": False,
                }
                if frame.interaction_policy:
                    value["text"] = render_lead_notice(frame.interaction_policy, "unknown", direction=frame.model_context.direction)
        else:
            page = await client.progress(actor, {"cursor": None})
            value = {
                "text": "已核实当前进展，请在进展列表查看记录。"
                if page.groups
                else "当前没有查到该方向的进展记录。"
            }
    elif name == "edit_application":
        if not application_id:
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
        async with factory.begin() as db:
            _, command, _, flow = await _checked(db, frame)
            drafts = (
                await db.scalars(
                    select(ReceptionDraft).where(
                        ReceptionDraft.conversation_id == frame.conversation_id,
                        ReceptionDraft.source_application_id == application_id,
                        ReceptionDraft.kind == "service",
                        ReceptionDraft.direction == frame.model_context.direction,
                        ReceptionDraft.state.in_(["collecting", "review"]),
                    )
                )
            ).all()
            if len(drafts) > 1:
                flow.active_draft_id = None
                value = {"text": "这份申请有多份修改草稿，请先在草稿列表选择要继续的一份。"}
                _save_stage(command, frame.agent_id, external_result=value)
                return value
            draft_id = drafts[0].id if drafts else None
            expected_revision = drafts[0].revision if drafts else None
        if draft_id is None:
            view = await create_draft_from_business(
                factory,
                frame.actor,
                frame.conversation_id,
                CreateDraft(
                    client_draft_id="edit:" + str(frame.command_id),
                    kind="service",
                    direction=frame.model_context.direction,
                    source_application_id=application_id,
                ),
                client,
                fence=frame.fence,
                definition_bundle_id=frame.bundle_id,
            )
            draft_id = UUID(view.id)
            expected_revision = view.revision
        async with factory.begin() as db:
            _, command, _, flow = await _checked(db, frame)
            draft = await db.scalar(
                select(ReceptionDraft).where(ReceptionDraft.id == draft_id).with_for_update()
            )
            if decision.candidates:
                await patch_locked_draft(
                    db,
                    draft,
                    expected_revision,
                    {key: item.value for key, item in decision.candidates.items()},
                )
            flow.active_draft_id = draft.id
            value = {
                "text": "已保存原申请的修改草稿，并同步到右侧资料。您可以继续补充，查看汇总并确认后才会更新正式申请。"
            }
            if frame.interaction_policy:
                value["text"] = render_lead_notice(frame.interaction_policy, "saved", direction=draft.direction)
            _save_stage(command, frame.agent_id, external_result=value)
        return value
    elif name == "start_onboarding":
        value = {"text": "合作与入驻已统一为一份申请，无需再次提交入驻资料。请在“我的进展”查看/修改原申请；审核通过与已完成入驻会分别显示。"}
    elif not application_id:
        value = {"text": "请先完成组织合作申请，受理后可在我的进展查看或修改。"}
    elif name == "record_organization_intent":
        from xiaohe.reception.organization import record_intent

        if decision.organization_intent is None:
            raise ServiceError("INPUT_INVALID", 422)
        if client is None:
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
        await record_intent(
            factory,
            frame.fence,
            application_id,
            decision.organization_intent,
            client,
            semantic_command_id=frame.command_id,
        )
        value = {"text": "已记录合作意向，具体安排及素材提供待生态经理确认。"}
    else:
        raise ServiceError("INPUT_INVALID", 422)
    async with factory.begin() as db:
        _, command, _, _ = await _checked(db, frame)
        _save_stage(command, frame.agent_id, external_result=value)
    return value
