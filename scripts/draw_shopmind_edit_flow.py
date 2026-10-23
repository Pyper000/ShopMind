"""Draw post-checklist edit routing (detailed) + a concrete timeline example."""
from __future__ import annotations

from pathlib import Path

OUT = Path("docs/assets")
OUT.mkdir(parents=True, exist_ok=True)

FONT = "'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif"


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=13, fill="#c3cddd", weight=400, anchor="start"):
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{esc(s)}</text>')


def rect(x, y, w, h, fill="#141a24", stroke="#334155", r=10, dash=False, sw=1.4, op=1):
    d = ' stroke-dasharray="6 5"' if dash else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" '
            f'fill-opacity="{op}" stroke="{stroke}" stroke-width="{sw}"{d}/>')


def arrow(points, color="#5b6b85", dash=False):
    pts = " ".join(f"{x},{y}" for x, y in points)
    d = ' stroke-dasharray="5 4"' if dash else ""
    (x1, y1), (x2, y2) = points[-2], points[-1]
    if abs(x2 - x1) >= abs(y2 - y1):
        head = "M -7,-4.5 L 0,0 L -7,4.5" if x2 > x1 else "M 7,-4.5 L 0,0 L 7,4.5"
    else:
        head = "M -4.5,-7 L 0,0 L 4.5,-7" if y2 > y1 else "M -4.5,7 L 0,0 L 4.5,7"
    return (f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5"{d}/>'
            f'<path d="{head}" transform="translate({x2},{y2})" fill="none" stroke="{color}" stroke-width="1.5"/>')


def pill(x, y, s, fill="#1e293b", stroke="#475569", color="#cbd5e1", size=11.5):
    w = 13 + len(s) * (size * 0.66)
    return rect(x, y, w, 22, fill, stroke, r=11) + text(x + w / 2, y + 15.5, s, size, color, 600, "middle")


# ---------------------------------------------------------------- figure 1

W, H = 2160, 2360

CASE_FILL = {
    "search": ("#0f2b2c", "#2dd4bf"),
    "state": ("#241b38", "#a78bfa"),
    "calc": ("#1a2331", "#94a3b8"),
    "risk": ("#33240f", "#f59e0b"),
}


def stage_box(x, y, w, title, lines, kind="calc", badge=""):
    fill, stroke = CASE_FILL[kind]
    h = 40 + 21 * len(lines) + 12
    out = [rect(x, y, w, h, fill, stroke, r=9)]
    out.append(text(x + 12, y + 24, title, 13.5, "#f1f5f9", 700))
    if badge:
        out.append(text(x + w - 12, y + 24, badge, 11, stroke, 700, "end"))
    for i, line in enumerate(lines):
        out.append(text(x + 12, y + 45 + 21 * i, line, 12.3, "#b8c4d6"))
    return "".join(out), h


svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
       f'<rect width="{W}" height="{H}" fill="#080b11"/>']

svg.append(text(56, 60, "清单之后的每一次改需求：谁接管、改什么状态、哪些候选作废、要不要再调 SearchAgent", 26, "#eef2fa", 700))
svg.append(text(56, 92, "紫色=MainAgent（唯一的状态所有者）  青色=SearchAgent（只召回/过滤/排序）  灰色=确定性代码  琥珀=必须先澄清或必须如实告知", 13, "#8f9cb5"))

# state model
svg.append(rect(56, 120, 660, 258, "#0e141d", "#3b4a63"))
svg.append(text(76, 150, "① 状态模型：改需求 = 改这里，而不是重写一段话", 15, "#e6ecf7", 700))
for i, line in enumerate([
    "ShoppingPlan vN（每轮 +1，落盘 schemes/<session>.json）",
    "tasks[] 有序；must_buy / optional / reusable / not_buy / unknowns",
    "budget_major · preferences[] · destination · people · goal · core_use",
    "CandidatePool：每个槽位缓存的 candidate_ids + 命中数",
    "UI slotStatus：用户点的按钮才是权威事实",
]):
    svg.append(text(78, 180 + 27 * i, "• " + line, 12.6, "#aebbd0"))
svg.append(rect(76, 320, 620, 40, "#1b2433", "#475569", r=8))
svg.append(text(88, 345, "不变量：检索结果永远不能反写 reusable / not_buy / unknowns；只有用户输入或按钮能改状态。", 12.2, "#cbd7e8", 600))

# router
svg.append(rect(740, 120, 1364, 258, "#0e141d", "#3b4a63"))
svg.append(text(760, 150, "② 入口路由：先看结构化事实，再看确定性词表，最后才用模型", 15, "#e6ecf7", 700))
router_steps = [
    (762, "L0 清单控件", "checklist_selection 存在", "最高优先级；直接提交，零模型"),
    (1090, "L1 槽位点名", "文本命中现有槽位名/别名", "改状态，不新建场景"),
    (1418, "L2 品类词表", "命中品类别名表（创可贴…）", "新建单槽位，不重出清单"),
    (1746, "L3 约束/场景词", "预算、轻便、人数、换场景", "进入对应变更类型"),
]
for x, title, cond, result in router_steps:
    svg.append(rect(x, 172, 300, 92, "#141c28", "#4b5f7d", r=9))
    svg.append(text(x + 12, 194, title, 13, "#eaf0fa", 700))
    svg.append(text(x + 12, 216, cond, 11.6, "#9fb0c6"))
    svg.append(text(x + 12, 240, "→ " + result, 11.6, "#7dd3c0"))
svg.append(text(762, 292, "L3 仍无法判定（“再便宜点”“换个更好的”这类没有数值/对象的表达）→ MainAgent 只问一个会改变结果的问题；不得整份重规划，也不得让 SearchAgent 猜业务意图。", 12.3, "#f0b95b"))
svg.append(text(762, 316, "L1/L2 判定不依赖 LLM 抽样：槽位名与别名表命中即走确定性分支；只有在同义改写无法命中时，才允许 MainAgent 做一次短规范化，产出仍必须落成单个槽位。", 12.3, "#aebbd0"))
svg.append(text(762, 344, "场景整体变更（换目的地/换活动/人数变化）不算“改需求微调”，必须重规划，但只重召回受影响槽位；场景+核心用途同时变才整场景重召回。", 12.3, "#aebbd0"))
svg.append(text(762, 366, "Stage 只能由 MainAgent 改：explore（等确认）/ search（有待购）/ compare（比较）/ confirm。SearchAgent 不判定阶段。", 12.3, "#aebbd0"))

# header for case table
TABLE_Y = 410
svg.append(text(56, TABLE_Y - 8, "③ 11 类后续改动，逐条链路（按发生概率排序）", 17, "#eef2fa", 700))
cols = [("用户这样说 / 这样点", 300), ("MainAgent 必做", 372), ("状态与候选怎么变", 392), ("SearchAgent 做什么", 372), ("UI 与延迟预算", 396)]
x = 56
svg.append(rect(56, TABLE_Y + 8, 2100, 34, "#111823", "#2f3d52", r=8))
for label, width in cols:
    svg.append(text(x + 12, TABLE_Y + 31, label, 12.8, "#93a4bd", 700))
    x += width + 14

cases = [
    ("C1 新增一个明确品类", "“我还想买创可贴”", ["识别为单槽位新增", "不调用完整场景重规划"], ["tasks += 创可贴", "status=must_buy", "旧槽位状态全部保留"], ["只派 创可贴", "走目录快路径"], ["旧卡片留在原位，追加分组", "目标 3–6s"], "search", "现网会重出全清单"),
    ("C2 现有项：未确认→要买", "“泳衣我没有，买一件”", ["匹配到已有槽位", "不新增重复项"], ["unknowns → must_buy", "只失效该槽位候选"], ["只搜 泳衣", "其余不重搜"], ["该分组出卡；其余不动", "目标 3–6s"], "state", ""),
    ("C3 现有项：要买→已有", "“泳镜我有了”", ["匹配槽位，改状态"], ["must_buy → reusable", "删除该槽位候选", "总价立即重算"], ["不调用", "不得为该槽位搜索"], ["卡片消失/折叠为“已有”", "目标 <0.5s"], "calc", "零搜索"),
    ("C4 不要了 / 暂缓", "“沙滩垫先不买”", ["匹配槽位，改状态"], ["→ not_buy", "删除候选与已选商品"], ["不调用"], ["分组移除；预算重算", "目标 <0.5s"], "calc", "零搜索"),
    ("C5 预算变化", "“预算改成 300”", ["数值解析 + 硬约束", "不重写清单"], ["budget_change", "全部候选保留待过滤"], ["每槽位 filter_and_rerank", "复用 candidate_ids；不足才局部补召回"], ["卡片按新价重排", "标出被预算挡掉的项", "目标 1–3s"], "search", "禁止重嵌入"),
    ("C6 偏好变化", "“要轻一点 / 性价比高”", ["写 preferences", "清单事实不变"], ["preference_change", "候选保留"], ["对同批候选重排", "必要时向用户说明取舍"], ["顺序变化，不换品类", "目标 1–3s"], "search", ""),
    ("C7 数量 / 人数", "“我们 4 个人去”", ["改 people / 数量", "重算每槽位份数"], ["scene_changed（人数）", "数量作为新约束"], ["受影响槽位 recall_local", "不整库乱搜"], ["清单数量更新，卡片重取", "目标 4–8s"], "state", ""),
    ("C8 换场景 / 换用途", "“不去海边了，改露营三天”", ["重建任务，保留明确沿用的预算/偏好"], ["scene_changed + core_use_changed", "候选池整场景清空"], ["按新方案整场景召回；仅用户已确认待购项"], ["显示清单差异，不静默抹掉确认", "目标 5–12s"], "risk", "确认要显式"),
    ("C9 指定具体候选", "“就买第二个 / 换长发款”", ["解析候选序号或标题", "绑定 product_id / sku"], ["selection[槽位] = product_id", "不改清单状态"], ["不调用"], ["选中态 + 合计立即更新", "目标 <0.2s"], "calc", "推荐≠选中"),
    ("C10 比较 / 追问细节", "“这两个差在哪”", ["使用已有卡片事实", "不新增召回"], ["状态不变"], ["不调用；资料缺才补一次"], ["给出对比与建议，不擅自替换", "目标 1–3s"], "calc", ""),
    ("C11 模糊改进", "“再便宜点 / 换个更好的”", ["缺数值或对象 → 只问一个问题"], ["不改状态，不重规划"], ["不调用"], ["给 2–3 个可选方向让用户点", "目标 <1s"], "risk", "禁止瞎猜"),
]

y = TABLE_Y + 50
for idx, (label, quote, main, state, search, ui, kind, note) in enumerate(cases):
    row_h = 118
    band = "#0d131b" if idx % 2 == 0 else "#0f1620"
    svg.append(rect(56, y, 2100, row_h - 8, band, "#26334a", r=8, sw=0.9))
    # label column
    svg.append(text(70, y + 24, label, 13.2, "#e7edf9", 700))
    svg.append(text(70, y + 46, quote, 12.2, "#8ea0b8"))
    if note:
        svg.append(pill(70, y + 58, note, "#2b2112", "#8a6420", "#e2b264", 10.5))
    cx = 56 + 300 + 14
    for lines, width, colkind in [(main, 372, "state"), (state, 392, kind), (search, 372, "search"), (ui, 396, "calc")]:
        body, _ = stage_box(cx, y + 8, width, "", lines, colkind)
        svg.append(body)
        cx += width + 14
    y += row_h

# footer
FY = y + 14
svg.append(rect(56, FY, 2100, 150, "#0d131b", "#3b4a63"))
svg.append(text(76, FY + 30, "④ 这 11 条共享的三条硬规则", 15, "#e6ecf7", 700))
svg.append(text(76, FY + 58, "1) 只有“新增槽位 / 换场景 / 人数变化”会改变清单结构；改预算、改偏好、指定候选、比较都不该重出清单。", 12.8, "#b8c4d6"))
svg.append(text(76, FY + 84, "2) 状态改成“已有 / 不买 / 待确认”时立刻删掉该槽位候选与已选商品；不能留着旧卡片继续参与合计。", 12.8, "#b8c4d6"))
svg.append(text(76, FY + 110, "3) C1–C4 是用户最常做的补充，必须零/一次模型调用；一轮里不要让 MainAgent 既重写清单又等 SearchAgent，再写一段总结。", 12.8, "#b8c4d6"))
svg.append(text(76, FY + 136, "注：以上“目标延迟”是待实测的产品预算，不是当前实测值；当前实测基线见 eval/results 与 docs/latency-iterations.md。", 12, "#8494ab"))

svg.append("</svg>")
(OUT / "shopmind-edit-flow.svg").write_text("".join(svg), encoding="utf-8")
print(OUT / "shopmind-edit-flow.svg")
