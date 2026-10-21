"""Run ten scripted ShopMind conversations against an explicitly selected API."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "eval" / "shopmind_scenarios.yaml"


def summarize_case(case: dict, turns: list[dict]) -> dict:
    plans = [
        event["payload"]["shopping_plan"]
        for turn in turns
        for event in turn.get("events", [])
        if event.get("type") == "plan.update"
        and isinstance(event.get("payload", {}).get("shopping_plan"), dict)
    ]
    latest = plans[-1] if plans else {}
    tasks = list(dict.fromkeys([
        *(latest.get("tasks") or []),
        *(latest.get("must_buy") or []),
        *(latest.get("optional") or []),
    ]))
    searches = [
        event["payload"]
        for turn in turns
        for event in turn.get("events", [])
        if event.get("type") == "tool.result"
        and event.get("payload", {}).get("tool") == "product_search_tool"
    ]
    dispatches = [
        event["payload"]
        for turn in turns
        for event in turn.get("events", [])
        if event.get("type") == "agent.dispatch"
        and event.get("payload", {}).get("agent") == "search_agent"
    ]
    search_tasks = {str(item.get("shopping_task") or "") for item in searches}
    expected = case.get("expected_slots", [])
    task_text = " ".join(tasks).casefold()
    expected_hits = [slot for slot in expected if slot.casefold() in task_text]
    reusable = latest.get("reusable") or []
    existing_hits = [owned for owned in case.get("existing_items", []) if any(
        owned.casefold() in str(item).casefold() or str(item).casefold() in owned.casefold()
        for item in reusable
    )]
    searched_existing = [item for item in search_tasks if item and any(
        owned.casefold() in item.casefold() or item.casefold() in owned.casefold()
        for owned in case.get("existing_items", [])
    )]
    request_count = len(turns)
    success_count = sum(1 for turn in turns if turn.get("http_status") == 200)
    latencies = [turn["latency_sec"] for turn in turns if turn.get("latency_sec") is not None]
    sorted_latencies = sorted(latencies)
    p95 = sorted_latencies[max(0, math.ceil(0.95 * len(sorted_latencies)) - 1)] if sorted_latencies else None
    candidate_tasks = {str(item.get("shopping_task") or "") for item in searches if item.get("hit_count", 0) > 0}
    covered_search_tasks = len(search_tasks & candidate_tasks)
    candidates_returned = sum(int(item.get("hit_count", 0) or 0) for item in searches)
    must_buy = latest.get("must_buy") or []
    minimum_prices = []
    for task in must_buy:
        prices = [
            float(hit["price_major"])
            for item in searches if item.get("shopping_task") == task
            for hit in item.get("hits", [])
            if hit.get("currency") == "CNY" and isinstance(hit.get("price_major"), (int, float))
        ]
        if prices:
            minimum_prices.append(min(prices))
    required_total = sum(minimum_prices) if must_buy and len(minimum_prices) == len(must_buy) else None
    budget = latest.get("budget_major")
    no_agent_error = all(not turn.get("error") for turn in turns)
    passed = (
        success_count == request_count
        and bool(plans)
        and len(expected_hits) == len(expected)
        and len(existing_hits) == len(case.get("existing_items", []))
        and len(search_tasks) >= int(case.get("min_search_tasks", 1))
        and len(search_tasks & candidate_tasks) >= int(case.get("min_search_tasks", 1))
        and not searched_existing
        and no_agent_error
    )
    return {
        "scenario_id": case["id"],
        "goal": case["goal"],
        "status": "PASS" if passed else "FAIL",
        "turns": request_count,
        "successful_requests": success_count,
        "failed_requests": request_count - success_count,
        "total_latency_sec": round(sum(latencies), 3),
        "mean_latency_sec": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "p95_turn_latency_sec": round(p95, 3) if p95 is not None else None,
        "plan_present": bool(plans),
        "task_slots": tasks,
        "expected_slots": expected,
        "expected_slots_found": expected_hits,
        "expected_slot_recall": round(len(expected_hits) / len(expected), 3) if expected else None,
        "existing_items_expected": case.get("existing_items", []),
        "existing_items_in_plan": reusable,
        "existing_item_recall": round(len(existing_hits) / len(case.get("existing_items", [])), 3)
        if case.get("existing_items") else None,
        "existing_item_search_leaks": searched_existing,
        "search_tasks": sorted(search_tasks),
        "search_tasks_with_candidates": sorted(search_tasks & candidate_tasks),
        "candidate_task_coverage": round(covered_search_tasks / len(search_tasks), 3) if search_tasks else 0,
        "product_candidates_returned": candidates_returned,
        "budget_limit_cny": budget,
        "minimum_required_total_cny": required_total,
        "budget_status": (
            "not_calculated" if budget is None or required_total is None
            else "within_budget" if required_total <= budget else "required_items_over_budget"
        ),
        "search_agent_dispatches": len(dispatches),
        "search_tool_calls": len(searches),
        "model_calls": None,
        "estimated_model_cost_cny": None,
        "test_type": "scripted synthetic conversation; not human research",
        "conversation": turns,
    }


async def run_scenario(client: httpx.AsyncClient, base_url: str, case: dict) -> dict:
    session_id = f"eval-{case['id']}-{uuid.uuid4().hex[:8]}"
    turns: list[dict] = []
    for query in case["turns"]:
        started = datetime.now(timezone.utc)
        try:
            response = await client.post(
                f"{base_url.rstrip('/')}/commerce/intents",
                json={
                    "shopping_session_id": session_id,
                    "buyer_id": f"eval-{case['id']}",
                    "locale": "zh-CN",
                    "currency": "CNY",
                    "raw_query": query,
                },
            )
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            payload = response.json()
            turns.append({
                "user": query,
                "assistant": payload.get("final_text", ""),
                "http_status": response.status_code,
                "latency_sec": elapsed,
                "events": payload.get("events", []),
                "error": payload.get("detail") or payload.get("error"),
            })
        except (httpx.HTTPError, ValueError) as err:
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            turns.append({
                "user": query,
                "assistant": "",
                "http_status": None,
                "latency_sec": elapsed,
                "events": [],
                "error": str(err),
            })
            break
    return summarize_case(case, turns)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Exact public or local Python AgentScope API base URL")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--only", help="Run one scenario by id")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "eval" / "results")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between requests")
    args = parser.parse_args()

    cases = yaml.safe_load(args.cases.read_text(encoding="utf-8"))["scenarios"]
    if args.only:
        cases = [case for case in cases if case["id"] == args.only]
        if not cases:
            raise SystemExit(f"unknown scenario: {args.only}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []
    async with httpx.AsyncClient(timeout=180) as client:
        for case in cases:
            print(f"== {case['id']} ({len(case['turns'])} turns)", flush=True)
            result = await run_scenario(client, args.base_url, case)
            results.append(result)
            print(
                f"   {result['status']} · {result['successful_requests']}/{result['turns']} requests · "
                f"{result['total_latency_sec']:.1f}s",
                flush=True,
            )
            if args.delay:
                await asyncio.sleep(args.delay)

    json_path = args.output_dir / f"shopmind-scenarios-{stamp}.json"
    csv_path = args.output_dir / f"shopmind-scenarios-{stamp}.csv"
    json_path.write_text(json.dumps({
        "base_url": args.base_url,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "scenario_count": len(results),
        "passed": sum(item["status"] == "PASS" for item in results),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    columns = [key for key, value in results[0].items() if not isinstance(value, (list, dict))] if results else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print(f"Detailed transcript: {json_path}")
    print(f"Metrics CSV: {csv_path}")


if __name__ == "__main__":
    asyncio.run(main())
