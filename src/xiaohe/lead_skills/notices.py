from xiaohe.lead_skills.models import NOTICE_VARIABLES, InteractionPolicy

ROLE_LABELS = {"buyer": "采购", "organization": "组织机构", "supplier": "供应商"}


def render_lead_notice(
    policy: InteractionPolicy,
    notice: str,
    *,
    direction: str,
    missing_labels: list[str] | None = None,
    public_guidance: str | None = None,
) -> str:
    if notice not in NOTICE_VARIABLES or direction not in ROLE_LABELS:
        raise ValueError("unsupported lead notice")
    if notice == "missing" and not missing_labels:
        raise ValueError("missing notice requires actual missing fields")
    if notice == "failed" and not public_guidance:
        raise ValueError("failed notice requires public guidance")
    return policy.notices[notice].format(
        direction=ROLE_LABELS[direction],
        missing="、".join(missing_labels or []),
        guidance=public_guidance or "",
    )
