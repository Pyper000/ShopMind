# -*- coding: utf-8 -*-
"""SearchAgent

跨境商品检索专家。基于 AgentScope 2.0 Agent：
    工具集：product_search_tool（embedding+rerank 二阶段召回）
          category_insight_tool（品类洞察 RAG，选购常识）
          web_search_tool（可选，跨境政策兜底）

对外通过 task_dispatch 工具被 MainAgent 调度（SubAgent as Tool 模式）。
每次调度新建独立实例：2.0 的对话上下文内建于 AgentState，独立实例天然上下文隔离。
"""
from __future__ import annotations

import json

from agentscope.agent import Agent, ReActConfig
from agentscope.message import TextBlock
from agentscope.rag import KnowledgeBase
from agentscope.tool import FunctionTool, Toolkit

from app.application.agents.context_policy import build_context_config
from app.application.agents.scheme_state import CandidatePool, SchemeStateRegistry
from app.application.prompts.loader import load_prompts
from app.application.tools.category_insight_tool import build_category_insight_tool
from app.application.tools.product_search_tool import build_product_search_tool
from app.application.tools.web_search_tool import build_web_search_tool
from app.application.usecases.catalog_search import CatalogSearchUseCase
from app.infrastructure.eventbus import TradeEventBus
from app.infrastructure.llm import create_chat_model
from app.infrastructure.throttle import GatewayThrottle
from app.infrastructure.resilience import (
    CircuitBreakerRegistry,
    ToolResilienceMiddleware,
)
from app.infrastructure.settings import Settings
from app.infrastructure.tracing import build_agent_middlewares


class SearchAgentFactory:
    def __init__(
        self,
        settings: Settings,
        catalog_search: CatalogSearchUseCase,
        bus: TradeEventBus,
        knowledge_base: KnowledgeBase | None,
        circuit_registry: CircuitBreakerRegistry,
        throttle: GatewayThrottle,
        scheme_states: SchemeStateRegistry | None = None,
        candidate_pool: CandidatePool | None = None,
    ) -> None:
        self._settings = settings
        self._catalog_search = catalog_search
        self._bus = bus
        self._knowledge_base = knowledge_base
        self._circuit_registry = circuit_registry
        # 闸门由组装根下发，三个工厂必须共用同一个，否则各限一份等于没限
        self._throttle = throttle
        self._scheme_states = scheme_states
        self._candidate_pool = candidate_pool

    async def run_fast(self, session_id: str, shopping_task: str) -> dict:
        """Run the deterministic catalog path for a committed atomic slot.

        MainAgent has already classified the slot and ``task_dispatch`` has
        already selected SearchAgent.  For this common MVP path there is no
        value in spending two more LLM turns to paraphrase the slot and then
        paraphrase the catalog JSON.  The SearchAgent still owns the path:
        ``product_search_tool`` receives the complete versioned SearchRequest,
        chooses recall vs cached rerank, and emits the normal retrieval trace.
        A model-backed ``build()`` remains available for nuanced “how to choose”
        requests that need category knowledge or a prose explanation.
        """
        request = self._scheme_states.get(session_id) if self._scheme_states else None
        if request is None:
            return {"hits": [], "notes": "缺少版本化方案，未执行商品召回。"}
        plan = request.scheme
        query_parts = [shopping_task, *plan.preferences]
        tool = build_product_search_tool(
            self._catalog_search, self._bus, self._scheme_states, self._candidate_pool,
        )
        chunk = await tool(
            normalized_query=" ".join(part for part in query_parts if part),
            category=None,
            ship_to=plan.destination,
            top_k=3,
            price_max_major=plan.budget_major,
            target_currency=plan.currency,
            shopping_task=shopping_task,
        )
        text = "\n".join(
            block.text for block in chunk.content if isinstance(block, TextBlock) and block.text
        )
        if text.startswith("[error]"):
            return {"hits": [], "notes": text}
        try:
            result = json.loads(text)
        except (TypeError, ValueError):
            return {"hits": [], "notes": "商品检索返回格式异常，未把非结构化内容当作商品事实。"}
        hits = []
        for hit in result.get("hits", []):
            highlights = hit.get("highlights") or []
            fact = str(highlights[0]) if highlights else "商品卡命中该任务"
            hits.append({
                "product_id": hit.get("product_id", ""),
                "reason": f"{shopping_task}：{fact}"[:30],
                "tradeoff": "价格、库存与规格以商品卡为准"[:30],
            })
        action = result.get("retrieval_action", "recall_local")
        explanation = result.get("retrieval_explanation", "按当前方案执行检索")
        notes = f"{explanation} 检索策略：{action}；返回 {len(hits)} 个候选。"
        return {"hits": hits, "notes": notes[:120]}

    def _resilience(self) -> list:
        return [ToolResilienceMiddleware(self._circuit_registry, self._bus)]

    def build_tools(self) -> list[FunctionTool]:
        """SearchAgent 独占的商品检索工具集（均带超时+熔断保护）。

        web_search_tool 按"有 TAVILY_API_KEY 才注册"设计，未配置时 Agent 看不到它。
        """
        tools = [
            FunctionTool(
                build_product_search_tool(
                    self._catalog_search, self._bus, self._scheme_states, self._candidate_pool,
                ),
                is_read_only=True,
                middlewares=self._resilience(),
            ),
        ]
        if self._knowledge_base is not None:
            tools.append(
                FunctionTool(
                    build_category_insight_tool(self._knowledge_base, self._bus),
                    is_read_only=True,
                    middlewares=self._resilience(),
                ),
            )
        if self._settings.tavily_api_key:
            tools.append(
                FunctionTool(
                    build_web_search_tool(self._settings, self._bus),
                    is_read_only=True,
                    middlewares=self._resilience(),
                ),
            )
        return tools

    def build(self) -> Agent:
        prompts = load_prompts()["sub_agents"]["search"]
        return Agent(
            name=prompts["name"],
            system_prompt=prompts["system_prompt"],
            model=create_chat_model(self._settings, throttle=self._throttle, bus=self._bus),
            toolkit=Toolkit(tools=list(self.build_tools())),
            middlewares=build_agent_middlewares(self._settings),
            context_config=build_context_config(
                self._settings.context_size,
                self._settings.tool_result_limit,
            ),
            # Enough for optional category insight, one product retrieval, and a final answer.
            # One retrieval call plus structured output; keep room for one
            # repair step, not the SDK's five extra grace iterations.
            react_config=ReActConfig(max_iters=3, structured_output_grace_iters=2),
        )
