"""仅保留标准 Skill；旧数据必须由显式维护操作预先清理。"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    remaining = (
        op.get_bind()
        .execute(
            sa.text("""
        SELECT EXISTS(SELECT 1 FROM xiaohe.runs WHERE runtime_version <> 'deepagent-v1')
          OR EXISTS(SELECT 1 FROM xiaohe.reception_drafts WHERE definition_bundle_id IS NOT NULL OR field_policy_id IS NULL)
          OR EXISTS(SELECT 1 FROM xiaohe.workflow_definitions)
          OR EXISTS(SELECT 1 FROM xiaohe.deep_runtime_bundles b,
                    jsonb_each(b.payload->'skills') s WHERE s.value ? 'legacy_id')
    """)
        )
        .scalar_one()
    )
    if remaining:
        raise RuntimeError("standard-only upgrade requires explicit legacy data cleanup")
    op.drop_constraint(
        "fk_reception_drafts_workflow_definition", "reception_drafts", schema="xiaohe"
    )
    op.drop_column("reception_drafts", "definition_bundle_id", schema="xiaohe")
    op.alter_column("reception_drafts", "field_policy_id", nullable=False, schema="xiaohe")
    op.drop_constraint("ck_runs_workflow_runtime", "runs", schema="xiaohe")
    op.drop_constraint("fk_runs_workflow_definition", "runs", schema="xiaohe")
    op.drop_column("runs", "workflow_bundle_id", schema="xiaohe")
    op.drop_column("runs", "runtime_version", schema="xiaohe")
    op.alter_column("runs", "deep_runtime_bundle_id", nullable=False, schema="xiaohe")
    op.drop_table("workflow_definitions", schema="xiaohe")


def downgrade():
    raise RuntimeError("standard-only cleanup is destructive; use a compatible native release")
