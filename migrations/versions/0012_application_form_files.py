"""表单临时附件独立归属，保留对象定位以回收迟到上传。"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("reception_form_files",
        sa.Column("id", pg.UUID(), primary_key=True),
        sa.Column("system_id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("form_id", pg.UUID(), nullable=False),
        sa.Column("client_upload_id", sa.String(200), nullable=False),
        sa.Column("upload_request", pg.JSONB(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String()),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("object_key", sa.String(), nullable=False, unique=True),
        sa.Column("object_keys", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("detached", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()),
        sa.Column("cleanup_checked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["user_id", "system_id", "tenant_id"],
            ["xiaohe.users.user_id", "xiaohe.users.system_id", "xiaohe.users.tenant_id"]),
        sa.UniqueConstraint("system_id", "tenant_id", "user_id", "form_id", "client_upload_id"),
        sa.CheckConstraint("status IN ('pending','ready','failed')"), schema="xiaohe")


def downgrade():
    op.drop_table("reception_form_files", schema="xiaohe")
