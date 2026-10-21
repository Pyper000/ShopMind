from scripts.eval_scenarios import DEFAULT_CASES, summarize_case
import yaml


def test_scenario_dataset_contains_ten_multi_turn_missions():
    cases = yaml.safe_load(DEFAULT_CASES.read_text(encoding="utf-8"))["scenarios"]
    assert len(cases) == 10
    assert len({case["id"] for case in cases}) == 10
    assert all(len(case["turns"]) >= 3 for case in cases)


def test_case_metrics_require_calls_planning_expected_slots_and_candidates():
    case = {
        "id": "sample",
        "goal": "周末出行",
        "turns": ["开始", "补充", "确认"],
        "expected_slots": ["防水袋"],
        "existing_items": ["背包"],
        "min_search_tasks": 1,
    }
    turns = [{
        "http_status": 200,
        "latency_sec": 1.0,
        "error": None,
        "events": [
            {"type": "plan.update", "payload": {"shopping_plan": {
                "tasks": ["手机防水袋"], "must_buy": ["手机防水袋"], "reusable": ["背包"],
            }}},
            {"type": "agent.dispatch", "payload": {"agent": "search_agent"}},
            {"type": "tool.result", "payload": {
                "tool": "product_search_tool", "shopping_task": "手机防水袋", "hit_count": 2,
            }},
        ],
    }]
    summary = summarize_case(case, turns)
    assert summary["status"] == "PASS"
    assert summary["expected_slot_recall"] == 1
    assert summary["candidate_task_coverage"] == 1
    assert summary["model_calls"] is None


def test_case_fails_when_owned_item_is_sent_to_search():
    case = {
        "id": "sample",
        "goal": "周末出行",
        "turns": ["开始"],
        "expected_slots": ["背包"],
        "existing_items": ["背包"],
        "min_search_tasks": 1,
    }
    turns = [{
        "http_status": 200,
        "latency_sec": 1.0,
        "error": None,
        "events": [
            {"type": "plan.update", "payload": {"shopping_plan": {
                "tasks": ["背包"], "reusable": ["背包"],
            }}},
            {"type": "tool.result", "payload": {
                "tool": "product_search_tool", "shopping_task": "双肩背包", "hit_count": 1,
            }},
        ],
    }]
    assert summarize_case(case, turns)["status"] == "FAIL"


def test_case_fails_when_expected_slot_has_no_candidates():
    case = {
        "id": "sample",
        "goal": "露营",
        "turns": ["开始"],
        "expected_slots": ["露营灯"],
        "existing_items": [],
        "min_search_tasks": 1,
    }
    turns = [{
        "http_status": 200,
        "latency_sec": 1.0,
        "error": None,
        "events": [
            {"type": "plan.update", "payload": {"shopping_plan": {
                "tasks": ["露营灯"], "must_buy": ["露营灯"],
            }}},
            {"type": "agent.dispatch", "payload": {"agent": "search_agent"}},
            {"type": "tool.result", "payload": {
                "tool": "product_search_tool", "shopping_task": "露营灯", "hit_count": 0,
            }},
        ],
    }]
    summary = summarize_case(case, turns)
    assert summary["candidate_task_coverage"] == 0
    assert summary["status"] == "FAIL"
