"""真实语义评测专用：虚构受信身份、独占测试库、无网络业务替代。"""

import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select

from xiaohe.admission import accept_agent_message, accept_message
from xiaohe.agents.runtime import load_agent_registry, run_agent_turn
from xiaohe.contracts import Actor, MessageInput, ServiceError, TrustedSystem, VerifiedCompletion
from xiaohe.conversations import default_conversation
from xiaohe.identity import create_user_session
from xiaohe.models import Command, Conversation, Message, Run, System
from xiaohe.reception.contracts import (
    ApplicationView,
    CreateDraft,
    IntentReceipt,
    ProgressPage,
    SubmissionResult,
    SubmitReceipt,
)
from xiaohe.reception.fields import PUBLIC_FIELDS
from xiaohe.reception.models import ReceptionDraft, ReceptionFlow, ReceptionSubmission
from xiaohe.reception.store import create_draft, locked_flow, patch_draft
from xiaohe.runs import acquire_run
from xiaohe.workflows.definitions import RESOURCE_ROOT

ORGANIZATION_FIELDS = {
    "organizationName": "虚构星桥协会",
    "memberSize": "100—500",
    "coverage": "本省",
    "onlineChannels": ["微信群"],
    "contactName": "虚构联系人",
}


class IsolatedBackend:
    def __init__(self):
        self.receipts, self.intents = {}, []

    async def result(self, actor, key):
        return SubmissionResult(
            status="accepted" if key in self.receipts else "not_found",
            receipt=self.receipts.get(key),
        )

    async def submit(self, actor, payload):
        value = SubmitReceipt(
            submission_key=payload.submission_key,
            kind="service",
            application_id="fixture-" + payload.submission_key,
            revision=1,
            status="accepted",
        )
        self.receipts[payload.submission_key] = value
        return value

    async def onboarding(self, actor, payload):
        value = SubmitReceipt(
            submission_key=payload.submission_key,
            kind="onboarding",
            application_id=payload.application_id,
            onboarding_id="fixture-onboarding",
            revision=1,
            status="accepted",
        )
        self.receipts[payload.submission_key] = value
        return value

    async def application(self, actor, application_id):
        return ApplicationView(
            id=application_id,
            subject_id="fixture-organization",
            direction="organization",
            revision=1,
            public_fields={
                key: value
                for key, value in ORGANIZATION_FIELDS.items()
                if key in PUBLIC_FIELDS["organization"]
            },
            request_fields={"contactName": ORGANIZATION_FIELDS["contactName"]},
            files=[],
        )

    async def intent(self, actor, request):
        self.intents.append(request)
        return IntentReceipt(
            id="fixture-intent", application_id=request.application_id, status="recorded"
        )

    async def progress(self, actor, query):
        return ProgressPage(groups=[], next_cursor=None)


async def build_runner(factory, model):
    backend = IsolatedBackend()
    registry = await load_agent_registry(factory, RESOURCE_ROOT, model, backend)
    suffix = uuid4().hex[:12]
    trusted = TrustedSystem(
        system_id="semantic-eval-" + suffix,
        tenant_id=int(suffix, 16),
        portal_client_id="fixture-" + suffix,
        allowed_origin="https://semantic-eval.invalid",
    )
    async with factory.begin() as db:
        db.add(System(**trusted.model_dump(), issuer="https://identity.semantic-eval.invalid"))

    async def runner(case, budget):
        started = time.perf_counter()
        initial_calls = budget.calls
        initial_intents = len(backend.intents)
        direction = case.agent_id.removeprefix("xiaohe-")
        if direction == "reception":
            direction = "unknown"
        async with factory.begin() as db:
            record, _ = await create_user_session(
                db,
                trusted,
                VerifiedCompletion(
                    issuer="https://identity.semantic-eval.invalid",
                    sub=suffix + ":" + case.id,
                    source_tenant_id=1,
                    authenticated_at=datetime.now(UTC),
                    portal_client_id=trusted.portal_client_id,
                    transaction_id=uuid4().hex,
                ),
            )
            actor = Actor(
                system=trusted, session_id=record.id, user_id=record.user_id, guest_id=None
            )
            conversation = await default_conversation(db, actor)
            conversation.direction = direction
            flow = await locked_flow(db, conversation)
            flow.invitation_state = case.invitation_state
            draft_id, before = None, {}
            if case.with_draft:
                view = await create_draft(
                    db,
                    actor,
                    conversation.id,
                    CreateDraft(client_draft_id=case.id, kind="service", direction=direction),
                    definition_bundle_id=registry.bundle.bundle_id,
                )
                if case.initial_fields:
                    view = await patch_draft(db, actor, view.id, view.revision, case.initial_fields)
                row = await db.get(ReceptionDraft, UUID(view.id))
                if case.summary_visible:
                    row.step, row.state, row.summary_version = "summary", "review", row.revision
                if case.source_application:
                    row.source_application_id, row.state = "fixture-application", "accepted"
                draft_id, before = row.id, dict(row.fields)
            for turn in case.recent_turns:
                db.add(
                    Message(
                        conversation_id=conversation.id,
                        system_id=trusted.system_id,
                        tenant_id=trusted.tenant_id,
                        role=turn.role,
                        parts=[{"type": "text", "text": turn.text}],
                    )
                )
            await db.flush()
            if direction == "unknown":
                accepted = await accept_message(db, actor, conversation.id, case.id, case.text)
            else:
                accepted = await accept_agent_message(
                    db,
                    actor,
                    case.agent_id,
                    conversation.id,
                    MessageInput(client_message_id=case.id, text=case.text),
                )
        async with factory.begin() as db:
            fence = await acquire_run(db, UUID(accepted.run.id))
        failures, outcome, recovered = [], None, []
        for attempt in range(2):
            try:
                outcome = await run_agent_turn(
                    factory,
                    fence,
                    bundle=registry.bundle,
                    model=model,
                    business_client=backend,
                    registry=registry,
                )
                break
            except Exception as error:
                if (
                    isinstance(error, ServiceError)
                    and error.code == "UPSTREAM_UNAVAILABLE"
                    and error.retryable
                    and not budget.exhausted
                    and attempt == 0
                ):
                    recovered.append(error.code)
                    continue
                failures.append(getattr(error, "code", type(error).__name__))
                break
        async with factory.begin() as db:
            command = await db.scalar(select(Command).where(Command.run_id == fence.run_id))
            stage = command.payload.get("agentStages", {}).get(case.agent_id, {})
            decision = stage.get("decision", {})
            effective_event = stage.get("effective_event")
            current = await db.get(Conversation, conversation.id)
            row = await db.get(ReceptionDraft, draft_id) if draft_id else None
            after = row.fields if row else {}
            submissions = (
                await db.scalars(
                    select(ReceptionSubmission).where(
                        ReceptionSubmission.conversation_id == conversation.id
                    )
                )
            ).all()
            onboardings = (
                await db.scalars(
                    select(ReceptionDraft).where(
                        ReceptionDraft.conversation_id == conversation.id,
                        ReceptionDraft.kind == "onboarding",
                    )
                )
            ).all()
            effects = {
                "direction": current.direction,
                "submission_count": len(submissions),
                "accepted_submissions": sum(item.status == "accepted" for item in submissions),
                "intent_count": len(backend.intents) - initial_intents,
                "onboarding_drafts": len(onboardings),
                "fields": after,
                "calls": budget.calls - initial_calls,
                "recovered_errors": recovered,
                "effective_event": effective_event,
                "invitation_state": (await db.get(ReceptionFlow, conversation.id)).invitation_state,
                "answer_bases": [
                    value["answer"]["basis"]
                    for value in command.payload.get("agentStages", {}).values()
                    if value.get("answer")
                ],
                "professional_answer": any(
                    value.get("answer")
                    for key, value in command.payload.get("agentStages", {}).items()
                    if key != "xiaohe-reception"
                ),
            }
            if effective_event not in case.expected_actions:
                failures.append("unexpected semantic action")
            if effective_event in case.forbidden_actions:
                failures.append("forbidden semantic action")
            if set(case.expected_topics) - set(decision.get("topics", [])):
                failures.append("expected topic absent")
            if case.expected_target and decision.get("target_direction") != case.expected_target:
                failures.append("unexpected semantic target")
            if case.expected_direction and current.direction != case.expected_direction:
                failures.append("unexpected conversation direction")
            if any(decision.get(key) != value for key, value in case.required_flags.items()):
                failures.append("unexpected semantic flag")
            if bool(submissions) != case.expect_submission or any(
                item.status != "accepted" for item in submissions
            ):
                failures.append("unexpected submission effect")
            if bool(effects["intent_count"]) != case.expect_intent:
                failures.append("unexpected cooperation intent effect")
            if bool(onboardings) != case.expect_onboarding:
                failures.append("unexpected onboarding effect")
            if case.expect_professional_answer and not effects["professional_answer"]:
                failures.append("professional answer missing after handoff")
            if (
                case.expected_invitation_state
                and effects["invitation_state"] != case.expected_invitation_state
            ):
                failures.append("unexpected invitation mutation")
            if case.forbid_field_changes and after != before:
                failures.append("forbidden field mutation")
            if any(after.get(key) != value for key, value in case.expected_fields.items()):
                failures.append("expected field update absent")
            run = await db.get(Run, fence.run_id)
            run.status, run.holds_slot = ("failed" if failures else "completed"), False
            command.completed_at = func.clock_timestamp()
        return {
            "id": case.id,
            "status": "not_run" if budget.exhausted else "evaluated",
            "passed": not failures,
            "failures": failures,
            "decision": decision,
            "outcome": outcome.model_dump(mode="json", by_alias=True) if outcome else None,
            "effects": effects,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }

    return runner, registry.bundle
