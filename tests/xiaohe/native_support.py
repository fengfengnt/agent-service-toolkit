from uuid import UUID, uuid4

from sqlalchemy import select
from test_identity_sessions import actor, bootstrap, login

from xiaohe.agents.contracts import AgentFrame, ModelContext
from xiaohe.contracts import ServiceError
from xiaohe.deep_runtime.bundles import load_runtime_bundle
from xiaohe.lead_skills.store import resolve_draft_policy
from xiaohe.models import Command
from xiaohe.reception.contracts import CreateDraft, SubmissionResult, SubmitReceipt
from xiaohe.reception.models import ReceptionDraft
from xiaohe.reception.store import create_draft, patch_draft
from xiaohe.resources import RESOURCE_ROOT
from xiaohe.runs import acquire_run


def skills(root=RESOURCE_ROOT):
    return load_runtime_bundle(root)


async def send_in_db(db, who, conversation, text):
    from xiaohe.admission import accept_message

    return await accept_message(db, who, conversation, str(uuid4()), text)


async def prepare(
    factory, tmp_path, *, text="就按这份资料提交吧", host="portal", direction="buyer"
):
    bundle = skills()
    options = {}
    if host == "portal":
        _, token = await login(factory)
    else:
        from test_identity_sessions import completion, system

        from xiaohe.identity import create_user_session

        async with factory.begin() as db:
            _, secret = await create_user_session(db, system(host), completion(host))
        token = secret.get_secret_value()
        options = {"system": host, "origin": system(host).allowed_origin}
    initial, _, _ = await bootstrap(factory, token, **options)
    who = await actor(factory, token, **options)
    conversation = UUID(initial.conversation_id)
    async with factory.begin() as db:
        view = await create_draft(
            db,
            who,
            conversation,
            CreateDraft(client_draft_id="one", direction=direction, kind="service"),
        )
        fields = (
            dict(
                organizationName="虚构协会",
                memberSize="100—500",
                coverage="本市",
                onlineChannels=["暂无"],
            )
            if direction == "organization"
            else {"companyName": "虚构甲公司"}
        )
        view = await patch_draft(
            db,
            who,
            view.id,
            1,
            fields | {"contactName": "测试联系人", "contactPhone": "测试联系电话"},
        )
        row = await db.get(ReceptionDraft, UUID(view.id))
        row.step, row.state, row.summary_version = "summary", "review", row.revision
        policy = await resolve_draft_policy(db, row)
        accepted = await send_in_db(db, who, conversation, text)
        command = await db.scalar(
            select(Command).where(Command.message_id == UUID(accepted.message.id))
        )
        command_id, message_id = command.id, command.message_id
    async with factory.begin() as db:
        fence = await acquire_run(db, UUID(accepted.run.id))
    frame = AgentFrame(
        fence=fence,
        command_id=command_id,
        message_id=message_id,
        conversation_id=conversation,
        actor=who,
        agent_id="xiaohe-" + direction,
        bundle_id=bundle.bundle_id,
        draft_id=row.id,
        draft_revision=row.revision,
        summary_version=row.summary_version,
        field_policy=policy,
        interaction_policy=bundle.interaction_policy,
        model_context=ModelContext(
            text=text,
            direction=direction,
            authenticated=True,
            draft_step=row.step,
            draft_state=row.state,
            saved_fields=row.fields,
            allowed_fields=[f.id for f in policy.fields],
            summary_visible=True,
            field_options={f.id: f.options for f in policy.fields if f.options},
            required_fields=policy.required_fields,
            field_labels={f.id: f.label for f in policy.fields},
        ),
    )
    return bundle, frame


class Business:
    def __init__(self, *, lose_reply=False):
        self.payloads = []
        self.receipt = None
        self.lose_reply = lose_reply

    async def result(self, actor, key):
        assert actor.ai_user_id and key
        return SubmissionResult(
            status="accepted" if self.receipt else "not_found", receipt=self.receipt
        )

    async def submit(self, actor, payload):
        self.payloads.append(payload.model_dump(mode="json", by_alias=True))
        self.receipt = SubmitReceipt(
            submission_key=payload.submission_key,
            kind="service",
            application_id="fake-application",
            revision=1,
            status="accepted",
        )
        if self.lose_reply:
            raise ServiceError("SUBMISSION_UNKNOWN", 503, True)
        return self.receipt


async def kill_at_committed_phase(tmp_path, frame, phase):
    import asyncio
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    import pytest

    ready = tmp_path / "ready"
    config = tmp_path / "child.json"
    config.write_text(
        json.dumps({"frame": frame.model_dump(mode="json"), "phase": phase, "ready": str(ready)}),
        "utf-8",
    )
    root = Path(__file__).resolve().parents[2]
    env = dict(
        os.environ, PYTHONPATH=os.pathsep.join([str(root / "src"), str(root / "tests/xiaohe")])
    )
    with (tmp_path / "child.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).parent / "fixtures/agent_crash_worker.py"),
                str(config),
            ],
            cwd=root,
            env=env,
            stdout=log,
            stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            for _ in range(200):
                if ready.exists():
                    break
                if process.poll() is not None:
                    pytest.fail(
                        "isolated crash child exited before committed phase; inspect private child.log"
                    )
                await asyncio.sleep(0.1)
            assert ready.exists(), "child did not reach crash window"
        finally:
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.wait, 10)
        assert process.returncode != 0
