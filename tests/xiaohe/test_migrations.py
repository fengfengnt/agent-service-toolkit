import os
import shutil
import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_orm_metadata_resolves_workflow_foreign_keys_without_alembic_imports():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from xiaohe.models import Base; from xiaohe.reception import models; "
            "assert 'xiaohe.workflow_definitions' in Base.metadata.tables; "
            "assert 'xiaohe.deep_runtime_bundles' in Base.metadata.tables; "
            "assert 'deep_runtime_bundle_id' in Base.metadata.tables['xiaohe.runs'].c; "
            "list(Base.metadata.sorted_tables)",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=os.environ | {"PYTHONPATH": str(Path.cwd() / "src")},
    )
    assert result.returncode == 0, result.stderr


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
