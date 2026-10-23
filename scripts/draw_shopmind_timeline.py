"""Draw a concrete multi-turn timeline: beach checklist -> edits -> selection."""
from __future__ import annotations

from pathlib import Path

OUT = Path("docs/assets")
OUT.mkdir(parents=True, exist_ok=True)
FONT = "'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif"


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def T(x, y, s, size=12.5, fill="#c3cddd", weight=400, anchor="start"):
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}">{esc(s)}</text>')


def R(x, y, w, h, fill="#111823", stroke="#2f3d52", r=9, dash=False, sw=1.2):
    d = ' stroke-dasharray="6 5"' if dash else ""
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>'


W, H = 2200, 1780
LANES = [
    ("用户", 120, "#7c9cff"),
    ("路由（确定性）", 300, "#94a3b8"),
    ("MainAgent", 480, "#a78bfa"),
    ("SearchAgent", 660, "#2dd4bf"),
    ("状态版本", 840, "#f0b95b"),
    ("前端可见", 1020, "#38bdf8"),
]
LEFT = 300

svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
       f'<rect width="{W}" height="{H}" fill="#080b11"/>']
svg.append(T(40, 52, "实例：海边清单已确认后，用户连续 6 次改需求 —— 每一轮走哪条链路、动了什么、调不调模型", 25, "#eef2fa", 700))
svg.append(T(40, 82, "每格标注「模型调用次数」。C1/C3/C9 是本次要修好的核心：用户的常见补充不该再等一次完整规划。", 13, "#8f9cb5"))

for name, y, color in LANES:
    svg.append(T(40, y + 22, name, 13.5, color, 700))
    svg.append(f'<line x1="{LEFT}" y1="{y}" x2="{W - 40}" y2="{y}" stroke="#1c2634" stroke-width="1"/>')

TURN_W = 296
TURN_X = [LEFT + 8 + i * (TURN_W + 8) for i in range(6)]
turn_titles = [
    "T1 首轮场景",
    "T2 清单确认",
    "T3 补一个品类",
    "T4 改预算",
    "T5 改已有状态",
    "T6 选具体款",
]
for x, title in zip(TURN_X, turn_titles):
    svg.append(R(x, 110, TURN_W, 30, "#16202e", "#3b4a63", r=7))
    svg.append(T(x + 10, 130, title, 12.8, "#dbe5f5", 700))


def cell(col, lane_index, lines, kind="plain", badge=""):
    x = TURN_X[col] + 4
    y = LANES[lane_index][1] + 14
    w = TURN_W - 8
    palette = {
        "plain": ("#101720", "#28354a"),
        "main": ("#241b38", "#a78bfa"),
        "search": ("#0f2b2c", "#2dd4bf"),
        "state": ("#2b2212", "#f0b95b"),
        "front": ("#10202f", "#38bdf8"),
        "warn": ("#33240f", "#f59e0b"),
    }
    fill, stroke = palette[kind]
    h = 20 * len(lines) + 14
    out = [R(x, y, w, h, fill, stroke, r=7)]
    for i, line in enumerate(lines):
        out.append(T(x + 9, y + 18 + 20 * i, line, 11.8, "#c1cddf"))
    if badge:
        out.append(T(x + w - 8, y + 13, badge, 10.5, stroke, 700, "end"))
    return "".join(out)


# T1 scene plan
svg.append(cell(0, 0, ["“周末去海边两天", "帮我准备清单”"]))
svg.append(cell(0, 1, ["命中场景模板", "海边（唯一已配置）"]))
svg.append(cell(0, 2, ["读模板 → 建 v1", "可选：短寒暄"]))
svg.append(cell(0, 3, ["不派发"]))
svg.append(cell(0, 4, ["v1 · 18 项", "全部 unknown"], "state"))
svg.append(cell(0, 5, ["清单立即出现", "首份清单 / 首条反馈"], "front"))

# T2 checklist confirm
svg.append(cell(1, 0, ["逐项点选 → 确认清单"]))
svg.append(cell(1, 1, ["checklist_selection", "最高优先级 L0"]))
svg.append(cell(1, 2, ["确定性落状态", "不解析自然语言"]))
svg.append(cell(1, 3, ["按待购槽位并行", "每槽位目录快路径"]))
svg.append(cell(1, 4, ["v2", "have / buy / skip 落定"], "state"))
svg.append(cell(1, 5, ["分组出卡", "首批商品时间"], "front"))

# T3 add item
svg.append(cell(2, 0, ["“我还想买创可贴”"]))
svg.append(cell(2, 1, ["L2 品类词命中", "→ 单槽位新增"], "warn"))
svg.append(cell(2, 2, ["只 patch 一个槽位", "清单结构不变"]))
svg.append(cell(2, 3, ["只搜 创可贴", "不重搜其他 17 项"]))
svg.append(cell(2, 4, ["v3", "仅 +创可贴"], "state"))
svg.append(cell(2, 5, ["旧卡片原地不动", "追加 1 个分组"], "front"))

# T4 budget
svg.append(cell(3, 0, ["“预算改成 300”"]))
svg.append(cell(3, 1, ["L3 约束词", "数值 300"]))
svg.append(cell(3, 2, ["记录 budget", "清单结构不变"]))
svg.append(cell(3, 3, ["每槽位 filter_and_rerank", "复用 candidate_ids"]))
svg.append(cell(3, 4, ["v4", "budget_change"], "state"))
svg.append(cell(3, 5, ["价格重排 + 超限提示", "不重新 embedding"], "front"))

# T5 have
svg.append(cell(4, 0, ["“泳镜我有了”"]))
svg.append(cell(4, 1, ["L1 点名已有槽位"]))
svg.append(cell(4, 2, ["must_buy→reusable", "不新增、不重写"]))
svg.append(cell(4, 3, ["不调用", "该槽位候选作废"]))
svg.append(cell(4, 4, ["v5", "删该槽位候选"], "state"))
svg.append(cell(4, 5, ["卡片消失→“已有”", "合计立即减少"], "front"))

# T6 pick
svg.append(cell(5, 0, ["点选：泳衣第 2 款", "再点确认方案"]))
svg.append(cell(5, 1, ["UI 事件，非自然语言", "product_id 明确"]))
svg.append(cell(5, 2, ["绑定选择，不改状态"]))
svg.append(cell(5, 3, ["不调用"]))
svg.append(cell(5, 4, ["v5 不变", "selection 更新"], "state"))
svg.append(cell(5, 5, ["合计按所选×数量", "确认=演示终点"], "front"))

# latency band
BAND_Y = 1130
svg.append(R(40, BAND_Y, W - 80, 330, "#0d131b", "#3b4a63", r=10))
svg.append(T(60, BAND_Y + 30, "对照：现在为什么慢，以及每轮该测哪一段", 16, "#e6ecf7", 700))
rows = [
    ("现在的实际链路（T3 这类补充）", "自由文本 → MainAgent 完整重写 18 项方案（1 次大模型）→ 再等 SearchAgent（每槽位 1 次）→ 再让 MainAgent 写总结（1 次）→ 前端才更新。所以一个“加创可贴”能拖到 60s+，还会重出整份清单。"),
    ("改造后的 T3", "路由器命中品类词 → patch 单槽位（0 次大模型）→ SearchAgent 目录快路径（0 次大模型）→ 出卡。目标 3–6s，且清单不重置。"),
    ("要打在事件流上的 4 个时间点", "① 请求发出 ② 首个有用文本/清单 ③ 首张对应商品卡 ④ final.result。按轮次分别记录，不能只报总耗时。"),
    ("必须同时记录的准确性字段", "槽位 → 是否返回同品类卡；返回卡片中被判为无关的条数；T3 是否发生 plan 结构重置（应为 0）。"),
]
for i, (head, body) in enumerate(rows):
    y = BAND_Y + 62 + i * 66
    svg.append(T(62, y, head, 12.8, "#f0b95b" if i < 2 else "#7dd3c0", 700))
    svg.append(T(62, y + 24, body, 12.2, "#b8c4d6"))

svg.append(T(60, BAND_Y + 316, "注：目标延迟是产品预算，尚未实测；上线前用三场景各一条主线 + 一条品类补充实测再定稿。", 11.8, "#8494ab"))

svg.append("</svg>")
(OUT / "shopmind-timeline.svg").write_text("".join(svg), encoding="utf-8")
print(OUT / "shopmind-timeline.svg")
