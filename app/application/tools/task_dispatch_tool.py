# -*- coding: utf-8 -*-
"""task_dispatch 工具

SubAgent as Tool 的调度工具——MainAgent 调它意味着"派一个专家子 Agent 去执行这段 demands"。
2.0 库级没有 subagent 原语，用 FunctionTool 包装子 Agent 实现同等语义。

子 Agent 每次调度新建实例（独立 AgentState = 上下文隔离），只把最终结论回传给 MainAgent，
中间的工具调用过程由业务工具自身通过 EventBus 直接上报前端。

真并行：本工具注册为 is_concurrency_safe，主 Agent 同一轮发起的多个 task_dispatch
会被 2.0 批量 asyncio.gather 并发执行；agent.dispatch 事件带 started_at，
完成时另发 tool.result 带 finished_at/elapsed_ms，可从事件流直接判定时间重叠。

买家偏好由**本工具服务端注入**，不依赖主 Agent 把偏好抄进 demands：
    buyer_id 从 ShoppingContext（ContextVar）取，作用域覆盖到 worker.reply()，
    因此子 Agent 无需改签名就能拿到偏好。以前靠提示词要求“demands 必须自包含偏好”
    是**软约束**：模型漏抄就静默降级为无偏好推荐，不报错、无告警。

注意：本模块不能用 `from __future__ import annotations`（AgentScope schema 生成依赖运行时注解）。
"""
import copy
import json
import logging
import time
from datetime import datetime, timezone
from typing import Literal, Optional

from agentscope.message import TextBlock, ToolResultState, UserMsg
from agentscope.tool import ToolChunk
from pydantic import BaseModel, Field

from app.application.agents.search_agent import SearchAgentFactory
from app.application.agents.scheme_state import SchemeStateRegistry
from app.application.agents.trade_agent import TradeAgentFactory
from app.application.memory.preference_selector import (
    PreferenceSelector,
    render_preference_hint,
)
from app.domain.buyer.preference import PreferenceStore
from app.infrastructure.context import ShoppingContext
from app.infrastructure.eventbus import TradeEventBus

logger = logging.getLogger(__name__)


class SearchHit(BaseModel):
    product_id: str = Field(description="product_search_tool 返回的商品 ID")
    reason: str = Field(description="只依据工具事实说明匹配原因")
    tradeoff: str = Field(description="限制、未知或未提供的信息")


class SearchResult(BaseModel):
    hits: list[SearchHit]
    notes: str = Field(description="实际检索路径、过滤和整体取舍")


def build_task_dispatch_tool(
    search_factory: SearchAgentFactory,
    trade_factory: TradeAgentFactory,
    bus: TradeEventBus,
    preference_store: Optional[PreferenceStore] = None,
    preference_selector: Optional[PreferenceSelector] = None,
    preference_top_k: int = 5,
    subagent_inject: bool = True,
    enable_trade: bool = True,
    scheme_states: Optional[SchemeStateRegistry] = None,
):
    selector = preference_selector or PreferenceSelector()
    # A MainAgent can repeat the same dispatch after receiving the first result.
    # Reuse that result within the same user turn and unchanged plan.
    cached_query_by_session: dict[str, str] = {}
    cached_results: dict[tuple[str, str, str, str], ToolChunk] = {}

    async def _preference_hint(subagent_type: str, demands: str):
        """给子 Agent 算出要注入的偏好块；无需注入时返回 None。

        只给 search_agent 注入：trade_agent 只按给定的 product_id/sku_id 执行下单，
        偏好影响不了它的行为，注入纯属白花 token，还会给它多余的发挥空间。
        """
        if not (subagent_inject and preference_store and subagent_type == "search_agent"):
            return None
        snapshot = ShoppingContext.current()
        buyer_id = snapshot.buyer_id if snapshot else ""
        if not buyer_id:
            return None
        try:
            preferences = await preference_store.list_by_buyer(buyer_id)
            selected = await selector.select(preferences, query=demands, top_k=preference_top_k)
        except Exception as err:  # noqa: BLE001 —— 读记忆失败不能让派发挂掉
            logger.warning("子 Agent 偏好注入跳过（读取失败）：%s", err)
            return None
        return render_preference_hint(selected) if selected else None

    async def task_dispatch(
        subagent_type: Literal["search_agent", "trade_agent"],
        demands: str,
    ) -> ToolChunk:
        """调度专家子代理执行子任务，返回子代理的结论（JSON 字符串）。

        仅当子任务满足"可并行 / 需要上下文隔离 / 内部调用链较深"任一条件时使用；
        简单的单步工具调用应自己直接调业务工具完成。
        多个彼此独立的子任务请在同一轮一次性发起多个本工具调用，系统会并发执行。

        Args:
            subagent_type (`str`):
                子代理类型："search_agent"（跨境商品检索专家）或 "trade_agent"（下单交易专家）。
            demands (`str`):
                自包含的自然语言指令，必须包含子代理完成任务所需的全部上下文
                （买家偏好、预算、product_id/sku_id、收货地址等），子代理看不到主对话历史。
        """
        session_id = ShoppingContext.current_session_id()
        if subagent_type == "trade_agent" and not enable_trade:
            return ToolChunk(
                content=[TextBlock(type="text", text="[error] 交易能力当前未接入购物规划主流程")],
                state=ToolResultState.ERROR,
            )
        snapshot = ShoppingContext.current()
        raw_query = snapshot.raw_query if snapshot else ""
        request = scheme_states.get(session_id) if scheme_states and subagent_type == "search_agent" else None
        plan_signature = ""
        requested_slots: list[str] = []
        if request:
            plan = request.scheme.to_dict()
            plan.pop("state_version", None)
            plan_signature = json.dumps(plan, ensure_ascii=False, sort_keys=True)
            requested_slots = list(dict.fromkeys([*request.scheme.must_buy, *request.scheme.optional]))
        matched_slot = next(
            (slot for slot in requested_slots if slot.casefold() in demands.casefold()),
            requested_slots[0] if len(requested_slots) == 1 else "",
        )
        cache_key = (session_id, subagent_type, matched_slot or demands.strip().casefold(), plan_signature)
        if raw_query:
            if cached_query_by_session.get(session_id) != raw_query:
                cached_query_by_session[session_id] = raw_query
                for key in [key for key in cached_results if key[0] == session_id]:
                    del cached_results[key]
            if cache_key in cached_results:
                logger.info("复用本轮 SearchAgent 结果（会话 %s，任务 %s）", session_id, matched_slot or demands[:80])
                return copy.deepcopy(cached_results[cache_key])
        started_at = datetime.now(timezone.utc).isoformat()
        started_monotonic = time.monotonic()
        bus.publish(
            session_id,
            "agent.dispatch",
            {"agent": subagent_type, "demands": demands, "started_at": started_at},
        )

        # The ordinary SearchAgent remains model-backed for nuanced requests.
        # A committed atomic shopping slot, however, has already gone through
        # MainAgent intent parsing and needs only the catalog decision path.
        # Let SearchAgentFactory execute that path directly so we do not pay
        # two extra LLM turns (tool selection + JSON paraphrase) for every
        # checklist confirmation.  The same product_search_tool emits invoke /
        # result events and applies the versioned incremental-retrieval policy.
        fast_search = getattr(search_factory, "run_fast", None)
        if subagent_type == "search_agent" and matched_slot and callable(fast_search):
            output_obj = await fast_search(session_id, matched_slot)
            output = json.dumps(output_obj, ensure_ascii=False)
            finished_at = datetime.now(timezone.utc).isoformat()
            elapsed_ms = round((time.monotonic() - started_monotonic) * 1000)
            bus.publish(
                session_id,
                "agent.result",
                {
                    "agent": subagent_type,
                    "shopping_task": matched_slot,
                    "output": output,
                    "execution_mode": "catalog_fast_path",
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "elapsed_ms": elapsed_ms,
                },
            )
            result_chunk = ToolChunk(
                content=[TextBlock(type="text", text=output)],
                state=ToolResultState.SUCCESS,
            )
            if raw_query and subagent_type == "search_agent":
                cached_results[cache_key] = copy.deepcopy(result_chunk)
            return result_chunk

        if subagent_type == "search_agent":
            worker = search_factory.build()
        elif subagent_type == "trade_agent":
            worker = trade_factory.build()
        else:
            return ToolChunk(
                content=[TextBlock(type="text", text=f"[error] 未知 subagent_type：{subagent_type}")],
                state=ToolResultState.ERROR,
            )

        inputs = [UserMsg("commerce_concierge", demands)]
        if subagent_type == "search_agent" and request:
            inputs.insert(
                0,
                UserMsg(
                    "shopping_search_request",
                    "<shopping-search-request>"
                    + json.dumps(request.to_dict(), ensure_ascii=False)
                    + "</shopping-search-request>",
                ),
            )
            if matched_slot:
                inputs.insert(
                    1,
                    UserMsg(
                        "search_scope",
                        f"<search-scope>本轮只处理待购槽位「{matched_slot}」。"
                        "调用 product_search_tool 时 shopping_task 必须使用该槽位名；"
                        "工具返回一次后立即输出最终 JSON，不得换槽位重复检索。</search-scope>",
                    ),
                )
        hint = await _preference_hint(subagent_type, demands)
        if hint:
            inputs.insert(0, UserMsg("memory_hint", hint))

        if subagent_type == "search_agent":
            reply = await worker.reply(inputs, structured_schema=SearchResult)
            output = (
                json.dumps(reply.structured_output or {}, ensure_ascii=False)
                if reply.structured_output is not None
                else reply.get_text_content() or ""
            )
        else:
            reply = await worker.reply(inputs)
            output = reply.get_text_content() or ""
        bus.publish(
            session_id,
            "agent.result",
            {
                "agent": subagent_type,
                "shopping_task": matched_slot,
                "output": output,
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": round((time.monotonic() - started_monotonic) * 1000),
            },
        )
        result_chunk = ToolChunk(
            content=[TextBlock(type="text", text=output)],
            state=ToolResultState.SUCCESS,
        )
        if raw_query and subagent_type == "search_agent":
            cached_results[cache_key] = copy.deepcopy(result_chunk)
        return result_chunk

    return task_dispatch
