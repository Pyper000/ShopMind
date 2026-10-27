"""Acceptance contract: Tokyo -> child -> swim -> cancel -> city/date revision."""
from dataclasses import replace
from datetime import date

import httpx
import pytest

from app.application.agents.orchestrator import MainAgentOrchestrator, SubmitIntentInput
from app.application.agents.scheme_state import SchemeStateRegistry
from app.application.travel_weather import update_travel_party, travel_activity_patch
from app.infrastructure.eventbus import TradeEventBus
from app.infrastructure.persistence.scene_template_store import SceneTemplateStore
from app.infrastructure.weather import OpenMeteoWeatherClient, parse_trip_weather_request
from tests.test_weather_reliability import Sessions, Preferences, items


@pytest.mark.parametrize(("query", "expected"), [
    ("孩子5岁", {"adults": 2, "child_ages": [5]}),
    ("改成1个成人", {"adults": 1, "child_ages": [5]}),
    ("孩子不去", {"adults": 2, "child_ages": []}),
    ("这次我一个人去", {"adults": 1, "child_ages": []}),
    ("我和我父母，以及我的7岁弟弟要一起出行", {"adults": 3, "child_ages": [7]}),
    ("我父母和7岁弟弟一起出行", {"adults": 2, "child_ages": [7]}),
    ("弟弟不去了", {"adults": 2, "child_ages": []}),
])
def test_party_updates_are_partial_and_cancellation_is_explicit(query, expected):
    assert update_travel_party(query, {"adults": 2, "child_ages": [5]}) == expected


def test_christmas_tokyo_play_query_extracts_city_duration_and_party_without_guessing_dates():
    query = "我和我父母，以及我的7岁弟弟要圣诞节去东京玩3天，帮我安排"
    request = parse_trip_weather_request(query, today=date(2026, 10, 26))
    assert request["destination"] == "东京"
    assert request["country_code"] == "JP"
    assert request["duration_days"] == 3
    assert request["start_date"] is None and request["end_date"] is None
    assert request["missing"] == ["travel_dates"]
    assert update_travel_party(query) == {"adults": 3, "child_ages": [7]}


@pytest.mark.parametrize(("query", "expected"), [
    ("游泳", True), ("取消下水", False), ("不游泳", False),
    ("不下水，不过后来决定游泳", True), ("计划游泳，改成不下水", False),
])
def test_last_explicit_activity_statement_wins(query, expected):
    assert travel_activity_patch(query) == {"swimming": expected}


def test_bare_start_date_keeps_duration_but_explicit_range_replaces_it():
    previous = {"destination": "东京", "start_date": "2026-10-28", "end_date": "2026-10-30", "duration_days": 3}
    moved = parse_trip_weather_request("改去广州，2026年11月2日出发", today=date(2026, 10, 25), previous_context=previous)
    assert moved["end_date"] == "2026-11-04" and moved["duration_days"] == 3
    moved = parse_trip_weather_request("改去广州，2026年11月2日至6日", today=date(2026, 10, 25), previous_context=previous)
    assert moved["duration_days"] == 5 and moved["end_date"] == "2026-11-06"
    activity = parse_trip_weather_request("去海边游泳", today=date(2026, 10, 25), previous_context=previous)
    assert activity is None or activity["destination"] == "东京"


def test_holiday_destination_is_a_trip_request_that_requires_exact_dates():
    request = parse_trip_weather_request("圣诞节去济州岛", today=date(2026, 10, 25))
    assert request["destination"] == "济州岛"
    assert request["start_date"] is None and "travel_dates" in request["missing"]


@pytest.mark.asyncio
@pytest.mark.parametrize("weather_failure", [False, True])
async def test_full_trip_chain_retains_facts_and_updates_only_changed_conditions(tmp_path, weather_failure):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if weather_failure:
            return httpx.Response(503)
        if request.url.host.startswith("geocoding"):
            tokyo = request.url.params["name"] == "Tokyo"
            return httpx.Response(200, json={"results": [{"name": "Tokyo" if tokyo else "广州",
                "latitude": 35 if tokyo else 23, "longitude": 139, "feature_code": "PPLC"}]})
        tokyo = request.url.params["latitude"] == "35"
        return httpx.Response(200, json={"daily": {
            "time": [request.url.params["start_date"]],
            "temperature_2m_min": [10 if tokyo else 25], "temperature_2m_max": [18 if tokyo else 32],
            "rain_sum": [3], "snowfall_sum": [0], "uv_index_max": [7],
        }})

    class FixedClient(OpenMeteoWeatherClient):
        async def for_query(self, query, **kwargs):
            return await super().for_query(query, today=date(2026, 10, 25), **kwargs)

    registry, bus = SchemeStateRegistry(tmp_path), TradeEventBus()
    orchestrator = MainAgentOrchestrator(Sessions(), bus, Preferences(), scheme_states=registry,
        scene_templates=SceneTemplateStore(), weather_client=FixedClient(transport=httpx.MockTransport(handler)))
    events = bus.subscribe("trip-chain")
    queries = ("东京3日游", "我带5岁孩子一起", "去海边游泳", "取消下水",
               "改去广州，2026年10月28日至11月1日", "改为2026年11月2日出发", "改成2个成人", "孩子不去")
    for version, query in enumerate(queries, start=1):
        result = await orchestrator.handle_intent(SubmitIntentInput(
            shopping_session_id="trip-chain", buyer_id="trip-test", locale="zh-CN", currency="CNY", raw_query=query))
        assert not result.final_text.startswith("[error]"), result.final_text
        if version == 1:
            assert "具体出发日期和返程日期" in result.final_text
        plan = registry.get("trip-chain").scheme
        entries = items(plan)
        assert plan.weather_context["destination"] == ("东京" if version < 5 else "广州")
        assert plan.weather_context.get("duration_days") == (3 if version < 5 else 5)
        assert plan.state_version >= version
        if version < 5:
            assert plan.weather_context["mode"] == "needs_details"
            assert "travel_dates" in plan.weather_context["missing"]
            assert not any("天气来源" in note for note in plan.prep)
            assert not any("短袖" in name or "长袖" in name for name in entries)
        if 2 <= version < 8:
            assert plan.weather_context["travel_party"]["child_ages"] == [5]
            if version < 5 or weather_failure:
                assert entries["5岁儿童换洗穿搭（上衣+下装）"]["quantity"].startswith("4套" if version < 5 else "6套")
            else:
                assert entries["5岁儿童短袖上衣"]["quantity"] == "6件"
                assert "本人长袖上衣" not in entries
        if version == 3:
            for label in ("本人", "5岁儿童"):
                assert entries[label + "泳衣"]["quantity"] == "1套"
                assert "仅在确认下水游泳" in entries[label + "泳衣"]["condition"]
        if version >= 4:
            assert plan.weather_context["activities"] == {"swimming": False}
            assert not any("泳衣" in name or "泳镜" in name for name in entries)
        if version >= 6:
            assert plan.weather_context["end_date"] == "2026-11-06"
        if version == 7:
            assert plan.people == 3 and plan.weather_context["travel_party"]["adults"] == 2
        if version == 8:
            assert plan.people == 2 and not plan.weather_context["travel_party"]["child_ages"]
            assert not any("5岁儿童" in name for name in entries)
        if version == 2:
            # Simulate an existing user-confirmed fact without triggering any product search.
            registry.update("trip-chain", replace(plan, reusable=["身份证件"],
                unknowns=[name for name in plan.unknowns if name != "身份证件"], budget_major=5000))
        if version >= 3:
            assert plan.reusable == ["身份证件"] and plan.budget_major == 5000
        assert SchemeStateRegistry(tmp_path).get("trip-chain").scheme.to_dict() == registry.get("trip-chain").scheme.to_dict()
    emitted = []
    while not events.empty():
        emitted.append(events.get_nowait())
    assert len([event for event in emitted if event.type == "plan.update"]) == len(queries)
    weather_events = [event for event in emitted if event.type == "weather.lookup"]
    assert len(weather_events) == len(queries) * 2
    assert any(event.payload.get("status") == "needs_details" for event in weather_events)
    expected_result = "unavailable" if weather_failure else "forecast"
    assert any(event.payload.get("status") == expected_result for event in weather_events)
    assert not any(event.type in {"model.call", "error"} for event in emitted)
