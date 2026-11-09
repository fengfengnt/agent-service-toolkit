"""独立表单的已确认提交，不持久化未提交输入或创建聊天草稿。"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("reception_form_submissions",
        sa.Column("system_id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("client_submission_id", sa.String(200), primary_key=True),
        sa.Column("form_id", pg.UUID(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("receipt", pg.JSONB()),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()),
        sa.ForeignKeyConstraint(["user_id", "system_id", "tenant_id"],
            ["xiaohe.users.user_id", "xiaohe.users.system_id", "xiaohe.users.tenant_id"]),
        sa.CheckConstraint("status IN ('pending','accepted','failed')"), schema="xiaohe")


def downgrade():
    op.drop_table("reception_form_submissions", schema="xiaohe")
