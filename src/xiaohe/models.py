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
        UniqueConstraint("id", "system_id", "tenant_id", name="uq_sessions_id_scope"),
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
    merge_operation_id: Mapped[UUID | None]
    cleanup_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    event_sequence: Mapped[int] = mapped_column(BigInteger, server_default="0")
    merge_pending: Mapped[bool] = mapped_column(server_default=text("false"))
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
        UniqueConstraint(
            "id", "conversation_id", "system_id", "tenant_id", name="uq_messages_id_scope"
        ),
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


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.conversations.id",
                "xiaohe.conversations.system_id",
                "xiaohe.conversations.tenant_id",
            ],
        ),
        ForeignKeyConstraint(
            ["session_id", "system_id", "tenant_id"],
            ["xiaohe.sessions.id", "xiaohe.sessions.system_id", "xiaohe.sessions.tenant_id"],
        ),
        ForeignKeyConstraint(
            ["initial_message_id", "conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.messages.id",
                "xiaohe.messages.conversation_id",
                "xiaohe.messages.system_id",
                "xiaohe.messages.tenant_id",
            ],
            deferrable=True,
            initially="IMMEDIATE",
        ),
        UniqueConstraint(
            "id", "conversation_id", "system_id", "tenant_id", name="uq_runs_id_scope"
        ),
        UniqueConstraint("id", "system_id", "tenant_id", name="uq_runs_id_tenant"),
        CheckConstraint(
            "status IN ('queued','running','waiting_input','completed','failed','cancelled','expired')"
        ),
        CheckConstraint("NOT holds_slot OR status IN ('queued','running','waiting_input')"),
        CheckConstraint("status NOT IN ('queued','running') OR holds_slot"),
        CheckConstraint("attempt >= 0 AND ownership_epoch >= 0"),
        CheckConstraint("(user_id IS NULL) <> (guest_id IS NULL)", name="ck_run_owner"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID]
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    session_id: Mapped[UUID]
    user_id: Mapped[str | None]
    guest_id: Mapped[UUID | None]
    execution_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    thread_id: Mapped[str | None]
    outcome: Mapped[dict | None] = mapped_column(JSONB)
    initial_message_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(server_default="queued")
    holds_slot: Mapped[bool] = mapped_column(server_default=text("true"))
    attempt: Mapped[int] = mapped_column(server_default="0")
    ownership_epoch: Mapped[int] = mapped_column(server_default="0")
    pending_request: Mapped[dict | None] = mapped_column(JSONB)
    job_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class Command(Base):
    __tablename__ = "commands"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.conversations.id",
                "xiaohe.conversations.system_id",
                "xiaohe.conversations.tenant_id",
            ],
        ),
        ForeignKeyConstraint(
            ["message_id", "conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.messages.id",
                "xiaohe.messages.conversation_id",
                "xiaohe.messages.system_id",
                "xiaohe.messages.tenant_id",
            ],
            deferrable=True,
            initially="IMMEDIATE",
        ),
        ForeignKeyConstraint(
            ["run_id", "conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.runs.id",
                "xiaohe.runs.conversation_id",
                "xiaohe.runs.system_id",
                "xiaohe.runs.tenant_id",
            ],
            deferrable=True,
            initially="IMMEDIATE",
        ),
        UniqueConstraint("conversation_id", "kind", "client_key", name="uq_command_idempotency"),
        CheckConstraint("kind IN ('message','action')"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID]
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str]
    client_key: Mapped[str] = mapped_column(String(128))
    payload_digest: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    question: Mapped[dict | None] = mapped_column(JSONB)
    message_id: Mapped[UUID]
    run_id: Mapped[UUID]
    request_id: Mapped[str | None] = mapped_column(String(128))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class RunAttempt(Base):
    __tablename__ = "run_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.runs.id",
                "xiaohe.runs.conversation_id",
                "xiaohe.runs.system_id",
                "xiaohe.runs.tenant_id",
            ],
            deferrable=True,
            initially="IMMEDIATE",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    attempt: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[UUID]
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[str]
    graph_run_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    status: Mapped[str] = mapped_column(server_default="running")
    chunk_receipts: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    graph_stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.conversations.id",
                "xiaohe.conversations.system_id",
                "xiaohe.conversations.tenant_id",
            ],
        ),
        UniqueConstraint("id", "system_id", "tenant_id", name="uq_artifacts_id_tenant"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID]
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    creation_key: Mapped[str] = mapped_column(String(64), unique=True)
    latest_version: Mapped[int] = mapped_column(server_default="0")


class ArtifactVersion(Base):
    __tablename__ = "artifact_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["artifact_id", "system_id", "tenant_id"],
            ["xiaohe.artifacts.id", "xiaohe.artifacts.system_id", "xiaohe.artifacts.tenant_id"],
        ),
        ForeignKeyConstraint(
            ["run_id", "system_id", "tenant_id"],
            ["xiaohe.runs.id", "xiaohe.runs.system_id", "xiaohe.runs.tenant_id"],
        ),
        UniqueConstraint("artifact_id", "version", name="uq_artifact_version"),
        UniqueConstraint(
            "artifact_id", "run_id", "content_digest", name="uq_artifact_content_retry"
        ),
        CheckConstraint("version > 0"),
        CheckConstraint("status IN ('pending','ready','failed')"),
        CheckConstraint(
            "status <> 'ready' OR (uploaded_at IS NOT NULL AND verified_digest IS NOT NULL AND verified_digest=content_digest AND bytes_size IS NOT NULL AND bytes_size >= 0 AND NOT cleanup_pending)"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    artifact_id: Mapped[UUID]
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    version: Mapped[int]
    run_id: Mapped[UUID]
    producer_attempt: Mapped[int]
    producer_epoch: Mapped[int]
    title: Mapped[str] = mapped_column(String(256))
    media_type: Mapped[str] = mapped_column(String(128))
    content_digest: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(unique=True, default=lambda: "xiaohe/" + uuid4().hex)
    status: Mapped[str] = mapped_column(server_default="pending")
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_digest: Mapped[str | None] = mapped_column(String(64))
    bytes_size: Mapped[int | None] = mapped_column(BigInteger)
    preview: Mapped[dict] = mapped_column(
        JSONB, server_default=text('\'{"format":"download_only"}\'::jsonb')
    )
    cleanup_pending: Mapped[bool] = mapped_column(server_default=text("true"))


class GuestObjectTombstone(Base):
    __tablename__ = "guest_object_tombstones"
    __table_args__ = (
        ForeignKeyConstraint(
            ["guest_id", "system_id", "tenant_id"],
            ["xiaohe.sessions.guest_id", "xiaohe.sessions.system_id", "xiaohe.sessions.tenant_id"],
        ),
    )
    object_key: Mapped[str] = mapped_column(primary_key=True)
    guest_id: Mapped[UUID]
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventTombstone(Base):
    __tablename__ = "event_tombstones"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.conversations.id",
                "xiaohe.conversations.system_id",
                "xiaohe.conversations.tenant_id",
            ],
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    conversation_id: Mapped[UUID]
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    sequence: Mapped[int] = mapped_column(BigInteger)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.conversations.id",
                "xiaohe.conversations.system_id",
                "xiaohe.conversations.tenant_id",
            ],
        ),
        ForeignKeyConstraint(
            ["run_id", "conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.runs.id",
                "xiaohe.runs.conversation_id",
                "xiaohe.runs.system_id",
                "xiaohe.runs.tenant_id",
            ],
        ),
        UniqueConstraint("conversation_id", "sequence", name="uq_event_sequence"),
        CheckConstraint("sequence > 0"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID]
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    sequence: Mapped[int] = mapped_column(BigInteger)
    run_id: Mapped[UUID | None]
    event_type: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class AuthContextRecord(Base):
    __tablename__ = "auth_contexts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["system_id", "tenant_id"], ["xiaohe.systems.system_id", "xiaohe.systems.tenant_id"]
        ),
        UniqueConstraint("id", "system_id", "tenant_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    origin: Mapped[str]
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuthOperationRecord(Base):
    __tablename__ = "auth_operations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["context_id", "system_id", "tenant_id"],
            [
                "xiaohe.auth_contexts.id",
                "xiaohe.auth_contexts.system_id",
                "xiaohe.auth_contexts.tenant_id",
            ],
        ),
        ForeignKeyConstraint(
            ["source_session_id", "system_id", "tenant_id"],
            ["xiaohe.sessions.id", "xiaohe.sessions.system_id", "xiaohe.sessions.tenant_id"],
        ),
        ForeignKeyConstraint(
            ["bound_user_id", "system_id", "tenant_id"],
            ["xiaohe.users.user_id", "xiaohe.users.system_id", "xiaohe.users.tenant_id"],
        ),
        UniqueConstraint("context_id", "request_digest"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    context_id: Mapped[UUID]
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    source_session_id: Mapped[UUID | None]
    intent: Mapped[str]
    oidc: Mapped[dict] = mapped_column(JSONB)
    browser_challenge: Mapped[str]
    request_digest: Mapped[str] = mapped_column(String(64))
    client_ip: Mapped[str] = mapped_column(String(45))
    user_agent: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stage: Mapped[str] = mapped_column(server_default="PHONE_REQUIRED")
    center_transaction_id: Mapped[str | None] = mapped_column(unique=True)
    center_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sms_key: Mapped[str | None]
    sms_request_digest: Mapped[str | None] = mapped_column(String(64))
    sms_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sms_result: Mapped[dict | None] = mapped_column(JSONB)
    retired_sms_digests: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    verified_completion: Mapped[dict | None] = mapped_column(JSONB)
    bound_user_id: Mapped[str | None]
    authenticated_session_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class GuestClaim(Base):
    __tablename__ = "guest_claims"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "system_id", "tenant_id"],
            ["xiaohe.users.user_id", "xiaohe.users.system_id", "xiaohe.users.tenant_id"],
        ),
        ForeignKeyConstraint(
            ["guest_id", "system_id", "tenant_id"],
            ["xiaohe.sessions.guest_id", "xiaohe.sessions.system_id", "xiaohe.sessions.tenant_id"],
        ),
        ForeignKeyConstraint(
            ["source_conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.conversations.id",
                "xiaohe.conversations.system_id",
                "xiaohe.conversations.tenant_id",
            ],
        ),
        ForeignKeyConstraint(
            ["target_conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.conversations.id",
                "xiaohe.conversations.system_id",
                "xiaohe.conversations.tenant_id",
            ],
        ),
        ForeignKeyConstraint(["operation_id"], ["xiaohe.auth_operations.id"]),
        ForeignKeyConstraint(
            ["target_session_id", "system_id", "tenant_id"],
            ["xiaohe.sessions.id", "xiaohe.sessions.system_id", "xiaohe.sessions.tenant_id"],
        ),
        UniqueConstraint("guest_id"),
        UniqueConstraint("operation_id"),
        CheckConstraint("status IN ('pending','merged','skipped_expired')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    operation_id: Mapped[UUID]
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    guest_id: Mapped[UUID]
    user_id: Mapped[str]
    source_conversation_id: Mapped[UUID]
    target_conversation_id: Mapped[UUID]
    target_session_id: Mapped[UUID]
    source_run_id: Mapped[UUID | None]
    continuation_run_id: Mapped[UUID | None]
    source_question: Mapped[dict | None] = mapped_column(JSONB)
    source_direction: Mapped[str]
    status: Mapped[str]
    job_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


Index(
    "uq_run_write_slot", Run.conversation_id, unique=True, postgresql_where=Run.holds_slot.is_(True)
)
Index(
    "uq_consumed_question",
    Command.run_id,
    Command.request_id,
    unique=True,
    postgresql_where=Command.request_id.is_not(None),
)
Index(
    "uq_inflight_command",
    Command.run_id,
    unique=True,
    postgresql_where=Command.completed_at.is_(None),
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
