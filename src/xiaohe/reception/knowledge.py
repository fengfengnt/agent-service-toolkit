from collections.abc import Awaitable, Callable

from xiaohe.reception.contracts import KnowledgeHit, KnowledgeRequest

# 适配器只返回已确认可对客的版本化资料；未配置时不发起外部请求。
KnowledgeQuery = Callable[[KnowledgeRequest], Awaitable[list[KnowledgeHit]]]
