"""Three core ShopMind MVP flows: travel, school start, rental move-in.

Scripted API regression, not a user study.  It records event-stream latency
milestones and strict task/category precision of returned cards.  Recall@K is
intentionally omitted because this run has no exhaustive gold product set.
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


CASES = [
    {
        "id": "travel",
        "initial": "周末出行两天，帮我准备购物清单",
        "template": "general_travel",
        "anchor": "旅行背包",
        "supplement": "我还想买一个防水腰包",
        "supplement_slot": "腰包与防水包",
    },
    {
        "id": "school-start",
        "initial": "开学了，帮我准备一份采购清单",
        "template": "school_start",
        "anchor": "学习文具",
        "supplement": "我还想买一个笔记本支架",
        "supplement_slot": "笔记本支架",
    },
    {
        "id": "rental-move-in",
        "initial": "第一次租房入住，帮我准备清单",
        "template": "rental_move_in",
        "anchor": "床品套件",
        "supplement": "我还想买一个收纳箱",
        "supplement_slot": "收纳箱",
    },
]


def plan_from(events: list[dict]) -> dict:
    plans = [
        event.get("payload", {}).get("shopping_plan", {})
        for event in events if event.get("type") in {"plan.update", "plan.preview"}
    ]
    return plans[-1] if plans else {}


def match_card(task: str, hit: dict) -> bool:
    evidence = [str(hit.get("title") or "")]
    evidence.extend(str(item) for item in hit.get("highlights") or [])
    text = " ".join(evidence).casefold()
    return task.casefold() in text


async def turn(client: httpx.AsyncClient, base_url: str, session: str, query: str,
               selection: dict | None = None) -> dict:
    body = {
        "shopping_session_id": session,
        "buyer_id": "mvp-core-acceptance",
        "locale": "zh-CN",
        "currency": "CNY",
        "raw_query": query,
    }
    if selection is not None:
        body["checklist_selection"] = selection
    events: list[dict] = []
    milestones: dict[str, float | None] = {
        "first_feedback_sec": None,
        "first_checklist_sec": None,
        "first_product_sec": None,
    }
    started = time.perf_counter()
    async with client.stream("POST", f"{base_url.rstrip('/')}/commerce/intents/stream", json=body) as response:
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            events.append(event)
            elapsed = round(time.perf_counter() - started, 3)
            kind = event.get("type")
            if kind in {"token.delta", "plan.preview", "plan.update"}:
                milestones["first_feedback_sec"] = milestones["first_feedback_sec"] or elapsed
            if kind == "plan.update":
                plan = event.get("payload", {}).get("shopping_plan") or {}
                if plan.get("stage") in {"explore", "clarify"} and plan.get("tasks"):
                    milestones["first_checklist_sec"] = milestones["first_checklist_sec"] or elapsed
            if kind == "tool.result" and event.get("payload", {}).get("tool") == "product_search_tool":
                if event.get("payload", {}).get("hits"):
                    milestones["first_product_sec"] = milestones["first_product_sec"] or elapsed
            if kind == "final.result":
                milestones["first_feedback_sec"] = milestones["first_feedback_sec"] or elapsed
    return {
        "http_status": response.status_code,
        "turn_end_sec": round(time.perf_counter() - started, 3),
        "milestones": milestones,
        "events": events,
    }


def make_selection(plan: dict, anchor: str) -> dict:
    tasks = list(plan.get("tasks") or [])
    optional = set(plan.get("optional") or [])
    if anchor not in tasks:
        raise ValueError(f"验收槽位未在清单内：{anchor}; tasks={tasks}")
    return {
        "have": [], "must_buy": [] if anchor in optional else [anchor],
        "optional_buy": [anchor] if anchor in optional else [], "deferred": [],
        "unknown": [item for item in tasks if item != anchor],
        "budget_major": None, "priority": "value",
    }


def summarize_turn(case_id: str, stage: str, query: str, out: dict, task: str = "") -> dict:
    events = out["events"]
    search_events = [event.get("payload", {}) for event in events
                     if event.get("type") == "tool.result"
                     and event.get("payload", {}).get("tool") == "product_search_tool"]
    hits = [hit for event in search_events for hit in event.get("hits", [])]
    matched = sum(match_card(task, hit) for hit in hits) if task else None
    plan = plan_from(events)
    routes = [event.get("payload", {}).get("route") for event in events
              if event.get("type") == "plan.update" and event.get("payload", {}).get("route")]
    return {
        "scenario_id": case_id,
        "stage": stage,
        "query": query,
        "http_status": out["http_status"],
        **out["milestones"],
        "turn_end_sec": out["turn_end_sec"],
        "template_id": plan.get("template_id"),
        "route": routes[-1] if routes else "",
        "model_calls": sum(event.get("type") == "model.call" for event in events),
        "search_agent_dispatches": sum(
            event.get("type") == "agent.dispatch"
            and event.get("payload", {}).get("agent") == "search_agent"
            for event in events
        ),
        "search_tasks": ";".join(str(event.get("shopping_task") or "") for event in search_events),
        "requested_task": task,
        "candidate_count": len(hits),
        "task_matching_candidates": matched,
        "returned_card_task_precision": round(matched / len(hits), 4) if hits and matched is not None else None,
        "retrieval_actions": ";".join(sorted({str(event.get("retrieval_action") or "") for event in search_events})),
        "error_count": sum(event.get("type") == "error" for event in events),
        "plan_tasks": plan.get("tasks") or [],
        "must_buy": plan.get("must_buy") or [],
        "events": events,
    }


async def run(base_url: str, output_dir: Path) -> dict:
    timeout = httpx.Timeout(120.0, connect=10.0)
    report: dict = {
        "version": "mvp-core-three-20261022-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "note": "脚本化合成用户回归，非真人研究。首屏时间是 SSE 首个有效事件到达时间，不是浏览器绘制时间。商品准确率只检查返回卡片标题/亮点是否包含目标槽位；没有穷尽相关 SKU 标注集，因此不报告标准 Recall@K。",
        "scenarios": [],
    }
    rows: list[dict] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for case in CASES:
            session = f"mvp-{case['id']}-{uuid.uuid4().hex[:8]}"
            initial = await turn(client, base_url, session, case["initial"])
            initial_row = summarize_turn(case["id"], "scene-plan", case["initial"], initial)
            plan = plan_from(initial["events"])
            initial_ok = (
                initial["http_status"] == 200
                and plan.get("template_id") == case["template"]
                and initial_row["model_calls"] == 0
                and bool(initial_row["first_checklist_sec"])
            )

            selection = make_selection(plan, case["anchor"])
            item_priority = "按需项" if case["anchor"] in set(plan.get("optional") or []) else "必备项"
            confirmation_query = f"清单已确认：只找明确要准备的{item_priority} {case['anchor']}，其余先保持待确认。"
            confirmation = await turn(client, base_url, session, confirmation_query, selection)
            confirmation_row = summarize_turn(
                case["id"], "checklist-search", confirmation_query, confirmation, case["anchor"],
            )
            confirm_ok = (
                confirmation["http_status"] == 200
                and confirmation_row["search_tasks"].split(";") == [case["anchor"]]
                and confirmation_row["candidate_count"] > 0
                and confirmation_row["returned_card_task_precision"] == 1.0
                and confirmation_row["model_calls"] == 0
            )

            before_tasks = list(confirmation_row["plan_tasks"])
            supplement = await turn(client, base_url, session, case["supplement"])
            supplement_row = summarize_turn(
                case["id"], "direct-category-followup", case["supplement"], supplement,
                case["supplement_slot"],
            )
            after_tasks = list(supplement_row["plan_tasks"])
            supplement_ok = (
                supplement["http_status"] == 200
                and supplement_row["route"] in {"buy_item", "set_buy"}
                and supplement_row["search_tasks"].split(";") == [case["supplement_slot"]]
                and supplement_row["candidate_count"] > 0
                and supplement_row["returned_card_task_precision"] == 1.0
                and supplement_row["model_calls"] == 0
                and set(before_tasks).issubset(after_tasks)
                and case["supplement_slot"] in after_tasks
            )
            rows.extend([initial_row, confirmation_row, supplement_row])
            report["scenarios"].append({
                "scenario_id": case["id"],
                "session_id": session,
                "checks": {
                    "template_plan_without_model": initial_ok,
                    "only_confirmed_slot_searched": confirm_ok,
                    "direct_followup_preserves_plan_and_searches_one_slot": supplement_ok,
                },
                "pass": initial_ok and confirm_ok and supplement_ok,
                "steps": [initial_row, confirmation_row, supplement_row],
            })
            print(json.dumps({
                "scenario": case["id"],
                "pass": initial_ok and confirm_ok and supplement_ok,
                "scene_checklist_sec": initial_row["first_checklist_sec"],
                "confirmed_first_card_sec": confirmation_row["first_product_sec"],
                "direct_followup_sec": supplement_row["turn_end_sec"],
                "direct_search_tasks": supplement_row["search_tasks"],
                "candidate_precision": [confirmation_row["returned_card_task_precision"], supplement_row["returned_card_task_precision"]],
            }, ensure_ascii=False), flush=True)
    report["aggregate"] = {
        "scenario_pass_count": sum(item["pass"] for item in report["scenarios"]),
        "scenario_count": len(report["scenarios"]),
        "all_returned_candidate_precision_1": all(
            row["returned_card_task_precision"] == 1.0
            for row in rows if row["requested_task"] and row["candidate_count"]
        ),
        "standard_recall_at_k": None,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"mvp-core-three-{stamp}.json"
    csv_path = output_dir / f"mvp-core-three-{stamp}.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_fields = [key for key in rows[0] if key != "events"] if rows else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                             for key, value in row.items() if key != "events"})
    report["artifacts"] = {"json": str(json_path.resolve()), "csv": str(csv_path.resolve())}
    # Persist artifact paths in the JSON too.
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON {json_path.resolve()}")
    print(f"CSV  {csv_path.resolve()}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--output-dir", type=Path, default=Path("eval/results/mvp_core_three_20261022"))
    args = parser.parse_args()
    report = asyncio.run(run(args.base_url, args.output_dir))
    if report["aggregate"]["scenario_pass_count"] != report["aggregate"]["scenario_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
