import shutil
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_standard_alembic_revision_generation(tmp_path):
    target = tmp_path / "migrations"
    shutil.copytree("migrations", target, ignore=shutil.ignore_patterns("__pycache__"))
    config = Config("alembic.ini")
    config.set_main_option("script_location", str(target))
    before = ScriptDirectory.from_config(config).get_current_head()
    revision = command.revision(config, message="验证后续业务迁移")
    assert revision.down_revision == before
    assert Path(revision.path).parent == target / "versions"
    compile(Path(revision.path).read_text("utf-8"), revision.path, "exec")
