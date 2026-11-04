"""guest_cleanup"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("cleanup_checked_at", "cleanup_stopped_at", "purged_at"):
        op.add_column("sessions", sa.Column(name, sa.DateTime(timezone=True)), schema="xiaohe")


def downgrade():
    for name in ("purged_at", "cleanup_stopped_at", "cleanup_checked_at"):
        op.drop_column("sessions", name, schema="xiaohe")
