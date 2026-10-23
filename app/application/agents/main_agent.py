# -*- coding: utf-8 -*-
"""MainAgent（CommerceConcierge）

跨境电商超级框总调度。基于 AgentScope 2.0 Agent，工具集分四层：
    1. 全部业务工具（product_search / create_order / query_order / cancel_order / [web_search]）
       ——与子 Agent 持有同一批工具，简单任务主 Agent 直接单干；
    2. 内置 Task 计划四件套（TaskCreate / TaskUpdate / TaskList / TaskGet）
       ——2.0 官方计划管理，挂在 AgentState.tasks_context 上；
    3. task_dispatch——满足"可并行 / 上下文隔离 / 链深"任一条件时派发专家子 Agent；
    4. remember_preference_tool——长期记忆写路径（读路径由 orchestrator 注入 hint）。

每个 shopping_session_id 对应一个 MainAgent 实例，由 SessionRegistry 缓存；
AgentState 每轮落盘 DATA_DIR/sessions/，服务重启后恢复多轮对话；
子 Agent 则每次调度新建（上下文隔离）。
"""
from __future__ import annotations

import logging
import asyncio
import json
from typing import Optional

from agentscope.agent import Agent, ReActConfig
from agentscope.middleware import MiddlewareBase
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, Toolkit, ToolChoice

from app.application.agents.context_policy import build_context_config
from app.application.agents.permissions import allow_business_tools
from app.application.agents.search_agent import SearchAgentFactory
from app.application.agents.scheme_state import CandidatePool, SchemeStateRegistry
from app.application.agents.trade_agent import TradeAgentFactory
from app.application.harness.assertions import SequencingTracker
from app.application.harness.loop_detector import LoopDetector
from app.application.memory.preference_selector import PreferenceSelector
from app.application.prompts.loader import load_prompts
from app.application.tools.forget_preference_tool import build_forget_preference_tool
from app.application.tools.remember_preference_tool import build_remember_preference_tool
from app.application.tools.shopping_plan_tool import build_update_shopping_plan_tool
from app.application.tools.task_dispatch_tool import build_task_dispatch_tool
from app.domain.buyer.preference import PreferenceStore
from app.domain.session.ports.session_store import SessionStore
from app.infrastructure.eventbus import TradeEventBus
from app.infrastructure.harness_middleware import HarnessToolMiddleware
from app.infrastructure.llm import create_chat_model
from app.infrastructure.throttle import GatewayThrottle
from app.infrastructure.resilience import (
    CircuitBreakerRegistry,
    ToolResilienceMiddleware,
)
from app.infrastructure.settings import Settings
from app.infrastructure.tracing import build_agent_middlewares

logger = logging.getLogger(__name__)


def _first_json(text: str) -> dict:
    """Decode the leading JSON object, ignoring harness notices appended after it."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in tool output")
    value, _ = decoder.raw_decode(text, start)
    if not isinstance(value, dict):
        raise ValueError("tool output is not an object")
    return value


class RequirePlanOnFirstCall(MiddlewareBase):
    """Make the MainAgent submit its structured plan before prose on each turn."""

    async def on_reasoning(self, agent, input_kwargs, next_handler):
        if agent.state.cur_iter == 0 and input_kwargs.get("tool_choice") is None:
            async for event in next_handler(
                tool_choice=ToolChoice(mode="update_shopping_plan_tool"),
            ):
                yield event
            return
        async for event in next_handler(**input_kwargs):
            yield event


class MainAgentFactory:
    def __init__(
        self,
        settings: Settings,
        search_factory: SearchAgentFactory,
        trade_factory: TradeAgentFactory,
        bus: TradeEventBus,
        preference_store: PreferenceStore,
        circuit_registry: CircuitBreakerRegistry,
        throttle: GatewayThrottle,
        sequencing: Optional[SequencingTracker] = None,
        loop_detector: Optional[LoopDetector] = None,
        preference_selector: Optional[PreferenceSelector] = None,
        scheme_states: Optional[SchemeStateRegistry] = None,
        candidate_pool: Optional[CandidatePool] = None,
    ) -> None:
        self._settings = settings
        self._search_factory = search_factory
        self._trade_factory = trade_factory
        self._bus = bus
        self._preference_store = preference_store
        self._circuit_registry = circuit_registry
        self._throttle = throttle
        # 与 orchestrator 共用同一个 selector，保证主/子 Agent 的偏好选取口径一致
        self._preference_selector = preference_selector or PreferenceSelector()
        self._scheme_states = scheme_states
        self._candidate_pool = candidate_pool
        self._dispatch_tool: Optional[FunctionTool] = None
        # 护栏判定器按会话累积状态，须跨 Agent 实例共享（与熔断注册表同理）
        self._sequencing = sequencing or SequencingTracker()
        self._loop_detector = loop_detector or LoopDetector(
            repeat_threshold=settings.loop_repeat_threshold,
        )

    def _resilience(self) -> list:
        """工具中间件链。

        洋葱顺序：Harness 在外、Resilience 在内——先做准入判定（顺序/循环），
        再进超时与熔断保护；这样被硬拒的调用不会白白占用一次熔断名额。
        """
        chain: list = []
        if self._settings.harness_enabled:
            chain.append(
                HarnessToolMiddleware(
                    sequencing=self._sequencing,
                    loop_detector=self._loop_detector,
                    bus=self._bus,
                ),
            )
        chain.append(ToolResilienceMiddleware(self._circuit_registry, self._bus))
        return chain

    def build(self, restored_state: Optional[AgentState] = None) -> Agent:
        prompts = load_prompts()["main_agent"]
        dispatch = FunctionTool(
            build_task_dispatch_tool(
                self._search_factory, self._trade_factory, self._bus,
                preference_store=self._preference_store,
                preference_selector=self._preference_selector,
                preference_top_k=self._settings.preference_top_k,
                subagent_inject=self._settings.preference_subagent_inject,
                enable_trade=False, scheme_states=self._scheme_states,
            ),
            is_concurrency_safe=True, middlewares=self._resilience(),
        )
        self._dispatch_tool = dispatch

        async def dispatch_search() -> list[dict]:
            return await self.dispatch_committed_search()

        tools = [
            # MainAgent 只提交完整购物方案；商品检索必须经 SearchAgent 子代理。
            FunctionTool(
                build_update_shopping_plan_tool(self._bus, self._scheme_states, self._candidate_pool, dispatch_search),
                middlewares=[ToolResilienceMiddleware(
                    self._circuit_registry, self._bus,
                    timeouts={"update_shopping_plan_tool": 200.0},
                )],
            ),
            # SubAgent as Tool 调度；多个商品槽位可并行搜索。
            # is_concurrency_safe 默认为 True，
            #    主 Agent 同一轮发起的多个派发会被 2.0 并发批执行）
            dispatch,
            # 长期偏好仅作为 ShopMind 的用户偏好信号，不改写本轮购物事实。
            FunctionTool(
                build_remember_preference_tool(self._preference_store, self._bus),
                middlewares=self._resilience(),
            ),
            FunctionTool(
                build_forget_preference_tool(self._preference_store, self._bus),
                middlewares=self._resilience(),
            ),
        ]

        return allow_business_tools(
            Agent(
                name=prompts["name"],
                system_prompt=prompts["system_prompt"],
                model=create_chat_model(self._settings, throttle=self._throttle, bus=self._bus),
                toolkit=Toolkit(tools=tools),
                middlewares=[*build_agent_middlewares(self._settings), RequirePlanOnFirstCall()],
                context_config=build_context_config(
                    self._settings.context_size,
                    self._settings.tool_result_limit,
                ),
                state=restored_state,
                # plan update -> optional sub-agent dispatch -> final reply
                react_config=ReActConfig(max_iters=3),
            ),
        )

    async def dispatch_committed_search(self, slots: Optional[list[str]] = None) -> list[dict]:
        """Send explicit pending purchase slots through task_dispatch/SearchAgent.

        ``slots`` narrows the dispatch to the slots this turn actually changed;
        a follow-up that adds one item must not re-run the other seventeen.
        """
        from app.infrastructure.context import ShoppingContext

        if self._dispatch_tool is None or self._scheme_states is None:
            return []
        session_id = ShoppingContext.current_session_id()
        request = self._scheme_states.get(session_id)
        if request is None:
            return []
        plan = request.scheme
        excluded = set(plan.reusable + plan.not_buy + plan.unknowns)
        # Optional items are searched only after the buyer explicitly selects "buy".
        wanted = [slot for slot in dict.fromkeys(plan.must_buy + plan.optional) if slot not in excluded]
        if slots:
            allowed = set(slots)
            scoped = [slot for slot in wanted if slot in allowed]
            if scoped:
                wanted = scoped
        slots = wanted

        async def run(slot: str) -> dict:
            result = await self._dispatch_tool(
                subagent_type="search_agent",
                demands=f"只处理待购槽位「{slot}」，结合完整方案和本轮变更召回、过滤并排序；只返回最多3个候选及简短取舍。",
            )
            chunks = [chunk async for chunk in result] if hasattr(result, "__aiter__") else [result]
            text = "\n".join(
                block.text for chunk in chunks for block in chunk.content if hasattr(block, "text")
            )
            try:
                return {"slot": slot, "result": _first_json(text)}
            except ValueError:
                return {"slot": slot, "error": text}

        results = await asyncio.gather(*(run(slot) for slot in slots), return_exceptions=True)
        return [
            {"slot": slot, "error": str(result)} if isinstance(result, Exception) else result
            for slot, result in zip(slots, results)
        ]


class SessionRegistry:
    """按 shopping_session_id 缓存 MainAgent 实例，支撑多轮对话；
    AgentState 经 SessionStore 端口落盘（SQLite 或文件），服务重启后恢复。"""

    def __init__(self, main_factory: MainAgentFactory, session_store: SessionStore) -> None:
        self._main_factory = main_factory
        self._session_store = session_store
        self._agents: dict[str, Agent] = {}

    async def get_or_create(self, shopping_session_id: str) -> Agent:
        if shopping_session_id not in self._agents:
            restored_state = await self._try_restore(shopping_session_id)
            self._agents[shopping_session_id] = self._main_factory.build(restored_state)
        return self._agents[shopping_session_id]

    async def persist(self, shopping_session_id: str) -> None:
        """每轮对话结束后落盘 AgentState 快照；失败仅告警不影响主链路。"""
        agent = self._agents.get(shopping_session_id)
        if agent is None:
            return
        try:
            await self._session_store.save(shopping_session_id, agent.state.model_dump_json())
        except Exception as err:  # noqa: BLE001
            logger.warning("会话状态落盘失败：%s（%s）", shopping_session_id, err)

    async def _try_restore(self, shopping_session_id: str) -> Optional[AgentState]:
        try:
            raw = await self._session_store.load(shopping_session_id)
        except Exception as err:  # noqa: BLE001 —— 存储不可用时按新会话继续，不阻断对话
            logger.warning("会话状态读取失败，按新会话处理：%s（%s）", shopping_session_id, err)
            return None
        if raw is None:
            return None
        try:
            state = AgentState.model_validate_json(raw)
            logger.info("会话状态已恢复：%s（%d 条上下文）", shopping_session_id, len(state.context))
            return state
        except Exception as err:  # noqa: BLE001 —— 快照损坏按新会话处理
            logger.warning("会话状态恢复失败，按新会话处理：%s（%s）", shopping_session_id, err)
            return None
