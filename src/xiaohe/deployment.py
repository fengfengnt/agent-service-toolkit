import os
import sys

from xiaohe.settings import Settings


def validate_runtime(settings, upstream):
    if (
        upstream.USE_FAKE_MODEL
        or upstream.LANGCHAIN_TRACING_V2
        or str(upstream.DEFAULT_MODEL) == "fake"
        or upstream.AUTH_SECRET != settings.auth_secret
        or str(upstream.DATABASE_TYPE) != "postgres"
        or not settings.database_url
        or not settings.trusted_proxy_cidrs
        or not all(
            (
                settings.minio_endpoint,
                settings.minio_bucket,
                settings.minio_access_key,
                settings.minio_secret_key,
            )
        )
    ):
        raise ValueError("unsafe deployment configuration")


def main():
    from core.settings import settings as upstream

    try:
        validate_runtime(Settings(), upstream)
    except Exception:
        raise SystemExit(
            "unsafe deployment configuration; check private runtime settings"
        ) from None
    match sys.argv[1:]:
        case ["api"]:
            args = [
                "-m",
                "uvicorn",
                "xiaohe.main:create_app",
                "--factory",
                "--host",
                "0.0.0.0",
                "--port",
                "8080",
                "--no-proxy-headers",
                "--no-access-log",
            ]
        case ["worker"]:
            args = ["-m", "xiaohe.worker"]
        case _:
            raise SystemExit("usage: python -m xiaohe.deployment api|worker")
    os.execv(sys.executable, [sys.executable, *args])


if __name__ == "__main__":
    main()
