import hashlib
import json

from xiaohe.agents.contracts import AgentFrame, LeadConfirmationEvidence, SemanticDecision
from xiaohe.contracts import ServiceError


def payload_digest(payload) -> str:
    data = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def validate_lead_evidence(evidence, row, command, run, message_id, user_id):
    if not isinstance(evidence, LeadConfirmationEvidence):
        raise ServiceError("INPUT_INVALID", 422)
    expected = (
        command.id,
        run.id,
        message_id,
        user_id,
        row.id,
        row.revision,
        "xiaohe-" + row.direction,
        row.field_policy_id,
        command.interaction_policy_id,
    )
    actual = (
        evidence.command_id,
        evidence.run_id,
        evidence.message_id,
        evidence.user_id,
        evidence.draft_id,
        evidence.revision,
        evidence.agent_id,
        evidence.field_policy_id,
        evidence.interaction_policy_id,
    )
    if actual != expected:
        raise ServiceError("DRAFT_CONFLICT", 409)
    stage = command.payload.get("agentStages", {}).get(evidence.agent_id, {})
    try:
        frame = AgentFrame.model_validate(stage["frame"])
        decision = SemanticDecision.model_validate(stage["decision"])
    except (KeyError, ValueError):
        raise ServiceError("INPUT_INVALID", 422) from None
    if stage.get("leadConfirmation") != evidence.model_dump(mode="json") or (
        frame.command_id,
        frame.message_id,
        frame.draft_id,
        frame.actor.user_id,
        frame.fence.run_id,
        frame.agent_id,
    ) != (command.id, message_id, row.id, user_id, run.id, evidence.agent_id):
        raise ServiceError("DRAFT_CONFLICT", 409)
    if (
        not frame.interaction_policy
        or frame.interaction_policy.policy_id != evidence.interaction_policy_id
    ):
        raise ServiceError("DRAFT_CONFLICT", 409)
    if not frame.field_policy or frame.field_policy.policy_id != evidence.field_policy_id:
        raise ServiceError("DRAFT_CONFLICT", 409)
    if decision.action != "confirm_current_summary" or any(
        (decision.negated, decision.deferred, decision.conditional, decision.needs_clarification)
    ):
        raise ServiceError("INPUT_INVALID", 422)
    if evidence.source != "semantic_direct" and (
        not frame.model_context.summary_visible or frame.summary_version != row.revision
    ):
        raise ServiceError("DRAFT_CONFLICT", 409)
    if evidence.source == "button":
        action = command.payload.get("reception", {})
        if (
            action.get("action"),
            action.get("draftId"),
            action.get("expectedRevision"),
            action.get("summaryVersion"),
        ) != ("confirm", str(row.id), row.revision, row.summary_version):
            raise ServiceError("ACTION_STALE", 409)
