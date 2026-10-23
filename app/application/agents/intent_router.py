# -*- coding: utf-8 -*-
"""Deterministic edit router for follow-up turns.

The MainAgent owns shopping state.  A checklist click already has a
deterministic commit path; this module gives *typed text* the same treatment
for the edits users make most often, so they do not trigger a full scene
re-plan (which re-emits the whole checklist and costs 20-60s).

Only spelling-level decisions live here: which slot, which field, which
value.  Anything genuinely ambiguous returns ``kind="vague"`` and the
caller falls back to the model.  No catalog or price logic here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.domain.session.shopping_plan import ShoppingPlan

# canonical slot -> spoken aliases.  Keep this list narrow: every entry is a
# claim that we can do without asking the model.
ITEM_ALIASES: dict[str, tuple[str, ...]] = {
    "创可贴": ("创可贴", "创口贴", "band-aid", "bandaid"),
    "消毒湿巾": ("消毒湿巾", "湿巾", "消毒纸巾"),
    "泳衣": ("泳衣", "泳装", "游泳衣"),
    "泳镜": ("泳镜", "游泳镜"),
    "泳帽": ("泳帽", "游泳帽"),
    "沙滩垫": ("沙滩垫", "沙滩毯", "野餐垫"),
    "沙滩拖鞋": ("沙滩拖鞋", "人字拖", "拖鞋"),
    "手机防水袋": ("手机防水袋", "防水袋", "防水手机套"),
    "防晒霜": ("防晒霜", "防晒乳", "防晒"),
    "遮阳帽": ("遮阳帽", "太阳帽", "帽子"),
    "饮用水": ("饮用水", "矿泉水", "水"),
    "T恤": ("T恤", "t恤", "短袖"),
    "短裤": ("短裤",), "内衣": ("内衣",), "袜子": ("袜子",),
    "毛巾": ("毛巾",), "牙刷": ("牙刷",), "牙膏": ("牙膏",),
    "书包": ("书包", "双肩包", "背包"),
    "旅行背包": ("旅行背包", "旅行包"),
    "腰包与防水包": ("腰包", "防水腰包", "旅行腰包"),
    "旅行充电器": ("旅行充电器", "旅行充电头"),
    "证件收纳包": ("证件收纳包", "护照收纳包", "证件包"),
    "行李箱": ("行李箱", "拉杆箱", "登机箱"),
    "学习文具": ("学习文具", "文具套装", "文具礼盒"),
    "笔记本支架": ("笔记本支架", "电脑支架"),
    "床品套件": ("床品套件", "床品四件套", "床单被套"),
    "入住清洁工具": ("入住清洁工具", "清洁工具套装", "清洁用品"),
    "家居收纳": ("家居收纳", "收纳用品"),
    "收纳箱": ("收纳箱", "折叠收纳箱", "布艺收纳箱"),
    "旅行收纳": ("旅行收纳", "旅行收纳袋", "压缩收纳袋"),
    "笔记本电脑": ("笔记本电脑", "电脑"),
    "中性笔": ("中性笔", "签字笔", "水笔", "笔"),
    "笔记本": ("笔记本子", "记事本", "本子", "笔记本"),
    "台灯": ("台灯", "护眼灯", "阅读灯"),
    "桌面收纳盒": ("桌面收纳盒", "笔筒", "收纳盒"),
    "床垫": ("床垫", "床褥"),
    "床单": ("床单", "床品", "四件套", "被套"),
    "枕头": ("枕头",), "被子": ("被子",),
    "衣架": ("衣架",), "垃圾桶": ("垃圾桶",),
    "烧水壶": ("烧水壶", "电热水壶", "水壶"),
    "插线板": ("插线板", "排插", "插排", "插座"),
    "清洁剂": ("清洁剂", "清洁用品", "洗洁精"),
    "垃圾桶袋": ("垃圾袋",),
    "雨伞": ("雨伞", "伞"),
    "充电宝": ("充电宝", "移动电源"),
    "保温杯": ("保温杯", "水杯", "杯子"),
    "婴儿喂养套装": ("婴儿喂养套装", "婴儿喂养用品", "喂养套装"),
    "宠物碗": ("宠物碗", "猫碗", "狗碗"),
    "猫砂盆": ("猫砂盆", "猫厕所", "猫咪厕所"),
    "帐篷": ("帐篷", "露营帐篷"),
    "睡袋": ("睡袋", "露营睡袋"),
    "营地灯": ("营地灯", "露营灯", "帐篷灯"),
}

_BUY_VERBS = re.compile(r"(还想买|再买|还要买|加上|加一个|加一件|帮我搜|搜一下|搜搜|买一个|买一件|买个|要买|需要买|来一个|来一件)")
_DIRECT_ITEM_CUES = re.compile(r"(想买|要买|需要买|买个|买一个|买一件|帮我(?:搜|找|买|推荐)|搜一下|搜索|找一个|找一件|推荐.*(?:个|件|款))")
_LIST_CUES = re.compile(r"(清单|要准备什么|带什么|准备.*(?:东西|物品|清单)|列一下)")
_HAVE_CUES = re.compile(r"(已经?有了?|我有|不用买|不需要|够用|家里有|不用再买)")
_MISSING_CUES = re.compile(r"(没有|没了|缺|没带|用完了|用光了)")
_SKIP_CUES = re.compile(r"(先不买|不买|暂时不|暂不|这次不|不要了|先不要|之后再买)")
_SCENE_WORDS = ("海边", "露营", "开学", "租房", "出差", "旅行", "徒步", "滑雪", "搬家")


@dataclass(frozen=True)
class EditRoute:
    kind: str
    slots: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    budget_major: Optional[float] = None
    preferences: list[str] = field(default_factory=list)
    people: Optional[int] = None


def canonical_item(text: str) -> Optional[str]:
    """Longest alias that literally occurs in the utterance."""
    lowered = text.casefold()
    best: Optional[str] = None
    best_len = 0
    for slot, aliases in ITEM_ALIASES.items():
        for alias in aliases:
            if alias.casefold() in lowered and len(alias) > best_len:
                best, best_len = slot, len(alias)
    return best


def find_plan_slot(text: str, plan: ShoppingPlan) -> Optional[str]:
    """Existing slot named (or aliased) in the utterance; longest wins."""
    lowered = text.casefold()
    best: Optional[str] = None
    best_len = 0
    for slot in [*plan.tasks, *plan.optional, *plan.must_buy, *plan.reusable, *plan.not_buy, *plan.unknowns]:
        for alias in (slot, *ITEM_ALIASES.get(slot, ())):
            if alias.casefold() in lowered and len(alias) > best_len:
                best, best_len = slot, len(alias)
    return best


def parse_budget(text: str) -> Optional[float]:
    if "预算" not in text:
        return None
    match = re.search(r"(\d{2,6}(?:\.\d+)?)\s*(?:元|块|rmb|人民币)?", text)
    if not match:
        return None
    value = float(match.group(1))
    return value if value > 0 else None


def parse_people(text: str) -> Optional[int]:
    match = re.search(r"(\d{1,2})\s*(?:个)?人", text)
    return int(match.group(1)) if match else None


def parse_preferences(text: str) -> list[str]:
    found = []
    for pattern, label in (
        (r"轻便|轻一点|轻量|少占空间|不占地", "轻便少占空间"),
        (r"便宜|实惠|性价比|省钱|划算", "实惠/性价比"),
        (r"耐用|品质|结实|抗造|质量好", "品质耐用"),
        (r"周全|齐全|一次买齐", "准备周全"),
        (r"好看|颜值|美观|搭配", "美观协调"),
    ):
        if re.search(pattern, text):
            found.append(label)
    return found


def route_direct_item(text: str) -> EditRoute:
    """Route a first-turn, explicit product request to a single-slot plan.

    Scene/checklist requests stay on the scene planner.  A named item with a
    direct buy/search verb bypasses full-scene planning and is normalized to
    one catalog slot; unknown categories remain on the normal MainAgent path.
    """
    query = (text or "").strip()
    item = canonical_item(query)
    if not item or not _DIRECT_ITEM_CUES.search(query):
        return EditRoute(kind="none")
    if any(word in query for word in _SCENE_WORDS) and _LIST_CUES.search(query):
        return EditRoute(kind="none")
    return EditRoute(kind="direct_item", added=[item])


def route_edit(text: str, plan: Optional[ShoppingPlan]) -> EditRoute:
    """Classify a follow-up utterance against the committed plan.

    Returns ``kind="none"`` when this is not an edit we can commit without
    the model, so the caller keeps the original LLM path.
    """
    query = (text or "").strip()
    if not query or plan is None:
        return EditRoute(kind="none")

    budget = parse_budget(query)
    if budget is not None and not _BUY_VERBS.search(query):
        return EditRoute(kind="budget", budget_major=budget)

    people = parse_people(query)
    scene_hit = next((word for word in _SCENE_WORDS if word in query), None)
    if scene_hit and re.search(r"改成|换成|不去|改去|变成|换去|不去了", query):
        return EditRoute(kind="scene", people=people)
    if people is not None and people != plan.people:
        return EditRoute(kind="people", people=people)

    slot = find_plan_slot(query, plan)
    if slot:
        if _MISSING_CUES.search(query):
            return EditRoute(kind="set_buy", slots=[slot])
        if _HAVE_CUES.search(query):
            return EditRoute(kind="set_have", slots=[slot])
        if _SKIP_CUES.search(query):
            return EditRoute(kind="set_skip", slots=[slot])
        if _BUY_VERBS.search(query) or re.search(r"要买|买", query):
            return EditRoute(kind="set_buy", slots=[slot])

    item = canonical_item(query)
    if item and (_BUY_VERBS.search(query) or _MISSING_CUES.search(query)):
        if item not in plan.tasks:
            return EditRoute(kind="buy_item", added=[item])
        return EditRoute(kind="set_buy", slots=[item])
    direct = route_direct_item(query)
    if direct.kind == "direct_item":
        if direct.added[0] in plan.tasks:
            return EditRoute(kind="set_buy", slots=direct.added)
        return EditRoute(kind="buy_item", added=direct.added)

    # “再便宜点”没有给出对象或数值：先问，别猜成一次偏好重排。
    if re.search(r"再便宜|便宜点|更好|换个|优化|调整一下", query) and not re.search(
        r"要|优先|尽量|希望|预算", query,
    ):
        return EditRoute(kind="vague")

    preferences = parse_preferences(query)
    if preferences and len(query) <= 30:
        return EditRoute(kind="preference", preferences=preferences)

    return EditRoute(kind="none")
