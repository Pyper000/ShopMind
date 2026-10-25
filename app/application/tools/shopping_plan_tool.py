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
from app.application.travel_weather import apply_weather_context
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

_MODULE_HINTS = (
    ("宠物安全与外出", ("猫包", "航空箱", "猫笼", "牵引绳", "身份牌", "外出笼")),
    ("宠物护理与健康", ("梳毛", "指甲剪", "驱虫", "疫苗", "健康记录", "宠物医院", "猫抓板", "猫爬架", "猫窝")),
    ("证件与安全", ("证件", "身份证", "护照", "急救", "安全", "手机", "充电", "照明", "导航", "保险")),
    ("吃喝与补给", ("饮", "水", "食物", "食品", "粮", "喂养", "食盆", "水碗", "零食", "能量", "补给", "炊具", "炉具")),
    ("衣物与个人用品", ("衣", "服", "袜", "鞋", "帽", "毛巾", "洗漱", "牙刷", "牙膏", "内裤")),
    ("清洁与卫生", ("清洁", "拖把", "扫把", "垃圾", "抹布", "湿巾", "猫砂", "厕所", "如厕", "卫生", "洗衣")),
    ("居住与休息", ("床", "睡", "枕", "被", "毯", "帐篷", "睡袋", "猫窝", "躲藏", "防潮垫", "灯")),
    ("学习与工作", ("学习", "文具", "书包", "笔", "电脑", "办公", "支架")),
    ("活动与装备", ("泳", "沙滩", "防晒", "遮阳", "旅行", "背包", "户外", "营地", "装备")),
)
_NON_PURCHASABLE_TERMS = (
    "身份证件", "身份证", "护照", "签证", "驾驶证", "驾照", "医保卡", "社保卡",
    "银行卡", "出生证明", "结婚证", "产检资料", "入院资料", "入院凭证", "病历",
    "就诊资料", "疫苗记录", "健康记录", "处方药",
)


def _is_non_purchasable_item(name: str) -> bool:
    if any(suffix in name for suffix in ("收纳包", "收纳袋", "证件包", "证件夹", "护照夹", "证件套", "卡套")):
        return False
    return any(term in name for term in _NON_PURCHASABLE_TERMS)


def _is_non_purchasable_slot(plan: ShoppingPlan, name: str) -> bool:
    return _is_non_purchasable_item(name) or any(
        item.get("name") == name and item.get("purchasable") is False
        for section in plan.checklist_sections
        for bucket in ("required", "optional")
        for item in section.get(bucket, [])
    )


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


def _normalize_checklist_sections(values: list[dict] | None) -> list[dict]:
    sections = []
    seen_items: set[str] = set()
    for index, value in enumerate(values or []):
        if not isinstance(value, dict):
            continue
        title = str(value.get("title") or "").strip()
        if not title:
            continue

        def normalize_items(items) -> list[dict]:
            result = []
            for item in items or []:
                item = {"name": item} if isinstance(item, str) else item
                if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                    continue
                name = str(item["name"]).strip()
                if name in seen_items:
                    continue
                seen_items.add(name)
                entry = {
                    "name": name,
                    "purchasable": item.get("purchasable") is not False and not _is_non_purchasable_item(name),
                }
                for key in ("quantity", "condition", "note"):
                    if item.get(key) is not None and str(item[key]).strip():
                        entry[key] = str(item[key]).strip()
                result.append(entry)
            return result

        section = {
            "id": str(value.get("id") or f"module-{index + 1}"),
            "title": title,
            "required": normalize_items(value.get("required")),
            "optional": normalize_items(value.get("optional")),
        }
        reference = value.get("reference")
        if isinstance(reference, dict) and reference.get("title"):
            section["reference"] = {
                key: str(reference[key]) for key in ("title", "url", "note")
                if reference.get(key) is not None
            }
        if section["required"] or section["optional"]:
            sections.append(section)
    return sections[:12]


def _fallback_checklist_sections(tasks: list[str | dict], optional: list[str]) -> list[dict]:
    """Keep first-time open-scene plans grouped if a model omits its section structure."""
    optional_set = set(optional or [])
    grouped: dict[str, dict] = {}
    for value in tasks or []:
        item = {"name": value} if isinstance(value, str) else value
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            continue
        name = str(item["name"]).strip()
        title = next((module for module, hints in _MODULE_HINTS if any(hint in name for hint in hints)), "任务准备")
        # ponytail: fixed item-name hints keep fallback grouping bounded; replace with a scene taxonomy only if coverage grows.
        if title not in grouped and len(grouped) >= 5:
            title = "其他准备"
        section = grouped.setdefault(title, {
            "id": f"module-{len(grouped) + 1}", "title": title, "required": [], "optional": [],
        })
        bucket = "optional" if name in optional_set else "required"
        entry = {
            "name": name,
            "purchasable": item.get("purchasable") is not False and not _is_non_purchasable_item(name),
        }
        for key in ("quantity", "condition", "note"):
            if item.get(key) is not None and str(item[key]).strip():
                entry[key] = str(item[key]).strip()
        section[bucket].append(entry)
    return list(grouped.values())


def _expand_section_broad_slots(sections: list[dict]) -> tuple[list[dict], list[str]]:
    expanded: list[dict] = []
    found: list[str] = []
    for section in sections:
        next_section = {**section, "required": [], "optional": []}
        for bucket in ("required", "optional"):
            for item in section[bucket]:
                name = item["name"]
                replacements = _BROAD_SLOT_EXPANSIONS.get(name)
                if name == "应急药品":
                    found.append(name)
                    continue
                if replacements:
                    found.append(name)
                    next_section[bucket].extend({
                        "name": value, "purchasable": not _is_non_purchasable_item(value),
                    } for value in replacements)
                else:
                    next_section[bucket].append(item)
        if next_section["required"] or next_section["optional"]:
            expanded.append(next_section)
    return expanded, found


def _section_names(sections: list[dict]) -> tuple[list[str], list[str]]:
    required = list(dict.fromkeys(
        item["name"] for section in sections for item in section["required"]
    ))
    optional = list(dict.fromkeys(
        item["name"] for section in sections for item in section["optional"]
        if item["name"] not in required
    ))
    return required, optional


def _filter_checklist_sections(sections: list[dict], allowed: set[str]) -> list[dict]:
    filtered = []
    for section in sections:
        current = {
            **section,
            "required": [item for item in section["required"] if item["name"] in allowed],
            "optional": [item for item in section["optional"] if item["name"] in allowed],
        }
        if current["required"] or current["optional"]:
            filtered.append(current)
    return filtered


def _apply_checklist_selection(plan: ShoppingPlan, selection: dict) -> ShoppingPlan:
    """Apply explicit UI facts to MainAgent's existing plan; never re-interpret clicks as prose."""
    buckets = {
        "have": set(selection.get("have", [])),
        "prepare": set(selection.get("prepare", [])),
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
    non_purchasable = {name for name in allowed if _is_non_purchasable_slot(plan, name)}
    if (buckets["must_buy"] | buckets["optional_buy"]) & non_purchasable:
        raise ValueError("证件、资料等非商品只能标记为需要准备，不能进入商品搜索")
    prepared_only = buckets["prepare"] & non_purchasable
    prepared_products = buckets["prepare"] - prepared_only
    must_buy = buckets["must_buy"] | (prepared_products & required_slots)
    optional_buy = buckets["optional_buy"] | (prepared_products & optional_slots)
    if must_buy - required_slots or optional_buy - optional_slots:
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
    has_purchase = bool(must_buy or optional_buy)
    return replace(
        plan,
        stage="search" if has_purchase else "confirm",
        budget_major=budget,
        preferences=preferences,
        must_buy=sorted(must_buy, key=plan.tasks.index),
        optional=sorted(optional_buy, key=plan.optional.index),
        reusable=sorted(buckets["have"]),
        prepared=sorted(prepared_only),
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
        checklist_sections: list[dict] | None = None,
        stage: str = "explore",
        must_buy: list[str] | None = None,
        optional: list[str] | None = None,
        unknowns: list[str] | None = None,
        reusable: list[str] | None = None,
        prepared: list[str] | None = None,
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
            tasks (`list[str]`): Up to 24 individually checkable items; never umbrella categories.
            checklist_sections (`list[dict] | None`): Modules with required/optional concrete items and quantities/conditions.
            stage (`str`): One of explore, clarify, search, compare, confirm, service.
            core_use (`str | None`): The main activity or purpose that defines this shopping mission.
            people (`int | None`): Number of people; leave unknown as None.
            destination (`str | None`): Two-letter destination code, such as US or JP.
            budget_major (`float | None`): Total budget in major currency units.
            currency (`str`): Budget currency, default CNY.
            preferences (`list[str] | None`): Explicit preferences such as lightweight or durable.
            must_buy (`list[str] | None`): Confirmed missing items to purchase.
            optional (`list[str] | None`): Optional purchase slots.
            reusable (`list[str] | None`): Items the buyer already owns.
            prepared (`list[str] | None`): Non-purchasable items the buyer will bring or prepare.
            not_buy (`list[str] | None`): Deferred or explicitly unwanted purchase slots.
            unknowns (`list[str] | None`): Purchase status not yet confirmed.
            excludes (`list[str] | None`): Explicit product exclusions.
            prep (`list[str] | None`): Non-product reminders; never searched.
            missing_slots (`list[str] | None`): Important unknowns that may require one question.
        """
        snapshot = ShoppingContext.current()
        raw_query = snapshot.raw_query if snapshot else ""
        session_id = ShoppingContext.current_session_id()
        can_repair_draft = bool(
            raw_query and not (snapshot and snapshot.checklist_selection)
            and stage in {"explore", "clarify"}
        )
        existing = scheme_states.get(session_id) if scheme_states else None
        sections = _normalize_checklist_sections(checklist_sections)
        if len(sections) == 1 and sections[0]["title"] in {"其他", "其他准备", "通用清单", "任务准备"}:
            sections = []
        # A same-goal edit that omitted sections keeps the current module structure;
        # a new goal must supply modules or receive freshly grouped fallback modules.
        if (not sections and existing is not None
                and existing.scheme.goal == goal
                and existing.scheme.checklist_sections):
            sections = _normalize_checklist_sections(existing.scheme.checklist_sections)
        # Validate at the tool boundary, not when loading historical saved schemes.
        # A model occasionally emits a broad label on the first explore turn.
        # Expand that one malformed draft locally so a repair model call does
        # not add another 10–20 seconds.  Direct tool calls and explicit UI
        # selections retain the strict error contract covered by unit tests.
        broad_slots = set(_BROAD_SLOT_EXPANSIONS) | {"应急药品"}
        section_slots = [
            item["name"] for section in sections
            for bucket in ("required", "optional") for item in section[bucket]
        ]
        all_values = (tasks, must_buy, optional, reusable, prepared, not_buy, unknowns, section_slots)
        ambiguous = sorted({
            item.strip() for items in all_values
            for item in (items or []) if isinstance(item, str) and item.strip() in broad_slots
        })
        if ambiguous and can_repair_draft:
            repaired_prep = list(prep or [])
            repaired = {
                "tasks": tasks,
                "must_buy": must_buy,
                "optional": optional,
                "reusable": reusable,
                "prepared": prepared,
                "not_buy": not_buy,
                "unknowns": unknowns,
            }
            for field, values in repaired.items():
                values, _ = _expand_initial_broad_slots(values)
                repaired[field] = [item for item in values if item != "应急药品"]
            sections, section_broad = _expand_section_broad_slots(sections)
            if "应急药品" in ambiguous or "应急药品" in section_broad:
                repaired_prep.append("请自行核对个人常用药；本清单不自动推荐药品")
            tasks = repaired["tasks"]
            must_buy = repaired["must_buy"]
            optional = repaired["optional"]
            reusable = repaired["reusable"]
            prepared = repaired["prepared"]
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
        if sections:
            # Sections are the authoritative structure. Also preserve individually
            # named slots emitted in top-level buckets by assigning them to a module.
            known_required, known_optional = _section_names(sections)
            optional_set = set(optional or [])
            uncovered = list(dict.fromkeys([
                *(tasks or []), *(must_buy or []), *(optional or []),
                *(reusable or []), *(prepared or []), *(not_buy or []), *(unknowns or []),
            ]))
            uncovered = [item for item in uncovered if item not in set(known_required + known_optional)]
            if uncovered:
                additions = _fallback_checklist_sections(uncovered, list(optional_set))
                sections.extend(additions[: max(0, 12 - len(sections))])

        if can_repair_draft and len(sections) > 6:
            items = [
                item for section in sections
                for bucket in ("required", "optional") for item in section[bucket]
            ]
            optional_items = [
                item["name"] for section in sections for item in section["optional"]
            ]
            sections = _fallback_checklist_sections(items, optional_items)

        if not sections and can_repair_draft:
            # Freeform scenes use the model-generated concrete slots, but never
            # leave them as one flat checklist when the model omitted structure.
            sections = _fallback_checklist_sections(
                list(dict.fromkeys([
                    *(tasks or []), *(must_buy or []), *(optional or []),
                    *(reusable or []), *(prepared or []), *(not_buy or []), *(unknowns or []),
                ])),
                optional or [],
            )
        if sections:
            section_required, section_optional = _section_names(sections)
            section_names = list(dict.fromkeys([*section_required, *section_optional]))
            tasks = list(dict.fromkeys([*section_names, *(tasks or [])]))
            # During initial planning, category optional means “按需准备”; later
            # it means an explicitly selected optional purchase and must not change.
            if can_repair_draft:
                optional = section_optional
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
            *(prepared or []), *(not_buy or []), *(unknowns or []),
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
            sections = _filter_checklist_sections(sections, allowed_tasks)
            must_buy = [item for item in (must_buy or []) if item in allowed_tasks]
            optional = [item for item in (optional or []) if item in allowed_tasks]
            reusable = [item for item in (reusable or []) if item in allowed_tasks]
            prepared = [item for item in (prepared or []) if item in allowed_tasks]
            not_buy = [item for item in (not_buy or []) if item in allowed_tasks]
            unknowns = [item for item in (unknowns or []) if item in allowed_tasks]
        # Missing status in an initial draft means pending confirmation, not owned or buy-now.
        if can_repair_draft:
            classified = set(must_buy or []) | set(optional or []) | set(reusable or []) | set(prepared or []) | set(not_buy or []) | set(unknowns or [])
            unknowns = list(dict.fromkeys([*(unknowns or []), *(item for item in tasks if item not in classified)]))
        if stage in {"search", "compare"}:
            non_purchasable = {
                item["name"] for section in sections
                for bucket in ("required", "optional") for item in section[bucket]
                if item.get("purchasable") is False or _is_non_purchasable_item(item["name"])
            }
            prepared = list(dict.fromkeys([
                *(prepared or []),
                *(item for item in [*(must_buy or []), *(optional or [])] if item in non_purchasable),
            ]))
            must_buy = [item for item in (must_buy or []) if item not in non_purchasable]
            optional = [item for item in (optional or []) if item not in non_purchasable]
            if not must_buy and not optional:
                stage = "confirm"
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
                prepared=prepared or [],
                not_buy=not_buy or [],
                unknowns=unknowns or [],
                excludes=excludes or [],
                prep=prep or [],
                missing_slots=missing_slots or [],
                checklist_sections=sections,
            )
        except ValueError as err:
            return ToolChunk(
                content=[TextBlock(type="text", text=f"[error] {err}")],
                state=ToolResultState.ERROR,
            )

        if snapshot and snapshot.weather_context:
            plan = apply_weather_context(plan, snapshot.weather_context)

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
