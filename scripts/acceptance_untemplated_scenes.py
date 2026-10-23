"""Model-backed regression for three scenes not present in scene_templates.json.

The checklist aliases are a small, source-derived coverage rubric, not a claim
that every household needs every item. Product relevance is judged against the
complete local demo catalog; this is not a live-commerce or user study.
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
        "id": "maternity-hospital-bag",
        "query": (
            "我近期要去医院待产，请按产妇住院、宝宝住院/出院、陪产人分组列一份可逐项核对的清单，"
            "把必备和按需分开；受医院规定或喂养方式影响的项目标待确认。不要给医疗或用药建议。"
        ),
        "gold": {
            "孕产/分娩资料": ["孕产妇手册", "产检资料", "产检本", "孕妇手册", "分娩计划", "出生计划", "病历"],
            "产妇舒适衣物": ["待产服", "生产衣物", "宽松衣物", "睡衣", "夜衣", "哺乳衣"],
            "拖鞋/便鞋": ["拖鞋", "便鞋", "室内鞋"],
            "产褥垫/产妇卫生用品": ["产褥垫", "产妇卫生巾", "产褥卫生巾", "卫生巾"],
            "产妇洗漱用品": ["牙刷", "牙膏", "洗漱包", "洗漱用品", "洗发", "毛巾"],
            "宝宝衣物": ["婴儿衣物", "新生儿衣物", "连体衣", "和尚服", "包屁衣", "婴儿服", "婴儿袜"],
            "纸尿裤": ["纸尿裤", "尿布"],
            "婴儿清洁用品": ["婴儿湿巾", "棉柔巾", "棉球", "棉片"],
            "纱布巾/包巾": ["纱布巾", "口水巾", "包巾", "襁褓"],
            "出院衣物/保暖": ["出院服", "出院衣物", "回家衣服", "保暖衣"],
            "手机与充电": ["手机", "充电器", "充电线", "充电宝"],
        },
        "conditional": {
            "婴儿汽车安全座椅": ["安全座椅", "婴儿提篮", "汽车座椅"],
            "哺乳相关用品": ["哺乳文胸", "哺乳胸罩", "防溢乳垫", "吸奶器"],
            "婴儿喂养用品": ["奶瓶", "喂养套装", "配方奶", "奶粉"],
        },
        "broad_terms": ["待产用品", "母婴用品", "产妇用品", "宝宝用品", "洗漱用品", "衣物"],
        "probes": [
            {
                "query": "我还想买婴儿喂养套装，帮我搜一下",
                "slot": "婴儿喂养套装",
                "gold_ids": ["P1091"],
            },
        ],
    },
    {
        "id": "two-day-car-camping",
        "query": (
            "我和朋友准备第一次自驾露营两天一夜，帮我列一份能逐项核对的装备和消耗品清单，"
            "按必备与按需分开；营地条件、天气会影响的项目标注出来，别把大类合成一项。"
        ),
        "gold": {
            "帐篷/遮蔽": ["帐篷", "天幕", "遮蔽帐"],
            "睡袋": ["睡袋"],
            "睡垫": ["睡垫", "防潮垫", "睡眠垫"],
            "照明": ["头灯", "手电", "营地灯", "露营灯", "灯笼"],
            "导航/离线地图": ["地图", "指南针", "gps", "离线导航"],
            "饮用水/水处理": ["饮用水", "水壶", "净水", "水处理"],
            "食物/额外补给": ["食物", "食品", "零食", "额外补给"],
            "急救用品": ["急救包", "急救用品", "创可贴", "止血"],
            "适合天气的衣物": ["保暖衣", "雨衣", "防风外套", "备用衣物", "换洗衣物"],
            "卫生用品": ["卫生纸", "厕纸", "牙刷", "牙膏", "洗漱包", "洗手液"],
        },
        "conditional": {
            "炉具/炊具": ["炉具", "炉头", "炊具", "锅具", "露营锅", "燃料"],
            "营地桌椅": ["露营桌", "折叠桌", "露营椅", "折叠椅", "露营凳"],
        },
        "broad_terms": ["露营装备", "露营用品", "睡眠系统", "衣物", "应急用品", "厨房用品"],
        "probes": [
            {
                "query": "我这次明确要买一个睡袋，帮我找商品",
                "slot": "睡袋",
                "gold_ids": ["P1043", "P1291", "P1292", "P1293", "P1294"],
            },
        ],
    },
    {
        "id": "first-cat-at-home",
        "query": (
            "我准备第一次接一只成年猫回家，请按喂养、如厕、休息躲藏、运输和互动分组列准备清单，"
            "必备与按需分开；不确定是否适合的健康事项请提醒问兽医，不要给医疗建议。"
        ),
        "gold": {
            "分开的食物碗和水碗": ["食物碗", "水碗", "猫碗", "宠物碗", "食盆"],
            "原有猫粮/过渡食物": ["猫粮", "猫食", "原来的食物", "过渡食物"],
            "猫砂盆与猫砂": ["猫砂盆", "猫厕所", "猫砂"],
            "猫床/安全躲藏处": ["猫床", "宠物床", "猫窝", "躲藏处", "安全房间", "纸箱"],
            "猫抓板/抓柱": ["猫抓板", "猫抓柱", "抓柱", "抓板"],
            "猫咪玩具": ["猫玩具", "逗猫棒", "玩具"],
            "安全猫包/航空箱": ["猫包", "猫航空箱", "宠物航空箱", "猫咪运输箱", "宠物箱"],
            "梳毛工具": ["梳子", "梳毛", "宠物梳", "刷毛"],
        },
        "conditional": {
            "额外躲藏/高处": ["猫爬架", "高处", "躲藏箱", "猫隧道"],
            "适应期用品": ["熟悉的毯子", "旧毯子", "安抚用品"],
        },
        "broad_terms": ["宠物用品", "猫咪用品", "喂养用品", "清洁用品", "休息用品"],
        "probes": [
            {
                "query": "我想买一个宠物碗，帮我搜一下",
                "slot": "宠物碗",
                "gold_ids": ["P1094"],
            },
            {
                "query": "我还需要买猫砂盆，请搜索",
                "slot": "猫砂盆",
                "gold_ids": [],
                "expect_gap": True,
            },
        ],
    },
]


def all_text(plan: dict) -> str:
    fields = ("tasks", "must_buy", "optional", "reusable", "not_buy", "unknowns", "prep")
    return " ".join(str(value) for key in fields for value in (plan.get(key) or []))


def first_plan(events: list[dict]) -> dict:
    for event in reversed(events):
        if event.get("type") in {"plan.update", "plan.preview"}:
            return event.get("payload", {}).get("shopping_plan") or {}
    return {}


def match_card(slot: str, hit: dict) -> bool:
    text = " ".join([
        str(hit.get("title") or ""),
        *(str(value) for value in hit.get("highlights") or []),
    ]).casefold()
    return slot.casefold() in text


async def turn(client: httpx.AsyncClient, base_url: str, session: str, query: str) -> dict:
    body = {
        "shopping_session_id": session,
        "buyer_id": "untemplated-scene-acceptance",
        "locale": "zh-CN",
        "currency": "CNY",
        "raw_query": query,
    }
    events: list[dict] = []
    marks: dict[str, float | None] = {
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
            if kind in {"token.delta", "plan.preview", "plan.update", "final.result"}:
                marks["first_feedback_sec"] = marks["first_feedback_sec"] or elapsed
            if kind in {"plan.preview", "plan.update"}:
                plan = event.get("payload", {}).get("shopping_plan") or {}
                if plan.get("tasks"):
                    marks["first_checklist_sec"] = marks["first_checklist_sec"] or elapsed
            if kind == "tool.result" and event.get("payload", {}).get("tool") == "product_search_tool":
                if event.get("payload", {}).get("hits"):
                    marks["first_product_sec"] = marks["first_product_sec"] or elapsed
    return {
        "http_status": response.status_code,
        "turn_end_sec": round(time.perf_counter() - started, 3),
        "milestones": marks,
        "events": events,
    }


def plan_metrics(case: dict, result: dict) -> dict:
    plan = first_plan(result["events"])
    text = all_text(plan).casefold()
    covered = [name for name, aliases in case["gold"].items()
               if any(alias.casefold() in text for alias in aliases)]
    conditional = [name for name, aliases in case["conditional"].items()
                   if any(alias.casefold() in text for alias in aliases)]
    tasks = [str(item) for item in plan.get("tasks") or []]
    broad = [task for task in tasks if any(term.casefold() == task.casefold() for term in case["broad_terms"])]
    model_calls = [event.get("payload", {}) for event in result["events"] if event.get("type") == "model.call"]
    must_buy = plan.get("must_buy") or []
    unknowns = set(plan.get("unknowns") or [])
    undeclared_purchase = [item for item in must_buy if item not in set(plan.get("optional") or [])]
    return {
        "http_status": result["http_status"],
        "first_feedback_sec": result["milestones"]["first_feedback_sec"],
        "first_checklist_sec": result["milestones"]["first_checklist_sec"],
        "turn_end_sec": result["turn_end_sec"],
        "template_id": plan.get("template_id"),
        "plan_stage": plan.get("stage"),
        "task_count": len(tasks),
        "tasks": tasks,
        "must_buy_count": len(must_buy),
        "unknown_count": len(unknowns),
        "unclassified_task_count": len(set(tasks) - unknowns - set(must_buy) - set(plan.get("reusable") or []) - set(plan.get("not_buy") or [])),
        "checklist_coverage": round(len(covered) / max(len(case["gold"]), 1), 4),
        "covered_reference_groups": covered,
        "missing_reference_groups": [name for name in case["gold"] if name not in covered],
        "conditional_groups_present": conditional,
        "broad_unexpanded_tasks": broad,
        "model_calls": len(model_calls),
        "model_call_details": [
            {key: call.get(key) for key in ("model", "elapsed_ms", "input_tokens", "output_tokens")}
            for call in model_calls
        ],
        "search_agent_dispatches": sum(
            event.get("type") == "agent.dispatch"
            and event.get("payload", {}).get("agent") == "search_agent"
            for event in result["events"]
        ),
        "error_events": [event.get("payload", {}) for event in result["events"] if event.get("type") == "error"],
        "events": result["events"],
    }


def search_metrics(probe: dict, result: dict) -> dict:
    searches = [event.get("payload", {}) for event in result["events"]
                if event.get("type") == "tool.result"
                and event.get("payload", {}).get("tool") == "product_search_tool"]
    hits = [hit for search in searches for hit in search.get("hits", [])]
    hit_ids = [str(hit.get("product_id") or hit.get("id") or "") for hit in hits]
    gold_ids = list(probe["gold_ids"])
    relevant_returned = [product_id for product_id in hit_ids if product_id in gold_ids]
    gap = any(bool(search.get("catalog_gap")) for search in searches)
    return {
        "query": probe["query"],
        "requested_slot": probe["slot"],
        "http_status": result["http_status"],
        "first_product_sec": result["milestones"]["first_product_sec"],
        "turn_end_sec": result["turn_end_sec"],
        "route": next((event.get("payload", {}).get("route") for event in result["events"]
                       if event.get("type") == "plan.update" and event.get("payload", {}).get("route")), ""),
        "model_calls": sum(event.get("type") == "model.call" for event in result["events"]),
        "search_agent_dispatches": sum(
            event.get("type") == "agent.dispatch"
            and event.get("payload", {}).get("agent") == "search_agent"
            for event in result["events"]
        ),
        "retrieval_actions": sorted({str(search.get("retrieval_action") or "") for search in searches}),
        "recall_strategies": sorted({str(search.get("recall_strategy") or "") for search in searches}),
        "candidate_ids": hit_ids,
        "candidate_titles": [str(hit.get("title") or "") for hit in hits],
        "gold_relevant_ids": gold_ids,
        "precision_at_k": round(len(relevant_returned) / len(hits), 4) if hits else (None if gold_ids else 1.0),
        "recall_at_k": round(len(set(relevant_returned)) / len(gold_ids), 4) if gold_ids else None,
        "catalog_gap_reported": gap,
        "candidate_count": len(hits),
        "events": result["events"],
    }


def persist(report: dict, rows: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output_dir / "metrics.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = list(dict.fromkeys(
            key for row in rows for key in row if key != "events"
        ))
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                             for key, value in row.items() if key != "events"})


async def run(base_url: str, output_dir: Path, checklist_only: bool = False) -> dict:
    report = {
        "version": "untemplated-scenes-20261023-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "note": (
            "合成 API 回归，非真人研究。清单覆盖度按来源列出的参考项目组计算；"
            "商品 precision/recall 使用本地模拟目录的人工相关 ID 标注；"
            "不代表真实商城或全行业统一标准。首屏延迟是 SSE 事件到达，不是浏览器绘制。"
        ),
        "scenarios": [],
        "aggregate": {},
    }
    rows: list[dict] = []
    timeout = httpx.Timeout(300.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for case in CASES:
            session = f"untemplated-{case['id']}-{uuid.uuid4().hex[:8]}"
            initial = await turn(client, base_url, session, case["query"])
            checklist = plan_metrics(case, initial)
            scenario_result = {"scenario_id": case["id"], "session_id": session, "query": case["query"],
                               "checklist": checklist, "search_probes": []}
            row = {"scenario_id": case["id"], "stage": "generated-checklist", **{
                key: value for key, value in checklist.items() if key != "events"
            }}
            rows.append(row)
            for probe in ([] if checklist_only else case["probes"]):
                result = await turn(client, base_url, session, probe["query"])
                measured = search_metrics(probe, result)
                scenario_result["search_probes"].append(measured)
                rows.append({"scenario_id": case["id"], "stage": f"search:{probe['slot']}", **{
                    key: value for key, value in measured.items() if key != "events"
                }})
            scenario_result["pass_checks"] = {
                "not_a_persisted_template": not checklist["template_id"],
                "model_was_invoked": checklist["model_calls"] > 0,
                "checklist_coverage_at_least_80pct": checklist["checklist_coverage"] >= 0.8,
                "no_unexpanded_broad_task": not checklist["broad_unexpanded_tasks"],
                "no_assumed_purchase": checklist["must_buy_count"] == 0,
                "all_task_statuses_resolved": checklist["unclassified_task_count"] == 0,
                "searches_are_precise": None if checklist_only else all(
                    probe["precision_at_k"] == 1.0
                    and (probe["recall_at_k"] is None or probe["recall_at_k"] >= 0.6)
                    for probe in scenario_result["search_probes"] if not probe.get("catalog_gap_reported")
                ),
                "catalog_gaps_are_not_filled_with_unrelated_items": all(
                    (probe["candidate_count"] == 0 and probe["catalog_gap_reported"])
                    for probe in scenario_result["search_probes"]
                    if next((item for item in case["probes"] if item["slot"] == probe["requested_slot"]), {}).get("expect_gap")
                ),
            }
            scenario_result["pass"] = all(
                value is not False for value in scenario_result["pass_checks"].values()
            )
            report["scenarios"].append(scenario_result)
            persist(report, rows, output_dir)
            print(json.dumps({
                "scenario": case["id"], "pass": scenario_result["pass"],
                "template": checklist["template_id"], "calls": checklist["model_calls"],
                "checklist_sec": checklist["first_checklist_sec"], "end_sec": checklist["turn_end_sec"],
                "coverage": checklist["checklist_coverage"],
                "search": [{"slot": p["requested_slot"], "first_card_sec": p["first_product_sec"],
                            "end_sec": p["turn_end_sec"], "precision": p["precision_at_k"],
                            "recall": p["recall_at_k"], "ids": p["candidate_ids"]}
                           for p in scenario_result["search_probes"]],
                "failed_checks": [key for key, ok in scenario_result["pass_checks"].items() if not ok],
            }, ensure_ascii=False), flush=True)
    report["aggregate"] = {
        "scenario_pass_count": sum(bool(item.get("pass")) for item in report["scenarios"]),
        "scenario_count": len(report["scenarios"]),
        "generated_checklist_model_calls": [item["checklist"]["model_calls"] for item in report["scenarios"]],
        "generated_checklist_latency_sec": [item["checklist"]["first_checklist_sec"] for item in report["scenarios"]],
        "source_coverage_proxy": [item["checklist"]["checklist_coverage"] for item in report["scenarios"]],
    }
    persist(report, rows, output_dir)
    print(f"REPORT {output_dir / 'report.json'}")
    print(f"CSV {output_dir / 'metrics.csv'}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--checklist-only", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(run(args.base_url, args.output_dir, checklist_only=args.checklist_only))
    if report["aggregate"].get("scenario_pass_count") != len(CASES):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
