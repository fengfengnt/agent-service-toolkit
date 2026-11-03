from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(schema="xiaohe")


class System(Base):
    __tablename__ = "systems"
    __table_args__ = (
        UniqueConstraint("tenant_id"),
        UniqueConstraint("system_id", "tenant_id"),
        CheckConstraint("tenant_id BETWEEN 0 AND 9007199254740991"),
    )

    system_id: Mapped[str] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    portal_client_id: Mapped[str]
    allowed_origin: Mapped[str]
    issuer: Mapped[str]
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        ForeignKeyConstraint(
            ["system_id", "tenant_id"], ["xiaohe.systems.system_id", "xiaohe.systems.tenant_id"]
        ),
        UniqueConstraint("issuer", "sub", "source_tenant_id", name="uq_user_external_identity"),
        UniqueConstraint("user_id", "system_id", "tenant_id"),
        CheckConstraint("user_id NOT LIKE 'guest:%'"),
        CheckConstraint("source_tenant_id BETWEEN 0 AND 9007199254740991"),
    )

    user_id: Mapped[str] = mapped_column(primary_key=True)
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    issuer: Mapped[str]
    sub: Mapped[str]
    source_tenant_id: Mapped[int] = mapped_column(BigInteger)
    display_name: Mapped[str] = mapped_column(server_default="")
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["system_id", "tenant_id"], ["xiaohe.systems.system_id", "xiaohe.systems.tenant_id"]
        ),
        ForeignKeyConstraint(
            ["user_id", "system_id", "tenant_id"],
            ["xiaohe.users.user_id", "xiaohe.users.system_id", "xiaohe.users.tenant_id"],
        ),
        UniqueConstraint("guest_id", "system_id", "tenant_id"),
        CheckConstraint("(user_id IS NULL) <> (guest_id IS NULL)"),
        CheckConstraint("message_count >= 0 AND message_limit > 0 AND retention_seconds > 0"),
        CheckConstraint("user_id IS NULL OR expires_at = created_at + interval '8 hours'"),
        CheckConstraint("(user_id IS NULL) = (authenticated_at IS NULL)"),
        CheckConstraint("user_id IS NULL OR toolkit_user_id = user_id"),
        CheckConstraint("guest_id IS NULL OR toolkit_user_id = 'guest:' || guest_id::text"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[str | None]
    guest_id: Mapped[UUID | None]
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    csrf_token: Mapped[str] = mapped_column(String(64))
    toolkit_user_id: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(Integer, server_default="0")
    message_limit: Mapped[int] = mapped_column(Integer, server_default="100")
    retention_seconds: Mapped[int] = mapped_column(Integer, server_default="14400")


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "system_id", "tenant_id"],
            ["xiaohe.users.user_id", "xiaohe.users.system_id", "xiaohe.users.tenant_id"],
        ),
        UniqueConstraint("id", "user_id", "system_id", "tenant_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[str]
    is_default: Mapped[bool] = mapped_column(server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["system_id", "tenant_id"], ["xiaohe.systems.system_id", "xiaohe.systems.tenant_id"]
        ),
        ForeignKeyConstraint(
            ["user_id", "system_id", "tenant_id"],
            ["xiaohe.users.user_id", "xiaohe.users.system_id", "xiaohe.users.tenant_id"],
        ),
        ForeignKeyConstraint(
            ["guest_id", "system_id", "tenant_id"],
            ["xiaohe.sessions.guest_id", "xiaohe.sessions.system_id", "xiaohe.sessions.tenant_id"],
        ),
        ForeignKeyConstraint(
            ["project_id", "user_id", "system_id", "tenant_id"],
            [
                "xiaohe.projects.id",
                "xiaohe.projects.user_id",
                "xiaohe.projects.system_id",
                "xiaohe.projects.tenant_id",
            ],
        ),
        UniqueConstraint("id", "system_id", "tenant_id"),
        CheckConstraint("(user_id IS NULL) <> (guest_id IS NULL)"),
        CheckConstraint("(user_id IS NULL) = (project_id IS NULL)"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[str | None]
    guest_id: Mapped[UUID | None]
    project_id: Mapped[UUID | None]
    purpose: Mapped[str] = mapped_column(server_default="portal")
    thread_id: Mapped[str] = mapped_column(unique=True, default=lambda: str(uuid4()))
    direction: Mapped[str] = mapped_column(server_default="unknown")
    ownership_epoch: Mapped[int] = mapped_column(server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.conversations.id",
                "xiaohe.conversations.system_id",
                "xiaohe.conversations.tenant_id",
            ],
        ),
        CheckConstraint("role IN ('user', 'assistant')"),
        UniqueConstraint("conversation_id", "client_message_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID]
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str]
    parts: Mapped[list[dict]] = mapped_column(JSONB)
    client_message_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


Index(
    "uq_default_project",
    Project.system_id,
    Project.user_id,
    unique=True,
    postgresql_where=Project.is_default.is_(True),
)
Index(
    "uq_user_portal_conversation",
    Conversation.system_id,
    Conversation.user_id,
    unique=True,
    postgresql_where=(Conversation.user_id.is_not(None) & (Conversation.purpose == "portal")),
)
Index(
    "uq_guest_conversation",
    Conversation.guest_id,
    unique=True,
    postgresql_where=Conversation.guest_id.is_not(None),
)
Index(
    "uq_guest_toolkit_subject",
    Session.toolkit_user_id,
    unique=True,
    postgresql_where=Session.guest_id.is_not(None),
)
Index("ix_message_history", Message.conversation_id, Message.created_at, Message.id)
