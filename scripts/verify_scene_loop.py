"""Run real-model scene conversations and save observed results, never mock replies."""
import argparse
import json
import time
import uuid
from pathlib import Path

import httpx

CASES = {
    "gift": ["想给妈妈买一件礼物，但不知道买什么。", "她喜欢喝茶，预算300元，寄到中国。", "她已经有茶具了，不要茶具，换个方向。"],
    "commute": ["想让每天的地铁通勤舒服一点，不知道该买什么。", "主要是太吵，预算300元，不想戴头戴式耳机，寄到中国。", "预算改为100元，优先轻便。"],
    "rental": ["想让出租屋温馨一点，不知道先买什么。", "预算300元，主要想改善灯光和桌面，不要香薰，寄到中国。", "先只选桌面用品，给我比较两个选择。"],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8012")
    parser.add_argument("--case", choices=CASES)
    args = parser.parse_args()
    records = []
    for name, queries in CASES.items():
        if args.case and name != args.case:
            continue
        session = "verify-" + uuid.uuid4().hex[:12]
        for query in queries:
            started = time.monotonic()
            response = httpx.post(args.base + "/commerce/intents", json={
                "shopping_session_id": session, "buyer_id": "verify-buyer", "raw_query": query,
            }, timeout=180)
            data = response.json()
            record = {"case": name, "query": query, "status": response.status_code,
                      "seconds": round(time.monotonic() - started, 2), "response": data}
            records.append(record)
            print(json.dumps(record, ensure_ascii=True), flush=True)
            Path("data").mkdir(exist_ok=True)
            Path("data/scene-loop-observed.json").write_text(
                json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            if response.status_code != 200 or data.get("final_text", "").startswith("[error]"):
                raise RuntimeError("Real model request failed; see observed report")


if __name__ == "__main__":
    main()
