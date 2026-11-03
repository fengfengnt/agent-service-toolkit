"""只建立贴有测试标签的 MinIO，不修改现有容器、卷或生产环境配置。"""

import json
import os
import secrets
import subprocess
import time
from pathlib import Path

import boto3
import httpx
from botocore.config import Config

ROOT = Path(__file__).resolve().parents[3]
SCRATCH = ROOT / ".superpowers/sdd/2026-11-03-xiaohe-ai-backend"
CONTAINER = "xiaohe-agent-test-minio"
VOLUME = "xiaohe-agent-test-minio-data"
ENV_NAMES = (
    "XIAOHE_TEST_MINIO_IMAGE",
    "XIAOHE_TEST_MINIO_ROOT_USER",
    "XIAOHE_TEST_MINIO_ROOT_PASSWORD",
    "XIAOHE_TEST_MINIO_APP_USER",
    "XIAOHE_TEST_MINIO_APP_SECRET",
)


def docker(*args, input=None, required=True):
    result = subprocess.run(
        ["wsl", "-d", "Ubuntu", "--", "docker", *args],
        input=input.encode("utf-8") if input is not None else None,
        capture_output=True,
        env={k: v for k, v in os.environ.items() if k not in ENV_NAMES},
    )
    result.stdout = result.stdout.decode("utf-8", errors="replace")
    result.stderr = result.stderr.decode("utf-8", errors="replace")
    if required and result.returncode:
        message = result.stderr
        path = SCRATCH / "minio-test.env"
        if path.is_file():
            for line in path.read_text("utf-8").splitlines():
                if "=" in line:
                    message = message.replace(line.split("=", 1)[1], "[REDACTED]")
        raise RuntimeError("isolated MinIO Docker operation failed: " + message[-1200:])
    return result


def wsl_path(path):
    return subprocess.run(
        ["wsl", "-d", "Ubuntu", "--", "wslpath", "-u", path.as_posix()],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


def main():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    config_path = SCRATCH / "minio-test-config.json"
    env_path = SCRATCH / "minio-test.env"
    lock = json.loads((ROOT / "deploy/xiaohe/images.lock.json").read_text("utf-8"))["minio"]
    image = lock["repository"] + "@" + lock["digest"]
    for target, kind in ((CONTAINER, "container"), (VOLUME, "volume")):
        existing = docker(
            kind,
            "inspect",
            target,
            "--format",
            "{{json .Labels}}" if kind == "volume" else "{{json .Config.Labels}}",
            required=False,
        )
        if existing.returncode == 0:
            labels = json.loads(existing.stdout)
            if (
                labels.get("codex.task") != "xiaohe-agent-test"
                or not config_path.is_file()
                or not env_path.is_file()
            ):
                raise RuntimeError(
                    "refusing to reuse an existing resource without this test's ownership/config"
                )
    if config_path.exists() != env_path.exists():
        raise RuntimeError("incomplete test fixture configuration; no resource reset performed")
    if not config_path.exists():
        values = {
            "XIAOHE_TEST_MINIO_IMAGE": image,
            "XIAOHE_TEST_MINIO_ROOT_USER": "testroot-" + secrets.token_hex(6),
            "XIAOHE_TEST_MINIO_ROOT_PASSWORD": secrets.token_urlsafe(32),
            "XIAOHE_TEST_MINIO_APP_USER": "testapp-" + secrets.token_hex(6),
            "XIAOHE_TEST_MINIO_APP_SECRET": secrets.token_urlsafe(32),
        }
        env_path.write_text("\n".join(k + "=" + v for k, v in values.items()) + "\n", "utf-8")
        config_path.write_text(
            json.dumps(
                {
                    "endpoint": "http://127.0.0.1:35418",
                    "bucket": "test-xiaohe-artifacts",
                    "container": CONTAINER,
                    "access_key": values["XIAOHE_TEST_MINIO_APP_USER"],
                    "secret_key": values["XIAOHE_TEST_MINIO_APP_SECRET"],
                }
            ),
            "utf-8",
        )
    values = dict(line.split("=", 1) for line in env_path.read_text("utf-8").splitlines() if line)
    if values["XIAOHE_TEST_MINIO_IMAGE"] != image:
        raise RuntimeError("fixture image differs from the checked lock; no automatic replacement")
    docker(
        "compose",
        "--env-file",
        wsl_path(env_path),
        "-f",
        wsl_path(ROOT / "deploy/xiaohe/compose.test.yml"),
        "--profile",
        "minio",
        "up",
        "-d",
        "minio",
    )
    data = json.loads(config_path.read_text("utf-8"))
    with httpx.Client(trust_env=False, timeout=2) as http:
        for _ in range(100):
            try:
                if http.get(data["endpoint"] + "/minio/health/ready").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("test MinIO did not become ready")
    root = boto3.client(
        "s3",
        endpoint_url=data["endpoint"],
        region_name="us-east-1",
        aws_access_key_id=values["XIAOHE_TEST_MINIO_ROOT_USER"],
        aws_secret_access_key=values["XIAOHE_TEST_MINIO_ROOT_PASSWORD"],
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, proxies={}),
    )
    existing = {row["Name"] for row in root.list_buckets()["Buckets"]}
    for name in (data["bucket"], "test-xiaohe-foreign"):
        if name not in existing:
            root.create_bucket(Bucket=name)
    root.close()
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetBucketLocation", "s3:ListBucket"],
                "Resource": ["arn:aws:s3:::" + data["bucket"]],
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": ["arn:aws:s3:::" + data["bucket"] + "/xiaohe/*"],
            },
        ],
    }
    script = (
        """set -eu
mc alias set xiaohe http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc admin user add xiaohe "$MINIO_TEST_APP_USER" "$MINIO_TEST_APP_SECRET" >/dev/null
cat >/tmp/xiaohe-test-policy.json <<'POLICY'
"""
        + json.dumps(policy)
        + """
POLICY
mc admin policy create xiaohe xiaohe-test /tmp/xiaohe-test-policy.json >/dev/null
mc admin policy attach xiaohe xiaohe-test --user "$MINIO_TEST_APP_USER" >/dev/null
"""
    )
    docker("exec", "-i", CONTAINER, "/bin/sh", "-s", input=script)
    print("MINIO_TEST_READY config=" + str(config_path))


if __name__ == "__main__":
    main()
