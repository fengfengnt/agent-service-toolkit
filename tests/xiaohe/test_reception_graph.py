import pytest

from xiaohe.contracts import ServiceError
from xiaohe.graph import _auth_intent


@pytest.mark.parametrize(
    "text,expected",
    [
        ("我要登录", "login"),
        ("我想注册", "register"),
        ("请帮我留资", "lead"),
        ("暂时不注册", None),
        ("如何关闭登录提醒", None),
        ("介绍登录功能", None),
    ],
)
def test_only_explicit_user_auth_intent_is_recognized(text, expected):
    assert _auth_intent(text) == expected


def test_fake_or_unconfigured_product_model_is_unavailable(monkeypatch):
    from core.settings import settings
    from schema.models import FakeModelName
    from xiaohe.graph import reception_model

    for value in (None, FakeModelName.FAKE):
        monkeypatch.setattr(settings, "DEFAULT_MODEL", value)
        with pytest.raises(ServiceError):
            reception_model()
