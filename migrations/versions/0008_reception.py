"""reception"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reception_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("system_id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("client_draft_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("subject_id", sa.String(), nullable=True),
        sa.Column("source_application_id", sa.String(), nullable=True),
        sa.Column("source_onboarding_id", sa.String(), nullable=True),
        sa.Column("source_revision", sa.BigInteger(), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("step", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary_version", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint("direction IN ('buyer','organization','supplier')"),
        sa.CheckConstraint(
            "kind <> 'onboarding' OR (direction = 'organization' AND source_application_id IS NOT NULL)"
        ),
        sa.CheckConstraint("kind IN ('service','onboarding')"),
        sa.CheckConstraint("state IN ('collecting','review','submitting','accepted')"),
        sa.CheckConstraint("step IN ('round1','round2','round3','files','summary')"),
        sa.CheckConstraint("revision BETWEEN 1 AND 9007199254740991"),
        sa.ForeignKeyConstraint(
            ["conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.conversations.id",
                "xiaohe.conversations.system_id",
                "xiaohe.conversations.tenant_id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "client_draft_id"),
        sa.UniqueConstraint("id", "conversation_id", "system_id", "tenant_id"),
        schema="xiaohe",
    )
    op.create_table(
        "reception_flows",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("system_id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("invitation_state", sa.String(), nullable=False),
        sa.Column("pending_request_id", sa.Uuid(), nullable=True),
        sa.Column("active_draft_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("invitation_state IN ('none','offered','deferred','accepted')"),
        sa.ForeignKeyConstraint(
            ["conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.conversations.id",
                "xiaohe.conversations.system_id",
                "xiaohe.conversations.tenant_id",
            ],
        ),
        sa.PrimaryKeyConstraint("conversation_id"),
        schema="xiaohe",
    )
    op.create_table(
        "reception_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("system_id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("object_key", sa.String(), nullable=False),
        sa.Column("detached", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('pending','ready','failed')"),
        sa.ForeignKeyConstraint(
            ["draft_id", "conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.reception_drafts.id",
                "xiaohe.reception_drafts.conversation_id",
                "xiaohe.reception_drafts.system_id",
                "xiaohe.reception_drafts.tenant_id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
        schema="xiaohe",
    )
    op.create_table(
        "reception_submissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("system_id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_revision", sa.BigInteger(), nullable=False),
        sa.Column("summary_version", sa.BigInteger(), nullable=False),
        sa.Column("confirmation_message_id", sa.Uuid(), nullable=False),
        sa.Column("submission_key", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("receipt", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["draft_id", "conversation_id", "system_id", "tenant_id"],
            [
                "xiaohe.reception_drafts.id",
                "xiaohe.reception_drafts.conversation_id",
                "xiaohe.reception_drafts.system_id",
                "xiaohe.reception_drafts.tenant_id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", "draft_revision"),
        sa.UniqueConstraint("submission_key"),
        schema="xiaohe",
    )


def downgrade():
    op.drop_table("reception_submissions", schema="xiaohe")
    op.drop_table("reception_files", schema="xiaohe")
    op.drop_table("reception_flows", schema="xiaohe")
    op.drop_table("reception_drafts", schema="xiaohe")
