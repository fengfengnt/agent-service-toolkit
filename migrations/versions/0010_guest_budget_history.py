"""匿名预算与 V2 历史结构；不推测或批量承接存量身份。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "guest_budgets",
        sa.Column("id", pg.UUID(), primary_key=True),
        sa.Column("system_id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("message_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("message_limit", sa.BigInteger(), nullable=False, server_default="100"),
        sa.Column("current_session_id", pg.UUID()),
        sa.ForeignKeyConstraint(
            ["system_id", "tenant_id"], ["xiaohe.systems.system_id", "xiaohe.systems.tenant_id"]
        ),
        sa.UniqueConstraint("id", "system_id", "tenant_id"),
        sa.CheckConstraint(
            "message_count BETWEEN 0 AND message_limit AND message_limit BETWEEN 1 AND 9007199254740991"
        ),
        schema="xiaohe",
    )
    op.add_column("sessions", sa.Column("budget_id", pg.UUID()), schema="xiaohe")
    op.create_foreign_key(
        "fk_session_guest_budget",
        "sessions",
        "guest_budgets",
        ["budget_id", "system_id", "tenant_id"],
        ["id", "system_id", "tenant_id"],
        source_schema="xiaohe",
        referent_schema="xiaohe",
    )
    op.create_foreign_key(
        "fk_guest_budget_current_session",
        "guest_budgets",
        "sessions",
        ["current_session_id", "system_id", "tenant_id"],
        ["id", "system_id", "tenant_id"],
        source_schema="xiaohe",
        referent_schema="xiaohe",
    )
    op.create_check_constraint(
        "ck_session_guest_budget_owner",
        "sessions",
        "budget_id IS NULL OR guest_id IS NOT NULL",
        schema="xiaohe",
    )
    op.create_table(
        "guest_budget_credentials",
        sa.Column("token_digest", sa.String(64), primary_key=True),
        sa.Column("budget_id", pg.UUID(), nullable=False),
        sa.Column("system_id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["budget_id", "system_id", "tenant_id"],
            [
                "xiaohe.guest_budgets.id",
                "xiaohe.guest_budgets.system_id",
                "xiaohe.guest_budgets.tenant_id",
            ],
        ),
        schema="xiaohe",
    )
    op.create_table(
        "guest_budget_receipts",
        sa.Column("budget_id", pg.UUID(), primary_key=True),
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("client_key_digest", sa.String(64), primary_key=True),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("session_id", pg.UUID(), nullable=False),
        sa.Column("system_id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["budget_id", "system_id", "tenant_id"],
            [
                "xiaohe.guest_budgets.id",
                "xiaohe.guest_budgets.system_id",
                "xiaohe.guest_budgets.tenant_id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "system_id", "tenant_id"],
            ["xiaohe.sessions.id", "xiaohe.sessions.system_id", "xiaohe.sessions.tenant_id"],
        ),
        schema="xiaohe",
    )
    for table, column in (("conversations", "history_epoch"), ("messages", "membership_sequence")):
        op.add_column(
            table,
            sa.Column(column, sa.BigInteger(), nullable=False, server_default="0"),
            schema="xiaohe",
        )
        op.create_check_constraint(
            f"ck_{table}_{column}",
            table,
            f"{column} BETWEEN 0 AND 9007199254740991",
            schema="xiaohe",
        )
    op.create_index(
        "ix_message_membership",
        "messages",
        ["conversation_id", "membership_sequence"],
        schema="xiaohe",
    )
    op.add_column("auth_operations", sa.Column("expected_identity", pg.JSONB()), schema="xiaohe")
    op.execute("""
        CREATE FUNCTION xiaohe.guard_guest_budget() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF ROW(NEW.id, NEW.system_id, NEW.tenant_id, NEW.message_limit)
               IS DISTINCT FROM ROW(OLD.id, OLD.system_id, OLD.tenant_id, OLD.message_limit)
             OR NEW.message_count < OLD.message_count
          THEN RAISE EXCEPTION 'immutable guest budget or decreasing count' USING ERRCODE = '23514'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER guard_guest_budget BEFORE UPDATE ON xiaohe.guest_budgets
          FOR EACH ROW EXECUTE FUNCTION xiaohe.guard_guest_budget();
        CREATE FUNCTION xiaohe.guard_session_budget() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.budget_id IS NOT NULL AND NEW.budget_id IS DISTINCT FROM OLD.budget_id
          THEN RAISE EXCEPTION 'immutable guest budget binding' USING ERRCODE = '23514'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER guard_session_budget BEFORE UPDATE ON xiaohe.sessions
          FOR EACH ROW EXECUTE FUNCTION xiaohe.guard_session_budget();
    """)


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM xiaohe.guest_budgets)")):
        raise RuntimeError("预算启用后不能回退为独立 Session 额度")
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM xiaohe.auth_operations WHERE expected_identity IS NOT NULL)"
        )
    ):
        raise RuntimeError("不能删除已冻结的身份恢复约束")
    op.execute("""
        DROP TRIGGER guard_session_budget ON xiaohe.sessions;
        DROP FUNCTION xiaohe.guard_session_budget();
        DROP TRIGGER guard_guest_budget ON xiaohe.guest_budgets;
        DROP FUNCTION xiaohe.guard_guest_budget();
    """)
    op.drop_column("auth_operations", "expected_identity", schema="xiaohe")
    op.drop_index("ix_message_membership", table_name="messages", schema="xiaohe")
    for table, column in (("messages", "membership_sequence"), ("conversations", "history_epoch")):
        op.drop_constraint(f"ck_{table}_{column}", table, schema="xiaohe")
        op.drop_column(table, column, schema="xiaohe")
    op.drop_table("guest_budget_receipts", schema="xiaohe")
    op.drop_table("guest_budget_credentials", schema="xiaohe")
    op.drop_constraint("fk_guest_budget_current_session", "guest_budgets", schema="xiaohe")
    op.drop_constraint("fk_session_guest_budget", "sessions", schema="xiaohe")
    op.drop_constraint("ck_session_guest_budget_owner", "sessions", schema="xiaohe")
    op.drop_column("sessions", "budget_id", schema="xiaohe")
    op.drop_table("guest_budgets", schema="xiaohe")
