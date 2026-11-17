"""仅在显式指定的、带专用标签的临时 PostgreSQL 容器执行原始交付 SQL。"""

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.pg
SCRIPTS = Path(__file__).resolve().parents[2] / "deploy/xiaohe/migrations/20261109_uat_domains"


@pytest.fixture
def sql():
    container = os.environ.get("XIAOHE_SQL_TEST_CONTAINER", "")
    if not container:
        pytest.skip("需显式指定临时 PostgreSQL 容器")
    assert container.startswith("xiaohe-domain-sql-test-")
    docker = (
        ["wsl.exe", "-d", "Ubuntu", "--cd", "/tmp", "--exec", "docker"]
        if os.name == "nt"
        else ["docker"]
    )
    inspected = subprocess.run(
        [*docker, "inspect", container], capture_output=True, text=True, check=True
    )
    assert json.loads(inspected.stdout)[0]["Config"]["Labels"].get("purpose") == (
        "xiaohe-uat-domain-sql-test"
    )

    def execute(source, *, success=True):
        result = subprocess.run(
            [
                *docker,
                "exec",
                "-i",
                container,
                "psql",
                "-U",
                "postgres",
                "-d",
                "xiaohe",
                "-v",
                "ON_ERROR_STOP=1",
                "-At",
            ],
            input=source,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert (result.returncode == 0) is success, result.stderr
        return result.stdout

    execute("""
        DROP SCHEMA IF EXISTS xiaohe CASCADE;
        CREATE SCHEMA xiaohe;
        CREATE TABLE xiaohe.systems (
          system_id text PRIMARY KEY, tenant_id bigint, portal_client_id text,
          allowed_origin text, issuer text, enabled boolean
        );
        CREATE TABLE xiaohe.users (system_id text, issuer text);
        INSERT INTO xiaohe.systems VALUES
          ('portal',1,'app_localportal','https://portal-uat.yuhezhiyuan.com',
           'https://sso-uat.yuhezhiyuan.com',true),
          ('other',2,'other-client','https://other.example','https://sso.other.example',false);
    """)
    return execute


def script(name):
    return (SCRIPTS / (name + ".sql")).read_text(encoding="utf-8")


def snapshot(sql):
    return sql("SELECT row_to_json(s) FROM xiaohe.systems s ORDER BY system_id;")


def test_apply_verify_rollback_preserves_all_other_columns(sql):
    before = snapshot(sql)
    sql(script("verify"), success=False)
    sql(script("apply"))
    sql(script("verify"))
    records = [json.loads(row) for row in snapshot(sql).splitlines()]
    assert records[0] == json.loads(before.splitlines()[0])
    expected = json.loads(before.splitlines()[1])
    expected.update(
        allowed_origin="https://portal-uat.yunhezhiyuan.com",
        issuer="https://sso-uat.yunhezhiyuan.com",
    )
    assert records[1] == expected
    after = snapshot(sql)
    sql(script("apply"), success=False)
    assert snapshot(sql) == after
    sql(script("rollback"))
    assert snapshot(sql) == before
    sql(script("rollback"), success=False)
    assert snapshot(sql) == before


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE xiaohe.systems SET tenant_id=9 WHERE system_id='portal';",
        "UPDATE xiaohe.systems SET issuer='https://unexpected.example' WHERE system_id='portal';",
        "INSERT INTO xiaohe.users VALUES ('portal','https://sso-uat.yuhezhiyuan.com');",
    ],
)
def test_apply_refuses_drift_or_existing_member_without_changes(sql, change):
    sql(change)
    before = snapshot(sql)
    sql(script("apply"), success=False)
    assert snapshot(sql) == before


def test_rollback_refuses_new_member_without_changes(sql):
    sql(script("apply"))
    sql("INSERT INTO xiaohe.users VALUES ('portal','https://sso-uat.yunhezhiyuan.com');")
    sql(script("verify"))
    before = snapshot(sql)
    sql(script("rollback"), success=False)
    assert snapshot(sql) == before
