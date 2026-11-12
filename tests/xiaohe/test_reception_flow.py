from uuid import UUID, uuid4

from deep_runtime_support import make_script_model
from native_support import send_in_db
from sqlalchemy import select
from test_deep_factory import final_response

from xiaohe.admission import accept_action, accept_agent_message
from xiaohe.contracts import MessageInput
from xiaohe.conversations import load_snapshot
from xiaohe.graph import build_graph
from xiaohe.models import Run
from xiaohe.reception.store import load_reception


async def install(pg_factory, runtime_env, monkeypatch, business_client=None, model=None):
    from agents.agents import agents

    graph = build_graph(
        factory=pg_factory,
        model=model
        or make_script_model(
            [
                final_response(
                    answer={
                        "text": "可以继续咨询服务方式。",
                        "basis": "general_advice",
                        "source_ids": [],
                    },
                    wait_for_input=True,
                )
            ]
        ),
        business_client=business_client,
    )
    graph.checkpointer, graph.store = runtime_env.graph.checkpointer, runtime_env.graph.store
    monkeypatch.setattr(agents["xiaohe"], "graph_like", graph)


async def talk(factory, env, who, conversation, text):
    async with factory.begin() as db:
        # The persisted pending request is authoritative, not the test's chat copy.
        waiting = await db.scalar(
            select(Run).where(Run.conversation_id == conversation, Run.status == "waiting_input")
        )
        if waiting:
            accepted = await accept_action(
                db,
                who,
                conversation,
                str(uuid4()),
                waiting.pending_request["requestId"],
                None,
                text,
            )
        else:
            role = {"采购商": "buyer", "社群组织": "organization", "供应商": "supplier"}.get(text)
            if role:
                accepted = await accept_agent_message(
                    db,
                    who,
                    "xiaohe-" + role,
                    conversation,
                    MessageInput(client_message_id=str(uuid4()), text=text),
                )
            else:
                accepted = await send_in_db(db, who, conversation, text)
    await env.worker.execute_run(UUID(accepted.run.id))
    async with factory.begin() as db:
        snapshot = await load_snapshot(db, who, conversation)
        return snapshot.messages[-1], await load_reception(db, who, conversation)
