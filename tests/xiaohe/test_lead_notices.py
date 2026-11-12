import importlib

import pytest
from native_support import skills


def renderer():
    assert importlib.util.find_spec("xiaohe.lead_skills.notices") is not None, (
        "critical notice renderer missing"
    )
    return importlib.import_module("xiaohe.lead_skills.notices").render_lead_notice


@pytest.mark.parametrize(
    "notice,kwargs,expected",
    [
        ("prepare", {}, "请在资料面板填写本次采购资料。"),
        (
            "missing",
            {"missing_labels": ["企业名称", "联系电话"]},
            "正式提交还需：企业名称、联系电话。",
        ),
        ("saved", {}, "本次资料已保存，尚未正式提交。"),
        ("submitting", {}, "正在提交本次资料。"),
        ("unknown", {}, "正在核实提交结果，请稍后查询，无需重复提交。"),
        ("accepted", {}, "本次需求已受理，可在“我的进展”查看状态。"),
        ("failed", {"public_guidance": "请核对资料。"}, "本次提交未完成。请核对资料。"),
    ],
)
def test_critical_notice_uses_real_state(notice, kwargs, expected):
    assert renderer()(skills().interaction_policy, notice, direction="buyer", **kwargs) == expected


def test_notice_rejects_unknown_category_and_fake_missing():
    render = renderer()
    for kind in ("approved", "paid", "missing"):
        with pytest.raises(ValueError):
            render(skills().interaction_policy, kind, direction="buyer")
