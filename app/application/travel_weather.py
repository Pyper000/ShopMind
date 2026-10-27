# -*- coding: utf-8 -*-
"""Turn fetched travel weather into transparent, optional checklist suggestions."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
import math
import re

from app.domain.session.shopping_plan import ShoppingPlan


def update_travel_party(query: str, previous: dict | None = None) -> dict | None:
    """Merge only explicit party facts; an age-only reply never erases adults."""
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6}
    adult = re.search(r"(\d+|[一二两三四五六])\s*(?:个)?(?:成人|大人)", query)
    child_terms = r"小孩|孩子|儿童|弟弟|妹妹|哥哥|姐姐|儿子|女儿|宝宝|宝贝"
    ages = [int(age) for age in re.findall(rf"(\d{{1,2}})\s*岁.{{0,5}}(?:{child_terms})", query)]
    if not ages:
        ages = [int(age) for age in re.findall(rf"(?:{child_terms}).{{0,5}}(\d{{1,2}})\s*岁", query)]
    solo = bool(re.search(r"(?:只|就)?我(?:自己|一个人)(?:去|出行|旅行|旅游)?", query))
    family_terms = ("孩子", "小孩", "儿童", "弟弟", "妹妹", "哥哥", "姐姐", "儿子", "女儿")
    no_children = bool(
        re.search(r"(?:不带|没有|取消带).{0,4}(?:孩子|小孩|儿童|弟弟|妹妹|哥哥|姐姐|儿子|女儿)?", query)
        or re.search(r"(?:孩子|小孩|儿童|弟弟|妹妹|哥哥|姐姐|儿子|女儿).{0,5}(?:不去|不来了|不同行)", query)
    ) and any(term in query for term in family_terms)
    traveling_with_parents = bool(re.search(r"我\s*(?:和|跟)\s*(?:我)?(?:的)?(?:父母|爸妈|爸爸妈妈|爸爸和妈妈)", query))
    mentions_parents = any(term in query for term in ("父母", "爸妈", "爸爸妈妈", "爸爸和妈妈"))
    if not (adult or ages or solo or no_children or mentions_parents):
        return None
    party = {**(previous or {"adults": 0, "child_ages": []})}
    party["child_ages"] = list(party.get("child_ages") or [])
    if adult:
        raw = adult.group(1)
        party["adults"] = int(raw) if raw.isdigit() else digits[raw]
    elif traveling_with_parents:
        party["adults"] = 3
    elif mentions_parents and not party.get("adults"):
        party["adults"] = 2
    elif solo or (ages and re.search(r"我和|我们|带", query) and not party.get("adults")):
        party["adults"] = 1
    if ages:
        party["child_ages"] = ages
    if solo or no_children:
        party["child_ages"] = []
    return party


def travel_activity_patch(query: str) -> dict:
    """The last explicit swim/cancel statement wins within this turn."""
    patch = {}
    for mention in re.finditer(r"游泳|下水", query):
        prefix = query[max(0, mention.start() - 8):mention.start()]
        prefix = re.split(r"[，,。；;]", prefix)[-1]
        if mention.group() == "游泳" and re.search(r"(?:决定|后来|改为|改成|然后|不过)\s*$", prefix):
            prefix = ""
        patch["swimming"] = not bool(re.search(r"取消|不再|不打算|不计划|不去|不想|不需要|不要|不|没有|无需", prefix))
    return patch


def _apply_travel_activities(plan: ShoppingPlan, context: dict) -> ShoppingPlan:
    swimming = (context.get("activities") or {}).get("swimming")
    if swimming is None:
        return plan
    sections = [{**section, **{bucket: [dict(item) for item in section.get(bucket, [])
                 if not item.get("activity_generated") and not any(term in item.get("name", "") for term in ("泳衣", "泳裤", "泳镜", "泳帽"))]
                 for bucket in ("required", "optional")}} for section in plan.checklist_sections]
    prep = list(plan.prep)
    if swimming:
        target = next((section for section in sections if section.get("id") == "travel-activities" or "游泳" in section.get("title", "")), None)
        if target is None:
            if len(sections) < 6:
                target = {"id": "travel-activities", "title": "活动与装备", "required": [], "optional": []}
                sections.append(target)
            else:
                target = sections[-1]
        members = _party(context, None) or [("", plan.people or 1)]
        for label, count in members:
            for name, unit, bucket in (("泳衣", "套", "required"), ("泳镜", "副", "optional")):
                target[bucket].append({
                    "name": label + name, "quantity": f"{count}{unit}" if label else f"1{unit}/人",
                    "condition": "仅在确认下水游泳且场地开放、天气及现场条件允许时准备；按游泳次数和晾晒条件调整",
                    "purchasable": True, "activity_generated": True,
                })
        prep.append("已记录计划游泳；若取消下水会移除游泳专用装备，不改变城市、日期和日常衣物数量。")
    else:
        prep.append("已记录取消下水；游泳专用装备已移除，仍保留日常衣物及适用的防晒、雨雪准备。")
    sections = [section for section in sections if section["required"] or section["optional"]]
    return _sync_checklist(plan, sections, prep=list(dict.fromkeys(prep)))


def _clothing_quantity(days: int, unit: str, people: int | None) -> str:
    count = days + 1
    if people:
        return f"{count * people}{unit}（{people}人×{days}天+每人1{unit}备用）"
    return f"{count}{unit}/人（{days}天×每天1{unit}+1{unit}备用）"


def _upperwear_quantity(days: int, people: int | None) -> str:
    count = days + 1
    if people:
        return f"{count * people}件（{people}人×{days}套日常穿搭+每人1件备用上衣）"
    return f"{count}件/人（{days}套日常穿搭+1件备用上衣）"


def _one_per_person(unit: str, people: int | None) -> str:
    return f"{people}{unit}（每人1{unit}）" if people else f"1{unit}/人"


def _bottom_quantity(days: int, people: int | None) -> str:
    count = max(1, (days + 1) // 2)
    if people:
        return f"{count * people}条（{people}人×约每2天轮换1条；每天更换则准备{days * people}条）"
    return f"{count}条/人（约每2天轮换1条；每天更换则准备{days}条）"


def _outfit_quantity(days: int, people: int | None) -> str:
    count = days + 1
    if people:
        return f"{count * people}套（{people}人×{days}天+每人1套备用）"
    return f"{count}套/人（{days}天每日1套+1套备用）"


def _party(context: dict, fallback_people: int | None) -> list[tuple[str, int]]:
    party = context.get("travel_party") or {}
    members = []
    adults = int(party.get("adults") or 0)
    if adults:
        members.append(("本人" if adults == 1 else "成人", adults))
    ages = party.get("child_ages") or []
    for age in dict.fromkeys(ages):
        count = ages.count(age)
        members.append((f"{age}岁儿童", count))
    if not members and fallback_people:
        members.append(("同行人员", fallback_people))
    return members


def _sync_checklist(plan: ShoppingPlan, sections: list[dict], **changes) -> ShoppingPlan:
    required_names = list(dict.fromkeys(
        item["name"] for section in sections for item in section.get("required", [])
    ))
    optional_names = list(dict.fromkeys(
        item["name"] for section in sections for item in section.get("optional", [])
        if item["name"] not in required_names
    ))
    names = required_names + optional_names
    for field in ("must_buy", "reusable", "prepared", "not_buy"):
        changes.setdefault(field, [item for item in getattr(plan, field) if item in names])
    confirmed = {item for field in ("must_buy", "reusable", "prepared", "not_buy") for item in changes[field]}
    unknowns = changes.pop("unknowns", plan.unknowns)
    changes["unknowns"] = list(dict.fromkeys([
        item for item in [*unknowns, *(name for name in names if name not in plan.tasks)]
        if item in names and item not in confirmed
    ]))
    return replace(
        plan,
        tasks=list(dict.fromkeys([*required_names, *optional_names])),
        optional=optional_names,
        checklist_sections=sections,
        **changes,
    )


def _duration_only(plan: ShoppingPlan, context: dict, prep: list[str], explanation: str) -> ShoppingPlan:
    start, end = context.get("start_date"), context.get("end_date")
    if start and end:
        try:
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        except (TypeError, ValueError):
            days = 0
    else:
        duration = re.search(r"（(\d{1,2})天）", plan.goal)
        days = context.get("duration_days") or (int(duration.group(1)) if duration else 0)
    if not 1 <= days <= 30:
        return replace(plan, prep=list(dict.fromkeys(prep)), weather_context=context)

    sections = []
    for original in plan.checklist_sections:
        section = dict(original)
        for bucket in ("required", "optional"):
            section[bucket] = [dict(item) for item in original.get(bucket, [])]
        sections.append(section)
    clothing = next((section for section in sections if "衣物" in section.get("title", "")), None)
    if clothing is None and len(sections) < 6:
        clothing = {"id": "weather-clothing", "title": "衣物与天气", "required": [], "optional": []}
        sections.append(clothing)
    if clothing is None:
        return replace(plan, prep=list(dict.fromkeys(prep)), weather_context=context)

    members = _party(context, None)
    generic = {"换洗上衣", "T恤", "短裤", "日常衣物", "衣物", "换洗穿搭（上衣+下装）"}
    for bucket in ("required", "optional"):
        clothing[bucket] = [item for item in clothing.get(bucket, []) if item.get("name") not in generic]
    if members:
        for label, count in members:
            clothing.setdefault("required", []).append({
                "name": f"{label}换洗穿搭（上衣+下装）",
                "quantity": (
                    f"{days + 1}套/人（{count}位{label}各{days + 1}套）"
                    if count > 1 else _outfit_quantity(days, count)
                ),
                "condition": explanation,
                "weather_generated": True,
            })
        name = None
    else:
        name = "换洗穿搭（上衣+下装）"
        clothing.setdefault("required", []).append({
            "name": name,
            "quantity": _outfit_quantity(days, plan.people),
            "condition": explanation,
            "weather_generated": True,
        })
    if members:
        basics = {"内衣", "内裤", "袜子"}
        for bucket in ("required", "optional"):
            clothing[bucket] = [item for item in clothing[bucket] if item.get("name") not in basics]
        for label, count in members:
            for basic, unit in (("内衣", "件"), ("袜子", "双")):
                clothing["required"].append({
                    "name": f"{label}{basic}",
                    "quantity": f"{days + 1}{unit}/人（{count}位{label}各{days + 1}{unit}）" if count > 1 else f"{days + 1}{unit}",
                    "condition": explanation,
                    "weather_generated": True,
                })
    # No dates means no weather-specific clothing type. Still count known basics
    # separately for adults/children instead of retaining stale template quantities.
    basics = {"内衣", "内裤", "袜子"}
    for bucket in ("required", "optional"):
        original = clothing[bucket]
        clothing[bucket] = []
        for item in original:
            if item.get("name") not in basics:
                clothing[bucket].append(item)
                continue
            unit = "双" if item["name"] == "袜子" else "件"
            for label, count in members or [("", plan.people or 1)]:
                clothing[bucket].append({**item, "name": label + item["name"],
                    "quantity": (
                        f"{days + 1}{unit}/人（{count}位{label}各{days + 1}{unit}）"
                        if label and count > 1
                        else f"{days + 1}{unit}" if label
                        else _clothing_quantity(days, unit, plan.people)
                    ),
                    "condition": explanation, "weather_generated": True})
    existing = {item["name"] for bucket in ("required", "optional") for item in clothing[bucket]}
    for label, count in members or [("", plan.people or 1)]:
        for basic, unit in (("内衣", "件"), ("袜子", "双")):
            if label + basic not in existing:
                clothing["required"].append({"name": label + basic,
                    "quantity": (
                        f"{days + 1}{unit}/人（{count}位{label}各{days + 1}{unit}）"
                        if label and count > 1
                        else f"{days + 1}{unit}" if label
                        else _clothing_quantity(days, unit, plan.people)
                    ),
                    "condition": explanation, "weather_generated": True})
    current_names = [item["name"] for section in sections for item in section.get("required", []) + section.get("optional", [])]
    unknowns = list(dict.fromkeys([*(item for item in plan.unknowns if item in current_names), *([name] if name else [item["name"] for item in clothing["required"] if item["name"].endswith("换洗穿搭（上衣+下装）")])]))
    return _sync_checklist(
        plan, sections, unknowns=unknowns,
        goal=f"{plan.goal.partition('（')[0].strip()}（{days}天）",
        prep=list(dict.fromkeys(prep)), weather_context=context,
    )


def apply_weather_context(plan: ShoppingPlan, context: dict) -> ShoppingPlan:
    """Replace weather hints, not user facts, on every trip revision."""
    previous_notes = (plan.weather_context or {}).get("checklist_notes", [])
    if plan.weather_context and not previous_notes:
        previous_notes = [note for note in plan.prep if note.startswith((
            "天气来源 ", "天气服务暂不可用", "未能准确匹配目的地", "目的地“", "补充具体出行日期后",
        ))]
    def generated(item: dict) -> bool:
        # Recognize snapshots saved before the weather_generated marker existed.
        return bool(item.get("weather_generated") or (plan.weather_context and str(item.get("condition", "")).startswith((
            "行程气温约 ", "预报最高 UV", "预报约 ", "预报降雪约 ", "最高降水概率 ",
            "过去同期气候参考（非逐日预报）", "天气服务暂不可用，按行程天数",
            "行程地点尚", "气温数据暂缺，按行程天数",
        ))))
    sections = [{**section, **{
        bucket: [dict(item) for item in section.get(bucket, []) if not generated(item)]
        for bucket in ("required", "optional")
    }} for section in plan.checklist_sections]
    baseline = replace(plan, checklist_sections=sections, prep=[note for note in plan.prep if note not in previous_notes])
    context = dict(context)
    for key in ("temperature_min_c", "temperature_max_c", "precipitation_probability_max_pct",
                "precipitation_mm_sum", "rainfall_mm_sum", "rain_days", "snowfall_cm_sum", "snow_days", "uv_index_max"):
        value = context.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            context[key] = None
    if context.get("mode") in {"forecast", "climate_reference"}:
        try:
            days = (date.fromisoformat(context["end_date"]) - date.fromisoformat(context["start_date"])).days + 1
            if not 1 <= days <= 30:
                raise ValueError("invalid trip length")
        except (KeyError, TypeError, ValueError):
            context.update(mode="unavailable", error_reason="invalid_trip_dates")
    result = _apply_travel_activities(_apply_weather_context(baseline, context), context)
    result = _sync_checklist(result, result.checklist_sections)
    notes = [note for note in result.prep if note not in baseline.prep]
    return replace(result, weather_context={**context, "checklist_notes": notes})


def _apply_weather_context(plan: ShoppingPlan, context: dict) -> ShoppingPlan:
    mode = context.get("mode")
    prep = list(plan.prep)
    if mode == "needs_details":
        missing = context.get("missing", [])
        detail = "和".join(
            label for field, label in (("destination", "目的地"), ("travel_dates", "具体出行日期"))
            if field in missing
        )
        if not detail:
            detail = "行程信息"
        prep.append(f"补充{detail}后，可查询当地天气并调整行前准备；未提供地点或日期时不猜测天气。")
        if "travel_dates" in missing:
            duration = re.search(r"（(\d{1,2})天）", plan.goal)
            if context.get("duration_days") or duration:
                days = context.get("duration_days") or int(duration.group(1))
                explanation = f"按{days}天每日1套并多备1套估算；提供具体日期后再按{context.get('destination') or '目的地'}天气细分短袖/长袖、外套及雨雪装备"
                return _duration_only(plan, context, prep, explanation)
        return replace(plan, prep=list(dict.fromkeys(prep)), weather_context=context)
    if mode == "unresolved_location":
        prep.append("未能准确匹配目的地；请补充城市及省份/国家后再查天气。")
        return _duration_only(
            plan, context, prep,
            "行程地点尚未匹配，按行程天数估算换洗套数；确认目的地后再细分衣物类别和雨雪装备",
        )
    if mode == "ambiguous_location":
        candidates = "、".join(context.get("candidates", []))
        prep.append(f"目的地“{context.get('destination', '')}”有多个地点匹配，请补充省份/国家后再查天气。" + (f"候选：{candidates}。" if candidates else ""))
        return _duration_only(
            plan, context, prep,
            "行程地点尚待确认，按行程天数估算换洗套数；确认地点后再细分衣物类别和雨雪装备",
        )
    if mode == "unavailable":
        prep.append("天气服务暂不可用，本清单未根据天气调整；请稍后重试或查看官方天气预报。")
        return _duration_only(
            plan, context, prep,
            "天气服务暂不可用，按行程天数估算换洗套数；请出发前复核当地预报再确定衣物类别",
        )
    if mode not in {"forecast", "climate_reference"}:
        return plan

    minimum = context.get("temperature_min_c")
    maximum = context.get("temperature_max_c")
    rain_probability = context.get("precipitation_probability_max_pct")
    rain_mm = context.get("precipitation_mm_sum")
    rainfall_mm = context.get("rainfall_mm_sum")
    rain_days = context.get("rain_days")
    snowfall_cm = context.get("snowfall_cm_sum")
    snow_days = context.get("snow_days")
    uv = context.get("uv_index_max")
    location = context.get("location") or context.get("destination") or "目的地"
    start, end = context.get("start_date"), context.get("end_date")
    date_label = start if start == end else f"{start} 至 {end}"
    duration_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    base_goal = plan.goal.partition("（")[0].strip()
    goal = f"{base_goal}（{duration_days}天）" if duration_days > 1 else f"{base_goal}（1天）"
    source_label = "预报" if mode == "forecast" else "过去同期气候参考（非逐日预报）"
    update_label = context.get("fetched_at")
    has_temperature = minimum is not None and maximum is not None
    if not has_temperature:
        prep.append("天气服务未返回完整气温数据；保留基础衣物准备，不推测短袖、长袖或外套类型。")
        plan = _duration_only(
            plan, context, prep,
            "气温数据暂缺，按行程天数估算基础换洗套数；出发前复核当地天气后再调整衣物类别",
        )
    metrics = []
    if has_temperature:
        metrics.append(f"气温约 {minimum:g}–{maximum:g}℃")
    if rain_probability is not None:
        metrics.append(f"最高降水概率 {rain_probability:g}%")
    if rain_mm is not None:
        metrics.append(f"累计降水约 {rain_mm:g}mm")
    if rainfall_mm is not None:
        metrics.append(
            f"降雨约 {rainfall_mm:g}mm" + (f"、{rain_days:g}个雨天" if rain_days else "")
            if rainfall_mm > 0 else "未见降雨"
        )
    if snowfall_cm is not None:
        metrics.append(
            f"降雪约 {snowfall_cm:g}cm" + (f"、{snow_days:g}个降雪日" if snow_days else "")
            if snowfall_cm > 0 else "未见降雪"
        )
    if uv is not None:
        metrics.append(f"最高 UV {uv:g}")
    prep.append(
        f"天气来源 {context.get('provider', 'Open-Meteo')}：{location}，{date_label}；"
        f"{source_label}" + (f"，{'；'.join(metrics)}" if metrics else "")
        + (f"；数据更新时间 {update_label}（UTC）。" if update_label else "。")
    )

    # ponytail: broad min/max temperature bands give an explainable packing baseline;
    # use daily itinerary data only if users need day-by-day outfits.
    attire: list[dict] = []
    if minimum is not None and maximum is not None:
        temperature = f"行程气温约 {minimum:g}–{maximum:g}℃"
        if maximum <= 10:
            shirt, outerwear = "保暖长袖上衣", "厚外套"
        elif maximum <= 18:
            shirt, outerwear = "长袖上衣", "保暖外套" if minimum <= 8 else "轻薄外套"
        elif minimum <= 16:
            shirt, outerwear = "长袖上衣", "轻薄外套"
        elif maximum < 23:
            shirt, outerwear = "长袖上衣", None
        elif minimum <= 18:
            shirt, outerwear = "短袖上衣", "轻薄外套"
        else:
            shirt, outerwear = "短袖上衣", None
        bottoms = "保暖长裤" if maximum <= 10 else "短裤" if minimum >= 24 else "轻薄长裤" if maximum >= 30 and minimum >= 20 else "长裤"
        attire.extend((
            {
                "name": shirt,
                "quantity": _upperwear_quantity(duration_days, plan.people),
                "_per_person": duration_days + 1, "_unit": "件",
                "condition": f"{temperature}；按每天换一件并留一件备用估算，可洗衣时减少",
                "purchasable": True,
            },
            {
                "name": bottoms,
                "quantity": _bottom_quantity(duration_days, plan.people),
                "_per_person": max(1, (duration_days + 1) // 2), "_unit": "条",
                "condition": f"{temperature}；下装按轮换穿着估算，若每天更换则按行程天数准备",
                "purchasable": True,
            },
        ))
        if outerwear:
            attire.append({
                "name": outerwear, "quantity": _one_per_person("件", plan.people),
                "_per_person": 1, "_unit": "件",
                "condition": f"{temperature}；早晚或户外时分层穿着，按个人体感调整",
                "purchasable": True,
            })

    rain_condition = None
    snow_expected = (snowfall_cm is not None and snowfall_cm > 0) or (snow_days is not None and snow_days > 0)
    rain_expected = (rain_days is not None and rain_days > 0) or (rainfall_mm is not None and rainfall_mm > 0)
    if rain_expected:
        rain_condition = source_label + (f"约 {rain_days:g}个雨天" if rain_days is not None else "有降雨") + (f"、累计降雨 {rainfall_mm:g}mm" if rainfall_mm is not None else "")
    elif rain_probability is not None and rain_probability >= 40 and not snow_expected:
        rain_condition = f"最高降水概率 {rain_probability:g}%；逐日天气可能有雨，建议携带"
    suggestions = list(attire)
    if rain_condition:
        suggestions.append({
            "name": "折叠雨伞", "quantity": "1把",
            "condition": f"{rain_condition}；已有雨具可直接带上",
            "purchasable": True,
        })
    if (snowfall_cm is not None and snowfall_cm >= 0.1) or (snow_days is not None and snow_days > 0):
        snow_detail = f"降雪约 {snowfall_cm:g}cm" if snowfall_cm is not None else "同期样本出现降雪"
        snow_day_detail = f"、{snow_days:g}个降雪日" if snow_days is not None and snow_days > 0 else ""
        suggestions.append({
            "name": "防水防滑鞋", "quantity": _one_per_person("双", plan.people),
            "condition": f"{source_label}{snow_detail}{snow_day_detail}；按当地路况和实时预报确认",
            "purchasable": True,
        })
    if uv is not None and uv >= 6:
        suggestions.extend((
            {"name": "防晒霜", "condition": f"{source_label}最高 UV 指数约 {uv:g}；按个人肤质和产品说明选择", "purchasable": True},
            {"name": "遮阳帽", "condition": f"{source_label}最高 UV 指数约 {uv:g}；长时间户外活动时携带", "purchasable": True},
        ))

    sections = []
    for original in plan.checklist_sections:
        section = dict(original)
        for bucket in ("required", "optional"):
            section[bucket] = [dict(item) for item in original.get(bucket, [])]
        sections.append(section)
    clothing_section = next((section for section in sections if "衣物" in section.get("title", "")), None)
    if clothing_section is None:
        if len(sections) < 6:
            clothing_section = {"id": "weather-clothing", "title": "天气与衣物准备", "required": [], "optional": []}
            sections.append(clothing_section)
        elif sections:
            clothing_section = sections[-1]
    if clothing_section is not None:
        members = _party(context, None)
        for bucket in ("required", "optional"):
            if has_temperature:
                clothing_section[bucket] = [
                    item for item in clothing_section[bucket]
                    if item.get("name") not in {"换洗上衣", "日常衣物", "衣物", "T恤", "短裤"}
                ]
            for item in clothing_section[bucket]:
                name = item.get("name", "")
                if name in {"内衣", "内裤", "袜子"}:
                    item["quantity"] = _clothing_quantity(duration_days, "双" if name == "袜子" else "件", plan.people)
                    item["note"] = "按每天更换并留一件备用估算；可洗衣时减少"
        if members and has_temperature:
            generic_clothing = {"换洗上衣", "日常衣物", "衣物", "T恤", "短裤", "内衣", "内裤", "袜子", "换洗穿搭（上衣+下装）"}
            for bucket in ("required", "optional"):
                clothing_section[bucket] = [item for item in clothing_section[bucket] if item.get("name") not in generic_clothing]
            clothing_section["required"] = [item for item in clothing_section["required"] if item.get("name") not in {"内衣", "内裤", "袜子"}]
            for item in attire:
                for label, count in members:
                    entry = {key: value for key, value in item.items() if not key.startswith("_")}
                    entry["name"] = f"{label}{item['name']}"
                    if count > 1:
                        entry["quantity"] = f"{item['_per_person']}{item['_unit']}/人（{count}位{label}各{item['_per_person']}{item['_unit']}）"
                    else:
                        entry["quantity"] = f"{item['_per_person']}{item['_unit']}"
                    entry["weather_generated"] = True
                    essential = any(token in item["name"] for token in ("上衣", "长裤", "短裤"))
                    clothing_section["required" if essential else "optional"].append(entry)
            for label, count in members:
                for name, unit in (("内衣", "件"), ("袜子", "双")):
                    clothing_section["required"].append({
                        "name": f"{label}{name}",
                        "quantity": (
                            f"{duration_days + 1}{unit}/人（{count}位{label}各{duration_days + 1}{unit}）"
                            if count > 1 else f"{duration_days + 1}{unit}"
                        ),
                        "condition": f"按{duration_days}天每日更换并多备1件/人估算",
                        "weather_generated": True,
                    })

    seen = {
        item.get("name")
        for section in sections for bucket in ("required", "optional") for item in section.get(bucket, [])
    }
    new_names = []
    for item in suggestions:
        if item["name"] not in seen:
            new_names.append(item["name"])
            seen.add(item["name"])
    if clothing_section is not None:
        for item in suggestions:
            if item["name"] in new_names:
                if members and item.get("_per_person"):
                    continue
                bucket = "optional" if item["name"] in {"轻薄外套", "保暖外套", "厚外套", "折叠雨伞", "防水防滑鞋", "防晒霜", "遮阳帽"} else "required"
                clothing_section[bucket].append({**{key: value for key, value in item.items() if not key.startswith("_")}, "weather_generated": True})
        if new_names:
            clothing_section["weather_reference"] = {
                "title": "Open-Meteo 天气数据",
                "url": "https://open-meteo.com/",
                "note": "衣物数量按行程天数估算；雨雪和远期气候参考请在出发前结合实时预报复核。",
            }

    unknowns = list(dict.fromkeys([*(item for item in plan.unknowns if item in {
        entry["name"] for section in sections for bucket in ("required", "optional")
        for entry in section.get(bucket, [])
    }), *new_names]))
    return _sync_checklist(
        plan, sections,
        goal=goal,
        unknowns=unknowns,
        prep=list(dict.fromkeys(prep)),
        weather_context=context,
    )
