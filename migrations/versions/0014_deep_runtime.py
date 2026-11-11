"""保留旧执行器并增加原生技能执行快照。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def runtime_constraint(native):
    old = (
        "(runtime_version = 'legacy' AND workflow_bundle_id IS NULL{null}) OR "
        "(runtime_version = 'dsl-v1' AND workflow_bundle_id IS NOT NULL{null})"
    ).format(null=" AND deep_runtime_bundle_id IS NULL" if native else "")
    return old + (
        " OR (runtime_version = 'deepagent-v1' AND workflow_bundle_id IS NULL "
        "AND deep_runtime_bundle_id IS NOT NULL)"
        if native
        else ""
    )


def upgrade():
    op.create_table(
        "deep_runtime_bundles",
        sa.Column("bundle_id", sa.String(64), primary_key=True),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        schema="xiaohe",
    )
    op.add_column("runs", sa.Column("deep_runtime_bundle_id", sa.String(64)), schema="xiaohe")
    op.create_foreign_key(
        "fk_runs_deep_runtime_bundle",
        "runs",
        "deep_runtime_bundles",
        ["deep_runtime_bundle_id"],
        ["bundle_id"],
        source_schema="xiaohe",
        referent_schema="xiaohe",
    )
    op.drop_constraint("ck_runs_workflow_runtime", "runs", schema="xiaohe")
    op.create_check_constraint(
        "ck_runs_workflow_runtime", "runs", runtime_constraint(True), schema="xiaohe"
    )
    op.add_column("reception_drafts", sa.Column("guidance_progress", pg.JSONB()), schema="xiaohe")


def downgrade():
    exists = (
        op.get_bind()
        .execute(
            sa.text("""SELECT
        EXISTS(SELECT 1 FROM xiaohe.deep_runtime_bundles) OR
        EXISTS(SELECT 1 FROM xiaohe.runs WHERE deep_runtime_bundle_id IS NOT NULL) OR
        EXISTS(SELECT 1 FROM xiaohe.commands WHERE payload ? 'deepExecution') OR
        EXISTS(SELECT 1 FROM xiaohe.reception_drafts WHERE guidance_progress IS NOT NULL)
    """)
        )
        .scalar_one()
    )
    if exists:
        raise RuntimeError("native runtime records exist; compatible rollback required")
    op.drop_column("reception_drafts", "guidance_progress", schema="xiaohe")
    op.drop_constraint("ck_runs_workflow_runtime", "runs", schema="xiaohe")
    op.create_check_constraint(
        "ck_runs_workflow_runtime", "runs", runtime_constraint(False), schema="xiaohe"
    )
    op.drop_constraint("fk_runs_deep_runtime_bundle", "runs", schema="xiaohe")
    op.drop_column("runs", "deep_runtime_bundle_id", schema="xiaohe")
    op.drop_table("deep_runtime_bundles", schema="xiaohe")
