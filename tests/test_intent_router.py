import pytest

from app.application.agents.intent_router import route_direct_item, route_edit
from app.domain.session.shopping_plan import ShoppingPlan


def beach():
    return ShoppingPlan(
        goal="海边周末出行（两日）", core_use="海边休闲与游泳", stage="search",
        budget_major=300, people=2,
        tasks=["泳衣", "防晒霜", "遮阳帽", "泳镜", "沙滩垫", "创可贴"],
        must_buy=["泳衣", "防晒霜"],
        optional=["泳镜", "沙滩垫", "创可贴"],
        reusable=["遮阳帽"],
        unknowns=[],
    )


def test_new_item_is_added_without_touching_existing_slots():
    route = route_edit("我还想买一件泳帽", beach())
    assert route.kind == "buy_item"
    assert route.added == ["泳帽"]


def test_ownership_change_is_not_a_new_slot():
    route = route_edit("泳镜我有了", beach())
    assert route.kind == "set_have"
    assert route.slots == ["泳镜"]


def test_missing_cue_marks_existing_optional_as_buy():
    route = route_edit("沙滩垫没有", beach())
    assert route.kind == "set_buy"
    assert route.slots == ["沙滩垫"]


def test_skip_cue():
    route = route_edit("创可贴先不买", beach())
    assert route.kind == "set_skip"
    assert route.slots == ["创可贴"]


def test_budget_change_is_parsed_as_number():
    route = route_edit("预算改成 300 元", beach())
    assert route.kind == "budget"
    assert route.budget_major == 300


def test_scene_change_routes_to_replan():
    route = route_edit("不去海边了，改成露营三天", beach())
    assert route.kind == "scene"


def test_vague_request_asks_instead_of_guessing():
    assert route_edit("再便宜点", beach()).kind == "vague"


def test_unrelated_text_stays_on_model_path():
    assert route_edit("给我讲讲怎么挑防晒霜", beach()).kind == "none"


def test_request_to_buy_identity_document_is_recorded_as_preparation():
    from app.application.agents.orchestrator import _apply_edit

    plan = ShoppingPlan(
        goal="出行准备", stage="confirm", tasks=["身份证件"], unknowns=["身份证件"],
        checklist_sections=[{"id": "docs", "title": "证件", "required": [
            {"name": "身份证件", "purchasable": False},
        ], "optional": []}],
    )
    route = route_edit("身份证件没有，需要购买", plan)

    assert route.kind == "set_buy"
    updated = _apply_edit(plan, route)
    assert updated.prepared == ["身份证件"]
    assert updated.must_buy == []
    assert updated.stage == "confirm"


def test_direct_named_product_is_single_item_route_not_scene_planning():
    direct = route_direct_item("我想买一个旅行背包")
    assert direct.kind == "direct_item"
    assert direct.added == ["旅行背包"]
    assert route_direct_item("周末去海边，帮我准备购物清单").kind == "none"


@pytest.mark.asyncio
async def test_edit_turn_skips_llm_and_reuses_search_scope(tmp_path):
    from types import SimpleNamespace
    from app.application.agents.orchestrator import MainAgentOrchestrator, SubmitIntentInput
    from app.application.agents.scheme_state import CandidatePool, SchemeStateRegistry
    from app.infrastructure.eventbus import TradeEventBus

    class Sessions:
        def __init__(self):
            self.agent = SimpleNamespace(name="commerce_concierge", state=SimpleNamespace(summary="", context=[]))

        async def get_or_create(self, session_id):
            return self.agent

        async def persist(self, session_id):
            return None

    class Preferences:
        async def list_by_buyer(self, buyer_id):
            return []

    bus = TradeEventBus()
    schemes = SchemeStateRegistry(tmp_path)
    schemes.update("s-edit", beach())
    scopes: list = []
    pool = CandidatePool()

    async def search(slots=None):
        scopes.append(slots)
        return [{"slot": "泳帽", "result": {"hits": [{"product_id": "P1"}]}}]

    orchestrator = MainAgentOrchestrator(
        Sessions(), bus, Preferences(), scheme_states=schemes,
        candidate_pool=pool, confirmed_search=search,
    )
    events = bus.subscribe("s-edit")
    result = await orchestrator.handle_intent(SubmitIntentInput(
        shopping_session_id="s-edit", buyer_id="b1", locale="zh-CN", currency="CNY",
        raw_query="我还想买一件泳帽",
    ))
    emitted = []
    while not events.empty():
        emitted.append(events.get_nowait())

    plan = schemes.get("s-edit").scheme
    assert "泳帽" in plan.must_buy
    assert "泳衣" in plan.must_buy            # old state survives
    assert plan.reusable == ["遮阳帽"]
    assert scopes == [["泳帽"]]               # only the new slot searched
    assert not any(event.type == "model.call" for event in emitted)
    assert "泳帽" in result.final_text

    pool.put("s-edit", "泳镜", ["GOG-1"], result_count=1)
    await orchestrator.handle_intent(SubmitIntentInput(
        shopping_session_id="s-edit", buyer_id="b1", locale="zh-CN", currency="CNY",
        raw_query="泳镜我有了",
    ))
    updated = schemes.get("s-edit").scheme
    assert "泳镜" in updated.reusable
    assert pool.get("s-edit", "泳镜") == []
    assert scopes == [["泳帽"]]  # owning a slot does not trigger a new SearchAgent call


@pytest.mark.asyncio
async def test_first_turn_direct_product_creates_one_slot_and_dispatches_search(tmp_path):
    from types import SimpleNamespace
    from app.application.agents.orchestrator import MainAgentOrchestrator, SubmitIntentInput
    from app.application.agents.scheme_state import CandidatePool, SchemeStateRegistry
    from app.infrastructure.eventbus import TradeEventBus

    class Sessions:
        def __init__(self):
            self.agent = SimpleNamespace(name="commerce_concierge", state=SimpleNamespace(summary="", context=[]))

        async def get_or_create(self, session_id):
            return self.agent

        async def persist(self, session_id):
            return None

    class Preferences:
        async def list_by_buyer(self, buyer_id):
            return []

    bus = TradeEventBus()
    schemes = SchemeStateRegistry(tmp_path)
    scopes: list = []

    async def search(slots=None):
        scopes.append(slots)
        return [{"slot": "旅行背包", "result": {"hits": [{"product_id": "BAG-1"}]}}]

    orchestrator = MainAgentOrchestrator(
        Sessions(), bus, Preferences(), scheme_states=schemes,
        candidate_pool=CandidatePool(), confirmed_search=search,
    )
    events = bus.subscribe("direct-1")
    result = await orchestrator.handle_intent(SubmitIntentInput(
        shopping_session_id="direct-1", buyer_id="b1", locale="zh-CN", currency="CNY",
        raw_query="我想买一个旅行背包",
    ))
    emitted = []
    while not events.empty():
        emitted.append(events.get_nowait())
    plan = schemes.get("direct-1").scheme
    assert plan.tasks == ["旅行背包"]
    assert plan.must_buy == ["旅行背包"]
    assert scopes == [["旅行背包"]]
    assert any(event.type == "plan.update" and event.payload.get("route") == "direct_item" for event in emitted)
    assert not any(event.type == "model.call" for event in emitted)
    assert "旅行背包共返回 1 个候选" in result.final_text
