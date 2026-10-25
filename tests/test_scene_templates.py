import json
from types import SimpleNamespace

import pytest

from app.application.agents.orchestrator import MainAgentOrchestrator, SubmitIntentInput
from app.application.agents.scheme_state import CandidatePool, SchemeStateRegistry
from app.application.tools.product_search_tool import build_product_search_tool
from app.application.usecases.catalog_search import CatalogSearchUseCase
from app.infrastructure.context import ShoppingContext, ShoppingContextSnapshot
from app.infrastructure.eventbus import TradeEventBus
from app.infrastructure.persistence.in_memory_repositories import InMemoryProductRepository
from app.infrastructure.persistence.scene_template_store import SceneTemplateStore


def test_beach_template_is_specific_and_keeps_ownership_unconfirmed():
    store = SceneTemplateStore()
    scene = store.match("周末去海边两天，帮我准备购物清单")
    assert scene is not None

    plan = store.build_plan("周末去海边两天，帮我准备购物清单", scene)
    assert plan.template_id == "beach_weekend"
    assert plan.template_version == 2
    assert {"泳衣", "防晒霜", "毛巾"}.issubset(plan.tasks)
    assert {"泳镜", "饮用水", "创可贴"}.issubset(plan.optional)
    assert set(plan.tasks) == set(plan.unknowns)
    assert plan.must_buy == []
    assert any("个人常用药" in reminder for reminder in plan.prep)
    assert [section["title"] for section in plan.checklist_sections] == ["换洗衣物", "游泳与海边活动", "安全与行前核对"]
    shirts = next(item for item in plan.checklist_sections[0]["required"] if item["name"] == "T恤")
    assert shirts["quantity"] == "3件"


def test_first_cat_has_only_required_and_optional_categories_with_reference():
    store = SceneTemplateStore()
    scene = store.match("第一次养猫，不知道要准备什么")
    assert scene is not None
    plan = store.build_plan("第一次养猫，不知道要准备什么", scene)
    assert [section["title"] for section in plan.checklist_sections] == ["吃喝", "如厕", "安全与外出", "休息与抓挠", "护理与健康记录"]
    assert all("suggested" not in section and "建议" not in section for section in plan.checklist_sections)
    assert all("required" in section and "optional" in section for section in plan.checklist_sections)
    assert any(section.get("reference", {}).get("url", "").startswith("https://www.cats.org.uk/") for section in plan.checklist_sections)


@pytest.mark.asyncio
async def test_adult_cat_paraphrase_matches_existing_template_without_exact_keyword():
    embedder = _CatConceptEmbedder()
    store = SceneTemplateStore(embedder=embedder)
    query = "我第一次养一只健康成年猫"
    assert store.match(query) is None

    scene = await store.match_semantic(query)

    assert scene is not None and scene["id"] == "first_cat_home"
    assert embedder.batch_calls == 1
    plan = store.build_plan(query, scene)
    assert len(plan.checklist_sections) == 5
    assert {"猫粮", "猫砂", "猫咪航空箱/外出箱"}.issubset(plan.tasks)
    assert store.match("我不打算养一只健康成年猫") is None


class _CatConceptEmbedder:
    def __init__(self):
        self.batch_calls = 0

    async def embed_batch(self, texts):
        self.batch_calls += 1
        return [self._vector(text) for text in texts]

    async def embed(self, text):
        return self._vector(text)

    @staticmethod
    def _vector(text):
        return [1.0, 0.0] if any(term in text for term in ("猫", "宠物", "毛孩子")) else [0.0, 1.0]


class _CatBiasedEmbedder:
    """Simulate a vector model that incorrectly scores a dog query as a cat scene."""
    async def embed_batch(self, texts):
        return [[1.0, 0.0] if "猫" in text else [0.0, 1.0] for text in texts]

    async def embed(self, text):
        return [1.0, 0.0]


@pytest.mark.asyncio
async def test_semantic_embedding_matches_cat_synonym_not_in_template_terms():
    store = SceneTemplateStore(embedder=_CatConceptEmbedder())

    scene = await store.match_semantic("准备迎接毛孩子入住")

    assert scene is not None and scene["id"] == "first_cat_home"


@pytest.mark.asyncio
async def test_semantic_embedding_does_not_route_dog_task_to_cat_template():
    store = SceneTemplateStore(embedder=_CatBiasedEmbedder())

    scene = await store.match_semantic("我第一次养一只健康成年狗，想准备狗到家物品")

    assert scene is None


@pytest.mark.asyncio
async def test_cat_intent_fallback_survives_embedding_provider_failure():
    class BrokenEmbedder:
        async def embed_batch(self, texts):
            return []

        async def embed(self, text):
            raise RuntimeError("embedding unavailable")

    scene = await SceneTemplateStore(embedder=BrokenEmbedder()).match_semantic(
        "我第一次养一只健康成年猫"
    )

    assert scene is not None and scene["id"] == "first_cat_home"


@pytest.mark.parametrize(("query", "template_id", "anchors"), [
    ("开学了，帮我准备采购清单", "school_start", {"学习文具", "书包", "台灯"}),
    ("第一次租房入住，帮我准备清单", "rental_move_in", {"床品套件（床单/被套/枕套）", "地面清洁工具（扫把/拖把）", "清洁布", "衣架", "烧水壶"}),
    ("周末出行两天，帮我准备购物清单", "general_travel", {"身份证件", "手机与充电器", "证件收纳包"}),
])
def test_core_scene_templates_are_concrete_and_unconfirmed(query, template_id, anchors):
    store = SceneTemplateStore()
    scene = store.match(query)
    assert scene is not None and scene["id"] == template_id
    plan = store.build_plan(query, scene)
    assert anchors.issubset(set(plan.tasks))
    assert set(plan.tasks) == set(plan.unknowns)
    assert plan.must_buy == []
    assert plan.optional
    if template_id == "rental_move_in":
        items = {item["name"]: item for section in plan.checklist_sections
                 for item in [*section["required"], *section["optional"]]}
        assert "基础清洁用品" not in items and "收纳用品" not in items
        assert items["床品套件（床单/被套/枕套）"]["quantity"] == "1套"
        assert items["地面清洁工具（扫把/拖把）"]["quantity"] == "按房间面积备1套"
        assert items["衣架"]["quantity"] == "先备5-10个"
        assert any("租赁合同" in reminder for reminder in plan.prep)
    if template_id == "general_travel":
        assert plan.goal == "出行准备（2天）"
        items = {item["name"]: item for section in plan.checklist_sections
                 for item in [*section["required"], *section["optional"]]}
        assert items["身份证件"]["purchasable"] is False


def test_date_range_is_not_mistaken_for_trip_duration():
    store = SceneTemplateStore()
    scene = store.match("2026年10月28日至30日去上海旅行")
    assert scene is not None and scene["id"] == "general_travel"
    plan = store.build_plan("2026年10月28日至30日去上海旅行", scene)
    assert plan.goal == "出行准备"


def test_first_time_birth_query_builds_detailed_three_person_checklist():
    store = SceneTemplateStore()
    query = "我第一次生产，想准备待产包"
    scene = store.match(query)
    assert scene is not None and scene["id"] == "maternity_hospital_bag"

    plan = store.build_plan(query, scene)

    assert [section["title"] for section in plan.checklist_sections] == ["孕妇", "宝宝", "陪护人"]
    mother = plan.checklist_sections[0]
    assert any(item["name"] == "内裤" and item.get("quantity") for item in mother["required"])
    assert {"待产宽松舒适衣物", "睡衣/睡袍", "浴袍/宽松开衫", "拖鞋/便鞋", "换洗衣物"}.issubset(
        {item["name"] for item in mother["required"]}
    )
    assert any(item["name"] == "睡衣/睡袍" and "NHS 未规定固定数量" in item["note"] for item in mother["required"])
    baby = plan.checklist_sections[1]
    assert any(item["name"] == "新生儿连体衣/包屁衣" and item.get("quantity") for item in baby["required"])
    companion = plan.checklist_sections[2]
    assert any(item["name"] == "陪护人证件/入院凭证" and "按本院规定" in item["condition"] for item in companion["optional"])
    assert any(item.get("condition") for item in mother["optional"])
    assert "NHS" in mother["reference"]["title"]
    assert "以本院清单" in mother["reference"]["note"]
    assert not {"待产包", "孕妇用品", "宝宝用品"}.intersection(plan.tasks)


class _Preferences:
    async def list_by_buyer(self, buyer_id):
        return []


class _Sessions:
    def __init__(self):
        self.agent = SimpleNamespace(
            name="commerce_concierge",
            state=SimpleNamespace(summary="", context=[]),
        )
        self.persisted = False

    async def get_or_create(self, session_id):
        return self.agent

    async def persist(self, session_id):
        self.persisted = True


@pytest.mark.asyncio
async def test_known_beach_first_turn_returns_checklist_without_llm(tmp_path):
    bus = TradeEventBus()
    sessions = _Sessions()
    schemes = SchemeStateRegistry(tmp_path)
    orchestrator = MainAgentOrchestrator(
        sessions, bus, _Preferences(), scheme_states=schemes,
        scene_templates=SceneTemplateStore(),
    )
    events = bus.subscribe("beach-1")

    result = await orchestrator.handle_intent(SubmitIntentInput(
        shopping_session_id="beach-1", buyer_id="buyer-1", locale="zh-CN",
        currency="CNY", raw_query="周末去海边两天，帮我准备购物清单",
    ))

    emitted = []
    while not events.empty():
        emitted.append(events.get_nowait())
    saved = schemes.get("beach-1")
    assert result.final_text
    assert saved is not None and saved.scheme.template_id == "beach_weekend"
    assert any(event.type == "plan.update" for event in emitted)
    assert any(event.type == "final.result" for event in emitted)
    assert not any(event.type == "model.call" for event in emitted)
    assert len(sessions.agent.state.context) == 2
    assert sessions.persisted
    bus.unsubscribe("beach-1", events)


@pytest.mark.asyncio
async def test_adult_cat_paraphrase_emits_modular_template_from_fresh_session(tmp_path):
    bus = TradeEventBus()
    sessions = _Sessions()
    schemes = SchemeStateRegistry(tmp_path)
    orchestrator = MainAgentOrchestrator(
        sessions, bus, _Preferences(), scheme_states=schemes,
        scene_templates=SceneTemplateStore(),
    )
    events = bus.subscribe("adult-cat-1")

    await orchestrator.handle_intent(SubmitIntentInput(
        shopping_session_id="adult-cat-1", buyer_id="buyer-1", locale="zh-CN",
        currency="CNY", raw_query="我第一次养一只健康成年猫",
    ))

    emitted = []
    while not events.empty():
        emitted.append(events.get_nowait())
    saved = schemes.get("adult-cat-1")
    plan_events = [event for event in emitted if event.type == "plan.update"]
    assert saved is not None and saved.scheme.template_id == "first_cat_home"
    assert len(saved.scheme.checklist_sections) == 5
    assert plan_events[0].payload["scene_match_mode"] == "semantic"
    assert not any(event.type == "model.call" for event in emitted)
    bus.unsubscribe("adult-cat-1", events)


@pytest.mark.asyncio
async def test_known_new_scene_replaces_previous_scene_in_same_session(tmp_path):
    bus = TradeEventBus()
    sessions = _Sessions()
    schemes = SchemeStateRegistry(tmp_path)
    orchestrator = MainAgentOrchestrator(
        sessions, bus, _Preferences(), scheme_states=schemes,
        scene_templates=SceneTemplateStore(),
    )
    for query in ("周末去海边两天，帮我准备购物清单", "待产住院包要准备什么"):
        await orchestrator.handle_intent(SubmitIntentInput(
            shopping_session_id="switch-scene", buyer_id="buyer-1", locale="zh-CN",
            currency="CNY", raw_query=query,
        ))
    saved = schemes.get("switch-scene")
    assert saved is not None and saved.scheme.template_id == "maternity_hospital_bag"
    assert [section["title"] for section in saved.scheme.checklist_sections] == ["孕妇", "宝宝", "陪护人"]


@pytest.mark.asyncio
async def test_checklist_selection_commits_then_dispatches_search_without_llm(tmp_path):
    bus = TradeEventBus()
    sessions = _Sessions()
    schemes = SchemeStateRegistry(tmp_path)
    templates = SceneTemplateStore()
    scene = templates.match("周末去海边两天")
    assert scene is not None
    initial = schemes.update("beach-2", templates.build_plan("周末去海边两天", scene))
    remaining = [item for item in initial.scheme.tasks if item not in {"泳衣", "泳镜"}]
    search_calls = []

    async def search():
        search_calls.append(True)
        return [{"slot": "泳衣", "result": {"hits": [{"product_id": "SWIM-1"}]}}]

    orchestrator = MainAgentOrchestrator(
        sessions, bus, _Preferences(), scheme_states=schemes,
        scene_templates=templates, candidate_pool=CandidatePool(),
        confirmed_search=search,
    )
    events = bus.subscribe("beach-2")
    result = await orchestrator.handle_intent(SubmitIntentInput(
        shopping_session_id="beach-2", buyer_id="buyer-1", locale="zh-CN",
        currency="CNY", raw_query="清单已逐项确认。已有：泳镜；需要购买：泳衣。",
        checklist_selection={
            "have": ["泳镜"], "must_buy": ["泳衣"], "optional_buy": [],
            "deferred": [], "unknown": remaining,
            "budget_major": 300, "priority": "lightweight",
        },
    ))

    emitted = []
    while not events.empty():
        emitted.append(events.get_nowait())
    current = schemes.get("beach-2")
    assert current is not None
    assert current.scheme.must_buy == ["泳衣"]
    assert current.scheme.reusable == ["泳镜"]
    assert current.scheme.budget_major == 300
    assert current.mutation.added_slots == ["泳衣"]
    assert search_calls == [True]
    assert "泳衣共返回 1 个候选" in result.final_text
    assert any(event.type == "plan.update" for event in emitted)
    assert any(event.type == "final.result" for event in emitted)
    assert not any(event.type == "model.call" for event in emitted)
    assert len(sessions.agent.state.context) == 2
    bus.unsubscribe("beach-2", events)


@pytest.mark.asyncio
async def test_beach_catalog_is_relevant_and_reports_real_gaps():
    bus = TradeEventBus()
    tool = build_product_search_tool(CatalogSearchUseCase(InMemoryProductRepository()), bus)
    token = ShoppingContext.set(ShoppingContextSnapshot(
        shopping_session_id="catalog-test", buyer_id="buyer-1", locale="zh-CN",
        currency="CNY", raw_query="购物清单商品检索",
    ))
    try:
        goggles = await tool(
            normalized_query="泳镜", shopping_task="泳镜", top_k=3,
            target_currency="CNY",
        )
        goggles_data = json.loads(goggles.content[0].text)
        assert 1 <= len(goggles_data["hits"]) <= 3
        assert all("泳镜" in hit["title"] or "泳镜" in " ".join(hit["highlights"])
                   for hit in goggles_data["hits"])

        gap = await tool(
            normalized_query="海滩冲浪板", shopping_task="海滩冲浪板", top_k=3,
            target_currency="CNY",
        )
        gap_data = json.loads(gap.content[0].text)
        assert gap_data["hits"] == []
        assert gap_data["catalog_gap"]["status"] == "not_in_demo_catalog"
        assert "待补充/待入驻" in gap_data["catalog_gap"]["message"]
    finally:
        ShoppingContext.reset(token)
