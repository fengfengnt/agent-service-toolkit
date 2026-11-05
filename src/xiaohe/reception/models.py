from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from xiaohe.models import Base


def _conversation_fk():
    return ForeignKeyConstraint(
        ["conversation_id", "system_id", "tenant_id"],
        [
            "xiaohe.conversations.id",
            "xiaohe.conversations.system_id",
            "xiaohe.conversations.tenant_id",
        ],
    )


class ReceptionFlow(Base):
    __tablename__ = "reception_flows"
    __table_args__ = (
        _conversation_fk(),
        CheckConstraint("invitation_state IN ('none','offered','deferred','accepted')"),
    )

    conversation_id: Mapped[UUID] = mapped_column(primary_key=True)
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    invitation_state: Mapped[str] = mapped_column(default="none")
    pending_request_id: Mapped[UUID | None]
    active_draft_id: Mapped[UUID | None]


class ReceptionDraft(Base):
    __tablename__ = "reception_drafts"
    __table_args__ = (
        _conversation_fk(),
        UniqueConstraint("conversation_id", "client_draft_id"),
        UniqueConstraint("id", "conversation_id", "system_id", "tenant_id"),
        CheckConstraint("kind IN ('service','onboarding')"),
        CheckConstraint("direction IN ('buyer','organization','supplier')"),
        CheckConstraint("state IN ('collecting','review','submitting','accepted')"),
        CheckConstraint("step IN ('round1','round2','round3','files','summary')"),
        CheckConstraint("revision BETWEEN 1 AND 9007199254740991"),
        CheckConstraint(
            "kind <> 'onboarding' OR (direction = 'organization' AND source_application_id IS NOT NULL)"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID]
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    client_draft_id: Mapped[str]
    creation_request: Mapped[dict] = mapped_column(JSONB, default=dict)
    kind: Mapped[str]
    direction: Mapped[str]
    subject_id: Mapped[str | None]
    source_application_id: Mapped[str | None]
    source_onboarding_id: Mapped[str | None]
    source_revision: Mapped[int | None] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(BigInteger, default=1)
    step: Mapped[str] = mapped_column(default="round1")
    state: Mapped[str] = mapped_column(default="collecting")
    fields: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_files: Mapped[list] = mapped_column(JSONB, default=list)
    summary_version: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


def _draft_fk():
    return ForeignKeyConstraint(
        ["draft_id", "conversation_id", "system_id", "tenant_id"],
        [
            "xiaohe.reception_drafts.id",
            "xiaohe.reception_drafts.conversation_id",
            "xiaohe.reception_drafts.system_id",
            "xiaohe.reception_drafts.tenant_id",
        ],
    )


class ReceptionSubmission(Base):
    __tablename__ = "reception_submissions"
    __table_args__ = (_draft_fk(), UniqueConstraint("draft_id", "draft_revision"))

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    draft_id: Mapped[UUID]
    conversation_id: Mapped[UUID]
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    draft_revision: Mapped[int] = mapped_column(BigInteger)
    summary_version: Mapped[int] = mapped_column(BigInteger)
    confirmation_message_id: Mapped[UUID]
    submission_key: Mapped[str] = mapped_column(unique=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    receipt: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(default="pending")


class ReceptionFile(Base):
    __tablename__ = "reception_files"
    __table_args__ = (_draft_fk(), CheckConstraint("status IN ('pending','ready','failed')"))

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    draft_id: Mapped[UUID]
    conversation_id: Mapped[UUID]
    system_id: Mapped[str]
    tenant_id: Mapped[int] = mapped_column(BigInteger)
    category: Mapped[str]
    filename: Mapped[str]
    media_type: Mapped[str]
    bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="pending")
    object_key: Mapped[str] = mapped_column(unique=True)
    detached: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
