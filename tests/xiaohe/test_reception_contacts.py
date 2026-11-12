import pytest
from pydantic import ValidationError

from xiaohe.admission import _safe_text
from xiaohe.contracts import ServiceError
from xiaohe.graph import _safe_model_text
from xiaohe.reception.contracts import ApplicationView, Submission
from xiaohe.reception.fields import missing_required, validate_fields
from xiaohe.reception.forms import form_definition


def test_explicit_business_phone_is_allowed_but_authentication_secrets_are_blocked():
    message = "我要更新电话号码是13912345678"
    _safe_text(message)
    assert _safe_model_text(message) == message
    for value in (
        "联系电话：+86 13912345678",
        "联系电话：张经理 13912345678",
        "把联系电话改成13912345678",
    ):
        _safe_text(value)
    for secret in (
        "登录手机号：13912345678",
        "13912345678",
        message + "，验证码：123456",
        message + "，token=secret",
    ):
        with pytest.raises(ServiceError, match="SENSITIVE_INPUT_REQUIRES_CARD"):
            _safe_text(secret)


@pytest.mark.parametrize("direction", ["buyer", "supplier", "organization"])
def test_contact_and_free_text_phone_are_required_for_each_direction(direction):
    definition = form_definition(direction)[1]
    fields = (
        dict(
            organizationName="测试协会",
            memberSize="100—500",
            coverage="本市",
            onlineChannels=["暂无"],
        )
        if direction == "organization"
        else {"companyName": "测试公司"}
    )
    assert missing_required(direction, fields, policy=definition) == [
        "contactName",
        "contactPhone",
    ]
    for phone in ("13912345678", "010-12345678 转 9", "+44 (0)20 1234 5678", "请联系分机总台"):
        value = fields | {"contactName": "张三", "contactPhone": phone}
        assert validate_fields(direction, value)["contactPhone"] == phone
        assert missing_required(direction, value, policy=definition) == []
    assert missing_required(
        direction, fields | {"contactName": "张三", "contactPhone": "  "}, policy=definition
    ) == ["contactPhone"]


@pytest.mark.parametrize(
    "direction,foreign",
    [("buyer", "capacity"), ("supplier", "annualPurchase"), ("organization", "companyName")],
)
def test_contact_extension_does_not_merge_agent_specific_fields(direction, foreign):
    with pytest.raises(ServiceError, match="INPUT_INVALID"):
        validate_fields(direction, {foreign: "不应写入"})


@pytest.mark.parametrize("key", ["contactName", "contactPhone"])
@pytest.mark.parametrize("value", [12345678, ["13912345678"]])
def test_contact_details_are_strings_not_numbers_or_lists(key, value):
    with pytest.raises(ServiceError, match="INPUT_INVALID"):
        validate_fields("buyer", {key: value})


def test_historical_application_is_readable_but_new_submission_requires_contacts():
    source = ApplicationView(
        id="old",
        subject_id="company",
        direction="buyer",
        revision=1,
        public_fields={"companyName": "历史公司"},
        request_fields={},
        files=[],
    )
    assert source.request_fields == {}
    with pytest.raises(ValidationError):
        Submission(
            submission_key="new",
            draft_id="draft",
            draft_revision=1,
            operation="create",
            subject_kind="company",
            direction="buyer",
            public_fields=source.public_fields,
            request_fields={},
            files=[],
        )


def test_persisted_submission_can_be_reconciled_without_lowering_new_submission_floor():
    payload = dict(
        submissionKey="old",
        draftId="draft",
        draftRevision=1,
        operation="create",
        subjectKind="company",
        direction="buyer",
        publicFields={"companyName": "历史公司"},
        requestFields={},
        files=[],
    )
    assert (
        Submission.model_validate(payload, context={"persisted_submission": True}).submission_key
        == "old"
    )
    with pytest.raises(ValidationError):
        Submission.model_validate(payload)
