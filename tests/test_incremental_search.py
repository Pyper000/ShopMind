from app.application.agents.scheme_state import (
    CandidatePool,
    SchemeStateRegistry,
    choose_retrieval_action,
)
from app.application.tools.shopping_plan_tool import build_update_shopping_plan_tool
from app.domain.session.shopping_plan import ShoppingPlan
from app.infrastructure.context import ShoppingContext, ShoppingContextSnapshot
from app.infrastructure.eventbus import TradeEventBus


def test_budget_or_preference_change_reuses_candidates_for_reranking():
    states = SchemeStateRegistry()
    pool = CandidatePool()
    states.update("s1", ShoppingPlan(
        goal="周末露营", core_use="露营", people=2, stage="search",
        tasks=["露营灯"], must_buy=["露营灯"],
    ))
    pool.put("s1", "露营灯", ["P1", "P2", "P3"], result_count=3)
    request = states.update("s1", ShoppingPlan(
        goal="周末露营", core_use="露营", people=2, stage="search", budget_major=500,
        preferences=["轻便"], tasks=["露营灯"], must_buy=["露营灯"],
    ))

    action, _ = choose_retrieval_action(
        request, "露营灯", pool.get("s1", "露营灯"), cached_count=pool.result_count("s1", "露营灯"),
    )
    assert action == "filter_and_rerank"


def test_new_or_underfilled_slot_uses_local_recall():
    states = SchemeStateRegistry()
    first = states.update("s1", ShoppingPlan(
        goal="周末露营", core_use="露营", stage="search", tasks=["露营灯"], must_buy=["露营灯"],
    ))
    pool = CandidatePool()
    action, _ = choose_retrieval_action(first, "露营灯", [], cached_count=0)
    assert action == "recall_local"

    states.update("s1", ShoppingPlan(
        goal="周末露营", core_use="露营", stage="search", tasks=["露营灯"], must_buy=["露营灯"],
    ))
    pool.put("s1", "露营灯", ["P1", "P2", "P3"], result_count=1)
    request = states.update("s1", ShoppingPlan(
        goal="周末露营", core_use="露营", stage="search", tasks=["露营灯"], must_buy=["露营灯"],
    ))
    action, _ = choose_retrieval_action(
        request, "露营灯", pool.get("s1", "露营灯"), cached_count=pool.result_count("s1", "露营灯"),
    )
    assert action == "recall_local"


def test_scene_change_triggers_full_recall_and_existing_items_are_removed():
    states = SchemeStateRegistry()
    pool = CandidatePool()
    states.update("s1", ShoppingPlan(
        goal="周末露营", core_use="露营", stage="search", tasks=["露营灯"], must_buy=["露营灯"],
    ))
    pool.put("s1", "露营灯", ["P1", "P2", "P3"], result_count=3)
    request = states.update("s1", ShoppingPlan(
        goal="海边游泳", core_use="游泳", stage="search", tasks=["防水手机袋"],
        must_buy=["防水手机袋"],
    ))
    pool.apply("s1", request.mutation)
    action, _ = choose_retrieval_action(request, "防水手机袋", [], cached_count=0)
    assert action == "recall_all"
    assert pool.get("s1", "露营灯") == []

    request = states.update("s1", ShoppingPlan(
        goal="海边游泳", core_use="游泳", stage="search", tasks=["防水手机袋"],
        reusable=["防水手机袋"],
    ))
    pool.apply("s1", request.mutation)
    assert pool.get("s1", "防水手机袋") == []


def test_existing_and_deferred_slots_are_removed_from_candidate_pool():
    states = SchemeStateRegistry()
    pool = CandidatePool()
    states.update("s1", ShoppingPlan(
        goal="周末露营", core_use="露营", stage="search",
        tasks=["露营灯", "登山杖", "保温杯"],
        must_buy=["露营灯", "登山杖"], optional=["保温杯"],
    ))
    for task in ("露营灯", "登山杖", "保温杯"):
        pool.put("s1", task, [f"{task}-P1"], result_count=1)

    request = states.update("s1", ShoppingPlan(
        goal="周末露营", core_use="露营", stage="search",
        tasks=["露营灯", "登山杖", "保温杯"], must_buy=["登山杖"],
        reusable=["露营灯"], not_buy=["保温杯"],
    ))
    pool.apply("s1", request.mutation)

    assert pool.get("s1", "露营灯") == []
    assert pool.get("s1", "保温杯") == []
    assert pool.get("s1", "登山杖") == ["登山杖-P1"]


def test_scheme_version_and_snapshot_survive_process_restart(tmp_path):
    plan = ShoppingPlan(
        goal="周末露营", core_use="露营", stage="search", tasks=["露营灯"], must_buy=["露营灯"],
    )
    first = SchemeStateRegistry(tmp_path).update("s1", plan)
    restored = SchemeStateRegistry(tmp_path)
    previous = restored.get("s1")
    assert previous is not None
    assert previous.scheme.state_version == first.scheme.state_version
    second = restored.update("s1", ShoppingPlan(
        goal="周末露营", core_use="露营", stage="search", budget_major=500,
        tasks=["露营灯"], must_buy=["露营灯"],
    ))
    assert second.scheme.state_version == 2
    assert second.mutation.budget_change == {"from": None, "to": 500}


async def test_main_agent_plan_tool_publishes_full_versioned_search_request():
    bus = TradeEventBus()
    queue = bus.subscribe("s1")
    states = SchemeStateRegistry()
    tool = build_update_shopping_plan_tool(bus, states, CandidatePool())
    token = ShoppingContext.set(ShoppingContextSnapshot(
        shopping_session_id="s1", buyer_id="b1", locale="zh-CN", currency="CNY",
    ))
    try:
        await tool(
            goal="周末露营", core_use="露营", stage="search", people=2,
            tasks=["露营灯"], must_buy=["露营灯"], preferences=["轻便"],
        )
    finally:
        ShoppingContext.reset(token)

    event = queue.get_nowait()
    assert event.type == "plan.update"
    assert event.payload["shopping_plan"]["state_version"] == 1
    assert event.payload["mutation"]["added_slots"] == ["露营灯"]
    assert event.payload["search_request"]["scheme"]["core_use"] == "露营"

class _RecordingSearch:
    def __init__(self):
        self.specs = []

    async def execute(self, spec):
        self.specs.append(spec)
        ids = list(spec.candidate_ids or ["P1", "P2", "P3"])
        hits = [{"product_id": product_id, "title": product_id} for product_id in ids[:3]]
        return {
            "hits": hits,
            "candidate_ids": ids,
            "total_candidates": len(ids),
            "recall_strategy": "recording",
            "rerank_applied": False,
        }


async def _run_product_search(tool, demands, task):
    token = ShoppingContext.set(ShoppingContextSnapshot(
        shopping_session_id="s1", buyer_id="b1", locale="zh-CN",
        currency="CNY", raw_query=demands,
    ))
    try:
        return await tool(
            normalized_query=task, category=None, ship_to=None, top_k=3,
            price_max_major=None, target_currency="CNY", shopping_task=task,
        )
    finally:
        ShoppingContext.reset(token)


async def test_product_search_executes_three_incremental_paths():
    states = SchemeStateRegistry()
    pool = CandidatePool()
    usecase = _RecordingSearch()
    bus = TradeEventBus()
    from app.application.tools.product_search_tool import build_product_search_tool
    tool = build_product_search_tool(usecase, bus, states, pool)

    states.update("s1", ShoppingPlan(
        goal="周末露营", core_use="露营", stage="search",
        tasks=["露营灯"], must_buy=["露营灯"], preferences=["轻便"],
    ))
    pool.put("s1", "露营灯", ["P1", "P2", "P3"], result_count=3)
    states.update("s1", ShoppingPlan(
        goal="周末露营", core_use="露营", stage="search", budget_major=300,
        tasks=["露营灯"], must_buy=["露营灯"], preferences=["轻便"],
    ))
    await _run_product_search(tool, "预算改成300元，还是露营灯", "露营灯")
    assert usecase.specs[-1].candidate_ids == ["P1", "P2", "P3"]

    states.update("s1", ShoppingPlan(
        goal="周末露营", core_use="露营", stage="search",
        tasks=["露营灯", "防潮垫"], must_buy=["露营灯", "防潮垫"], preferences=["轻便"],
    ))
    await _run_product_search(tool, "新增防潮垫", "防潮垫")
    assert usecase.specs[-1].candidate_ids is None

    states.update("s1", ShoppingPlan(
        goal="海边旅行", core_use="海边拍照", stage="search",
        tasks=["手机防水袋"], must_buy=["手机防水袋"], preferences=["轻便"],
    ))
    result = await _run_product_search(tool, "改成海边拍照，只买手机防水袋", "手机防水袋")
    assert usecase.specs[-1].candidate_ids is None
    assert '"retrieval_action": "recall_all"' in result.content[0].text


def test_python_search_catalog_matches_public_demo_catalog():
    from app.infrastructure.persistence.seed_products import build_seed_products
    products = build_seed_products()
    products_by_id = {product.product_id: product for product in products}
    assert len(products) > 480
    assert any("手机防水袋" in product.title for product in products)
    assert sum("泳镜" in product.title for product in products) >= 3
    assert "P2001" in products_by_id and "P2155" in products_by_id
    assert "待产包" in products_by_id["P2001"].description
    assert "不代表所有人必须购买" in products_by_id["P2001"].description

async def test_one_user_turn_commits_only_one_plan_version():
    bus = TradeEventBus()
    states = SchemeStateRegistry()
    tool = build_update_shopping_plan_tool(bus, states, CandidatePool())
    token = ShoppingContext.set(ShoppingContextSnapshot(
        shopping_session_id="s1", buyer_id="b1", locale="zh-CN",
        currency="CNY", raw_query="第一次露营需要什么",
    ))
    try:
        first = await tool(goal="第一次露营", stage="explore", tasks=["露营灯"])
        second = await tool(goal="第一次露营", stage="explore", tasks=["露营灯", "防潮垫"])
    finally:
        ShoppingContext.reset(token)
    assert first.content[0].text != second.content[0].text
    assert "不得再次更新" in second.content[0].text
    assert states.get("s1").scheme.state_version == 1
