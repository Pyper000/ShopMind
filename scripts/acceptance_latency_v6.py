"""Small real-model acceptance run for the v6 latency/dispatch path.

This is a scripted regression, not a user study.  It records the four UX
milestones separately so a slow final prose turn cannot hide a usable stream:
first feedback, committed checklist, first product event, and final result.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx


SCENARIOS = [
    {
        "scenario_id": "beach-weekend",
        "initial": "周末去海边两天，帮我准备一份别漏关键东西、也别买太多的清单。",
        "confirm": "清单已确认，先只找明确缺少的必备项，已有和待确认的不要推荐。",
        "must_buy_hint": ("泳衣", "泳镜"),
        "budget": 300,
    },
    {
        "scenario_id": "first-rental",
        "initial": "我第一次租房入住，不知道哪些东西要买，帮我按先后安排。",
        "confirm": "一个人住，房间有床和衣柜，不能打孔；预算800元，先解决睡觉、照明和清洁。床品还没有，先只找床品候选，尺寸之后确认。",
        "must_buy_hint": ("床单", "床品", "床垫"),
        "budget": 800,
    },
    {
        "scenario_id": "road-trip",
        "initial": "两个人周末自驾去郊外，帮我列一份不漏关键东西的采购清单。",
        "confirm": "已有手机和充电线，只找明确缺少的车载充电器；其他项目先不推荐，预算300元。",
        "must_buy_hint": ("车载充电器", "充电宝", "手机支架"),
        "budget": 300,
    },
    {
        "scenario_id": "beginner-camping",
        "initial": "第一次露营，帮我按能过夜的最低够用方案列清单。",
        "confirm": "帐篷和睡袋已经有了，只找缺少的露营灯，其他东西保持待确认，预算250元。",
        "must_buy_hint": ("露营灯", "头灯", "防潮垫"),
        "budget": 250,
    },
    {
        "scenario_id": "home-coffee",
        "initial": "我想在家开始手冲咖啡，不知道最少要买什么。",
        "confirm": "只买手冲壶和滤杯，咖啡豆先不决定；预算400元，按性价比推荐。",
        "must_buy_hint": ("手冲壶", "滤杯", "咖啡壶"),
        "budget": 400,
    },
]


def csv_row(case: dict, model: str) -> dict:
    initial = case["initial"]
    confirmation = case["confirmation"]
    events = [*initial["events"], *confirmation["events"]]
    plans = [
        event.get("payload", {}).get("shopping_plan", {})
        for event in events
        if event.get("type") == "plan.update"
    ]
    latest = plans[-1] if plans else {}
    model_calls = [event.get("payload", {}) for event in events if event.get("type") == "model.call"]
    product_events = [
        event.get("payload", {}) for event in events
        if event.get("type") == "tool.result"
        and event.get("payload", {}).get("tool") == "product_search_tool"
    ]
    dispatches = [
        event for event in events
        if event.get("type") == "agent.dispatch"
        and event.get("payload", {}).get("agent") == "search_agent"
    ]
    return {
        "scenario_id": case["scenario_id"],
        "model": model,
        "initial_http": initial["http_status"],
        "confirmation_http": confirmation["http_status"],
        "initial_elapsed_sec": initial["elapsed_sec"],
        "confirmation_elapsed_sec": confirmation["elapsed_sec"],
        "initial_first_feedback_sec": initial["milestones"].get("first_feedback_sec"),
        "initial_checklist_sec": initial["milestones"].get("checklist_sec"),
        "confirmation_first_feedback_sec": confirmation["milestones"].get("first_feedback_sec"),
        "confirmation_checklist_sec": confirmation["milestones"].get("checklist_sec"),
        "confirmation_first_product_sec": confirmation["milestones"].get("first_product_sec"),
        "confirmation_final_sec": confirmation["milestones"].get("final_sec"),
        "plan_task_slots": len(latest.get("tasks") or []),
        "plan_must_buy_slots": len(latest.get("must_buy") or []),
        "plan_optional_slots": len(latest.get("optional") or []),
        "plan_reusable_slots": len(latest.get("reusable") or []),
        "plan_unknown_slots": len(latest.get("unknowns") or []),
        "search_agent_dispatches": len(dispatches),
        "product_candidates_returned": sum(int(item.get("hit_count", 0) or 0) for item in product_events),
        "retrieval_actions": ";".join(sorted({str(item.get("retrieval_action") or "") for item in product_events})),
        "fast_path_dispatches": sum(
            event.get("payload", {}).get("execution_mode") == "catalog_fast_path"
            for event in events if event.get("type") == "agent.result"
        ),
        "model_call_count": len(model_calls),
        "model_latency_total_sec": round(sum(float(item.get("elapsed_ms") or 0) for item in model_calls) / 1000, 3),
        "model_input_tokens": sum(int(item.get("input_tokens") or 0) for item in model_calls),
        "model_output_tokens": sum(int(item.get("output_tokens") or 0) for item in model_calls),
        "error_count": sum(event.get("type") == "error" for event in events),
        "event_types": ";".join(event.get("type", "") for event in events),
    }


async def run_turn(client: httpx.AsyncClient, base_url: str, session_id: str, query: str, selection: dict | None = None) -> dict:
    body = {
        "shopping_session_id": session_id,
        "buyer_id": "acceptance-v6",
        "locale": "zh-CN",
        "currency": "CNY",
        "raw_query": query,
    }
    if selection is not None:
        body["checklist_selection"] = selection
    events: list[dict] = []
    milestones: dict[str, float] = {}
    started = time.perf_counter()
    async with client.stream("POST", f"{base_url.rstrip('/')}/commerce/intents/stream", json=body) as response:
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            events.append(event)
            elapsed = time.perf_counter() - started
            event_type = event.get("type")
            if event_type in {"plan.preview", "plan.update", "tool.result", "final.result"}:
                milestones.setdefault({
                    "plan.preview": "first_feedback_sec",
                    "plan.update": "checklist_sec",
                    "tool.result": "first_product_sec",
                    "final.result": "final_sec",
                }[event_type], round(elapsed, 3))
    return {
        "http_status": response.status_code,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "milestones": milestones,
        "events": events,
    }


def choose_selection(initial: dict, hints: tuple[str, ...], budget: int) -> dict:
    plans = [
        event.get("payload", {}).get("shopping_plan", {})
        for event in initial["events"]
        if event.get("type") == "plan.update"
    ]
    plan = plans[-1] if plans else {}
    tasks = [str(item) for item in plan.get("tasks", [])]
    chosen = next((item for item in tasks if any(hint in item for hint in hints)), tasks[0] if tasks else "")
    return {
        "have": [],
        "must_buy": [chosen] if chosen else [],
        "optional_buy": [],
        "deferred": [],
        "unknown": [item for item in tasks if item != chosen],
        "budget_major": budget,
        "priority": "value",
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=len(SCENARIOS))
    parser.add_argument("--csv-output", type=Path)
    args = parser.parse_args()
    report = {
        "version": "latency-v6-20261022",
        "model": "deepseek-ai/DeepSeek-V3.2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "脚本化合成用户回归，非真人测试，不代表用户满意度或真实用户研究。",
        "scenarios": [],
    }
    timeout = httpx.Timeout(180.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for case in SCENARIOS[: max(1, min(args.limit, len(SCENARIOS)))]:
            session_id = f"acceptance-v6-{case['scenario_id']}-{uuid.uuid4().hex[:8]}"
            initial = await run_turn(client, args.base_url, session_id, case["initial"])
            selection = choose_selection(initial, case["must_buy_hint"], case["budget"])
            confirmation = await run_turn(client, args.base_url, session_id, case["confirm"], selection)
            report["scenarios"].append({
                "scenario_id": case["scenario_id"],
                "session_id": session_id,
                "initial": initial,
                "confirmation": confirmation,
                "selection": selection,
            })
            print(json.dumps({
                "scenario_id": case["scenario_id"],
                "initial": initial["milestones"],
                "confirmation": confirmation["milestones"],
            }, ensure_ascii=False), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.csv_output:
        args.csv_output.parent.mkdir(parents=True, exist_ok=True)
        fields = list(csv_row(report["scenarios"][0], report["model"]).keys()) if report["scenarios"] else []
        with args.csv_output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for case in report["scenarios"]:
                writer.writerow(csv_row(case, report["model"]))
        print(f"CSV {args.csv_output}")
    print(f"REPORT {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
