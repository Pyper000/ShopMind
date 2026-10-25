# -*- coding: utf-8 -*-
"""工具层与事件总线单测：工具直调（绕过 LLM）+ EventBus 订阅。"""
import asyncio
import json

import pytest

from app.application.tools.order_tools import build_create_order_tool
from app.application.tools.product_search_tool import build_product_search_tool
from app.application.tools.shopping_plan_tool import _apply_checklist_selection, build_update_shopping_plan_tool
from app.application.usecases.catalog_search import CatalogSearchUseCase
from app.application.usecases.order_usecases import PlaceOrderUseCase
from app.infrastructure.context import ShoppingContext, ShoppingContextSnapshot
from app.infrastructure.eventbus import TradeEventBus
from app.infrastructure.persistence.in_memory_repositories import (
    InMemoryOrderRepository,
    InMemoryProductRepository,
)

ADDRESS = {
    "recipient_name": "张三",
    "country": "CN",
    "state": "浙江",
    "city": "杭州",
    "address_line": "西湖区某路 1 号",
    "postal_code": "310000",
    "phone": "13800000000",
}


class TestTradeEventBus:
    async def test_publish_routes_to_subscriber(self):
        bus = TradeEventBus()
        queue = bus.subscribe("s1")
        other = bus.subscribe("s2")
        bus.publish("s1", "final.result", {"text": "done"})

        event = await asyncio.wait_for(queue.get(), timeout=1)
        assert event.type == "final.result"
        assert other.empty(), "事件不能串台到其他会话"

    def test_reject_unknown_event_type(self):
        bus = TradeEventBus()
        with pytest.raises(ValueError, match="未知事件类型"):
            bus.publish("s1", "not.a.type", {})


class TestToolsDirectInvoke:
    async def test_shopping_plan_tool_publishes_structured_plan(self):
        bus = TradeEventBus()
        queue = bus.subscribe("s1")
        tool = build_update_shopping_plan_tool(bus)
        token = ShoppingContext.set(
            ShoppingContextSnapshot(shopping_session_id="s1", buyer_id="b1", locale="zh-CN", currency="CNY"),
        )
        try:
            response = await tool(
                goal="准备美国露营",
                stage="search",
                destination="us",
                budget_major="500",
                preferences=["轻便", "耐用"],
                tasks=["露营灯", "防潮垫", "户外炊具"],
            )
        finally:
            ShoppingContext.reset(token)

        payload = json.loads(response.content[0].text)
        assert payload["destination"] == "US"
        assert payload["budget_major"] == 500
        event = await queue.get()
        assert event.type == "plan.update"
        assert event.payload["shopping_plan"]["tasks"] == ["露营灯", "防潮垫", "户外炊具"]

    async def test_new_open_scene_is_grouped_when_model_omits_sections(self):
        bus = TradeEventBus()
        tool = build_update_shopping_plan_tool(bus)
        token = ShoppingContext.set(ShoppingContextSnapshot(
            shopping_session_id="open-scene", buyer_id="b1", locale="zh-CN",
            currency="CNY", raw_query="我准备第一次去山里露营",
        ))
        try:
            response = await tool(
                goal="第一次山地露营准备",
                stage="explore",
                tasks=["帐篷", "睡袋", "头灯", "饮用水", "能量棒", "身份证", "湿巾"],
                optional=["能量棒"],
            )
        finally:
            ShoppingContext.reset(token)

        payload = json.loads(response.content[0].text)
        sections = payload["checklist_sections"]
        assert len(sections) >= 3
        assert {"居住与休息", "吃喝与补给", "证件与安全", "清洁与卫生"}.issubset(
            {section["title"] for section in sections}
        )
        section_items = {
            item["name"]: bucket
            for section in sections
            for bucket in ("required", "optional")
            for item in section[bucket]
        }
        assert set(payload["tasks"]) == set(section_items)
        assert section_items["能量棒"] == "optional"
        assert payload["optional"] == ["能量棒"]

    async def test_non_purchasable_documents_are_flagged_but_holders_are_products(self):
        tool = build_update_shopping_plan_tool(TradeEventBus())
        token = ShoppingContext.set(ShoppingContextSnapshot(
            shopping_session_id="document-items", buyer_id="b1", locale="zh-CN",
            currency="CNY", raw_query="准备出行要带哪些东西",
        ))
        try:
            response = await tool(
                goal="短途出行准备", stage="explore",
                tasks=["身份证", "护照", "证件收纳包", "护照夹"],
            )
        finally:
            ShoppingContext.reset(token)

        payload = json.loads(response.content[0].text)
        items = {item["name"]: item for section in payload["checklist_sections"]
                 for bucket in ("required", "optional") for item in section[bucket]}
        assert items["身份证"]["purchasable"] is False
        assert items["护照"]["purchasable"] is False
        assert items["证件收纳包"]["purchasable"] is True
        assert items["护照夹"]["purchasable"] is True

    def test_need_prepare_records_documents_and_searches_only_products(self):
        from app.domain.session.shopping_plan import ShoppingPlan

        plan = ShoppingPlan(
            goal="出行准备", stage="explore",
            tasks=["身份证件", "手机与充电器", "证件收纳包"],
            optional=["证件收纳包"],
            unknowns=["身份证件", "手机与充电器", "证件收纳包"],
            checklist_sections=[
                {"id": "documents", "title": "证件与电子设备",
                 "required": [
                     {"name": "身份证件", "purchasable": False},
                     {"name": "手机与充电器", "purchasable": True},
                 ],
                 "optional": [{"name": "证件收纳包", "purchasable": True}]},
            ],
        )
        updated = _apply_checklist_selection(plan, {
            "prepare": ["身份证件", "手机与充电器", "证件收纳包"],
        })
        assert updated.prepared == ["身份证件"]
        assert updated.must_buy == ["手机与充电器"]
        assert updated.optional == ["证件收纳包"]
        assert updated.stage == "search"

        documents_only = _apply_checklist_selection(plan, {"prepare": ["身份证件"]})
        assert documents_only.prepared == ["身份证件"]
        assert documents_only.must_buy == documents_only.optional == []
        assert documents_only.stage == "confirm"

    def test_non_purchasable_documents_cannot_enter_search_bucket(self):
        from app.domain.session.shopping_plan import ShoppingPlan

        plan = ShoppingPlan(
            goal="出行准备", tasks=["身份证件"], unknowns=["身份证件"],
            checklist_sections=[{"id": "docs", "title": "证件", "required": [
                {"name": "身份证件", "purchasable": False},
            ], "optional": []}],
        )
        with pytest.raises(ValueError, match="非商品只能标记为需要准备"):
            _apply_checklist_selection(plan, {"must_buy": ["身份证件"]})

    async def test_dog_fallback_groups_do_not_use_cat_only_titles(self):
        tool = build_update_shopping_plan_tool(TradeEventBus())
        token = ShoppingContext.set(ShoppingContextSnapshot(
            shopping_session_id="dog-open-scene", buyer_id="b1", locale="zh-CN",
            currency="CNY", raw_query="我第一次养一只健康成年狗",
        ))
        try:
            response = await tool(
                goal="第一次养狗准备",
                stage="explore",
                tasks=["狗粮", "食盆", "狗窝", "牵引绳", "项圈", "指甲剪", "尿垫", "玩具"],
            )
        finally:
            ShoppingContext.reset(token)

        payload = json.loads(response.content[0].text)
        titles = {section["title"] for section in payload["checklist_sections"]}
        assert "宠物安全与外出" in titles
        assert "宠物护理与健康" in titles
        assert not any("猫" in title for title in titles)
        assert set(payload["tasks"]) == {
            item["name"] for section in payload["checklist_sections"]
            for bucket in ("required", "optional") for item in section[bucket]
        }

    async def test_open_scene_over_six_model_modules_are_regrouped_with_item_details(self):
        tool = build_update_shopping_plan_tool(TradeEventBus())
        names = ["狗粮", "狗窝", "牵引绳", "尿垫", "指甲剪", "狗玩具", "身份证", "清洁抹布"]
        sections = [
            {"title": f"模型模块{i}", "required": [{"name": name, "quantity": "1个"}], "optional": []}
            for i, name in enumerate(names[:7], start=1)
        ]
        sections[0]["optional"] = [{"name": "反光背心", "condition": "夜间外出时"}]
        token = ShoppingContext.set(ShoppingContextSnapshot(
            shopping_session_id="over-six-open-scene", buyer_id="b1", locale="zh-CN",
            currency="CNY", raw_query="我第一次养一只健康成年狗",
        ))
        try:
            response = await tool(
                goal="第一次养狗准备",
                stage="explore",
                tasks=[*names[:7], "反光背心"],
                checklist_sections=sections,
                optional=["反光背心"],
            )
        finally:
            ShoppingContext.reset(token)

        payload = json.loads(response.content[0].text)
        grouped = payload["checklist_sections"]
        items = {
            item["name"]: item
            for section in grouped
            for bucket in ("required", "optional")
            for item in section[bucket]
        }
        assert 2 <= len(grouped) <= 6
        assert set(items) == set(payload["tasks"])
        assert items["狗粮"]["quantity"] == "1个"
        assert items["反光背心"]["condition"] == "夜间外出时"
        assert payload["optional"] == ["反光背心"]

    async def test_open_scene_sections_preserve_quantity_and_conditions(self):
        tool = build_update_shopping_plan_tool(TradeEventBus())
        token = ShoppingContext.set(ShoppingContextSnapshot(
            shopping_session_id="open-sections", buyer_id="b1", locale="zh-CN",
            currency="CNY", raw_query="帮我准备一次园艺体验",
        ))
        try:
            response = await tool(
                goal="家庭园艺体验",
                stage="explore",
                tasks=["园艺手套", "小铲子", "遮阳帽"],
                checklist_sections=[
                    {"title": "种植工具", "required": [
                        {"name": "园艺手套", "quantity": "1双"},
                        {"name": "小铲子", "quantity": "1把"},
                    ], "optional": []},
                    {"title": "户外防护", "required": [], "optional": [
                        {"name": "遮阳帽", "condition": "户外日照强时"},
                    ]},
                ],
                optional=["遮阳帽"],
            )
        finally:
            ShoppingContext.reset(token)

        payload = json.loads(response.content[0].text)
        items = {item["name"]: item for section in payload["checklist_sections"]
                 for bucket in ("required", "optional") for item in section[bucket]}
        assert len(payload["checklist_sections"]) == 2
        assert set(payload["tasks"]) == set(items)
        assert payload["optional"] == [
            item["name"] for section in payload["checklist_sections"] for item in section["optional"]
        ]
        assert items["园艺手套"]["quantity"] == "1双"
        assert items["遮阳帽"]["condition"] == "户外日照强时"

    async def test_generic_single_section_is_regrouped_for_open_scene(self):
        tool = build_update_shopping_plan_tool(TradeEventBus())
        tasks = ["床品套件", "地面清洁工具", "台灯", "垃圾袋"]
        token = ShoppingContext.set(ShoppingContextSnapshot(
            shopping_session_id="generic-section", buyer_id="b1", locale="zh-CN",
            currency="CNY", raw_query="第一次搬进自己的新公寓",
        ))
        try:
            response = await tool(
                goal="新公寓入住",
                stage="explore",
                tasks=tasks,
                checklist_sections=[{"title": "通用清单", "required": tasks, "optional": []}],
            )
        finally:
            ShoppingContext.reset(token)

        payload = json.loads(response.content[0].text)
        assert len(payload["checklist_sections"]) >= 2
        assert "通用清单" not in {section["title"] for section in payload["checklist_sections"]}

    async def test_shopping_plan_limits_scene_scope(self):
        tool = build_update_shopping_plan_tool(TradeEventBus())
        response = await tool(goal="准备露营", tasks=[str(n) for n in range(25)])
        assert response.content[0].text.startswith("[error] 单份清单最多包含 24 件具体物品")

    @pytest.mark.parametrize("slot", ["游泳装备", "防晒用品", "换洗衣物", "应急物品", "应急药品"])
    async def test_plan_rejects_umbrella_categories_without_committing(self, slot):
        bus = TradeEventBus()
        queue = bus.subscribe("s1")
        tool = build_update_shopping_plan_tool(bus)
        token = ShoppingContext.set(ShoppingContextSnapshot(
            shopping_session_id="s1", buyer_id="b1", locale="zh-CN", currency="CNY",
        ))
        try:
            response = await tool(goal="海边游泳", tasks=[slot])
        finally:
            ShoppingContext.reset(token)
        assert "清单项过于概括" in response.content[0].text
        assert queue.empty()

    async def test_plan_supports_atomic_items_with_independent_inventory(self):
        from app.application.agents.scheme_state import CandidatePool, SchemeStateRegistry
        states = SchemeStateRegistry()
        tool = build_update_shopping_plan_tool(TradeEventBus(), states, CandidatePool())
        token = ShoppingContext.set(ShoppingContextSnapshot(
            shopping_session_id="s1", buyer_id="b1", locale="zh-CN", currency="CNY",
        ))
        try:
            response = await tool(
                goal="海边游泳", tasks=["泳衣", "泳镜", "防晒霜", "毛巾", "拖鞋", "换洗上衣"],
                reusable=["泳镜"], must_buy=["泳衣"], unknowns=["毛巾"],
                optional=["创可贴"], prep=["自行核对个人常用药"],
            )
        finally:
            ShoppingContext.reset(token)
        payload = json.loads(response.content[0].text)
        assert len(payload["tasks"]) == 6
        assert payload["reusable"] == ["泳镜"]
        assert payload["must_buy"] == ["泳衣"]
        assert payload["unknowns"] == ["毛巾"]
        statuses = {item["name"]: item["status"] for item in payload["mutation"]["target_slots"]}
        assert statuses["泳镜"] == "have"
        assert statuses["泳衣"] == "must_buy"
        assert statuses["毛巾"] == "pending"

    async def test_product_search_tool(self):
        bus = TradeEventBus()
        queue = bus.subscribe("s1")
        tool = build_product_search_tool(CatalogSearchUseCase(InMemoryProductRepository()), bus)

        token = ShoppingContext.set(
            ShoppingContextSnapshot(shopping_session_id="s1", buyer_id="b1", locale="zh-CN", currency="CNY"),
        )
        try:
            response = await tool(normalized_query="旅行三件套 抗造")
        finally:
            ShoppingContext.reset(token)

        payload = json.loads(response.content[0].text)
        assert payload["hits"][0]["product_id"] == "P1001"
        # tool.invoke + tool.result 两条事件
        assert queue.qsize() == 2

    async def test_product_search_tool_accepts_numeric_string(self):
        """回归：模型（如 qwen3-max）会把数字参数传成字符串，工具必须接住并强转，
        而不是在 schema 校验层被拒收（实测 price_max_major="300" 曾导致检索全程失败）。"""
        bus = TradeEventBus()
        queue = bus.subscribe("s1")
        tool = build_product_search_tool(CatalogSearchUseCase(InMemoryProductRepository()), bus)

        token = ShoppingContext.set(
            ShoppingContextSnapshot(shopping_session_id="s1", buyer_id="b1", locale="zh-CN", currency="CNY"),
        )
        try:
            response = await tool(normalized_query="旅行三件套 抗造", price_max_major="300", top_k="3")
        finally:
            ShoppingContext.reset(token)

        payload = json.loads(response.content[0].text)
        # 字符串被强转为数字后进检索链路，预算硬约束生效：候选主价均不超过 300
        assert payload["hits"], "传字符串价格上限不应导致检索为空"
        for hit in payload["hits"]:
            assert hit["price_major"] <= 300
        # tool.invoke + tool.result 两条事件
        assert queue.qsize() == 2

    async def test_product_search_tool_rejects_bad_numeric_string(self):
        """非法数字字符串应返回 [error] 而不是抛异常。"""
        bus = TradeEventBus()
        bus.subscribe("s1")
        tool = build_product_search_tool(CatalogSearchUseCase(InMemoryProductRepository()), bus)

        token = ShoppingContext.set(
            ShoppingContextSnapshot(shopping_session_id="s1", buyer_id="b1", locale="zh-CN", currency="CNY"),
        )
        try:
            response = await tool(normalized_query="旅行三件套", price_max_major="不是数字")
        finally:
            ShoppingContext.reset(token)

        assert response.content[0].text.startswith("[error] price_max_major 非法")

    async def test_create_order_tool_and_error_path(self):
        bus = TradeEventBus()
        product_repo = InMemoryProductRepository()
        tool = build_create_order_tool(PlaceOrderUseCase(product_repo, InMemoryOrderRepository()), bus)

        # 买家身份由 ShoppingContext 注入，而非模型入参
        token = ShoppingContext.set(
            ShoppingContextSnapshot(shopping_session_id="s1", buyer_id="b1", locale="zh-CN", currency="CNY"),
        )
        try:
            ok = await tool(
                items=[{"product_id": "P1001", "sku_id": "P1001-S1", "quantity": 1}],
                shipping_address=ADDRESS,
            )
            snapshot = json.loads(ok.content[0].text)
            assert snapshot["status"] == "CONFIRMED"
            assert snapshot["order_id"].startswith("GBX-")
            assert snapshot["buyer_id"] == "b1"

            bad = await tool(
                items=[{"product_id": "P9999", "sku_id": "X", "quantity": 1}],
                shipping_address=ADDRESS,
            )
            assert bad.content[0].text.startswith("[error]")
        finally:
            ShoppingContext.reset(token)
