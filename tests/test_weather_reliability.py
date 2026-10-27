"""Deterministic weather branches and persisted, multi-turn trip revisions."""
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
import asyncio

import httpx
import pytest

from app.application.agents.orchestrator import MainAgentOrchestrator, SubmitIntentInput
from app.application.agents.scheme_state import SchemeStateRegistry
from app.application.travel_weather import apply_weather_context
from app.infrastructure.eventbus import TradeEventBus
from app.infrastructure.persistence.scene_template_store import SceneTemplateStore
from app.infrastructure.weather import OpenMeteoWeatherClient, parse_trip_weather_request


TODAY = date(2026, 10, 25)
QUERY = "2026年10月28日至30日去上海旅行，帮我准备清单"
CONTEXT = {
    "mode": "forecast", "destination": "上海", "location": "上海，中国",
    "start_date": "2026-10-28", "end_date": "2026-10-30",
    "temperature_min_c": 24, "temperature_max_c": 32,
    "uv_index_max": 9, "rainfall_mm_sum": 0, "snowfall_cm_sum": 0,
}


def base_plan():
    store = SceneTemplateStore()
    return store.build_plan(QUERY, store.match(QUERY))


def items(plan):
    entries = [item for section in plan.checklist_sections
               for bucket in ("required", "optional") for item in section[bucket]]
    assert len(entries) == len({item["name"] for item in entries}), "duplicate checklist names"
    assert set(plan.tasks) == {item["name"] for item in entries}
    assert set(plan.optional) == {item["name"] for section in plan.checklist_sections for item in section["optional"]}
    for field in ("unknowns", "reusable", "prepared", "must_buy", "not_buy"):
        assert set(getattr(plan, field)) <= set(plan.tasks)
    return {item["name"]: item for item in entries}


@pytest.mark.parametrize("mode", ["forecast", "climate_reference"])
@pytest.mark.parametrize("uv", [6, 9, 12])
def test_high_uv_is_safe_counted_and_labeled(mode, uv):
    plan = apply_weather_context(base_plan(), {**CONTEXT, "mode": mode, "uv_index_max": uv})
    entries = items(plan)
    for name in ("防晒霜", "遮阳帽"):
        assert f"UV 指数约 {uv}" in entries[name]["condition"]
    if mode == "climate_reference":
        assert "非逐日预报" in entries["防晒霜"]["condition"]


@pytest.mark.parametrize(("rain", "snow", "expected"), [
    (3, 0, {"折叠雨伞"}), (0, 3, {"防水防滑鞋"}),
    (3, 3, {"折叠雨伞", "防水防滑鞋"}),
])
def test_rain_snow_without_day_counts_does_not_confuse_precipitation(rain, snow, expected):
    plan = apply_weather_context(base_plan(), {
        **CONTEXT, "rainfall_mm_sum": rain, "snowfall_cm_sum": snow,
        "precipitation_probability_max_pct": 80,
    })
    names = set(items(plan)) & {"折叠雨伞", "防水防滑鞋"}
    assert names == expected


@pytest.mark.parametrize("bad_temperature", [None, "unknown", float("nan"), float("inf"), True])
def test_missing_or_invalid_temperature_preserves_counted_basics_and_party(bad_temperature):
    plan = apply_weather_context(base_plan(), {
        **CONTEXT, "temperature_min_c": bad_temperature, "temperature_max_c": None,
        "travel_party": {"adults": 1, "child_ages": [5]},
    })
    entries = items(plan)
    assert entries["本人换洗穿搭（上衣+下装）"]["quantity"] == "4套（1人×3天+每人1套备用）"
    assert "5岁儿童换洗穿搭（上衣+下装）" in entries
    assert not any("短袖" in name or "长袖" in name for name in entries)
    assert any("未返回完整气温数据" in note for note in plan.prep)


@pytest.mark.parametrize("patch", [
    {"start_date": None}, {"end_date": "not-a-date"},
    {"end_date": "2026-10-27"}, {"end_date": "2027-10-30"},
])
def test_invalid_dates_degrade_instead_of_breaking_checklist(patch):
    plan = apply_weather_context(base_plan(), {**CONTEXT, **patch})
    assert items(plan)
    assert plan.weather_context["mode"] == "unavailable"
    assert plan.weather_context["error_reason"] == "invalid_trip_dates"


def test_null_provider_metrics_remain_missing_not_zero_rain_or_snow():
    context = OpenMeteoWeatherClient._forecast_context({"daily": {
        "time": ["2026-10-28"], "precipitation_sum": None,
        "rain_sum": None, "snowfall_sum": None,
        "temperature_2m_min": [None, float("nan")], "temperature_2m_max": [None],
    }}, CONTEXT, "上海")
    assert context["temperature_min_c"] is None
    assert context["precipitation_mm_sum"] is None
    assert context["rain_days"] is None and context["snow_days"] is None
    plan = apply_weather_context(base_plan(), context)
    assert "换洗穿搭（上衣+下装）" in items(plan)
    assert not any("未见降雨" in note or "未见降雪" in note for note in plan.prep)


def test_rain_daily_nulls_do_not_shift_showers_onto_wrong_day():
    context = OpenMeteoWeatherClient._forecast_context({"daily": {
        "time": ["2026-10-28", "2026-10-29"],
        "rain_sum": [None, 1], "showers_sum": [2, None],
    }}, CONTEXT, "上海")
    assert context["rain_days"] == 2
    assert context["rainfall_mm_sum"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["geocoding", "forecast", "archive"])
@pytest.mark.parametrize("failure", ["503", "timeout", "invalid_json", "wrong_shape", "empty_daily"])
async def test_service_failures_keep_basic_checklist(stage, failure):
    def handler(request):
        is_geo = request.url.host.startswith("geocoding")
        if (stage == "geocoding") == is_geo:
            if failure == "503":
                return httpx.Response(503)
            if failure == "timeout":
                raise httpx.ReadTimeout("test timeout", request=request)
            if failure == "invalid_json":
                return httpx.Response(200, text="not JSON")
            if failure == "wrong_shape":
                return httpx.Response(200, json=["unexpected list"])
            return httpx.Response(200, json={"daily": {}})
        return httpx.Response(200, json={"results": [{
            "name": "上海", "latitude": 31.2, "longitude": 121.5,
        }]})

    query = QUERY.replace("2026年10月", "2027年10月") if stage == "archive" else QUERY
    client = OpenMeteoWeatherClient(transport=httpx.MockTransport(handler))
    context = await client.for_query(query, today=TODAY)
    assert context["mode"] in {"unavailable", "unresolved_location"}
    assert context["destination"] == "上海"
    plan = apply_weather_context(base_plan(), context)
    assert "身份证件" in items(plan)
    assert "换洗穿搭（上衣+下装）" in items(plan)
    assert not any("天气来源" in note for note in plan.prep)


def test_trip_inheritance_is_per_field_and_invalid_new_date_is_not_ignored():
    previous = {**CONTEXT, "destination": "东京", "country_code": "JP"}
    changed_city = parse_trip_weather_request("改去广州", today=TODAY, previous_context=previous)
    assert changed_city["destination"] == "广州"
    assert changed_city["start_date"] == previous["start_date"]
    assert changed_city["inherited_fields"] == ["travel_dates"]
    assert "country_code" not in changed_city
    changed_dates = parse_trip_weather_request("改成2026年11月2日至4日", today=TODAY, previous_context=previous)
    assert changed_dates["destination"] == "东京"
    assert changed_dates["end_date"] == "2026-11-04"
    assert changed_dates["inherited_fields"] == ["destination"]
    changed_duration = parse_trip_weather_request("改成5天", today=TODAY, previous_context=previous)
    assert changed_duration["end_date"] == "2026-11-01"
    assert changed_duration["inherited_fields"] == ["destination", "start_date"]
    invalid = parse_trip_weather_request("改为2026年2月30日出行", today=TODAY, previous_context=previous)
    assert invalid["start_date"] is None and "travel_dates" in invalid["missing"]
    legacy = {key: value for key, value in CONTEXT.items() if key != "destination"}
    assert parse_trip_weather_request("改成5天", today=TODAY, previous_context=legacy)["destination"] == "上海"


def test_weather_reapplication_is_idempotent_and_failure_removes_stale_claims():
    plan = apply_weather_context(base_plan(), CONTEXT)
    plan = replace(plan, reusable=["身份证件"], unknowns=[name for name in plan.unknowns if name != "身份证件"])
    reapplied = apply_weather_context(plan, CONTEXT)
    assert items(reapplied) == items(plan)
    assert reapplied.prep == plan.prep
    failed = apply_weather_context(reapplied, {
        "mode": "unavailable", "destination": "札幌", "start_date": "2026-10-28", "end_date": "2026-11-01",
    })
    assert "短袖上衣" not in items(failed) and "防晒霜" not in items(failed)
    assert items(failed)["换洗穿搭（上衣+下装）"]["quantity"].startswith("6套/人")
    assert failed.goal.endswith("（5天）")
    assert not any("上海" in note or "32℃" in note or "UV 9" in note for note in failed.prep)
    assert failed.reusable == ["身份证件"]


class Sessions:
    def __init__(self):
        self.agent = SimpleNamespace(name="main", state=SimpleNamespace(summary="", context=[]))

    async def get_or_create(self, session_id):
        return self.agent

    async def persist(self, session_id):
        pass


class Preferences:
    async def list_by_buyer(self, buyer_id):
        return []


@pytest.mark.asyncio
async def test_real_client_multiturn_city_date_party_inheritance_persists_and_emits(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host.startswith("geocoding"):
            city = request.url.params["name"]
            return httpx.Response(200, json={"results": [{"name": city, "latitude": 31, "longitude": 121}]})
        # Guangzhou is warm; the requested dates must reach the provider on every correction.
        return httpx.Response(200, json={"daily": {
            "time": [request.url.params["start_date"]], "temperature_2m_min": [25],
            "temperature_2m_max": [32], "uv_index_max": [9],
        }})

    class FixedDateClient(OpenMeteoWeatherClient):
        async def for_query(self, query, **kwargs):
            return await super().for_query(query, today=TODAY, **kwargs)

    client = FixedDateClient(transport=httpx.MockTransport(handler))
    bus, registry = TradeEventBus(), SchemeStateRegistry(tmp_path)
    orchestrator = MainAgentOrchestrator(Sessions(), bus, Preferences(), scheme_states=registry,
                                        scene_templates=SceneTemplateStore(), weather_client=client)
    events = bus.subscribe("weather-trip")
    for version, query in enumerate((QUERY, "我和我的5岁小孩一起", "改去广州", "改成5天"), start=1):
        result = await orchestrator.handle_intent(SubmitIntentInput(
            shopping_session_id="weather-trip", buyer_id="weather-test", locale="zh-CN", currency="CNY", raw_query=query,
        ))
        assert not result.final_text.startswith("[error]")
        saved = registry.get("weather-trip").scheme
        assert saved.state_version == version
        entries = items(saved)
        if version >= 2:
            assert saved.weather_context["travel_party"] == {"adults": 1, "child_ages": [5]}
            assert entries["本人短袖上衣"]["quantity"] == ("6件" if version == 4 else "4件")
            assert entries["5岁儿童短袖上衣"]["quantity"] == ("6件" if version == 4 else "4件")
        # Reconstruct the registry to prove the snapshot survives process/session reload.
        assert SchemeStateRegistry(tmp_path).get("weather-trip").scheme.to_dict() == saved.to_dict()
    assert saved.weather_context["destination"] == "广州"
    assert saved.weather_context["end_date"] == "2026-11-01"
    assert len(calls) == 6  # initial + city change + date change, not the party-only turn
    emitted = []
    while not events.empty():
        emitted.append(events.get_nowait())
    assert len([event for event in emitted if event.type == "plan.update"]) == 4
    assert not any(event.type in {"error", "model.call"} for event in emitted)
    assert len([event for event in emitted if event.type == "final.result"]) == 4


@pytest.mark.asyncio
async def test_orchestrator_timeout_keeps_request_and_does_not_leak_weather_to_cat(tmp_path, monkeypatch):
    class SlowWeather:
        async def for_query(self, query, **kwargs):
            await asyncio.sleep(1)

    monkeypatch.setattr("app.application.agents.orchestrator._TRAVEL_WEATHER_TIMEOUT_SECONDS", 0.01)
    registry = SchemeStateRegistry(tmp_path)
    orchestrator = MainAgentOrchestrator(Sessions(), TradeEventBus(), Preferences(), scheme_states=registry,
                                        scene_templates=SceneTemplateStore(), weather_client=SlowWeather())
    for query in (QUERY, "改成5天", "第一次养猫，需要准备清单"):
        result = await orchestrator.handle_intent(SubmitIntentInput(
            shopping_session_id="timeout-trip", buyer_id="test", locale="zh-CN", currency="CNY", raw_query=query,
        ))
        assert not result.final_text.startswith("[error]")
        plan = registry.get("timeout-trip").scheme
        assert items(plan)
        if "猫" not in query:
            assert plan.weather_context["mode"] == "unavailable"
            assert plan.weather_context["destination"] == "上海"
        else:
            assert plan.template_id == "first_cat_home"
            assert plan.weather_context is None


@pytest.mark.asyncio
async def test_duration_without_dates_and_open_scene_both_support_trip_revisions(tmp_path):
    client = OpenMeteoWeatherClient()
    registry = SchemeStateRegistry(tmp_path)
    orchestrator = MainAgentOrchestrator(Sessions(), TradeEventBus(), Preferences(), scheme_states=registry,
                                        scene_templates=SceneTemplateStore(), weather_client=client)
    for query in ("去东京旅游3天，帮我准备清单", "改成5天", "我和我的5岁小孩一起"):
        result = await orchestrator.handle_intent(SubmitIntentInput(
            shopping_session_id="undated-trip", buyer_id="test", locale="zh-CN", currency="CNY", raw_query=query,
        ))
        assert not result.final_text.startswith("[error]")
    plan = registry.get("undated-trip").scheme
    assert plan.goal.endswith("（5天）")
    assert plan.weather_context["mode"] == "needs_details"
    assert "6套" in items(plan)["5岁儿童换洗穿搭（上衣+下装）"]["quantity"]
    # A model-generated open travel scene must inherit weather just like a shipped template.
    registry.update("undated-trip", replace(plan, template_id=None))
    result = await orchestrator.handle_intent(SubmitIntentInput(
        shopping_session_id="undated-trip", buyer_id="test", locale="zh-CN", currency="CNY", raw_query="改成4天",
    ))
    assert not result.final_text.startswith("[error]")
    revised = registry.get("undated-trip").scheme
    assert revised.template_id is None
    assert "5套" in items(revised)["5岁儿童换洗穿搭（上衣+下装）"]["quantity"]
