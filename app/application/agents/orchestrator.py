# -*- coding: utf-8 -*-
"""MainAgentOrchestrator

应用层编排入口：
    1. 把会话快照写入 ShoppingContext（ContextVar，工具与子 Agent 透明可读）；
    2. 长期记忆读路径：买家偏好经 PreferenceSelector 按本轮 query 相关性挑选后，
       有变化时随本轮输入注入一条 <buyer-preferences> hint 消息（dislike 不参与截断）；
    3. 消费 MainAgent 的 reply_stream 类型化事件流并映射到 TradeEventBus：
       TextBlockDeltaEvent → token.delta
       Task* 工具结果      → plan.update（从 AgentState.tasks_context 快照）
       （业务工具的 tool.invoke / tool.result 与 agent.dispatch 由工具自身发布）
    4. 上下文压缩检测：本轮结束后 AgentState.summary 发生变化即发布 context.compressed；
    5. 上游瞬时错误（限流/并发/5xx）有界重试；
    6. 结束后发布 final.result / error，落盘 AgentState，返回最终文本。

为何重试要放在这一层：2.0 模型层只对"建流阶段"的异常重试，而 OpenAI 兼容网关常把
限流错误写在 SSE 流中间（报 openai.APIError），此时已经走出模型层重试范围，不兜底就会
整轮失败。重试期间前端可能看到重复的流式片段，final.result 到达时会被覆盖。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Optional

from agentscope.agent import Agent
from agentscope.event import (
    ModelCallEndEvent,
    ModelCallStartEvent,
    TextBlockDeltaEvent,
    ToolCallDeltaEvent,
    ToolCallStartEvent,
    ToolResultTextDeltaEvent,
    ToolResultEndEvent,
)
from agentscope.message import Msg, TextBlock, UserMsg

from app.application.agents.main_agent import SessionRegistry
from app.application.agents.scheme_state import CandidatePool, SchemeStateRegistry
from app.application.agents.intent_router import EditRoute, route_direct_item, route_edit
from app.application.tools.shopping_plan_tool import commit_checklist_selection
from app.application.harness.drift_detector import DriftDetector
from app.application.harness.loop_detector import LoopDetector
from app.application.memory.preference_selector import (
    PreferenceSelector,
    render_preference_hint,
    render_preference_lines,
)
from app.domain.buyer.preference import PreferenceStore
from app.domain.session.ports.conversation_store import (
    ConversationEventRecord,
    ConversationStore,
    ConversationTurn,
)
from app.domain.session.ports.session_store import SessionStore  # noqa: F401 —— 保留类型引用
from app.infrastructure.cache.semantic_cache import SemanticCache
from app.infrastructure.context import ShoppingContext, ShoppingContextSnapshot
from app.infrastructure.eventbus import TradeEventBus
from app.infrastructure.persistence.scene_template_store import SceneTemplateStore
from app.infrastructure.budget import init_budget
from app.infrastructure.security.output_guard import audit_output
from app.infrastructure.transient import is_transient_error
from app.domain.session.shopping_plan import ShoppingPlan

logger = logging.getLogger(__name__)

# 内置 Task 计划工具名，其结果落地后向前端推送 plan.update 快照
_TASK_TOOL_NAMES = {"TaskCreate", "TaskUpdate", "TaskList", "TaskGet"}

# 上游瞬时故障判据与模型层共用同一份（app/infrastructure/transient.py），避免两处标记表漂移。
# 模型层已做一轮退避重试 + 备用模型回退，这里是最外层兜底：覆盖模型层之外
# （工具、子 Agent 调度、事件消费）招致的瞬时失败。
_MAX_TURN_RETRIES = 1
_RETRY_BASE_SECONDS = 3.0


def _partial_checklist(arguments: str) -> Optional[dict]:
    """Return only complete task names from a streaming plan-tool argument."""
    decoder = json.JSONDecoder()
    def read_strings(field: str) -> list[str]:
        match = re.search(rf'"{field}"\s*:\s*\[', arguments)
        if match is None:
            return []
        cursor = match.end()
        values: list[str] = []
        while cursor < len(arguments):
            while cursor < len(arguments) and arguments[cursor].isspace():
                cursor += 1
            if cursor >= len(arguments) or arguments[cursor] == "]":
                break
            try:
                value, end = decoder.raw_decode(arguments, cursor)
            except json.JSONDecodeError:
                break
            if not isinstance(value, str):
                break
            values.append(value)
            cursor = end
            while cursor < len(arguments) and arguments[cursor].isspace():
                cursor += 1
            if cursor < len(arguments) and arguments[cursor] == ",":
                cursor += 1
            else:
                break
        return values

    tasks = list(dict.fromkeys(
        item for field in ("tasks", "must_buy", "optional", "unknowns", "reusable", "not_buy")
        for item in read_strings(field)
    ))
    if len(tasks) < 3:
        return None
    goal = ""
    goal_match = re.search(r'"goal"\s*:\s*', arguments)
    if goal_match:
        try:
            goal, _ = decoder.raw_decode(arguments, goal_match.end())
        except json.JSONDecodeError:
            pass
    return {
        "goal": goal if isinstance(goal, str) else "正在整理本次任务",
        "stage": "explore",
        "tasks": tasks,
        "optional": [],
        "prep": [],
    }


@dataclass(frozen=True)
class SubmitIntentInput:
    shopping_session_id: str
    buyer_id: str
    locale: str
    currency: str
    raw_query: str
    checklist_selection: Optional[dict] = None


@dataclass(frozen=True)
class SubmitIntentOutput:
    shopping_session_id: str
    final_text: str


def _tasks_snapshot(agent: Agent) -> dict:
    tasks = agent.state.tasks_context.tasks
    return {
        "tasks": [
            {"id": task.id, "subject": task.subject, "state": str(task.state)}
            for task in tasks
        ],
    }


def _plan_ack_text(payload: dict) -> str:
    """Render the short acknowledgement after an explore/clarify plan commit.

    The MainAgent has already done the expensive work here: intent parsing and
    structured state submission.  A second LLM turn that merely says “please
    check the list” adds latency without changing the plan.  Keep this renderer
    deliberately factual; search/compare turns still go through the model so
    SearchAgent evidence and trade-offs can be explained in context.
    """
    goal = str(payload.get("goal") or "这次任务")
    items = list(dict.fromkeys([
        *(payload.get("tasks") or []),
        *(payload.get("optional") or []),
    ]))
    shown = "、".join(str(item) for item in items[:8])
    if len(items) > 8:
        shown += f"等 {len(items)} 项"
    if not shown:
        shown = "待确认项目"
    return (
        f"我先按“{goal}”整理了清单：{shown}。"
        "请在下方逐项标记已有、需要购买或还没确认；我只会为明确要买的项目找商品。"
    )


def _search_ack_text(payload: dict) -> str:
    """Render a factual receipt after SearchAgent has returned candidates."""
    results = payload.get("search_results") or []
    searched: list[str] = []
    candidate_count = 0
    failed: list[str] = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        slot = str(entry.get("slot") or "待购项目")
        result = entry.get("result")
        if isinstance(result, dict):
            hits = result.get("hits") or []
            candidate_count += len(hits)
            searched.append(slot)
        elif entry.get("error"):
            failed.append(slot)
    if searched:
        slots = "、".join(searched[:6])
        suffix = f"；{slots}共返回 {candidate_count} 个候选。"
    else:
        suffix = "本轮没有拿到可展示的候选。"
    if failed:
        suffix += "未完成检索：" + "、".join(failed[:4]) + "。"
    return "已按确认的待购项完成检索" + suffix + "请直接比较商品卡；已有和待确认项目未参与推荐。"


def _edit_ack_text(route: EditRoute, plan) -> str:
    """One short factual receipt for a deterministic edit turn."""
    if route.kind == "buy_item":
        return f"已加上「{route.added[0]}」，只为这一项找商品；其余清单和已确认状态不变。"
    if route.kind == "set_buy":
        return f"已把「{route.slots[0]}」记为这次要买，只重新匹配这一项。"
    if route.kind == "set_have":
        return f"已把「{route.slots[0]}」记为已有，不再为它推荐商品；合计已更新。"
    if route.kind == "set_skip":
        return f"已把「{route.slots[0]}」记为这次不买，不再为它推荐商品。"
    if route.kind == "budget":
        return f"预算按 {route.budget_major:g} 元执行，正在同一批候选中重新过滤排序，不会重开清单。"
    if route.kind == "preference":
        return "已按新的偏好重排当前候选，清单事实不变。"
    if route.kind == "people":
        return f"人数按 {route.people} 人更新，正在重算受影响的项目。"
    return "已更新方案。"


def _apply_edit(plan, route: EditRoute):
    """Return a new immutable plan with exactly one edit applied."""
    tasks = list(plan.tasks)
    must_buy = list(plan.must_buy)
    optional = list(plan.optional)
    reusable = list(plan.reusable)
    not_buy = list(plan.not_buy)
    unknowns = list(plan.unknowns)

    def bucket(remove: str, target: list[str]) -> None:
        for pool in (must_buy, optional, reusable, not_buy, unknowns):
            while remove in pool:
                pool.remove(remove)
        if remove not in target:
            target.append(remove)

    if route.kind == "buy_item":
        slot = route.added[0]
        if slot not in tasks:
            tasks.append(slot)
        bucket(slot, must_buy)
    elif route.kind == "set_buy":
        bucket(route.slots[0], must_buy)
    elif route.kind == "set_have":
        bucket(route.slots[0], reusable)
    elif route.kind == "set_skip":
        bucket(route.slots[0], not_buy)

    has_purchase = bool(must_buy)
    if route.kind in {"budget", "preference"}:
        stage = "search" if has_purchase else plan.stage
    elif must_buy:
        stage = "search"
    else:
        stage = "confirm"
    return replace(
        plan,
        tasks=tasks,
        must_buy=must_buy,
        optional=optional,
        reusable=reusable,
        not_buy=not_buy,
        unknowns=unknowns,
        stage=stage,
        budget_major=route.budget_major if route.kind == "budget" else plan.budget_major,
        preferences=route.preferences or plan.preferences,
        people=route.people if route.kind == "people" else plan.people,
    )


class MainAgentOrchestrator:
    def __init__(
        self,
        sessions: SessionRegistry,
        bus: TradeEventBus,
        preference_store: PreferenceStore,
        conversation_store: Optional[ConversationStore] = None,
        semantic_cache: Optional[SemanticCache] = None,
        output_guard_enabled: bool = True,
        loop_detector: Optional[LoopDetector] = None,
        token_budget_total: int = 0,
        drift_detector: Optional[DriftDetector] = None,
        preference_selector: Optional[PreferenceSelector] = None,
        preference_top_k: int = 5,
        scheme_states: Optional[SchemeStateRegistry] = None,
        scene_templates: Optional[SceneTemplateStore] = None,
        candidate_pool: Optional[CandidatePool] = None,
        confirmed_search: Optional[Callable[..., Awaitable[list[dict]]]] = None,
    ) -> None:
        self._sessions = sessions
        self._bus = bus
        self._preference_store = preference_store
        self._conversation_store = conversation_store
        self._semantic_cache = semantic_cache
        self._output_guard_enabled = output_guard_enabled
        self._loop_detector = loop_detector
        self._token_budget_total = token_budget_total
        self._drift_detector = drift_detector
        # 默认 selector 不带 embedder，退化为“按时间倒序取 top_k”，单测与无凭据环境可直接跑
        self._preference_selector = preference_selector or PreferenceSelector()
        self._preference_top_k = preference_top_k
        self._scheme_states = scheme_states
        self._scene_templates = scene_templates
        self._candidate_pool = candidate_pool
        self._confirmed_search = confirmed_search
        # 会话内已注入的偏好快照，变化时才重新注入，避免每轮重复填充上下文
        self._injected_preferences: dict[str, str] = {}

    def _guard_final_text(self, session_id: str, text: str) -> str:
        """L4 输出审核：最终回复推给买家前脱敏内部信息。

        命中只脱敏并发一条告警事件，**不阻断回复**：
        一条误判不应让整轮对话失败。
        """
        if not self._output_guard_enabled or not text:
            return text
        safe, cleaned = audit_output(text)
        if not safe:
            logger.warning("L4 输出审核命中敏感内容，已脱敏（会话 %s）", session_id)
            self._bus.publish(session_id, "error", {"message": "输出审核命中内部信息，已脱敏后下发"})
        return cleaned

    async def handle_intent(self, intent: SubmitIntentInput) -> SubmitIntentOutput:
        session_id = intent.shopping_session_id
        snapshot = ShoppingContextSnapshot(
            shopping_session_id=session_id,
            buyer_id=intent.buyer_id,
            locale=intent.locale,
            currency=intent.currency,
            raw_query=intent.raw_query,
            checklist_selection=intent.checklist_selection,
        )
        token = ShoppingContext.set(snapshot)
        started_at = time.monotonic()
        # 本轮 Token 预算（TOKEN_BUDGET_TOTAL=0时为 None，不启用四档降级）
        init_budget(self._token_budget_total)
        if self._drift_detector is not None:
            self._drift_detector.start_turn(session_id, intent.raw_query)
        # 开始录事件轨迹（本轮结束后批量入库）
        trace = self._bus.subscribe(session_id) if self._conversation_store else None
        final_text = ""
        try:
            agent = await self._sessions.get_or_create(session_id)
            summary_before = agent.state.summary
            has_history = bool(agent.state.context)
            saved_scheme = self._scheme_states.get(session_id) if self._scheme_states else None
            matched_scene = self._scene_templates.match(intent.raw_query) if self._scene_templates else None
            direct_item = route_direct_item(intent.raw_query)
            if (
                saved_scheme is None
                and not intent.checklist_selection
                and self._scheme_states is not None
                and self._confirmed_search is not None
                and direct_item.kind == "direct_item"
            ):
                # A first-turn, explicit category is a one-slot shopping task,
                # not a reason to generate an unrelated full-scene checklist.
                item = direct_item.added[0]
                request = self._scheme_states.update(session_id, ShoppingPlan(
                    goal=f"购买{item}", core_use=item, stage="search",
                    tasks=[item], must_buy=[item], currency=intent.currency,
                ))
                self._bus.publish(
                    session_id,
                    "plan.update",
                    {
                        "shopping_plan": request.scheme.to_dict(),
                        "mutation": request.mutation.to_dict(),
                        "search_request": request.to_dict(),
                        "route": "direct_item",
                    },
                )
                results = await self._confirmed_search([item])
                final_text = self._guard_final_text(
                    session_id, _search_ack_text({"search_results": results}),
                )
                agent.state.context.append(UserMsg(intent.buyer_id, intent.raw_query))
                agent.state.context.append(Msg(
                    name=agent.name,
                    content=[TextBlock(type="text", text=final_text)],
                    role="assistant",
                ))
                self._bus.publish(session_id, "final.result", {"text": final_text})
                return SubmitIntentOutput(shopping_session_id=session_id, final_text=final_text)
            if (
                matched_scene is not None
                and (saved_scheme is None or saved_scheme.scheme.template_id != matched_scene["id"])
                and not intent.checklist_selection
                and self._scheme_states is not None
                and self._scene_templates is not None
            ):
                plan = self._scene_templates.build_plan(intent.raw_query, matched_scene)
                request = self._scheme_states.update(session_id, plan)
                # A newly matched scene replaces an old scene deterministically.
                # No LLM round-trip is needed to repeat a known checklist.
                agent.state.context.append(UserMsg(intent.buyer_id, intent.raw_query))
                final_text = self._guard_final_text(
                    session_id, _plan_ack_text(request.scheme.to_dict()),
                )
                agent.state.context.append(Msg(
                    name=agent.name,
                    content=[TextBlock(type="text", text=final_text)],
                    role="assistant",
                ))
                self._bus.publish(
                    session_id,
                    "plan.update",
                    {
                        "shopping_plan": request.scheme.to_dict(),
                        "mutation": request.mutation.to_dict(),
                        "search_request": request.to_dict(),
                    },
                )
                self._bus.publish(session_id, "final.result", {"text": final_text})
                return SubmitIntentOutput(shopping_session_id=session_id, final_text=final_text)
            if (
                intent.checklist_selection
                and saved_scheme is not None
                and self._scheme_states is not None
                and self._confirmed_search is not None
            ):
                try:
                    request = commit_checklist_selection(
                        self._bus, self._scheme_states, self._candidate_pool,
                        session_id, intent.checklist_selection,
                    )
                except ValueError as err:
                    final_text = f"[error] {err}"
                    self._bus.publish(session_id, "error", {"message": str(err)})
                else:
                    if request.scheme.stage in {"search", "compare"}:
                        search_results = await self._confirmed_search()
                        final_text = _search_ack_text({"search_results": search_results})
                    else:
                        final_text = "已记录清单状态；目前没有明确待购项目，暂未启动商品搜索。"
                final_text = self._guard_final_text(session_id, final_text)
                selection_text = intent.raw_query + "\n<shopmind-checklist-selection>" + json.dumps(
                    intent.checklist_selection, ensure_ascii=False,
                ) + "</shopmind-checklist-selection>"
                agent.state.context.append(UserMsg(intent.buyer_id, selection_text))
                agent.state.context.append(Msg(
                    name=agent.name,
                    content=[TextBlock(type="text", text=final_text)],
                    role="assistant",
                ))
                self._bus.publish(session_id, "final.result", {"text": final_text})
                return SubmitIntentOutput(shopping_session_id=session_id, final_text=final_text)
            # Deterministic edit path: a follow-up that names an item, a budget,
            # or an ownership change must not re-run full scene planning.  The
            # MainAgent still owns the state; only the expensive round-trip is
            # skipped.  Anything ambiguous falls through to the model below.
            if (
                saved_scheme is not None
                and not intent.checklist_selection
                and self._scheme_states is not None
            ):
                route = route_edit(intent.raw_query, saved_scheme.scheme)
                if route.kind in {
                    "buy_item", "set_buy", "set_have", "set_skip",
                    "budget", "preference", "people",
                }:
                    plan = _apply_edit(saved_scheme.scheme, route)
                    request = self._scheme_states.update(session_id, plan)
                    if self._candidate_pool is not None:
                        self._candidate_pool.apply(session_id, request.mutation)
                    self._bus.publish(
                        session_id,
                        "plan.update",
                        {
                            "shopping_plan": request.scheme.to_dict(),
                            "mutation": request.mutation.to_dict(),
                            "search_request": request.to_dict(),
                            "route": route.kind,
                        },
                    )
                    if route.kind in {"set_have", "set_skip"}:
                        # Dropping a slot never needs retrieval; its candidates
                        # were already removed from the pool by CandidatePool.apply.
                        final_text = _edit_ack_text(route, request.scheme)
                    elif (
                        request.scheme.stage in {"search", "compare"}
                        and self._confirmed_search is not None
                    ):
                        touched = [*route.slots, *route.added] if route.kind in {"buy_item", "set_buy"} else None
                        results = await self._confirmed_search(touched)
                        final_text = _search_ack_text({"search_results": results})
                    else:
                        final_text = _edit_ack_text(route, request.scheme)
                    final_text = self._guard_final_text(session_id, final_text)
                    agent.state.context.append(UserMsg(
                        intent.buyer_id,
                        intent.raw_query + "\n<shopmind-route>" + route.kind + "</shopmind-route>",
                    ))
                    agent.state.context.append(Msg(
                        name=agent.name,
                        content=[TextBlock(type="text", text=final_text)],
                        role="assistant",
                    ))
                    self._bus.publish(session_id, "final.result", {"text": final_text})
                    return SubmitIntentOutput(shopping_session_id=session_id, final_text=final_text)
            # 语义缓存：仅首轮（无历史上下文）且非写操作意图时尝试命中，命中则零模型调用
            cached = await self._lookup_cache(intent, has_history)
            if cached is not None:
                final_text = self._guard_final_text(session_id, cached)
                self._bus.publish(session_id, "final.result", {"text": final_text})
                return SubmitIntentOutput(shopping_session_id=session_id, final_text=final_text)

            inputs = await self._build_inputs(intent, session_id)

            final_text = await self._reply_with_retry(session_id, agent, inputs)
            final_text = self._guard_final_text(session_id, final_text)
            await self._check_drift(session_id)

            self._publish_compression(session_id, agent, summary_before)
            self._bus.publish(session_id, "final.result", {"text": final_text})
            await self._remember_cache(intent, final_text, has_history)
            return SubmitIntentOutput(shopping_session_id=session_id, final_text=final_text)
        except Exception as err:  # noqa: BLE001 —— 兜底转事件，避免长任务静默失败
            logger.exception("MainAgent 异常")
            self._bus.publish(session_id, "error", {"message": str(err)})
            final_text = f"[error] {err}"
            return SubmitIntentOutput(shopping_session_id=session_id, final_text=final_text)
        finally:
            # 无论成功失败都落盘会话状态（失败内部仅告警）
            await self._sessions.persist(session_id)
            await self._record_conversation(
                intent, final_text, int((time.monotonic() - started_at) * 1000), trace,
            )
            # 循环检测是"本轮内是不是在打转"的判定，轮末必须清零；
            # 阶段无关的顺序记录（SequencingTracker）则按会话保留，
            # 否则第 1 轮检索、第 3 轮下单会被误判为"未检索就下单"。
            if self._loop_detector is not None:
                self._loop_detector.reset(session_id)
            if self._drift_detector is not None:
                self._drift_detector.reset(session_id)
            ShoppingContext.reset(token)

    async def _preference_scope(self, buyer_id: str) -> str:
        """买家当前偏好的指纹，作为语义缓存的分桶维度。

        用**全量偏好**而不是本轮选中的子集：选中子集随 query 变，拿它做 key
        会让缓存碎成一盘沙。全量偏好只在真正新增/撤回时变，恰好是正确的失效时机。
        """
        try:
            preferences = await self._preference_store.list_by_buyer(buyer_id)
        except Exception as err:  # noqa: BLE001 —— 读不到就当无偏好，不阻断对话
            logger.warning("读取偏好指纹失败，缓存按无偏好分桶：%s", err)
            return ""
        if not preferences:
            return ""
        return hashlib.sha256(
            render_preference_lines(preferences).encode(),
        ).hexdigest()[:16]

    async def _lookup_cache(self, intent: SubmitIntentInput, has_history: bool) -> Optional[str]:
        """语义缓存查询；命中时发 cache.hit 事件让过程可见（不静默复用）。"""
        if self._semantic_cache is None:
            return None
        hit = await self._semantic_cache.lookup(
            intent.buyer_id,
            intent.raw_query,
            has_history,
            scope=await self._preference_scope(intent.buyer_id),
        )
        if hit is None:
            return None
        logger.info("语义缓存命中（%.4f）：%s", hit.similarity, intent.raw_query)
        self._bus.publish(
            intent.shopping_session_id,
            "cache.hit",
            {"similarity": hit.similarity, "matched_query": hit.matched_query},
        )
        return hit.reply

    async def _remember_cache(
        self, intent: SubmitIntentInput, final_text: str, has_history: bool,
    ) -> None:
        if self._semantic_cache is None:
            return
        await self._semantic_cache.remember(
            intent.buyer_id,
            intent.raw_query,
            final_text,
            has_history,
            scope=await self._preference_scope(intent.buyer_id),
        )

    async def _record_conversation(
        self,
        intent: SubmitIntentInput,
        final_text: str,
        latency_ms: int,
        trace: Optional[asyncio.Queue],
    ) -> None:
        """对话流水 + 事件轨迹入库。写库失败只告警，不影响已经返回给买家的结果。"""
        if self._conversation_store is None:
            return
        session_id = intent.shopping_session_id
        events: list[ConversationEventRecord] = []
        if trace is not None:
            self._bus.unsubscribe(session_id, trace)
            while not trace.empty():
                event = trace.get_nowait()
                # token.delta 量大且已被 final.result 汇总，不入库
                if event.type == "token.delta":
                    continue
                events.append(
                    ConversationEventRecord(
                        session_id=session_id,
                        type=event.type,
                        payload=event.payload if isinstance(event.payload, dict) else {"value": event.payload},
                        occurred_at=event.occurred_at,
                    ),
                )
        try:
            await self._conversation_store.touch_session(
                session_id, intent.buyer_id, intent.locale, intent.currency,
            )
            await self._conversation_store.append_turn(
                ConversationTurn(
                    session_id=session_id,
                    buyer_id=intent.buyer_id,
                    role="buyer",
                    content=intent.raw_query,
                ),
            )
            await self._conversation_store.append_turn(
                ConversationTurn(
                    session_id=session_id,
                    buyer_id=intent.buyer_id,
                    role="agent",
                    content=final_text,
                    latency_ms=latency_ms,
                ),
            )
            await self._conversation_store.append_events(events)
        except Exception as err:  # noqa: BLE001
            logger.warning("对话记录写入失败：%s（%s）", session_id, err)

    async def _reply_with_retry(self, session_id: str, agent: Agent, inputs: list[Msg]) -> str:
        """跑一轮 Agent 并映射事件流；上游瞬时错误按指数退避重试。"""
        last_error: Exception | None = None
        for attempt in range(_MAX_TURN_RETRIES + 1):
            try:
                return await self._consume_reply(session_id, agent, inputs)
            except Exception as err:  # noqa: BLE001
                if not is_transient_error(err) or attempt >= _MAX_TURN_RETRIES:
                    raise
                last_error = err
                # 指数退避：网关速率类限流对固定间隔重试不敏感
                delay = _RETRY_BASE_SECONDS * (3**attempt)
                logger.warning(
                    "上游瞬时故障，%.0fs 后重试（第 %d/%d 次）：%s",
                    delay,
                    attempt + 1,
                    _MAX_TURN_RETRIES,
                    err,
                )
                self._bus.publish(
                    session_id,
                    "error",
                    {"message": f"上游瞬时故障，正在重试：{err}", "retrying": True},
                )
                # 重试时不再重复送入 inputs，避免上下文里出现两次买家发言
                inputs = []
                await asyncio.sleep(delay)
        raise last_error if last_error else RuntimeError("reply 重试耗尽")

    async def _consume_reply(self, session_id: str, agent: Agent, inputs: list[Msg]) -> str:
        final_text = ""
        # tool_call_id → 工具名，用于把 ToolResultEndEvent 关联回 Task 工具
        call_names: dict[str, str] = {}
        call_arguments: dict[str, str] = {}
        tool_result_text: dict[str, str] = {}
        preview_counts: dict[str, int] = {}
        model_call_started: dict[str, float] = {}
        stream = agent.reply_stream(inputs or None, yield_final_msg=True)
        early_final = False
        try:
            async for event in stream:
                if isinstance(event, Msg):
                    final_text = event.get_text_content() or ""
                elif isinstance(event, TextBlockDeltaEvent):
                    if event.delta:
                        self._bus.publish(
                            session_id,
                            "token.delta",
                            {"name": agent.name, "token": event.delta},
                        )
                elif isinstance(event, ModelCallStartEvent):
                    model_call_started[event.reply_id] = (time.monotonic(), event.model_name)
                elif isinstance(event, ModelCallEndEvent):
                    call = model_call_started.pop(event.reply_id, None)
                    self._bus.publish(
                        session_id,
                        "model.call",
                        {
                            "agent": agent.name,
                            "model": call[1] if call else "",
                            "elapsed_ms": round((time.monotonic() - call[0]) * 1000) if call else None,
                            "input_tokens": event.input_tokens,
                            "output_tokens": event.output_tokens,
                            "finished_reason": str(event.finished_reason),
                        },
                    )
                elif isinstance(event, ToolCallStartEvent):
                    call_names[event.tool_call_id] = event.tool_call_name
                elif isinstance(event, ToolCallDeltaEvent):
                    if call_names.get(event.tool_call_id) == "update_shopping_plan_tool":
                        arguments = call_arguments.get(event.tool_call_id, "") + event.delta
                        call_arguments[event.tool_call_id] = arguments
                        preview = _partial_checklist(arguments)
                        count = len(preview["tasks"]) if preview else 0
                        previous_count = preview_counts.get(event.tool_call_id, 0)
                        if preview and (previous_count == 0 or count >= previous_count + 2):
                            preview_counts[event.tool_call_id] = count
                            self._bus.publish(
                                session_id,
                                "plan.preview",
                                {"shopping_plan": preview, "provisional": True},
                            )
                elif isinstance(event, ToolResultTextDeltaEvent):
                    tool_result_text[event.tool_call_id] = (
                        tool_result_text.get(event.tool_call_id, "") + event.delta
                    )
                elif isinstance(event, ToolResultEndEvent):
                    tool_name = call_names.get(event.tool_call_id)
                    tool_text = tool_result_text.pop(event.tool_call_id, "")
                    if tool_text.startswith("[error]"):
                        self._bus.publish(
                            session_id,
                            "error",
                            {"message": tool_text, "tool": tool_name, "retrying": False},
                        )
                    if tool_name in _TASK_TOOL_NAMES:
                        self._bus.publish(session_id, "plan.update", _tasks_snapshot(agent))
                    self._observe_for_drift(session_id, tool_name, event)

                    # For a plan-only turn the structured tool result is the
                    # authoritative MainAgent output.  Stop before a redundant
                    # prose-only model call; this is the main first-turn latency
                    # win.  Search/compare turns must continue for evidence-based
                    # synthesis, so they are intentionally excluded.
                    if tool_name == "update_shopping_plan_tool" and not tool_text.startswith("[error]"):
                        try:
                            committed = json.loads(tool_text.split("\n", 1)[0])
                        except (TypeError, ValueError):
                            committed = {}
                        stage = committed.get("stage")
                        if stage in {"explore", "clarify"} and "search_results" not in committed:
                            final_text = _plan_ack_text(committed)
                            early_final = True
                            break
                        if stage in {"search", "compare"} and "search_results" in committed:
                            final_text = _search_ack_text(committed)
                            early_final = True
                            break
        finally:
            if early_final and hasattr(stream, "aclose"):
                await stream.aclose()
        return final_text

    def _observe_for_drift(self, session_id: str, tool_name: Optional[str], event: Any) -> None:
        """把一次工具结果记进漂移轨迹（开关关时零开销）。"""
        if self._drift_detector is None or not tool_name:
            return
        text = ""
        try:
            blocks = getattr(event, "output", None) or []
            text = "\n".join(
                str(getattr(block, "text", "") or (block.get("text", "") if isinstance(block, dict) else ""))
                for block in blocks
            )
        except Exception:  # noqa: BLE001 —— 观测不能影响主链路
            text = ""
        # “无候选”的判据：检索类工具返回的 hits 为空
        result_empty = bool(text) and ('"hits": []' in text or '"hits":[]' in text)
        self._drift_detector.observe_action(
            session_id, f"{tool_name} {text[:200]}", result_empty=result_empty,
        )

    async def _check_drift(self, session_id: str) -> None:
        """轮末漂移判定：命中只发事件 + 记日志。

        不在这里改写回复——本轮已结束，注入纠正提示已经来不及了；
        漂移信号的价值在于**被看见**（进事件流与 trace，供 bad case 回收）。
        """
        if self._drift_detector is None:
            return
        try:
            report = await self._drift_detector.check(session_id)
        except Exception as err:  # noqa: BLE001
            logger.warning("漂移检测异常，忽略：%s", err)
            return
        if report.drifted:
            logger.warning("检测到静默漂移（会话 %s）：%s", session_id, report.reasons or report.verdict)
            self._bus.publish(
                session_id,
                "error",
                {
                    "message": "检测到可能的目标漂移",
                    "reasons": report.reasons,
                    "verdict": report.verdict,
                },
            )

    def _publish_compression(self, session_id: str, agent: Agent, summary_before: str | None) -> None:
        """上下文压缩发生时，2.0 会把早期消息压成摘要写入 AgentState.summary，
        比对本轮前后的 summary 即可判定并上报。"""
        summary_after = agent.state.summary
        if not summary_after or summary_after == summary_before:
            return
        self._bus.publish(
            session_id,
            "context.compressed",
            {
                "summary_length": len(summary_after),
                "context_messages": len(agent.state.context),
            },
        )

    async def _build_inputs(self, intent: SubmitIntentInput, session_id: str) -> list[Msg]:
        """长期记忆读路径：偏好有变化时随本轮输入注入 hint 消息。"""
        content = intent.raw_query
        request = self._scheme_states.get(session_id) if self._scheme_states else None
        if request is not None:
            content += "\n<shopmind-current-plan>" + json.dumps(
                request.to_dict(), ensure_ascii=False,
            ) + "</shopmind-current-plan>"
        if intent.checklist_selection:
            content += "\n<shopmind-checklist-selection>" + json.dumps(
                intent.checklist_selection, ensure_ascii=False,
            ) + "</shopmind-checklist-selection>"
        user_msg = UserMsg(intent.buyer_id, content)
        try:
            preferences = await self._preference_store.list_by_buyer(intent.buyer_id)
        except Exception as err:  # noqa: BLE001 —— 记忆读取失败不阻断对话
            logger.warning("读取买家偏好失败：%s", err)
            preferences = []
        if not preferences:
            return [user_msg]

        # 按与本轮 query 的相关性挑选：偏好越攒越多时，全量铺进去会把真正相关的那几条稀释。
        # dislike 不参与截断（见 PreferenceSelector 文档字符串）。
        selected = await self._preference_selector.select(
            preferences, query=intent.raw_query, top_k=self._preference_top_k,
        )
        if not selected:
            return [user_msg]

        rendered = render_preference_lines(selected)
        if self._injected_preferences.get(session_id) == rendered:
            return [user_msg]
        self._injected_preferences[session_id] = rendered
        hint_msg = UserMsg("memory_hint", render_preference_hint(selected))
        return [hint_msg, user_msg]
