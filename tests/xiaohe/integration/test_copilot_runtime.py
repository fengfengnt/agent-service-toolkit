"""真实候选 HTTPS/PG/worker/MinIO；不使用会 TRUNCATE 的 pg_factory。"""

import hashlib
import json
import os
import ssl
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session as DatabaseSession

from xiaohe.models import Command, Conversation, Event, EventTombstone, Message, Run, Session
from xiaohe.settings import Settings

PRIVATE = "/home/fengfeng/yunhezhiyuan/copilot-candidate"
ORIGIN = "https://portal.lvh.me"
BASE = ORIGIN + "/copilot-candidate"
RUNTIME = "yunhezhiyuan-aibackend-copilotruntime"


def wsl(*args):
    result = subprocess.run(
        ["wsl", "-d", "Ubuntu", "--", *args], capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0, "candidate operation failed (private output withheld)"
    return result.stdout


@pytest.fixture(scope="module")
def environment():
    if os.environ.get("XIAOHE_COPILOT_CANDIDATE_DIR") != PRIVATE:
        pytest.fail("explicit dedicated candidate configuration required")
    assert wsl("cat", PRIVATE + "/owner") == "xiaohe-copilot-candidate-20261105"
    settings = Settings.model_validate_json(wsl("cat", PRIVATE + "/candidate.json"))
    target = make_url(settings.database_url.get_secret_value())
    assert (target.host, target.port, target.database) == (
        "127.0.0.1",
        35427,
        "xiaohe_copilot_candidate_20261105",
    )
    assert not target.query and not settings.sso_bridge_enabled
    assert settings.minio_bucket == "xiaohe-copilot-candidate-20261105"
    tls = ssl.create_default_context(
        cadata=wsl("cat", "/home/fengfeng/yunhezhiyuan/certs/local-ca.pem")
    )
    engine = create_engine(target, hide_parameters=True)
    yield engine, tls
    engine.dispose()


@contextmanager
def guest(environment):
    _, tls = environment
    with httpx.Client(
        verify=tls, trust_env=False, timeout=40, headers={"Origin": ORIGIN}
    ) as client:
        response = client.post(BASE + "/ai-api/v1/session", json={})
        assert response.status_code == 200
        initial = response.json()
        client.headers[initial["csrf"]["headerName"]] = initial["csrf"]["token"]
        yield client, initial["conversationId"]


def input_for(thread, key=None, content="我代表供应商，测试接待"):
    return dict(
        threadId=thread,
        runId=key or str(uuid4()),
        state={},
        messages=[dict(id=str(uuid4()), role="user", content=content)],
        tools=[],
        context=[],
        forwardedProps={},
    )


def frames(lines):
    for line in lines:
        if line.startswith("data:"):
            yield json.loads(line[5:])


def run(client, payload):
    response = client.post(BASE + "/copilot-api/agent/default/run", json=payload)
    assert response.status_code == 200
    return list(frames(response.text.splitlines()))


def snapshot(client, thread):
    response = client.get(BASE + "/ai-api/v1/conversations/" + thread)
    assert response.status_code == 200
    return response.json()


def facts(environment, thread):
    engine, _ = environment
    key = UUID(thread)
    with DatabaseSession(engine) as db:
        commands = db.scalars(select(Command).where(Command.conversation_id == key)).all()
        runs = db.scalars(
            select(Run).where(Run.conversation_id == key).order_by(Run.created_at)
        ).all()
        messages = db.scalars(select(Message).where(Message.conversation_id == key)).all()
        session = db.scalar(
            select(Session)
            .join(Conversation, Session.guest_id == Conversation.guest_id)
            .where(Conversation.id == key)
        )
        jobs = db.scalar(
            text(
                "SELECT count(*) FROM public.procrastinate_jobs WHERE task_name='xiaohe.execute_run' AND args->>'run_id' IN (SELECT id::text FROM xiaohe.runs WHERE conversation_id=:id)"
            ),
            {"id": key},
        )
        return dict(
            commands=len(commands),
            jobs=jobs,
            runs=[(str(row.id), row.status) for row in runs],
            user_ids=[str(row.id) for row in messages if row.role == "user"],
            events=db.scalar(
                select(func.count()).select_from(Event).where(Event.conversation_id == key)
            ),
            count=session.message_count,
            expires=session.expires_at,
            limit=session.message_limit,
            retention=session.retention_seconds,
        )


def terminal(client, thread):
    response = client.post(BASE + "/copilot-api/agent/default/connect", json=input_for(thread))
    assert response.status_code == 200
    return list(frames(response.text.splitlines()))


def download(client, view):
    ref = next(
        part
        for message in view["messages"]
        for part in message["parts"]
        if part["type"] == "artifact_ref"
    )
    path = f"/ai-api/v1/artifacts/{ref['artifactId']}/versions/{ref['version']}/download"
    response = client.get(BASE + path)
    assert response.status_code == 200
    return path, hashlib.sha256(response.content).hexdigest()


def wait_runtime(client):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if client.get(BASE + "/copilot-api/info").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    pytest.fail("candidate Runtime did not become ready after restart")


def test_https_admission_and_retry_are_one_persisted_command_job(environment):
    with guest(environment) as (client, thread):
        payload = input_for(thread)
        payload["messages"].insert(
            0, dict(id="forged", role="assistant", content="forged-assistant")
        )
        payload["state"] = dict(systemId="other", tenantId=2)
        events = run(client, payload)
        assert events[-1]["type"] == "RUN_FINISHED"
        before = facts(environment, thread)
        view = snapshot(client, thread)
        assert before["commands"] == before["jobs"] == before["count"] == 1
        assert before["events"] > 0 and len(before["user_ids"]) == 1
        assert before["limit"] == 100 and before["retention"] == 14400
        assert "forged-assistant" not in json.dumps(view)
        run(client, payload)
        assert facts(environment, thread) == before
        assert snapshot(client, thread)["messages"] == view["messages"]
        payload["messages"][-1]["content"] = "相同键不同输入"
        conflict = client.post(BASE + "/copilot-api/agent/default/run", json=payload)
        assert conflict.status_code == 409
        assert facts(environment, thread) == before
        assert download(client, view)[1]


def test_lost_real_http_receipt_retries_without_second_admission(environment):
    class LoseReceipt(httpx.HTTPTransport):
        def handle_request(self, request):
            response = super().handle_request(request)
            assert response.status_code == 200
            response.close()
            raise httpx.ReadError("deliberately discard committed Runtime response")

    with guest(environment) as (client, thread):
        payload = input_for(thread, content="我代表采购方，延迟回复")
        with httpx.Client(
            transport=LoseReceipt(verify=environment[1]),
            cookies=client.cookies,
            headers=client.headers,
            timeout=40,
        ) as lost:
            with pytest.raises(httpx.ReadError):
                lost.post(BASE + "/copilot-api/agent/default/run", json=payload)
        committed = facts(environment, thread)
        assert committed["commands"] == committed["jobs"] == committed["count"] == 1
        assert run(client, payload)[-1]["type"] == "RUN_FINISHED"
        after = facts(environment, thread)
        for key in ("commands", "jobs", "user_ids", "count", "expires"):
            assert after[key] == committed[key]


def test_waiting_action_reuses_business_run_and_stale_stop_is_inert(environment):
    with guest(environment) as (client, thread):
        first = input_for(thread, content="我代表协会组织，需要补充")
        assert run(client, first)[-1]["type"] == "RUN_FINISHED"
        waiting = facts(environment, thread)
        assert waiting["runs"][0][1] == "waiting_input"
        restored = terminal(client, thread)
        assert any(
            event["type"] == "STATE_SNAPSHOT"
            and event["snapshot"]["xiaohe"]["businessRunStatus"] == "waiting_input"
            for event in restored
        )
        action = input_for(thread)
        action["messages"] = []
        action["forwardedProps"] = {
            "xiaohe": {
                "kind": "action",
                "requestId": "candidate-question",
                "text": "补充需求，延迟回复",
            }
        }
        with client.stream(
            "POST", BASE + "/copilot-api/agent/default/run", json=action
        ) as response:
            assert response.status_code == 200
            for event in frames(response.iter_lines()):
                if event["type"] == "STATE_SNAPSHOT":
                    break
            stale = client.post(
                BASE + f"/copilot-api/agent/default/stop/{thread}", json={"runId": first["runId"]}
            )
            assert stale.status_code == 200 and stale.json()["stopped"] is False
        assert terminal(client, thread)[-1]["type"] == "RUN_FINISHED"
        final = facts(environment, thread)
        assert len(final["runs"]) == 1 and final["runs"][0][0] == waiting["runs"][0][0]
        assert final["commands"] == final["jobs"] == final["count"] == 2
        run(client, action)
        assert facts(environment, thread) == final
        obsolete = input_for(thread)
        obsolete["messages"] = []
        obsolete["forwardedProps"] = {
            "xiaohe": {"kind": "action", "requestId": "obsolete-question", "text": "延迟"}
        }
        rejected = client.post(BASE + "/copilot-api/agent/default/run", json=obsolete)
        assert rejected.status_code == 409
        assert facts(environment, thread) == final


def test_runtime_restart_replays_without_second_job(environment):
    with guest(environment) as (client, thread):
        run(client, input_for(thread))
        old = snapshot(client, thread)
        file_path, file_hash = download(client, old)
        payload = input_for(thread, content="我代表供应商，延迟生成第二份文件")
        with client.stream(
            "POST", BASE + "/copilot-api/agent/default/run", json=payload
        ) as response:
            assert response.status_code == 200
            for event in frames(response.iter_lines()):
                if event["type"] == "STATE_SNAPSHOT":
                    break
            before = facts(environment, thread)
            accepted_message_id = next(
                key
                for key in before["user_ids"]
                if key not in {item["id"] for item in old["messages"]}
            )
            assert before["runs"][-1][1] in ("queued", "running")
            wsl("docker", "restart", "--time", "3", RUNTIME)
        wait_runtime(client)
        events = terminal(client, thread)
        assert events[-1]["type"] == "RUN_FINISHED"
        after = facts(environment, thread)
        assert after["jobs"] == before["jobs"] == 2
        assert after["commands"] == 2 and after["count"] == 2
        assert after["user_ids"].count(accepted_message_id) == 1
        current = snapshot(client, thread)
        old_by_id = {message["id"]: message for message in old["messages"]}
        assert all(
            message == old_by_id[message["id"]]
            for message in current["messages"]
            if message["id"] in old_by_id
        )
        downloaded = client.get(BASE + file_path)
        assert (
            downloaded.status_code == 200
            and hashlib.sha256(downloaded.content).hexdigest() == file_hash
        )


def test_close_only_detaches_and_explicit_stop_cancels(environment):
    with guest(environment) as (client, thread):
        payload = input_for(thread, content="我代表供应商，延迟回复供停止验证")
        with client.stream(
            "POST", BASE + "/copilot-api/agent/default/run", json=payload
        ) as response:
            assert response.status_code == 200
            for event in frames(response.iter_lines()):
                if event["type"] == "STATE_SNAPSHOT":
                    break
        assert facts(environment, thread)["runs"][-1][1] in ("queued", "running")
        stopped = client.post(
            BASE + f"/copilot-api/agent/default/stop/{thread}", json={"runId": payload["runId"]}
        )
        assert stopped.status_code == 200 and stopped.json()["stopped"] is True
        assert facts(environment, thread)["runs"][-1][1] == "cancelled"
        events = terminal(client, thread)
        assert not any(e["type"] == "RUN_FINISHED" for e in events)
        public = [e["snapshot"]["xiaohe"] for e in events if e["type"] == "STATE_SNAPSHOT"]
        assert public[-1]["activeRun"] is None
        assert (
            client.post(
                BASE + f"/copilot-api/agent/default/stop/{thread}", json={"runId": payload["runId"]}
            ).json()["stopped"]
            is False
        )


def test_two_subjects_and_two_systems_cannot_read_run_or_stop_each_other(environment):
    with guest(environment) as (a, ta), guest(environment) as (b, tb):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda pair: run(pair[0], input_for(pair[1])), [(a, ta), (b, tb)])
            )
        assert all(result[-1]["type"] == "RUN_FINISHED" for result in results)
        for client, foreign in ((a, tb), (b, ta)):
            for verb in ("run", "connect"):
                response = client.post(
                    BASE + "/copilot-api/agent/default/" + verb, json=input_for(foreign)
                )
                assert response.status_code == 403
            assert (
                client.post(
                    BASE + f"/copilot-api/agent/default/stop/{foreign}", json={}
                ).status_code
                == 403
            )
            assert client.get(BASE + "/ai-api/v1/conversations/" + foreign).status_code == 403
        file_path, _ = download(a, snapshot(a, ta))
        assert b.get(BASE + file_path).status_code == 403
        with httpx.Client(
            trust_env=False,
            headers={"Origin": ORIGIN, "X-Forwarded-Proto": "https", "X-Xiaohe-System-Id": "other"},
        ) as internal:
            other = internal.post("http://127.0.0.1:18111/ai-api/v1/session", json={})
            assert other.status_code == 200
            credentials = {
                "Cookie": "__Host-XIAOHESESSION=" + internal.cookies.get("__Host-XIAOHESESSION"),
                "X-Xiaohe-CSRF": other.json()["csrf"]["token"],
            }
            for thread in (ta, other.json()["conversationId"]):
                response = a.post(
                    BASE + "/copilot-api/agent/default/connect",
                    json=input_for(thread),
                    headers=credentials,
                )
                assert response.status_code == 403
        forged = a.post(
            BASE + "/copilot-api/agent/default/connect",
            json=input_for(ta),
            headers={
                "X-Xiaohe-System-Id": "other",
                "X-Forwarded-For": "forged",
                "X-Forwarded-Proto": "http",
            },
        )
        assert forged.status_code == 200
        assert all(
            event["snapshot"]["xiaohe"]["conversationId"] == ta
            for event in frames(forged.text.splitlines())
            if event["type"] == "STATE_SNAPSHOT"
        )


def test_retired_cursor_recovers_snapshot_without_resending_input(environment):
    with guest(environment) as (client, thread):
        run(client, input_for(thread))
        original = snapshot(client, thread)
        engine, _ = environment
        with DatabaseSession(engine) as db, db.begin():
            first = db.scalar(
                select(Event)
                .where(Event.conversation_id == UUID(thread))
                .order_by(Event.sequence)
                .limit(1)
            )
            cursor = str(first.id)
            db.add(
                EventTombstone(
                    id=first.id,
                    conversation_id=first.conversation_id,
                    system_id=first.system_id,
                    tenant_id=first.tenant_id,
                    sequence=first.sequence,
                )
            )
            db.delete(first)
        before = facts(environment, thread)
        old = client.get(
            BASE + f"/ai-api/v1/conversations/{thread}/events", params={"after": cursor}
        )
        assert old.status_code == 410 and old.json()["error"]["code"] == "EVENT_CURSOR_EXPIRED"
        events = terminal(client, thread)
        public = [
            event["snapshot"]["xiaohe"] for event in events if event["type"] == "STATE_SNAPSHOT"
        ]
        assert public[-1]["messages"] == original["messages"]
        assert facts(environment, thread) == before


def test_https_rejects_sdk_management_routes_missing_csrf_and_wrong_origin(environment):
    with guest(environment) as (client, thread):
        denied = [
            ("GET", "/threads"),
            ("GET", "/threads/x/messages"),
            ("GET", "/threads/x/events"),
            ("GET", "/threads/x/state"),
            ("POST", "/threads/clear"),
            ("PATCH", "/threads/x"),
            ("DELETE", "/threads/x"),
            ("POST", "/threads/x/archive"),
            ("POST", "/threads/subscribe"),
            ("POST", "/transcribe"),
            ("GET", "/inspector-metadata"),
            ("GET", "/inspector-learning"),
            ("POST", "/agent/default/suggest"),
            ("POST", "/trajectory/x/connect"),
            ("GET", "/memories"),
            ("POST", "/memories/recall"),
            ("POST", "/memories/subscribe"),
            ("PATCH", "/memories/x"),
            ("DELETE", "/memories/x"),
            ("POST", "/annotate"),
            ("GET", "/cpk-debug-events"),
            ("POST", "/agent/other/run"),
            ("POST", "/prefix/agent/default/run"),
        ]
        before = facts(environment, thread)
        for method, path in denied:
            response = client.request(
                method, BASE + "/copilot-api" + path, **({} if method == "GET" else {"json": {}})
            )
            assert response.status_code == 404, (method, path, response.status_code)
        for headers in ({"X-Xiaohe-CSRF": ""}, {"Origin": "https://foreign.invalid"}):
            response = client.post(
                BASE + "/copilot-api/agent/default/run", json=input_for(thread), headers=headers
            )
            assert response.status_code == 403
        assert (
            client.post(
                BASE + "/copilot-api/agent/default/connect", json=input_for(str(uuid4()))
            ).status_code
            == 403
        )
        assert facts(environment, thread) == before


@contextmanager
def short_visitor_limits():
    script = (
        "import json,pathlib,sys; p=pathlib.Path('" + PRIVATE + "/candidate.json'); "
        "v=json.loads(p.read_text()); "
        "v.update(visitor_message_limit=int(sys.argv[1]),visitor_retention_seconds=int(sys.argv[2])); "
        "p.write_text(json.dumps(v))"
    )

    def change(limit, retention):
        wsl("python3", "-c", script, str(limit), str(retention))
        wsl("docker", "restart", "--time", "3", "yunhezhiyuan-aibackend-candidate-api")
        deadline = time.monotonic() + 40
        with httpx.Client(trust_env=False, timeout=2) as check:
            while time.monotonic() < deadline:
                try:
                    if (
                        check.get("http://127.0.0.1:18111/internal/xiaohe/health/ready").status_code
                        == 200
                    ):
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.3)
        pytest.fail("dedicated candidate API did not become ready")

    try:
        change(2, 5)
        yield
    finally:
        change(100, 14400)


def test_small_quota_and_short_real_expiry_do_not_renew_on_read_or_retry(environment):
    with short_visitor_limits():
        with guest(environment) as (client, thread):
            initial = facts(environment, thread)
            assert (initial["limit"], initial["retention"]) == (2, 5)
            terminal(client, thread)
            assert facts(environment, thread) == initial
            first = input_for(thread)
            run(client, first)
            accepted = facts(environment, thread)
            assert accepted["count"] == 1 and accepted["expires"] > initial["expires"]
            run(client, first)
            assert facts(environment, thread) == accepted
            run(client, input_for(thread))
            second = facts(environment, thread)
            assert second["count"] == 2 and second["expires"] > accepted["expires"]
            limited = client.post(BASE + "/copilot-api/agent/default/run", json=input_for(thread))
            assert (
                limited.status_code == 429
                and limited.json()["error"]["code"] == "VISITOR_LIMIT_REACHED"
            )
            assert facts(environment, thread) == second
        with guest(environment) as (client, thread):
            run(client, input_for(thread))
            file_path, _ = download(client, snapshot(client, thread))
            payload = input_for(thread, content="我代表供应商，延迟回复直到访客真实到期")
            events = run(client, payload)
            assert any(
                event["type"] == "RUN_ERROR" and event.get("code") == "VISITOR_EXPIRED"
                for event in events
            )
            assert not any(event["type"] == "RUN_FINISHED" for event in events)
            error_index = next(i for i, event in enumerate(events) if event["type"] == "RUN_ERROR")
            assert not events[error_index + 1 :]
            assert client.get(BASE + "/ai-api/v1/conversations/" + thread).status_code == 410
            assert client.get(BASE + file_path).status_code == 410
            assert client.post(BASE + "/ai-api/v1/session", json={}).status_code == 410
            assert (
                client.post(
                    BASE + "/copilot-api/agent/default/connect", json=input_for(thread)
                ).status_code
                == 410
            )
            assert (
                client.post(
                    BASE + "/copilot-api/agent/default/run", json=input_for(thread)
                ).status_code
                == 410
            )
            assert facts(environment, thread)["count"] == 2
            engine, _ = environment
            deadline = time.monotonic() + 5
            while True:
                with DatabaseSession(engine) as db:
                    active = db.scalar(
                        select(func.count())
                        .select_from(Run)
                        .where(
                            Run.conversation_id == UUID(thread),
                            Run.status.in_(("queued", "running", "waiting_input")),
                        )
                    )
                if not active or time.monotonic() >= deadline:
                    break
                time.sleep(0.2)
            assert active == 0
