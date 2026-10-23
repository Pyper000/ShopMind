from app.application.agents.orchestrator import _partial_checklist


def test_partial_checklist_exposes_only_complete_task_names():
    preview = _partial_checklist(
        '{"goal":"周末海边","stage":"explore","tasks":["泳衣","泳镜","防晒霜","遮阳帽"'
    )

    assert preview == {
        "goal": "周末海边",
        "stage": "explore",
        "tasks": ["泳衣", "泳镜", "防晒霜", "遮阳帽"],
        "optional": [],
        "prep": [],
    }
    assert _partial_checklist('{"tasks":["泳衣","泳镜"]') is None


def test_partial_checklist_reads_status_buckets_when_task_list_is_empty():
    preview = _partial_checklist(
        '{"tasks":[],"must_buy":["泳衣","泳镜","防晒霜"'
    )

    assert preview is not None
    assert preview["tasks"] == ["泳衣", "泳镜", "防晒霜"]


async def test_empty_plan_is_rejected_instead_of_becoming_a_committed_checklist():
    from agentscope.message import ToolResultState
    from app.application.tools.shopping_plan_tool import build_update_shopping_plan_tool
    from app.infrastructure.eventbus import TradeEventBus

    bus = TradeEventBus()
    events = bus.subscribe("empty-plan")
    result = await build_update_shopping_plan_tool(bus)(goal="周末海边", tasks=[])

    assert result.state == ToolResultState.ERROR
    assert "购物清单不能为空" in result.content[0].text
    assert events.empty()


async def test_main_agent_forces_structured_plan_submission_on_first_model_call():
    from app.application.agents.main_agent import RequirePlanOnFirstCall

    class State:
        cur_iter = 0

    class Agent:
        state = State()

    choices = []

    async def next_handler(**kwargs):
        choices.append(kwargs["tool_choice"].mode)
        yield "model-event"

    events = [event async for event in RequirePlanOnFirstCall().on_reasoning(
        Agent(), {"tool_choice": None}, next_handler,
    )]

    assert events == ["model-event"]
    assert choices == ["update_shopping_plan_tool"]


async def test_plan_tool_adds_status_only_items_to_visible_checklist():
    import json

    from app.application.tools.shopping_plan_tool import build_update_shopping_plan_tool
    from app.infrastructure.eventbus import TradeEventBus

    tool = build_update_shopping_plan_tool(TradeEventBus())
    response = await tool(
        goal="周末海边", stage="explore", tasks=[],
        must_buy=["泳衣", "泳镜"], optional=["防晒霜"], unknowns=["拖鞋"],
    )
    plan = json.loads(response.content[0].text)

    assert plan["tasks"] == ["泳衣", "泳镜", "拖鞋"]
    assert plan["optional"] == ["防晒霜"]
