from datetime import date

import httpx
import pytest

from app.application.travel_weather import apply_weather_context
from app.application.agents.orchestrator import _plan_ack_text
from app.domain.session.shopping_plan import ShoppingPlan
from app.infrastructure.persistence.scene_template_store import SceneTemplateStore
from app.infrastructure.weather import OpenMeteoWeatherClient, parse_trip_weather_request


_FORECAST = {
    "daily": {
        "time": ["2026-10-28", "2026-10-29", "2026-10-30"],
        "temperature_2m_min": [8, 9, 11],
        "temperature_2m_max": [17, 19, 20],
        "precipitation_probability_max": [70, 20, 10],
        "precipitation_sum": [5, 0, 0],
        "rain_sum": [5, 0, 0],
        "snowfall_sum": [0, 0, 0],
        "wind_speed_10m_max": [12, 8, 10],
        "uv_index_max": [2, 3, 4],
    },
}


def test_trip_weather_request_requires_explicit_place_and_date_but_parses_common_ranges():
    request = parse_trip_weather_request(
        "2026年10月28日至30日去上海旅行", today=date(2026, 10, 25),
    )
    assert request == {
        "destination": "上海", "start_date": "2026-10-28", "end_date": "2026-10-30", "missing": [], "duration_days": 3,
    }
    assert parse_trip_weather_request("2026年10月28到30去上海旅行", today=date(2026, 10, 25))["end_date"] == "2026-10-30"
    assert parse_trip_weather_request("2027年11月10日去上海旅行", today=date(2026, 10, 25))["end_date"] == "2027-11-10"
    assert parse_trip_weather_request("2027年11月10日去上海旅行10天", today=date(2026, 10, 25))["end_date"] == "2027-11-19"
    assert parse_trip_weather_request("明年?", today=date(2026, 10, 25)) is None
    assert parse_trip_weather_request("9月1日去上海旅行", today=date(2026, 10, 25))["start_date"] == "2027-09-01"
    assert parse_trip_weather_request("2026年10月28日至30日去东京（日本）旅行")["country_code"] == "JP"
    assert parse_trip_weather_request("2026年10月28日至30日去东京旅行")["country_code"] == "JP"
    assert parse_trip_weather_request("去上海旅行", today=date(2026, 10, 25))["missing"] == ["travel_dates"]
    assert parse_trip_weather_request("买一个旅行背包", today=date(2026, 10, 25)) is None


@pytest.mark.asyncio
async def test_forecast_adds_weather_items_and_keeps_plan_and_module_items_aligned():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json={"results": [{
                "name": "上海", "admin1": "上海市", "country": "中国",
                "latitude": 31.23, "longitude": 121.47, "timezone": "Asia/Shanghai",
                "feature_code": "PPLA", "population": 24000000,
            }]})
        return httpx.Response(200, json=_FORECAST)

    client = OpenMeteoWeatherClient(transport=httpx.MockTransport(handler))
    query = "2026年10月28日至30日去上海旅行，帮我准备清单"
    context = await client.for_query(query, today=date(2026, 10, 25))
    assert context["mode"] == "forecast"
    assert context["location"] == "上海，上海市，中国"
    assert context["temperature_min_c"] == 8
    assert context["temperature_max_c"] == 20
    assert context["precipitation_probability_max_pct"] == 70
    assert context["rain_days"] == 1
    assert context["snow_days"] == 0
    assert len(calls) == 2

    template = SceneTemplateStore().match(query)
    assert template and template["id"] == "general_travel"
    plan = SceneTemplateStore().build_plan(query, template)
    assert plan.goal == "出行准备"
    enriched = apply_weather_context(plan, context)
    items = {item["name"]: item for section in enriched.checklist_sections
             for item in [*section["required"], *section["optional"]]}
    assert {"折叠雨伞", "轻薄外套", "长袖上衣", "长裤"} <= items.keys()
    assert all(items[name]["condition"] for name in ("折叠雨伞", "轻薄外套", "长袖上衣", "长裤"))
    assert items["长袖上衣"]["quantity"] == "4件/人（3套日常穿搭+1件备用上衣）"
    assert items["长裤"]["quantity"] == "2条/人（约每2天轮换1条；每天更换则准备3条）"
    assert items["内衣"]["quantity"] == "4件/人（3天×每天1件+1件备用）"
    assert items["袜子"]["quantity"] == "4双/人（3天×每天1双+1双备用）"
    assert "1个雨天" in items["折叠雨伞"]["condition"]
    assert "换洗上衣" not in items
    assert set(enriched.tasks) == set(items)
    assert enriched.goal == "出行准备（3天）"
    assert set(enriched.optional) == {
        item["name"] for section in enriched.checklist_sections for item in section["optional"]
    }
    assert any("上海" in reminder and "8–20℃" in reminder for reminder in enriched.prep)
    assert any("未见降雪" in reminder for reminder in enriched.prep)
    assert any("数据更新时间" in reminder and "（UTC）" in reminder for reminder in enriched.prep)

    cached = await client.for_query(query, today=date(2026, 10, 25))
    assert cached == context
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_long_range_trip_uses_labeled_historical_climate_reference():
    endpoints = []

    def handler(request):
        endpoints.append(request.url.host)
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json={"results": [{
                "name": "上海", "country": "中国", "latitude": 31.23, "longitude": 121.47,
                "timezone": "Asia/Shanghai",
                "feature_code": "PPLA", "population": 24000000,
            }]})
        return httpx.Response(200, json={"daily": {
            "temperature_2m_min": [10, 11], "temperature_2m_max": [20, 21],
            "precipitation_sum": [2, 1], "wind_speed_10m_max": [8, 10],
        }})

    client = OpenMeteoWeatherClient(transport=httpx.MockTransport(handler))
    context = await client.for_query("2027年11月10日去上海旅行", today=date(2026, 10, 25))
    assert context["mode"] == "climate_reference"
    assert context["end_date"] == "2027-11-10"
    assert context["sample_years"] == [2025, 2024, 2023]
    assert context["precipitation_probability_max_pct"] is None
    assert endpoints.count("archive-api.open-meteo.com") == 3
    plan = apply_weather_context(ShoppingPlan(goal="旅行准备"), context)
    assert any("过去同期气候参考（非逐日预报）" in reminder for reminder in plan.prep)


def test_snowy_cold_trip_suggests_warm_layers_and_snow_safe_shoes_not_an_umbrella():
    context = {
        "mode": "forecast", "provider": "Open-Meteo", "location": "札幌，日本",
        "start_date": "2026-12-01", "end_date": "2026-12-03",
        "temperature_min_c": -5, "temperature_max_c": 2,
        "precipitation_probability_max_pct": 75, "precipitation_mm_sum": 8,
        "rainfall_mm_sum": 0, "rain_days": 0,
        "snowfall_cm_sum": 3, "snow_days": 2,
    }
    plan = ShoppingPlan(
        goal="出行准备",
        tasks=["换洗上衣", "内衣", "袜子"],
        checklist_sections=[{"id": "clothes", "title": "衣物与洗漱",
                             "required": [{"name": "换洗上衣"}, {"name": "内衣"}, {"name": "袜子"}],
                             "optional": []}],
    )
    enriched = apply_weather_context(plan, context)
    items = {item["name"]: item for section in enriched.checklist_sections
             for item in [*section["required"], *section["optional"]]}
    assert {"保暖长袖上衣", "保暖长裤", "厚外套", "防水防滑鞋"} <= items.keys()
    assert "折叠雨伞" not in items
    assert items["保暖长袖上衣"]["quantity"] == "4件/人（3套日常穿搭+1件备用上衣）"
    assert "降雪约 3cm" in items["防水防滑鞋"]["condition"]
    assert set(enriched.tasks) == set(items)


def test_forecast_snowfall_is_extracted_and_drives_snow_clothing():
    request = {"start_date": "2026-12-01", "end_date": "2026-12-03"}
    context = OpenMeteoWeatherClient._forecast_context({"daily": {
        "time": ["2026-12-01", "2026-12-02", "2026-12-03"],
        "temperature_2m_min": [-5, -3, -1], "temperature_2m_max": [0, 1, 2],
        "precipitation_probability_max": [75, 60, 20],
        "precipitation_sum": [3, 4, 0], "rain_sum": [0, 0, 0],
        "snowfall_sum": [1.5, 2, 0], "wind_speed_10m_max": [10, 11, 8],
        "uv_index_max": [1, 1, 2],
    }}, request, "札幌，日本")
    assert context["snowfall_cm_sum"] == 3.5
    assert context["snow_days"] == 2
    plan = apply_weather_context(ShoppingPlan(goal="出行准备"), context)
    items = {item["name"]: item for section in plan.checklist_sections
             for item in [*section["required"], *section["optional"]]}
    assert "厚外套" in items
    assert "防水防滑鞋" in items
    assert "折叠雨伞" not in items
    assert "降雪约 3.5cm" in items["防水防滑鞋"]["condition"]


def test_warm_trip_suggests_counted_short_sleeves():
    context = {
        "mode": "forecast", "provider": "Open-Meteo", "location": "广州，中国",
        "start_date": "2026-07-01", "end_date": "2026-07-03",
        "temperature_min_c": 25, "temperature_max_c": 34,
        "precipitation_probability_max_pct": 10, "rainfall_mm_sum": 0,
        "rain_days": 0, "snowfall_cm_sum": 0, "snow_days": 0,
    }
    enriched = apply_weather_context(ShoppingPlan(goal="出行准备"), context)
    items = {item["name"]: item for section in enriched.checklist_sections
             for item in [*section["required"], *section["optional"]]}
    assert items["短袖上衣"]["quantity"] == "4件/人（3套日常穿搭+1件备用上衣）"
    assert "短裤" in items


def test_trip_without_date_still_gets_duration_count_but_not_guessed_weather_clothing():
    query = "需要去东京旅游3天，帮我准备清单"
    store = SceneTemplateStore()
    template = store.match(query)
    assert template and template["id"] == "general_travel"
    plan = store.build_plan(query, template)
    assert plan.goal.endswith("（3天）")
    enriched = apply_weather_context(plan, {
        "mode": "needs_details", "destination": "东京", "missing": ["travel_dates"],
    })
    items = {item["name"]: item for section in enriched.checklist_sections
             for item in [*section["required"], *section["optional"]]}
    assert items["换洗穿搭（上衣+下装）"]["quantity"] == "4套/人（3天每日1套+1套备用）"
    assert "具体日期后再按东京天气细分" in items["换洗穿搭（上衣+下装）"]["condition"]
    assert "长袖上衣" not in items
    assert set(enriched.tasks) == set(items)
    assert "衣物建议" in _plan_ack_text(enriched.to_dict())
    assert "4套/人" in _plan_ack_text(enriched.to_dict())


def test_explicit_adult_and_child_party_gets_separate_weather_clothing_counts():
    query = "需要去东京旅游3天，我和我的5岁小孩一起，帮我准备清单"
    store = SceneTemplateStore()
    template = store.match(query)
    assert template and template["id"] == "general_travel"
    plan = store.build_plan(query, template)
    context = {
        "mode": "forecast", "location": "东京，日本",
        "start_date": "2026-10-28", "end_date": "2026-10-30",
        "temperature_min_c": 16, "temperature_max_c": 25,
        "precipitation_probability_max_pct": 10, "rainfall_mm_sum": 0,
        "rain_days": 0, "snowfall_cm_sum": 0, "snow_days": 0,
        "travel_party": {"adults": 1, "child_ages": [5]},
    }
    enriched = apply_weather_context(plan, context)
    items = {item["name"]: item for section in enriched.checklist_sections
             for item in [*section["required"], *section["optional"]]}
    assert items["本人长袖上衣"]["quantity"] == "4件"
    assert items["5岁儿童长袖上衣"]["quantity"] == "4件"
    assert items["本人长裤"]["quantity"] == "2条"
    assert items["5岁儿童内衣"]["quantity"] == "4件"
    assert "换洗上衣" not in items and "内衣" not in items
    assert set(enriched.tasks) == set(items)


def test_multiple_adults_show_per_person_quantity_instead_of_aggregate_total():
    plan = SceneTemplateStore().build_plan(
        "2026年12月24日至26日去济州岛旅行", SceneTemplateStore().match("2026年12月24日至26日去济州岛旅行")
    )
    enriched = apply_weather_context(plan, {
        "mode": "forecast", "destination": "济州岛", "location": "济州岛，韩国",
        "start_date": "2026-12-24", "end_date": "2026-12-26",
        "duration_days": 3,
        "temperature_min_c": 2, "temperature_max_c": 9,
        "travel_party": {"adults": 3, "child_ages": []},
    })
    entries = {item["name"]: item for section in enriched.checklist_sections
               for item in [*section["required"], *section["optional"]]}
    assert entries["成人保暖长袖上衣"]["quantity"] == "4件/人（3位成人各4件）"
    assert "12件" not in entries["成人保暖长袖上衣"]["quantity"]
    acknowledgement = _plan_ack_text(enriched.to_dict())
    assert "成人换洗穿搭 4套/人（3位成人各4套）" in acknowledgement
    assert "成人厚外套" in acknowledgement


def test_missing_weather_still_shows_each_adult_basics_not_aggregate_total():
    store = SceneTemplateStore()
    query = "去济州岛旅游3天，3个成人，帮我准备清单"
    plan = store.build_plan(query, store.match(query))
    enriched = apply_weather_context(plan, {
        "mode": "needs_details", "destination": "济州岛", "missing": ["travel_dates"],
        "duration_days": 3, "travel_party": {"adults": 3, "child_ages": []},
    })
    entries = {item["name"]: item for section in enriched.checklist_sections
               for item in [*section["required"], *section["optional"]]}
    assert entries["成人换洗穿搭（上衣+下装）"]["quantity"] == "4套/人（3位成人各4套）"
    assert entries["成人内衣"]["quantity"] == "4件/人（3位成人各4件）"
    assert "12" not in entries["成人内衣"]["quantity"]


def test_beach_swimwear_is_conditional_on_planning_to_swim():
    template = SceneTemplateStore().match("计划去海边旅行，准备行李清单")
    assert template and template["id"] == "beach_weekend"
    plan = SceneTemplateStore().build_plan("计划去海边旅行，准备行李清单", template)
    swimsuit = next(item for section in plan.checklist_sections for item in section["required"]
                    if item["name"] == "泳衣")
    assert "计划下水游泳时" in swimsuit["condition"]


@pytest.mark.asyncio
async def test_weather_failure_and_missing_details_degrade_without_blocking():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    client = OpenMeteoWeatherClient(transport=httpx.MockTransport(handler))
    missing = await client.for_query("去上海旅行", today=date(2026, 10, 25))
    assert missing["mode"] == "needs_details"
    assert calls == []
    failed = await client.for_query(
        "2026年10月28日去上海旅行", today=date(2026, 10, 25),
    )
    assert failed["mode"] == "unavailable"
    plan = apply_weather_context(ShoppingPlan(goal="旅行准备"), failed)
    assert any("天气服务暂不可用" in reminder for reminder in plan.prep)


@pytest.mark.asyncio
async def test_ambiguous_city_is_shown_for_confirmation_without_forecast_call():
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(200, json={"results": [
            {"name": "江城", "admin1": "湖北", "country": "中国", "latitude": 30.6, "longitude": 114.3},
            {"name": "江城", "admin1": "吉林", "country": "中国", "latitude": 43.8, "longitude": 126.5},
        ]})

    client = OpenMeteoWeatherClient(transport=httpx.MockTransport(handler))
    query = "2026年10月28日至30日去江城旅行"
    context = await client.for_query(query, today=date(2026, 10, 25))
    assert context["mode"] == "ambiguous_location"
    assert len(context["candidates"]) == 2
    assert calls == ["geocoding-api.open-meteo.com"]
    store = SceneTemplateStore()
    template = store.match(query)
    assert template is not None
    plan = apply_weather_context(store.build_plan(query, template), context)
    assert any("请补充省份/国家" in reminder for reminder in plan.prep)
    clothes = next(item for section in plan.checklist_sections for item in section["required"]
                   if item["name"] == "换洗穿搭（上衣+下装）")
    assert clothes["quantity"] == "4套/人（3天每日1套+1套备用）"


@pytest.mark.asyncio
async def test_major_city_candidate_is_preferred_over_same_name_small_place():
    def handler(request):
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json={"results": [
                {"name": "上海", "admin1": "上海市", "country": "中国", "latitude": 31.22,
                 "longitude": 121.46, "feature_code": "PPLA", "population": 24000000,
                 "timezone": "Asia/Shanghai"},
                {"name": "上海", "admin1": "浙江", "country": "中国", "latitude": 29.32,
                 "longitude": 121.05, "feature_code": "PPL", "population": 500},
            ]})
        return httpx.Response(200, json=_FORECAST)

    client = OpenMeteoWeatherClient(transport=httpx.MockTransport(handler))
    context = await client.for_query("2026年10月28日至30日去上海旅行", today=date(2026, 10, 25))
    assert context["mode"] == "forecast"
    assert context["location"] == "上海，上海市，中国"


@pytest.mark.asyncio
async def test_explicit_japan_qualifier_disambiguates_tokyo_geocoding():
    def handler(request):
        if request.url.host == "geocoding-api.open-meteo.com":
            assert request.url.params["countryCode"] == "JP"
            assert request.url.params["name"] == "Tokyo"
            assert request.url.params["language"] == "en"
            return httpx.Response(200, json={"results": [{
                "name": "东京", "admin1": "东京都", "country": "日本",
                "latitude": 35.68, "longitude": 139.69, "timezone": "Asia/Tokyo",
                "feature_code": "PPLC", "population": 14000000,
            }]})
        return httpx.Response(200, json=_FORECAST)

    client = OpenMeteoWeatherClient(transport=httpx.MockTransport(handler))
    context = await client.for_query("2026年10月28日至30日去东京（日本）旅行", today=date(2026, 10, 25))
    assert context["mode"] == "forecast"
    assert context["location"] == "东京，东京都，日本"


def test_mild_cool_forecast_suggests_optional_layer_without_claiming_it_is_required():
    context = {
        "mode": "forecast", "provider": "Open-Meteo", "location": "上海，中国",
        "start_date": "2026-11-01", "end_date": "2026-11-01",
        "temperature_min_c": 13, "temperature_max_c": 22,
        "precipitation_probability_max_pct": 10, "precipitation_mm_sum": 0,
        "uv_index_max": 2,
    }
    plan = apply_weather_context(ShoppingPlan(goal="出行准备"), context)
    item = next(
        item for section in plan.checklist_sections for item in section["optional"]
        if item["name"] == "轻薄外套"
    )
    assert "气温约 13–22℃" in item["condition"]
    assert item["name"] in plan.optional
