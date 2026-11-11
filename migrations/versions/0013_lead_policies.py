"""独立固定留资字段与交互规则，并保留真实确认来源。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lead_policies",
        sa.Column("policy_id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.CheckConstraint("kind IN ('fields','interaction')"),
        schema="xiaohe",
    )
    for table, column in (
        ("reception_drafts", "field_policy_id"),
        ("commands", "interaction_policy_id"),
    ):
        op.add_column(table, sa.Column(column, sa.String(64)), schema="xiaohe")
        op.create_foreign_key(
            f"fk_{table}_{column}",
            table,
            "lead_policies",
            [column],
            ["policy_id"],
            source_schema="xiaohe",
            referent_schema="xiaohe",
        )
    op.alter_column("reception_submissions", "summary_version", nullable=True, schema="xiaohe")
    op.add_column(
        "reception_submissions", sa.Column("confirmation_evidence", pg.JSONB()), schema="xiaohe"
    )
    op.create_check_constraint(
        "ck_submission_confirmation_source",
        "reception_submissions",
        """
        (confirmation_evidence IS NULL AND summary_version IS NOT NULL) OR
        (confirmation_evidence IS NOT NULL AND COALESCE(
            confirmation_evidence->>'version' = '1' AND (
                (confirmation_evidence->>'source' = 'semantic_direct' AND summary_version IS NULL) OR
                (confirmation_evidence->>'source' IN ('semantic_reviewed','button') AND
                 summary_version = draft_revision AND summary_version IS NOT NULL)
            ), false))
        """,
        schema="xiaohe",
    )


def downgrade():
    bound = (
        op.get_bind()
        .execute(
            sa.text("""SELECT
        EXISTS(SELECT 1 FROM xiaohe.reception_drafts WHERE field_policy_id IS NOT NULL) OR
        EXISTS(SELECT 1 FROM xiaohe.commands WHERE interaction_policy_id IS NOT NULL) OR
        EXISTS(SELECT 1 FROM xiaohe.reception_submissions WHERE confirmation_evidence IS NOT NULL)
        """)
        )
        .scalar_one()
    )
    if bound:
        raise RuntimeError(
            "lead policy bindings or confirmation evidence exist; compatible rollback required"
        )
    op.drop_constraint(
        "ck_submission_confirmation_source", "reception_submissions", schema="xiaohe"
    )
    op.drop_column("reception_submissions", "confirmation_evidence", schema="xiaohe")
    op.alter_column("reception_submissions", "summary_version", nullable=False, schema="xiaohe")
    for table, column in (
        ("commands", "interaction_policy_id"),
        ("reception_drafts", "field_policy_id"),
    ):
        op.drop_constraint(f"fk_{table}_{column}", table, schema="xiaohe")
        op.drop_column(table, column, schema="xiaohe")
    op.drop_table("lead_policies", schema="xiaohe")
