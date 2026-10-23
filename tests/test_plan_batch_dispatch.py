import asyncio
import json

from agentscope.message import AssistantMsg

from app.application.agents.main_agent import MainAgentFactory
from app.application.agents.scheme_state import SchemeStateRegistry, CandidatePool
from app.domain.session.shopping_plan import ShoppingPlan
from app.infrastructure.context import ShoppingContext, ShoppingContextSnapshot
from app.infrastructure.eventbus import TradeEventBus
from app.infrastructure.resilience import CircuitBreakerRegistry
from app.infrastructure.settings import load_settings
from app.infrastructure.throttle import GatewayThrottle


async def test_committed_plan_dispatches_independent_slots_together(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', 'test-not-a-real-key')
    bus = TradeEventBus()
    events = bus.subscribe('batch')
    started, completed = [], []
    both_started = asyncio.Event()

    class Worker:
        async def reply(self, inputs, structured_schema=None):
            scope = next(m.get_text_content() for m in inputs if m.name == 'search_scope')
            started.append(scope)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), 1)
            completed.append(scope)
            return AssistantMsg('search', '{"hits": [], "notes": "no candidates"}')

    class Factory:
        def build(self):
            return Worker()

    class Preferences:
        async def list_by_buyer(self, buyer_id):
            return []

    factory = MainAgentFactory(
        load_settings(), Factory(), Factory(), bus, Preferences(),
        CircuitBreakerRegistry(), GatewayThrottle(2, 0),
        scheme_states=SchemeStateRegistry(), candidate_pool=CandidatePool(),
    )
    agent = factory.build()
    tool = await agent.toolkit.get_tool('update_shopping_plan_tool')
    token = ShoppingContext.set(ShoppingContextSnapshot(
        shopping_session_id='batch', buyer_id='buyer', locale='zh-CN', currency='CNY', raw_query='买泳衣和防水袋',
    ))
    try:
        result = await tool(goal='海边', stage='search', tasks=['泳衣', '防水袋', '泳镜', '毛巾'],
                            must_buy=['泳衣', '防水袋'], reusable=['泳镜'], unknowns=['毛巾'])
        chunks = [chunk async for chunk in result]
    finally:
        ShoppingContext.reset(token)
    assert len(started) == len(completed) == 2
    assert all('泳镜' not in scope and '毛巾' not in scope for scope in started)
    assert events.get_nowait().type == 'plan.update'
    assert [events.get_nowait().type for _ in range(2)] == ['agent.dispatch', 'agent.dispatch']
    assert len(json.loads(chunks[-1].content[0].text)['search_results']) == 2


async def test_checklist_commit_never_searches_before_confirmation():
    from app.application.tools.shopping_plan_tool import build_update_shopping_plan_tool
    calls = []

    async def dispatch():
        calls.append(True)
        return []

    tool = build_update_shopping_plan_tool(TradeEventBus(), dispatch_search=dispatch)
    await tool(goal='海边', stage='explore', tasks=['泳衣'], optional=['泳镜'])
    assert calls == []


async def test_structured_checklist_clicks_override_prose_stage_and_dispatch_only_purchase():
    from app.application.tools.shopping_plan_tool import build_update_shopping_plan_tool

    bus = TradeEventBus()
    events = bus.subscribe('selection')
    states, candidates, dispatched = SchemeStateRegistry(), CandidatePool(), []
    states.update('selection', ShoppingPlan(
        goal='海边游泳', stage='explore', budget_major=200,
        tasks=['泳衣', '泳镜', '防晒霜', '创可贴'], optional=['创可贴'],
    ))

    async def dispatch():
        request = states.get('selection')
        dispatched.append(request.scheme.must_buy)
        return [{'slot': slot, 'result': {'hits': []}} for slot in request.scheme.must_buy]

    tool = build_update_shopping_plan_tool(bus, states, candidates, dispatch)
    token = ShoppingContext.set(ShoppingContextSnapshot(
        shopping_session_id='selection', buyer_id='buyer', locale='zh-CN', currency='CNY',
        raw_query='清单确认后找商品', checklist_selection={
            'have': ['泳镜'], 'must_buy': ['泳衣'], 'optional_buy': [],
            'deferred': [], 'unknown': ['防晒霜', '创可贴'],
            'budget_major': 200, 'priority': 'value',
        },
    ))
    try:
        chunks = [await tool(goal='海边游泳', stage='explore', tasks=['泳衣', '泳镜', '防晒霜', '创可贴'],
                             must_buy=[], optional=['创可贴'])]
    finally:
        ShoppingContext.reset(token)

    payload = json.loads(chunks[-1].content[0].text)
    assert payload['stage'] == 'search'
    assert payload['must_buy'] == ['泳衣']
    assert payload['reusable'] == ['泳镜']
    assert set(payload['unknowns']) == {'防晒霜', '创可贴'}
    assert dispatched == [['泳衣']]
    assert events.get_nowait().type == 'plan.update'
