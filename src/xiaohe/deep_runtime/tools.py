import hashlib
import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select

from xiaohe.agents.confirmation import payload_digest
from xiaohe.agents.contracts import (
    AgentFrame,
    FieldCandidate,
    LeadConfirmationEvidence,
    ModelContext,
    SemanticDecision,
)
from xiaohe.agents.semantic import validate_candidates
from xiaohe.agents.state import _save_stage
from xiaohe.contracts import Actor, ServiceError, WriteFence
from xiaohe.conversations import require_conversation
from xiaohe.deep_runtime.bundles import select_binding
from xiaohe.deep_runtime.models import (
    DeepExecution,
    RuntimeBinding,
    RuntimeBundle,
    SubmitIntent,
    ToolResult,
)
from xiaohe.lead_skills.store import resolve_draft_policy
from xiaohe.models import Command, Message, User
from xiaohe.reception.contracts import CreateDraft
from xiaohe.reception.models import ReceptionDraft, ReceptionSubmission
from xiaohe.reception.store import create_draft, locked_flow, patch_locked_draft, require_draft
from xiaohe.reception.submission import (
    actor_from_user,
    confirm_locked_draft,
    create_draft_from_business,
    prepare_submission_payload,
    submit_confirmed,
)
from xiaohe.runs import require_write


@dataclass(frozen=True)
class ToolContext:
    factory: object
    actor: Actor
    fence: WriteFence
    command_id: UUID
    conversation_id: UUID
    binding: RuntimeBinding
    bundle: RuntimeBundle
    text: str
    business_client: object = None


async def checked_context(db, context: ToolContext, operation: str):
    run = await require_write(db, context.fence)
    command = await db.get(Command, context.command_id, populate_existing=True)
    if (
        command is None
        or command.completed_at is not None
        or (
            run.id,
            run.conversation_id,
            run.session_id,
            run.user_id,
            run.guest_id,
            run.system_id,
            run.tenant_id,
        )
        != (
            command.run_id,
            context.conversation_id,
            context.actor.session_id,
            context.actor.user_id,
            context.actor.guest_id,
            context.actor.system.system_id,
            context.actor.system.tenant_id,
        )
    ):
        raise ServiceError("FORBIDDEN", 403)
    if operation == "submit_lead" and (
        command.payload.get("authContinuation")
        or command.payload.get("receptionControl") in ("accept_invitation", "start_collection")
    ):
        raise ServiceError("INPUT_INVALID", 422)
    execution = DeepExecution.model_validate(command.payload.get("deepExecution", {}))
    if (
        execution.bundle_id != context.bundle.bundle_id
        or execution.active != context.binding.agent_id
    ):
        raise ServiceError("ACTION_STALE", 409)
    allowed = select_binding(context.bundle, context.actor, context.binding.agent_id)
    if operation not in allowed.tools or operation not in context.binding.tools:
        raise ServiceError("FORBIDDEN", 403)
    conversation = await require_conversation(db, context.actor, context.conversation_id)
    role = context.binding.agent_id.removeprefix("xiaohe-")
    if role != "reception" and conversation.direction != role:
        raise ServiceError("ACTION_STALE", 409)
    message = await db.get(Message, command.message_id)
    actual_text = "\n".join(
        part["text"] for part in message.parts if part["type"] in ("text", "system_notice")
    )
    if actual_text != context.text:
        raise ServiceError("INPUT_INVALID", 422)
    return run, command, conversation, await locked_flow(db, conversation)


async def tool_frame(db, context, command, row):
    policy = await resolve_draft_policy(db, row)
    allowed = [key for group in policy.rounds for key in group.fields]
    return AgentFrame(
        fence=context.fence,
        command_id=command.id,
        message_id=command.message_id,
        conversation_id=context.conversation_id,
        actor=context.actor,
        agent_id=context.binding.agent_id,
        bundle_id=context.bundle.bundle_id,
        draft_id=row.id,
        draft_revision=row.revision,
        summary_version=row.summary_version,
        field_policy=policy,
        interaction_policy=context.bundle.interaction_policy,
        model_context=ModelContext(
            text=context.text,
            direction=row.direction,
            allowed_fields=allowed,
            saved_fields=row.fields,
            authenticated=bool(context.actor.user_id),
            required_fields=policy.required_fields,
            field_labels={field.id: field.label for field in policy.fields},
            field_options={field.id: field.options for field in policy.fields if field.options},
            summary_visible=row.state == "review" and row.summary_version == row.revision,
        ),
    )


async def active_draft(db, context, flow, draft_id=None):
    try:
        identifier = UUID(str(draft_id)) if draft_id else flow.active_draft_id
    except ValueError:
        raise ServiceError("INPUT_INVALID", 422) from None
    if identifier is None or identifier != flow.active_draft_id:
        raise ServiceError("ACTION_STALE", 409)
    row = await require_draft(db, context.actor, identifier)
    if row.direction != context.binding.agent_id.removeprefix("xiaohe-"):
        raise ServiceError("FORBIDDEN", 403)
    return row


def _save_execution(command, execution, key, result):
    updated = execution.model_copy(
        update={"effects": execution.effects | {key: result.model_dump(mode="json")}}
    )
    command.payload = command.payload | {"deepExecution": updated.model_dump(mode="json")}


async def execute_effect(
    context: ToolContext, operation: str, *, draft_id, expected_revision, payload: dict
) -> ToolResult:
    if operation == "query_progress":
        return await _progress(context)
    if operation == "prepare_lead" and payload.get("mode") == "edit":
        return await _edit_draft(context)
    if operation == "compose_form":
        async with context.factory.begin() as db:
            _, _, _, flow = await checked_context(db, context, operation)
            row = await active_draft(db, context, flow)
            draft_id, expected_revision = row.id, row.revision
    identity = json.dumps(
        [
            str(context.command_id),
            context.binding.agent_id,
            operation,
            str(draft_id) if draft_id else None,
            expected_revision,
            payload,
        ],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    key = hashlib.sha256(identity.encode()).hexdigest()
    submission_id = None
    async with context.factory.begin() as db:
        run, command, conversation, flow = await checked_context(db, context, operation)
        execution = DeepExecution.model_validate(command.payload["deepExecution"])
        cached = execution.effects.get(key)
        if cached:
            result = ToolResult.model_validate(cached)
            if result.status != "unknown":
                return result
            submission_id = UUID(result.receipt_id)
        elif operation == "prepare_lead":
            flow.invitation_state = "accepted"
            if context.actor.user_id is None:
                result = ToolResult(
                    operation=operation, status="auth", data={"auth": True, "wait": False}
                )
            else:
                role = context.binding.agent_id.removeprefix("xiaohe-")
                rows = list(
                    await db.scalars(
                        select(ReceptionDraft).where(
                            ReceptionDraft.conversation_id == conversation.id,
                            ReceptionDraft.direction == role,
                            ReceptionDraft.state.in_(["collecting", "review", "submitting"]),
                        )
                    )
                )
                if not rows and payload.get("mode", "resume") != "new" and flow.active_draft_id:
                    active = await require_draft(db, context.actor, flow.active_draft_id)
                    if active.direction == role and active.state == "accepted":
                        rows = [active]
                requested = str(draft_id) if draft_id else payload.get("draft_id")
                if requested:
                    action = command.payload.get("reception", {})
                    if (action.get("action"), action.get("draftId")) != ("resume", requested):
                        raise ServiceError("INPUT_INVALID", 422)
                    rows = [
                        row
                        for row in rows
                        if str(row.id) == requested
                        and action.get("expectedRevision") == row.revision
                    ]
                    if not rows:
                        raise ServiceError("ACTION_STALE", 409)
                if name := payload.get("draft_name"):
                    if name not in context.text:
                        raise ServiceError("INPUT_INVALID", 422)
                    rows = [
                        row
                        for row in rows
                        if name
                        in (row.fields.get("companyName"), row.fields.get("organizationName"))
                    ]
                    if len(rows) != 1:
                        raise ServiceError("ACTION_STALE", 409)
                if len(rows) > 1 and payload.get("mode") != "new":
                    result = ToolResult(
                        operation=operation,
                        status="choice",
                        data={
                            "drafts": [
                                {
                                    "id": str(row.id),
                                    "revision": row.revision,
                                    "name": row.fields.get("companyName")
                                    or row.fields.get("organizationName"),
                                }
                                for row in rows
                            ]
                        },
                    )
                else:
                    if rows and payload.get("mode", "resume") != "new":
                        row = rows[0]
                        flow.active_draft_id = row.id
                    else:
                        view = await create_draft(
                            db,
                            context.actor,
                            conversation.id,
                            CreateDraft(
                                client_draft_id="deep:" + str(command.id),
                                kind="service",
                                direction=role,
                            ),
                            field_policy=context.bundle.field_policies[role],
                        )
                        row = await db.get(ReceptionDraft, UUID(view.id))
                    result = ToolResult(
                        operation=operation,
                        revision=row.revision,
                        data={
                            "draft_id": str(row.id),
                            "collection": row.state in ("collecting", "review"),
                            "state": row.state,
                        },
                    )
        elif operation == "request_handoff":
            direction = payload.get("direction")
            if (
                direction not in ("buyer", "organization", "supplier")
                or direction == conversation.direction
            ):
                raise ServiceError("INPUT_INVALID", 422)
            evidence = payload.get("evidence", "")
            if (
                not evidence
                or evidence not in context.text
                or (conversation.direction != "unknown" and not payload.get("explicit_switch"))
            ):
                raise ServiceError("INPUT_INVALID", 422)
            result = ToolResult(operation=operation, data={"handoff": "xiaohe-" + direction})
        else:
            row = await active_draft(db, context, flow, draft_id)
            if operation == "read_lead":
                return ToolResult(
                    operation=operation,
                    revision=row.revision,
                    data={
                        "draft_id": str(row.id),
                        "fields": row.fields,
                        "state": row.state,
                        "required_fields": (await resolve_draft_policy(db, row)).required_fields,
                    },
                )
            if operation == "compose_form":
                from xiaohe.deep_runtime.forms import compose_locked_form

                if row.revision != expected_revision:
                    raise ServiceError("DRAFT_CONFLICT", 409)
                view = await compose_locked_form(
                    db, context, row, payload["field_groups"], payload["prompt"]
                )
                result = ToolResult(
                    operation=operation,
                    revision=row.revision,
                    data={"form": view.model_dump(mode="json", by_alias=True), "collection": True},
                )
            else:
                if row.revision != expected_revision:
                    raise ServiceError("DRAFT_CONFLICT", 409)
                frame = await tool_frame(db, context, command, row)
                decision = SemanticDecision(
                    action="provide_details", candidates=payload.get("candidates", {})
                )
                decision = validate_candidates(decision, frame)
                if operation == "submit_lead":
                    if command.payload.get("receptionControl") in (
                        "accept_invitation",
                        "start_collection",
                    ):
                        raise ServiceError("INPUT_INVALID", 422)
                    intent = SubmitIntent.model_validate(payload.get("confirmation", {}))
                    if (
                        not intent.confirmed
                        or intent.evidence not in context.text
                        or any(
                            (
                                intent.negated,
                                intent.deferred,
                                intent.conditional,
                                intent.needs_clarification,
                            )
                        )
                    ):
                        raise ServiceError("INPUT_INVALID", 422)
                    decision = decision.model_copy(update={"action": "confirm_current_summary"})
                elif operation != "save_lead":
                    raise ServiceError("INPUT_INVALID", 422)
                if decision.candidates:
                    await patch_locked_draft(
                        db,
                        row,
                        row.revision,
                        {name: candidate.value for name, candidate in decision.candidates.items()},
                    )
                if operation == "submit_lead":
                    frame = await tool_frame(db, context, command, row)
                    action = command.payload.get("reception", {})
                    source = "button" if action.get("action") == "confirm" else "semantic_direct"
                    if source == "button" and (
                        row.state != "review" or row.summary_version != row.revision
                    ):
                        raise ServiceError("ACTION_STALE", 409)
                    evidence = LeadConfirmationEvidence(
                        source=source,
                        command_id=command.id,
                        run_id=run.id,
                        message_id=command.message_id,
                        user_id=run.user_id,
                        draft_id=row.id,
                        revision=row.revision,
                        agent_id=context.binding.agent_id,
                        summary_version=row.summary_version if source == "button" else None,
                        field_policy_id=row.field_policy_id,
                        interaction_policy_id=command.interaction_policy_id,
                        payload_digest=payload_digest(await prepare_submission_payload(db, row)),
                    )
                    _save_stage(
                        command,
                        context.binding.agent_id,
                        frame=frame.model_dump(mode="json"),
                        decision=decision.model_dump(mode="json"),
                        leadConfirmation=evidence.model_dump(mode="json"),
                    )
                    submission = await confirm_locked_draft(
                        db,
                        row,
                        row.revision,
                        evidence.summary_version,
                        command.message_id,
                        run.user_id,
                        evidence=evidence,
                    )
                    submission_id = submission.id
                    result = ToolResult(
                        operation=operation,
                        status="unknown",
                        revision=row.revision,
                        receipt_id=str(submission_id),
                    )
                else:
                    result = ToolResult(
                        operation=operation,
                        revision=row.revision,
                        receipt_id=key,
                        data={"draft_id": str(row.id), "saved": True},
                    )
        _save_execution(command, execution, key, result)
    if submission_id is not None:
        try:
            receipt = await submit_confirmed(
                context.factory, context.fence, submission_id, context.business_client
            )
            result = result.model_copy(
                update={
                    "status": "ok",
                    "data": {"receipt": receipt.model_dump(mode="json", by_alias=True)},
                }
            )
        except ServiceError as error:
            if error.code != "SUBMISSION_UNKNOWN":
                raise
        async with context.factory.begin() as db:
            _, command, _, _ = await checked_context(db, context, operation)
            execution = DeepExecution.model_validate(command.payload["deepExecution"])
            _save_execution(command, execution, key, result)
    return result


async def _progress(context):
    async with context.factory.begin() as db:
        run, command, _, flow = await checked_context(db, context, "query_progress")
        if run.user_id is None:
            result = ToolResult(operation="query_progress", status="auth", data={"auth": True})
            _record_progress(command, result)
            return result
        actor = actor_from_user(await db.get(User, run.user_id))
        pending = await db.scalar(
            select(ReceptionSubmission).where(
                ReceptionSubmission.draft_id == flow.active_draft_id,
                ReceptionSubmission.status == "pending",
            )
        )
        pending_id = pending.id if pending else None
    if context.business_client is None:
        raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
    if pending_id:
        try:
            await submit_confirmed(
                context.factory, context.fence, pending_id, context.business_client
            )
        except ServiceError as error:
            if error.code != "SUBMISSION_UNKNOWN":
                raise
            result = ToolResult(
                operation="query_progress", status="unknown", receipt_id=str(pending_id)
            )
            async with context.factory.begin() as db:
                _, command, _, _ = await checked_context(db, context, "query_progress")
                _record_progress(command, result)
            return result
    page = await context.business_client.progress(actor, {"cursor": None})
    async with context.factory.begin() as db:
        _, command, _, _ = await checked_context(db, context, "query_progress")
        result = ToolResult(
            operation="query_progress",
            data={"progress": page.model_dump(mode="json", by_alias=True)},
        )
        _record_progress(command, result)
        return result


def _record_progress(command, result):
    execution = DeepExecution.model_validate(command.payload["deepExecution"])
    _save_execution(command, execution, "query_progress", result)


async def _edit_draft(context):
    key = hashlib.sha256(
        f"{context.command_id}:{context.binding.agent_id}:edit".encode()
    ).hexdigest()
    async with context.factory.begin() as db:
        _, command, conversation, flow = await checked_context(db, context, "prepare_lead")
        execution = DeepExecution.model_validate(command.payload["deepExecution"])
        if cached := execution.effects.get(key):
            return ToolResult.model_validate(cached)
        row = await active_draft(db, context, flow)
        if row.state != "accepted":
            raise ServiceError("ACTION_STALE", 409)
        formal = await db.scalar(
            select(ReceptionSubmission)
            .where(ReceptionSubmission.draft_id == row.id, ReceptionSubmission.status == "accepted")
            .order_by(ReceptionSubmission.draft_revision.desc())
            .limit(1)
        )
        application_id = formal.receipt["applicationId"] if formal else row.source_application_id
        if not application_id:
            raise ServiceError("ACTION_STALE", 409)
        candidates = list(
            await db.scalars(
                select(ReceptionDraft).where(
                    ReceptionDraft.conversation_id == conversation.id,
                    ReceptionDraft.source_application_id == application_id,
                    ReceptionDraft.direction == row.direction,
                    ReceptionDraft.state.in_(["collecting", "review"]),
                )
            )
        )
        if len(candidates) > 1:
            return ToolResult(operation="prepare_lead", status="choice", data={"choose": True})
        if candidates:
            flow.active_draft_id = candidates[0].id
            result = ToolResult(
                operation="prepare_lead",
                revision=candidates[0].revision,
                data={"draft_id": str(candidates[0].id), "collection": True},
            )
            _save_execution(command, execution, key, result)
            return result
        request = CreateDraft(
            client_draft_id="deep-edit:" + str(command.id),
            kind="service",
            direction=row.direction,
            source_application_id=application_id,
        )
    view = await create_draft_from_business(
        context.factory,
        context.actor,
        context.conversation_id,
        request,
        context.business_client,
        fence=context.fence,
        field_policy=context.bundle.field_policies[request.direction],
    )
    async with context.factory.begin() as db:
        _, command, _, _ = await checked_context(db, context, "prepare_lead")
        execution = DeepExecution.model_validate(command.payload["deepExecution"])
        result = ToolResult(
            operation="prepare_lead",
            revision=view.revision,
            data={"draft_id": view.id, "collection": True},
        )
        _save_execution(command, execution, key, result)
        return result


def make_business_tools(context: ToolContext) -> list[BaseTool]:
    async def call(operation, draft_id=None, expected_revision=None, **payload):
        try:
            return (
                await execute_effect(
                    context,
                    operation,
                    draft_id=draft_id,
                    expected_revision=expected_revision,
                    payload=payload,
                )
            ).model_dump(mode="json")
        except ServiceError as error:
            return ToolResult(
                operation=operation, status="error", error_code=error.code
            ).model_dump(mode="json")

    @tool
    async def read_lead() -> dict:
        """读取当前授权草稿的已有字段和真实状态。"""
        return await call("read_lead")

    @tool
    async def prepare_lead(
        mode: Literal["resume", "new", "edit"] = "resume",
        draft_id: str | None = None,
        draft_name: str | None = None,
    ) -> dict:
        """接受留资邀请后认证或准备草稿；多份草稿必须先让用户选择。"""
        return await call("prepare_lead", mode=mode, draft_id=draft_id, draft_name=draft_name)

    @tool
    async def compose_form(field_groups: list[list[str]], prompt: str) -> dict:
        """按当前草稿允许的已有字段组合表单，不改变字段要求。"""
        return await call("compose_form", field_groups=field_groups, prompt=prompt)

    @tool
    async def save_lead(
        draft_id: str, expected_revision: int, candidates: dict[str, FieldCandidate]
    ) -> dict:
        """保存本轮用户明确提供且有原文证据的字段，不代表正式提交。"""
        return await call(
            "save_lead",
            draft_id,
            expected_revision,
            candidates={key: value.model_dump() for key, value in candidates.items()},
        )

    @tool
    async def submit_lead(
        draft_id: str,
        expected_revision: int,
        confirmation: SubmitIntent,
        candidates: dict[str, FieldCandidate] | None = None,
    ) -> dict:
        """本轮用户在提交语境中明确同意后提交；必须提供语义确认候选及本轮原文证据。"""
        return await call(
            "submit_lead",
            draft_id,
            expected_revision,
            confirmation=confirmation.model_dump(),
            candidates={key: value.model_dump() for key, value in (candidates or {}).items()},
        )

    @tool
    async def query_progress() -> dict:
        """查询当前草稿真实状态；结果未知的提交沿原请求核对。"""
        return await call("query_progress")

    @tool
    async def request_handoff(
        direction: Literal["buyer", "organization", "supplier"],
        evidence: str,
        explicit_switch: bool = False,
    ) -> dict:
        """用户明确选择或要求切换服务方向时请求交接；临时咨询不切换。"""
        return await call(
            "request_handoff",
            direction=direction,
            evidence=evidence,
            explicit_switch=explicit_switch,
        )

    available = {
        item.name: item
        for item in [
            read_lead,
            prepare_lead,
            compose_form,
            save_lead,
            submit_lead,
            query_progress,
            request_handoff,
        ]
    }
    return [available[name] for name in context.binding.tools]
