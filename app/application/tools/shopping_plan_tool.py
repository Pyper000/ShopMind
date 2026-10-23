# -*- coding: utf-8 -*-
"""MainAgent tool for publishing one structured shopping mission."""
import json
from dataclasses import replace
from typing import Awaitable, Callable
from typing import Optional

from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolChunk

from app.domain.session.shopping_plan import ShoppingPlan
from app.application.agents.scheme_state import CandidatePool, SchemeStateRegistry
from app.infrastructure.context import ShoppingContext
from app.infrastructure.eventbus import TradeEventBus


_BROAD_SLOT_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "游泳装备": ("泳衣", "泳镜", "沙滩拖鞋"),
    "防晒用品": ("防晒霜", "遮阳帽"),
    "换洗衣物": ("T恤", "短裤", "内衣", "袜子"),
    "应急物品": ("创可贴", "消毒湿巾"),
    "洗漱用品": ("牙刷", "牙膏", "毛巾"),
    "床上用品": ("床单", "被子", "枕头"),
    "清洁用品": ("垃圾袋", "抹布", "清洁剂"),
}


def _expand_initial_broad_slots(
    values: list[str] | None,
) -> tuple[list[str], list[str]]:
    """Split model-generated umbrella slots into checkable MVP items.

    This is a narrow validation fallback for the *initial* natural-language
    turn.  It does not infer ownership or purchase intent: every expanded item
    remains unclassified until the buyer clicks a checklist state.  Explicit
    checklist updates still go through the strict rejection path below.
    """
    expanded: list[str] = []
    found: list[str] = []
    for value in values or []:
        replacements = _BROAD_SLOT_EXPANSIONS.get(value.strip())
        if replacements:
            found.append(value.strip())
            expanded.extend(replacements)
        else:
            expanded.append(value)
    return list(dict.fromkeys(expanded)), found


def _apply_checklist_selection(plan: ShoppingPlan, selection: dict) -> ShoppingPlan:
    """Apply explicit UI facts to MainAgent's existing plan; never re-interpret clicks as prose."""
    buckets = {
        "have": set(selection.get("have", [])),
        "must_buy": set(selection.get("must_buy", [])),
        "optional_buy": set(selection.get("optional_buy", [])),
        "deferred": set(selection.get("deferred", [])),
        "unknown": set(selection.get("unknown", [])),
    }
    names = [name for values in buckets.values() for name in values]
    allowed = set(plan.tasks) | set(plan.optional)
    if any(not isinstance(name, str) or name not in allowed for name in names):
        raise ValueError("清单状态包含当前方案之外的物品，请刷新清单后重试")
    if sum(map(len, buckets.values())) != len(set(names)):
        raise ValueError("同一物品不能同时选择多个清单状态")

    optional_slots = set(plan.optional)
    required_slots = set(plan.tasks) - optional_slots
    if buckets["must_buy"] - required_slots or buckets["optional_buy"] - optional_slots:
        raise ValueError("待购状态与清单物品类别不匹配，请刷新清单后重试")
    # Any omitted checkbox remains pending; a click is the only source of a status change.
    classified = set(names)
    pending = allowed - classified | buckets["unknown"]
    priority = {
        "lightweight": "轻便少占空间", "value": "实惠/性价比",
        "quality": "品质耐用", "complete": "准备周全", "appearance": "美观协调",
    }.get(selection.get("priority", ""))
    preferences = [priority] if priority else plan.preferences
    budget = selection.get("budget_major") or plan.budget_major
    has_purchase = bool(buckets["must_buy"] or buckets["optional_buy"])
    return replace(
        plan,
        stage="search" if has_purchase else "confirm",
        budget_major=budget,
        preferences=preferences,
        must_buy=sorted(buckets["must_buy"], key=plan.tasks.index),
        optional=sorted(buckets["optional_buy"], key=plan.optional.index),
        reusable=sorted(buckets["have"]),
        not_buy=sorted(buckets["deferred"]),
        unknowns=sorted(pending),
    )


def commit_checklist_selection(
    bus: TradeEventBus,
    scheme_states: SchemeStateRegistry,
    candidate_pool: CandidatePool | None,
    session_id: str,
    selection: dict,
):
    """Commit explicit UI choices without asking an LLM to re-parse button clicks."""
    existing = scheme_states.get(session_id)
    if existing is None:
        raise ValueError("找不到待确认的方案，请重新生成清单")
    plan = _apply_checklist_selection(existing.scheme, selection)
    request = scheme_states.update(session_id, plan)
    if candidate_pool:
        candidate_pool.apply(session_id, request.mutation)
    bus.publish(
        session_id,
        "plan.update",
        {
            "shopping_plan": request.scheme.to_dict(),
            "mutation": request.mutation.to_dict(),
            "search_request": request.to_dict(),
        },
    )
    return request


def build_update_shopping_plan_tool(
    bus: TradeEventBus,
    scheme_states: SchemeStateRegistry | None = None,
    candidate_pool: CandidatePool | None = None,
    dispatch_search: Callable[[], Awaitable[list[dict]]] | None = None,
):
    submitted_turn: dict[str, str] = {}

    async def update_shopping_plan_tool(
        goal: str,
        tasks: list[str],
        stage: str = "explore",
        must_buy: list[str] | None = None,
        optional: list[str] | None = None,
        unknowns: list[str] | None = None,
        reusable: list[str] | None = None,
        not_buy: list[str] | None = None,
        prep: list[str] | None = None,
        missing_slots: list[str] | None = None,
        core_use: Optional[str] = None,
        people: int | str | None = None,
        destination: Optional[str] = None,
        budget_major: float | str | None = None,
        currency: str = "CNY",
        preferences: list[str] | None = None,
        excludes: list[str] | None = None,
    ) -> ToolChunk:
        """Create or update the structured plan for a scene-based shopping request.

        Args:
            goal (`str`): The buyer's overall mission, such as "prepare for a US camping trip".
            tasks (`list[str]`): At least one individually checkable item, e.g. swimsuit and goggles as separate entries.
            stage (`str`): One of explore, clarify, search, compare, confirm, service.
            core_use (`str | None`): The main activity or purpose that defines this shopping mission.
            people (`int | None`): Number of people; leave unknown as None.
            destination (`str | None`): Two-letter destination code, such as US or JP.
            budget_major (`float | None`): Total budget in major currency units.
            currency (`str`): Budget currency, default CNY.
            preferences (`list[str] | None`): Explicit preferences such as lightweight or durable.
            tasks (`list[str] | None`): Up to 24 individually checkable items, e.g. swimsuit and goggles as separate entries; never umbrella categories.
            must_buy (`list[str] | None`): Confirmed missing items to purchase.
            optional (`list[str] | None`): Optional purchase slots.
            reusable (`list[str] | None`): Items the buyer already owns.
            not_buy (`list[str] | None`): Deferred or explicitly unwanted purchase slots.
            unknowns (`list[str] | None`): Purchase status not yet confirmed.
            excludes (`list[str] | None`): Explicit product exclusions.
            prep (`list[str] | None`): Non-product reminders; never searched.
            missing_slots (`list[str] | None`): Important unknowns that may require one question.
        """
        snapshot = ShoppingContext.current()
        raw_query = snapshot.raw_query if snapshot else ""
        # Validate at the tool boundary, not when loading historical saved schemes.
        # A model occasionally emits a broad label on the first explore turn.
        # Expand that one malformed draft locally so a repair model call does
        # not add another 10–20 seconds.  Direct tool calls and explicit UI
        # selections retain the strict error contract covered by unit tests.
        broad_slots = set(_BROAD_SLOT_EXPANSIONS) | {"应急药品"}
        all_values = (tasks, must_buy, optional, reusable, not_buy, unknowns)
        ambiguous = sorted({
            item.strip() for items in all_values
            for item in (items or []) if isinstance(item, str) and item.strip() in broad_slots
        })
        can_repair_draft = bool(
            raw_query and not (snapshot and snapshot.checklist_selection)
            and stage in {"explore", "clarify"}
        )
        if ambiguous and can_repair_draft:
            repaired_prep = list(prep or [])
            if "应急药品" in ambiguous:
                repaired_prep.append("请自行核对个人常用药；本清单不自动推荐药品")
            repaired = {
                "tasks": tasks,
                "must_buy": must_buy,
                "optional": optional,
                "reusable": reusable,
                "not_buy": not_buy,
                "unknowns": unknowns,
            }
            for field, values in repaired.items():
                values, _ = _expand_initial_broad_slots(values)
                repaired[field] = [item for item in values if item != "应急药品"]
            tasks = repaired["tasks"]
            must_buy = repaired["must_buy"]
            optional = repaired["optional"]
            reusable = repaired["reusable"]
            not_buy = repaired["not_buy"]
            unknowns = repaired["unknowns"]
            prep = repaired_prep
        elif ambiguous:
            return ToolChunk(
                content=[TextBlock(type="text", text=
                    "[error] 清单项过于概括：" + "、".join(ambiguous)
                    + "。请拆成可独立核对的具体物品，例如泳衣、泳镜；不要把组合项的已有或待购状态"
                    "直接赋给所有子项，未明确的子项保持待确认。药品仅放个人自备提醒，不自动推荐。")],
                state=ToolResultState.ERROR,
            )
        if isinstance(budget_major, str):
            try:
                budget_major = float(budget_major)
            except ValueError:
                return ToolChunk(
                    content=[TextBlock(type="text", text=f"[error] budget_major 非法：{budget_major}")],
                    state=ToolResultState.ERROR,
                )
        if isinstance(people, str):
            try:
                people = int(people)
            except ValueError:
                return ToolChunk(
                    content=[TextBlock(type="text", text=f"[error] people 非法：{people}")],
                    state=ToolResultState.ERROR,
                )
        # Keep the checklist complete even when the model puts named items
        # only in a status bucket and leaves the display task list empty.
        tasks = list(dict.fromkeys([
            *(tasks or []), *(must_buy or []), *(reusable or []),
            *(not_buy or []), *(unknowns or []),
        ]))
        # A long first draft should not trigger a second LLM repair turn just
        # because the model emitted one or two low-priority tail items.  Keep
        # the first 24 concrete slots in the visible checklist; later explicit
        # UI edits remain subject to the normal hard limit.
        if len(tasks) > 24 and not can_repair_draft:
            return ToolChunk(
                content=[TextBlock(type="text", text="[error] 单份清单最多包含 24 件具体物品；请拆分为多个任务后重试。")],
                state=ToolResultState.ERROR,
            )
        if can_repair_draft and len(tasks) > 24:
            tasks = tasks[:24]
            allowed_tasks = set(tasks)
            must_buy = [item for item in (must_buy or []) if item in allowed_tasks]
            optional = [item for item in (optional or []) if item in allowed_tasks]
            reusable = [item for item in (reusable or []) if item in allowed_tasks]
            not_buy = [item for item in (not_buy or []) if item in allowed_tasks]
            unknowns = [item for item in (unknowns or []) if item in allowed_tasks]
        # Missing status in an initial draft means pending confirmation, not owned or buy-now.
        if can_repair_draft:
            classified = set(must_buy or []) | set(optional or []) | set(reusable or []) | set(not_buy or []) | set(unknowns or [])
            unknowns = list(dict.fromkeys([*(unknowns or []), *(item for item in tasks if item not in classified)]))
        if not tasks:
            return ToolChunk(
                content=[TextBlock(
                    type="text",
                    text="[error] 购物清单不能为空。请提交至少一项可独立核对的具体物品；可有可无的项目放 optional，未确认的项目保留 unknowns。",
                )],
                state=ToolResultState.ERROR,
            )
        try:
            plan = ShoppingPlan(
                goal=goal,
                core_use=core_use,
                people=people,
                stage=stage,
                destination=destination.upper() if destination else None,
                budget_major=budget_major,
                currency=currency.upper(),
                preferences=preferences or [],
                tasks=tasks or [],
                must_buy=must_buy or [],
                optional=optional or [],
                reusable=reusable or [],
                not_buy=not_buy or [],
                unknowns=unknowns or [],
                excludes=excludes or [],
                prep=prep or [],
                missing_slots=missing_slots or [],
            )
        except ValueError as err:
            return ToolChunk(
                content=[TextBlock(type="text", text=f"[error] {err}")],
                state=ToolResultState.ERROR,
            )

        session_id = ShoppingContext.current_session_id()
        if snapshot and snapshot.checklist_selection and scheme_states:
            existing = scheme_states.get(session_id)
            if existing is None:
                return ToolChunk(
                    content=[TextBlock(type="text", text="[error] 找不到待确认的方案，请重新生成清单")],
                    state=ToolResultState.ERROR,
                )
            try:
                plan = _apply_checklist_selection(existing.scheme, snapshot.checklist_selection)
            except ValueError as err:
                return ToolChunk(
                    content=[TextBlock(type="text", text=f"[error] {err}")],
                    state=ToolResultState.ERROR,
                )
        if raw_query and submitted_turn.get(session_id) == raw_query and scheme_states:
            existing = scheme_states.get(session_id)
            if existing is not None:
                payload = existing.scheme.to_dict()
                payload["mutation"] = existing.mutation.to_dict()
                payload["search_request"] = existing.to_dict()
                return ToolChunk(
                    content=[TextBlock(
                        type="text",
                        text=json.dumps(payload, ensure_ascii=False)
                        + "\n[notice] 本轮方案已经提交，不得再次更新；请直接生成最终回复。",
                    )],
                    state=ToolResultState.SUCCESS,
                )
        request = scheme_states.update(session_id, plan) if scheme_states else None
        if raw_query:
            submitted_turn[session_id] = raw_query
        if request and candidate_pool:
            candidate_pool.apply(session_id, request.mutation)
        if request:
            plan = request.scheme
        payload = plan.to_dict()
        if request:
            payload["mutation"] = request.mutation.to_dict()
            payload["search_request"] = request.to_dict()
        bus.publish(
            session_id,
            "plan.update",
            {
                "shopping_plan": plan.to_dict(),
                **({"mutation": request.mutation.to_dict(), "search_request": request.to_dict()} if request else {}),
            },
        )
        if dispatch_search and plan.stage in {"search", "compare"}:
            payload["search_results"] = await dispatch_search()
        return ToolChunk(
            content=[TextBlock(type="text", text=json.dumps(payload, ensure_ascii=False))],
            state=ToolResultState.SUCCESS,
        )

    return update_shopping_plan_tool
