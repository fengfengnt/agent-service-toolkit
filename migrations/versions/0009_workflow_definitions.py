"""持久保存工作流集合并区分旧执行与 YAML 执行。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_definitions",
        sa.Column("bundle_id", sa.String(64), primary_key=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        schema="xiaohe",
    )
    op.add_column("runs", sa.Column("workflow_bundle_id", sa.String(64)), schema="xiaohe")
    op.add_column(
        "runs",
        sa.Column("runtime_version", sa.String(), nullable=False, server_default="legacy"),
        schema="xiaohe",
    )
    op.create_foreign_key(
        "fk_runs_workflow_definition",
        "runs",
        "workflow_definitions",
        ["workflow_bundle_id"],
        ["bundle_id"],
        source_schema="xiaohe",
        referent_schema="xiaohe",
    )
    op.create_check_constraint(
        "ck_runs_workflow_runtime",
        "runs",
        "(runtime_version = 'legacy' AND workflow_bundle_id IS NULL) OR "
        "(runtime_version = 'dsl-v1' AND workflow_bundle_id IS NOT NULL)",
        schema="xiaohe",
    )
    op.add_column(
        "reception_drafts", sa.Column("definition_bundle_id", sa.String(64)), schema="xiaohe"
    )
    op.create_foreign_key(
        "fk_reception_drafts_workflow_definition",
        "reception_drafts",
        "workflow_definitions",
        ["definition_bundle_id"],
        ["bundle_id"],
        source_schema="xiaohe",
        referent_schema="xiaohe",
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM xiaohe.workflow_definitions)")):
        raise RuntimeError("不能回退并丢弃已保存的工作流集合")
    op.drop_constraint(
        "fk_reception_drafts_workflow_definition", "reception_drafts", schema="xiaohe"
    )
    op.drop_column("reception_drafts", "definition_bundle_id", schema="xiaohe")
    op.drop_constraint("ck_runs_workflow_runtime", "runs", schema="xiaohe")
    op.drop_constraint("fk_runs_workflow_definition", "runs", schema="xiaohe")
    op.drop_column("runs", "runtime_version", schema="xiaohe")
    op.drop_column("runs", "workflow_bundle_id", schema="xiaohe")
    op.drop_table("workflow_definitions", schema="xiaohe")
