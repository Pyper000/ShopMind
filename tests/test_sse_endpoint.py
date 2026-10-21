import asyncio
import os

from fastapi.testclient import TestClient

from app.application.agents.orchestrator import SubmitIntentOutput
from app.infrastructure.eventbus import TradeEventBus

os.environ.setdefault("LLM_API_KEY", "unit-test-placeholder")

from app.presentation import server


class FakeContainer:
    def __init__(self):
        self.bus = TradeEventBus()
        self.task_queue = None
        self.backplane = None
        self.orchestrator = self

    async def startup(self):
        pass

    async def shutdown(self):
        pass

    async def handle_intent(self, intent):
        self.bus.publish(intent.shopping_session_id, "plan.update", {"shopping_plan": {"goal": "海边出行"}})
        await asyncio.sleep(0.01)
        self.bus.publish(intent.shopping_session_id, "agent.dispatch", {"agent": "search_agent"})
        await asyncio.sleep(0.01)
        self.bus.publish(intent.shopping_session_id, "agent.result", {"agent": "search_agent", "output": "{}"})
        self.bus.publish(intent.shopping_session_id, "final.result", {"text": "方案已完成"})
        return SubmitIntentOutput(intent.shopping_session_id, "方案已完成")


def test_intent_stream_returns_ordered_sse_events(monkeypatch):
    monkeypatch.setattr(server, "build_container_origins", lambda: [])
    monkeypatch.setattr(server, "build_container", lambda: _fake_container())
    with TestClient(server.build_app()) as client:
        response = client.post("/commerce/intents/stream", json={
            "shopping_session_id": "test-sse",
            "buyer_id": "test-buyer",
            "raw_query": "计划海边出行",
        })

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    event_names = [line.removeprefix("event: ") for line in response.text.splitlines() if line.startswith("event: ")]
    assert event_names == ["plan.update", "agent.dispatch", "agent.result", "final.result"]


async def _fake_container():
    return FakeContainer()
