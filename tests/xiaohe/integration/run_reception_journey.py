"""创建并清理本次独占测试资源；不部署、不加载已有 .env。Windows/WSL 本地执行。"""

import asyncio
import ipaddress
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import httpx
from botocore.config import Config as S3Config
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
WORKTREES = ROOT.parent
JAVA = WORKTREES / "xiaocai-collaborator"
PORTAL = WORKTREES / "reception-portal" / "frontend"
MAVEN = "C:/Program Files/apache-maven-3.9.16/bin/mvn.cmd"
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def docker(*args):
    result = subprocess.run(
        ["wsl.exe", "-e", "docker", *args], capture_output=True, creationflags=FLAGS
    )
    if result.returncode:
        raise RuntimeError("隔离 Docker 操作失败（未输出私有参数）")
    return result.stdout.decode().strip()


def port(container, target):
    for _ in range(8):
        try:
            value = docker("port", container, str(target) + "/tcp")
        except RuntimeError:
            time.sleep(0.3)
            continue
        if value.startswith("127.0.0.1:"):
            return int(value.rsplit(":", 1)[1])
        time.sleep(0.2)
    state = docker(
        "inspect",
        container,
        "--format",
        "{{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}",
    )
    raise RuntimeError("隔离端口未就绪：" + state)


def ready(check, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (OSError, httpx.HTTPError):
            pass
        time.sleep(0.25)
    raise RuntimeError("隔离服务等待超时")


async def initialize(url, origin):
    from alembic import command
    from alembic.config import Config

    from xiaohe.jobs import build_job_app
    from xiaohe.models import System

    engine = create_engine(url, hide_parameters=True)
    with engine.begin() as db:
        assert db.scalar(text("SELECT current_database()")).startswith("test_xiaohe_reception_")
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["connection"] = db
        command.upgrade(config, "head")
    with Session(engine) as db, db.begin():
        db.add(
            System(
                system_id="portal",
                tenant_id=1,
                portal_client_id="fixture-portal",
                allowed_origin=origin,
                issuer=origin,
            )
        )
    engine.dispose()

    async def never(run_id):
        raise AssertionError("初始化不得执行任务")

    async with build_job_app(url, never).open_async() as app:
        await app.schema_manager.apply_schema_async()


def main():
    if os.environ.get("RECEPTION_REAL_JOURNEY_ENABLED") != "1":
        raise RuntimeError("请显式设置 RECEPTION_REAL_JOURNEY_ENABLED=1")
    assert ROOT.name == "reception-agents" and JAVA.is_dir() and PORTAL.is_dir()
    nonce = secrets.token_hex(5)
    directory = ROOT / ".superpowers/sdd/2026-11-06-xiaohe-independent-agents" / ("j1-" + nonce)
    directory.mkdir(parents=True)
    shutil.copytree(ROOT / "resources/xiaohe", directory / "resources")
    control, secret, file_secret, toolkit_secret = [secrets.token_hex(24) for _ in range(4)]
    pg_password, mysql_password, minio_secret = [secrets.token_hex(24) for _ in range(3)]
    containers = []
    processes = []
    logs = []
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
            ("XIAOHE_", "RECEPTION_", "SSO_", "OPENAI_", "DEEPSEEK_", "OPENROUTER_")
        )
    }
    env.update(
        USE_FAKE_MODEL="true",
        DEFAULT_MODEL="fake",
        RECEPTION_REAL_JOURNEY_ENABLED="1",
        PYTHONPATH=str(ROOT / "src") + os.pathsep + str(ROOT / "tests/xiaohe"),
        COPILOTKIT_TELEMETRY_DISABLED="true",
        DO_NOT_TRACK="1",
    )

    def start(args, cwd, environment, name):
        log = (directory / (name + ".log")).open("w", encoding="utf-8")
        logs.append(log)
        process = subprocess.Popen(
            args,
            cwd=cwd,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=FLAGS,
        )
        processes.append(process)
        return process

    def command(script, cwd, environment, name):
        process = start(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            cwd,
            environment,
            name,
        )
        assert process.wait() == 0, name + "失败，见本次隔离日志"

    def container(kind, image, variables, mount, command=()):
        name = "xiaohe-reception-j1-" + kind + "-" + nonce
        args = [
            "run",
            "--rm",
            "-d",
            "--name",
            name,
            "--label",
            "codex.task=xiaohe-reception-j1",
            "--memory",
            "1g",
            "--tmpfs",
            mount,
            "-p",
            "127.0.0.1::" + str({"pg": 5432, "mysql": 3306, "minio": 9000}[kind]),
        ]
        for key, value in variables.items():
            args.extend(["-e", key + "=" + value])
        value = docker(*args, image, *command)
        assert len(value) == 64
        containers.append(value)
        return value

    api = worker = None
    java = None
    try:
        print("J1：建立独占 PostgreSQL、MySQL、MinIO（tmpfs，无已有卷）", flush=True)
        pg_db = "test_xiaohe_reception_" + nonce
        mysql_db = "test_reception_" + nonce
        pg = container(
            "pg",
            "postgres:17",
            {"POSTGRES_PASSWORD": pg_password, "POSTGRES_DB": pg_db},
            "/var/lib/postgresql/data:rw,size=512m",
        )
        mysql = container(
            "mysql",
            "mysql:8",
            {"MYSQL_ROOT_PASSWORD": mysql_password, "MYSQL_DATABASE": mysql_db},
            "/var/lib/mysql:rw,size=512m",
            ("--default-time-zone=+00:00",),
        )
        lock = json.loads((ROOT / "deploy/xiaohe/images.lock.json").read_text("utf-8"))["minio"]
        minio = container(
            "minio",
            lock["repository"] + "@" + lock["digest"],
            {"MINIO_ROOT_USER": "journey-root", "MINIO_ROOT_PASSWORD": minio_secret},
            "/data:rw,size=128m",
            ("server", "/data", "--console-address", ":9001"),
        )
        pg_port, mysql_port, minio_port = port(pg, 5432), port(mysql, 3306), port(minio, 9000)
        ready(
            lambda: (
                "accepting connections"
                in docker("exec", pg, "pg_isready", "-U", "postgres", "-d", pg_db)
            )
        )

        def mysql_ready():
            value = subprocess.run(
                [
                    "wsl.exe",
                    "-e",
                    "docker",
                    "exec",
                    mysql,
                    "sh",
                    "-c",
                    'mysqladmin ping -h127.0.0.1 -uroot -p"$MYSQL_ROOT_PASSWORD" --silent',
                ],
                capture_output=True,
                creationflags=FLAGS,
            )
            return value.returncode == 0

        ready(mysql_ready)
        minio_origin = f"http://127.0.0.1:{minio_port}"
        ready(
            lambda: (
                httpx.get(minio_origin + "/minio/health/ready", trust_env=False).status_code == 200
            )
        )
        bucket = "test-xiaohe-reception-" + nonce
        boto3.client(
            "s3",
            endpoint_url=minio_origin,
            aws_access_key_id="journey-root",
            aws_secret_access_key=minio_secret,
            region_name="us-east-1",
            config=S3Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        ).create_bucket(Bucket=bucket)
        sockets = []
        for _ in range(3):
            handle = socket.socket()
            handle.bind(("127.0.0.1", 0))
            sockets.append(handle)
        gateway_port, api_port, runtime_port = [handle.getsockname()[1] for handle in sockets]
        for handle in sockets:
            handle.close()
        origin = f"https://127.0.0.1:{gateway_port}"
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "isolated reception journey")])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
            .not_valid_after(datetime.now(UTC) + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                False,
            )
            .sign(key, hashes.SHA256())
        )
        cert_path, key_path = directory / "server.pem", directory / "server.key"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        pg_url = f"postgresql+psycopg://postgres:{pg_password}@127.0.0.1:{pg_port}/{pg_db}"
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(initialize(pg_url, origin))
        java_env = {
            **env,
            "RECEPTION_TEST_MYSQL_URL": f"jdbc:mysql://127.0.0.1:{mysql_port}/{mysql_db}?useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=UTC",
            "RECEPTION_TEST_MYSQL_USERNAME": "root",
            "RECEPTION_TEST_MYSQL_PASSWORD": mysql_password,
            "RECEPTION_JOURNEY_ENABLED": "1",
            "RECEPTION_JOURNEY_SECRET": secret,
            "RECEPTION_JOURNEY_CONTROL": control,
            "RECEPTION_JOURNEY_FILE_SECRET": file_secret,
            "RECEPTION_JOURNEY_ISSUER": origin,
            "RECEPTION_JOURNEY_DIRECTORY": str(directory),
            "RECEPTION_JOURNEY_AI_ORIGIN": f"http://127.0.0.1:{api_port}",
        }
        java = start(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"& '{MAVEN}' -pl yudao-module-collaborator -am '-Dtest=ReceptionJourneyServerIT' '-Dsurefire.failIfNoSpecifiedTests=false' test -q; exit $LASTEXITCODE",
            ],
            JAVA,
            java_env,
            "java",
        )
        ready(lambda: (directory / "java-ready.json").is_file() or java.poll() is not None, 180)
        assert (directory / "java-ready.json").is_file(), "Java 测试容器未启动"
        java_port = json.loads((directory / "java-ready.json").read_text())["port"]
        settings = {
            "toolkit_base_url": f"http://127.0.0.1:{api_port}",
            "auth_secret": toolkit_secret,
            "database_url": pg_url,
            "minio_endpoint": minio_origin,
            "minio_bucket": bucket,
            "minio_access_key": "journey-root",
            "minio_secret_key": minio_secret,
            "reception_base_url": f"http://127.0.0.1:{java_port}",
            "reception_application_id": "journey-ai",
            "reception_secret": secret,
            "reception_file_application_id": "journey-files",
            "reception_file_secret": file_secret,
            "reception_file_system_id": "portal",
            "sso_bridge_enabled": True,
            "sso_bridge_base_url": origin,
            "sso_bridge_application_id": "journey-center",
            "sso_bridge_secret": control,
            "sso_bridge_ca_file": str(cert_path),
            "sso_bridge_redirect_uri": origin + "/portal/auth/callback",
            "trusted_proxy_cidrs": ["127.0.0.1/32"],
        }
        config = {
            "directory": str(directory),
            "origin": origin,
            "api_origin": f"http://127.0.0.1:{api_port}",
            "runtime_origin": f"http://127.0.0.1:{runtime_port}",
            "java_origin": f"http://127.0.0.1:{java_port}",
            "gateway_port": gateway_port,
            "api_port": api_port,
            "certificate": str(cert_path),
            "key": str(key_path),
            "portal_root": str(directory / "portal-spa"),
            "resource_root": str(directory / "resources"),
            "control": control,
            "settings": settings,
        }
        config_path = directory / "private.json"
        config_path.write_text(json.dumps(config), "utf-8")
        env["RECEPTION_JOURNEY_CONFIG"] = str(config_path)
        runtime_script = str(ROOT / "tests/xiaohe/integration/reception_journey_runtime.py")
        api = start([sys.executable, runtime_script, "api"], ROOT, env, "api")

        def api_ready():
            assert api.poll() is None, "隔离 API 启动失败，见 api.log"
            return (
                httpx.get(
                    config["api_origin"] + "/internal/xiaohe/health/ready", trust_env=False
                ).status_code
                == 200
            )

        ready(api_ready)
        worker = start([sys.executable, runtime_script, "worker"], ROOT, env, "worker")
        start([sys.executable, runtime_script, "gateway"], ROOT, env, "gateway")
        print("J1：真实 Java/AI/worker 已启动，执行 HTTP 与数据库闭环", flush=True)
        pytest = start(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/xiaohe/integration/test_reception_journey.py",
                "-q",
                "-x",
            ],
            ROOT,
            env,
            "pytest",
        )
        while pytest.poll() is None:
            if (directory / "restart-runtime").exists() and not (
                directory / "runtime-restarted"
            ).exists():
                worker.kill()
                api.kill()
                worker.wait()
                api.wait()
                (directory / "java-release").write_text("release")
                api = start([sys.executable, runtime_script, "api"], ROOT, env, "api-restarted")
                ready(api_ready)
                worker = start(
                    [sys.executable, runtime_script, "worker"], ROOT, env, "worker-restarted"
                )
                (directory / "runtime-restarted").write_text("restarted")
            time.sleep(0.2)
        assert pytest.returncode == 0, "真实 HTTP 联合验收失败，见本次 pytest.log"
        diagnostic = subprocess.run(
            ["wsl.exe", "-e", "docker", "logs", pg], capture_output=True, creationflags=FLAGS
        )
        assert b"deadlock detected" not in diagnostic.stdout + diagnostic.stderr, (
            "隔离数据库出现死锁，不允许按自动重试掩盖"
        )
        print("J1：服务闭环通过，构建原 Native Runtime 与门户", flush=True)
        if not (ROOT / "copilot-runtime/node_modules").is_dir():
            command("npm.cmd ci", ROOT / "copilot-runtime", env, "runtime-install")
        command("npm.cmd run build", ROOT / "copilot-runtime", env, "runtime-build")
        node_env = {
            **env,
            "XIAOHE_BUSINESS_GATEWAY_URL": origin + "/ai-api/v1",
            "XIAOHE_PUBLIC_ORIGIN": origin,
            "NODE_EXTRA_CA_CERTS": str(cert_path),
            "PORT": str(runtime_port),
        }
        start(
            ["node", str(ROOT / "copilot-runtime/dist/index.js")],
            ROOT / "copilot-runtime",
            node_env,
            "runtime",
        )
        ready(
            lambda: (
                httpx.get(config["runtime_origin"] + "/healthz", trust_env=False).status_code == 200
            )
        )
        portal_env = {
            **env,
            "SSO_ISSUER": origin,
            "SSO_PORTAL_CLIENT_ID": "fixture-portal",
            "SSO_PORTAL_ORIGIN": origin,
            "SSO_XIAOHE_BRIDGE_ENABLED": "true",
            "SSO_DEV_API_TARGET": "",
            "AI_DEV_API_TARGET": "",
            "RECEPTION_REAL_PORTAL_ORIGIN": origin,
            "PLAYWRIGHT_CHANNEL": "chrome",
            "RECEPTION_TEST_DIST": str(directory / "portal-spa"),
        }
        command("npm.cmd run build", PORTAL, portal_env, "portal-build")
        command(
            "npm.cmd run test:browser -- --config playwright.reception-real.config.ts --headed",
            PORTAL,
            portal_env,
            "browser",
        )
        (directory / "result.json").write_text(
            json.dumps(
                {
                    "http": "passed",
                    "browser": "passed",
                    "controlled": ["model", "center", "admin directory"],
                    "real": [
                        "native runtime",
                        "product routers/worker",
                        "Java controllers/services/filter",
                        "PostgreSQL",
                        "MySQL",
                        "MinIO",
                    ],
                }
            ),
            "utf-8",
        )
        print("J1：HTTP + Native 浏览器通过；证据目录 " + str(directory), flush=True)
    finally:
        if containers:
            diagnostic = subprocess.run(
                ["wsl.exe", "-e", "docker", "logs", containers[0]],
                capture_output=True,
                creationflags=FLAGS,
            )
            message = (diagnostic.stdout + diagnostic.stderr).decode("utf-8", errors="replace")
            for private in (
                control,
                secret,
                file_secret,
                toolkit_secret,
                pg_password,
                mysql_password,
                minio_secret,
            ):
                message = message.replace(private, "[redacted]")
            (directory / "postgres.log").write_text(message, "utf-8")
        for process in processes:
            if process is not java and process.poll() is None:
                process.kill()
                process.wait()
        (directory / "java-stop").write_text("stop")
        if java and java.poll() is None:
            try:
                java.wait(timeout=30)
            except subprocess.TimeoutExpired:
                java.kill()
                java.wait()
        for log in logs:
            log.close()
        for value in reversed(containers):
            try:
                assert (
                    docker("inspect", value, "--format", '{{index .Config.Labels "codex.task"}}')
                    == "xiaohe-reception-j1"
                )
                docker("stop", value)
            except RuntimeError:
                pass
        for name in ("private.json", "server.key"):
            path = directory / name
            if path.is_file():
                path.unlink()


if __name__ == "__main__":
    main()
