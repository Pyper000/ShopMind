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
