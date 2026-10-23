"""Draw the ShopMind route/flow diagram as SVG (then PNG via Edge).

Deterministic layout, no diagram dependency: boxes + orthogonal arrows.
"""
from __future__ import annotations

from pathlib import Path

W, H = 1780, 1460
OUT = Path("docs/assets")

COLORS = {
    "user": ("#1f2a44", "#7c9cff"),
    "main": ("#2a2140", "#a78bfa"),
    "search": ("#123033", "#2dd4bf"),
    "catalog": ("#1c2433", "#94a3b8"),
    "gap": ("#3a2b14", "#f59e0b"),
    "front": ("#14263d", "#38bdf8"),
}


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def box(x, y, w, title, lines, kind="catalog", h=None, dashed=False):
    fill, stroke = COLORS[kind]
    h = h or 44 + 24 * len(lines) + 14
    parts = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="1.6"' + (' stroke-dasharray="7 5"' if dashed else '') + '/>',
        f'<text x="{x + 16}" y="{y + 28}" class="t">{esc(title)}</text>',
    ]
    for index, line in enumerate(lines):
        parts.append(f'<text x="{x + 16}" y="{y + 54 + 24 * index}" class="s">{esc(line)}</text>')
    return "".join(parts)


def arrow(points, dashed=False, color="#64748b"):
    path = " ".join(f"{x},{y}" for x, y in points)
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    end = points[-1]
    prev = points[-2]
    dx, dy = end[0] - prev[0], end[1] - prev[1]
    if abs(dx) >= abs(dy):
        d = "M -8,-5 L 0,0 L -8,5" if dx > 0 else "M 8,-5 L 0,0 L 8,5"
    else:
        d = "M -5,-8 L 0,0 L 5,-8" if dy > 0 else "M -5,8 L 0,0 L 5,8"
    return (
        f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="1.6"{dash}/>'
        f'<path d="{d}" transform="translate({end[0]},{end[1]})" fill="none" stroke="{color}" '
        'stroke-width="1.6"/>'
    )


body = []
body.append('<text x="48" y="58" class="h">ShopMind：从一句需求到商品方案（建议目标流程）</text>')
body.append('<text x="48" y="88" class="n">紫色=MainAgent 理解与状态 · 青色=SearchAgent 检索 · 琥珀=目录缺位如实告知 · 灰色=确定性计算。当前代码只实现左列前两段与右侧灰色段，其余为待确认目标。</text>')

# Entry
body.append(box(48, 130, 250, "用户输入", ["场景表达：帮我准备开学", "明确品类：还想买创可贴", "约束微调 / 场景变更"], "user"))
body.append(arrow([(298, 186), (352, 186)]))
body.append(box(352, 130, 290, "MainAgent 先判意图", ["① 整场景需求", "② 明确单品/品类", "③ 预算/偏好微调", "④ 场景/人数/用途变更", "⑤ 信息不足"], "main", h=176))

# Route rows: (y, label, detail lines, kind)
routes = [
    (340, "① 场景规划", ["模板优先（如海边）；未覆盖时", "只问一个会改变清单的问题"], "main"),
    (520, "② 明确品类直达", ["别名/品类词命中：新增单槽位", "已有方案：只改该槽位，不重出清单"], "main"),
    (700, "③ 约束微调", ["预算/偏好变化：保留方案与候选", "只对该槽位过滤、重排"], "main"),
    (880, "④ 实质变更", ["场景/人数/核心用途变化才重规划", "按影响范围：局部补召回 / 整场景召回"], "main"),
]
for y, title, lines, kind in routes:
    body.append(box(352, y, 290, title, lines, kind, h=104))
    body.append(arrow([(642, y + 52), (700, y + 52)]))

body.append(box(700, 340, 250, "具体必备 / 按需清单", ["逐项可核对，不合并大类", "按需项不自动进入待购"], "user", h=104))
body.append(arrow([(950, 392), (1000, 392), (1000, 330)]))
body.append(box(700, 520, 250, "单槽位方案更新", ["不重开整份场景清单", "保留已确认状态"], "main", h=104))
body.append(arrow([(950, 572), (1000, 572), (1000, 470)]))
body.append(box(700, 700, 250, "复用原候选重排", ["候选不足才局部补召回"], "search", h=104))
body.append(arrow([(950, 752), (1000, 752), (1000, 610)]))
body.append(box(700, 880, 250, "受影响范围重召回", ["不无故抹掉用户确认"], "search", h=104))
body.append(arrow([(950, 932), (1000, 932), (1000, 750)]))

# Clarify loop
body.append(box(352, 1060, 290, "⑤ 只问一个关键问题", ["问题必须会改变品类/数量/安全适配"], "main", h=80))
body.append(arrow([(352, 1100), (322, 1100), (322, 156), (352, 156)], dashed=True))

# SearchAgent bar
body.append('<rect x="1000" y="250" width="250" height="700" rx="16" fill="#123033" stroke="#2dd4bf" stroke-width="1.8"/>')
body.append('<text x="1020" y="290" class="t">SearchAgent（子代理）</text>')
body.append('<text x="1020" y="322" class="s">职责：召回 / 硬约束 / 排序</text>')
body.append('<text x="1020" y="348" class="s">不改写购物清单事实</text>')
body.append('<text x="1020" y="386" class="s">• 一批独立槽位并行派发</text>')
body.append('<text x="1020" y="412" class="s">• 只处理本轮受影响槽位</text>')
body.append('<text x="1020" y="438" class="s">• 槽位→商品必须同品类</text>')
body.append('<text x="1020" y="476" class="s">检索工具：Qdrant 向量召回*</text>')
body.append('<text x="1020" y="502" class="s">→ Reranker 精排；异常时降级</text>')
body.append('<text x="1020" y="528" class="s">→ 关键词 2-gram（如实标注）</text>')
body.append('<text x="1020" y="566" class="s">硬约束：价格上限 / 目的国</text>')
body.append('<text x="1020" y="592" class="s">在工具内完成，不让模型猜</text>')
body.append('<text x="1020" y="630" class="s">返回：每槽位 ≤3 个候选</text>')
body.append('<text x="1020" y="656" class="s">+ 匹配依据 / 未知 / 检索路径</text>')
body.append('<text x="1020" y="694" class="s">没检索到就返回空，并说明</text>')
body.append('<text x="1020" y="720" class="s">“模拟目录未收录 / 待入驻”</text>')
body.append('<text x="1020" y="758" class="s">禁止拿相邻品类凑数</text>')
body.append('<text x="1020" y="796" class="s">性能目标：首屏先给清单/卡片，</text>')
body.append('<text x="1020" y="822" class="s">不让用户干等整轮结束</text>')

# Right column
rx, rw = 1300, 430
body.append(arrow([(1250, 470), (1270, 470), (1270, 186), (1300, 186)]))
body.append(box(rx, 130, rw, "ProductSearchTool", ["Qdrant 召回 → Reranker；故障降级关键词", "candidate_ids 可复用，避免重复召回", "严格同品类校验 + 价格/配送硬约束"], "catalog", h=104))
body.append(arrow([(rx + rw / 2, 234), (rx + rw / 2, 280)]))
body.append(box(rx, 280, rw, "候选是否存在？", ["有：按槽位展示，每槽位最多 3 个候选卡"], "front", h=70))
body.append(arrow([(rx + 90, 350), (rx + 90, 396)]))
body.append(arrow([(rx + 330, 350), (rx + 330, 396)]))
body.append(box(rx, 396, 200, "有同品类候选", ["卡片含价格、依据、", "取舍与未知"], "front", h=104))
body.append(box(rx + 230, 396, 200, "没有 → 缺口", ["显示模拟目录未覆盖 /", "待入驻；不凑无关类目"], "gap", h=104))
body.append(arrow([(rx + 100, 500), (rx + 100, 546), (rx + 215, 546)]))
body.append(arrow([(rx + 330, 500), (rx + 330, 546), (rx + 215, 546)]))
body.append(box(rx, 546, rw, "用户选择", ["每个待购槽位选 0 或 1 件；按需项需主动纳入", "“加入比较”只比较，不计入采购总价"], "user", h=104))
body.append(arrow([(rx + rw / 2, 650), (rx + rw / 2, 696)]))
body.append(box(rx, 696, rw, "应用层计算商品价合计（确定性）", ["Σ 选中 SKU 单价 × 数量；显示预算上限差额", "不需要 LLM；TradeAgent 不是报价器", "未确认配送地时不声称到手价"], "catalog", h=118))
body.append(arrow([(rx + rw / 2, 814), (rx + rw / 2, 860)]))
body.append(box(rx, 860, rw, "用户确认这套采购方案（MVP 终点）", ["当前只记录演示选择：不加购物车、不创建订单", "如需下单：用户明确要求 + 二次确认商品/数量/地址"], "front", h=104))
body.append(arrow([(rx + rw / 2, 964), (rx + rw / 2, 1010)], dashed=True))
body.append(box(rx, 1010, rw, "TradeAgent（未来，本 MVP 关闭）", ["仅在用户明确“下单”后校验库存并创建订单", "create_order_tool 返回订单状态；不含支付/物流", "金额只能取工具/目录事实，禁止自行计算"], "main", h=118, dashed=True))

# Foot notes
body.append('<text x="48" y="1190" class="n">当前代码现状：清单控件提交 → 直接提交状态 → 只搜明确待购槽位；但聊天框里“我还想买创可贴”会进入完整方案更新（首工具强制 update_shopping_plan_tool），因此会重出清单。</text>')
body.append('<text x="48" y="1220" class="n">本次改造只补“路由器 + 单槽位直达 + 受控召回范围 + 商品选择/合计”，保留 MainAgent 管状态、SearchAgent 管召回排序、TradeAgent 休眠的三层边界。</text>')
body.append('<text x="48" y="1250" class="n">清单 UI 的按钮事实仍优先于自然语言推断；任何召回结果都不能反向改写“已有 / 不买 / 待确认”。</text>')
body.append('<text x="48" y="1280" class="n">* 未配置 embedding/Qdrant 或调用异常时自动降级关键词召回，并在 trace 中如实标注召回策略。</text>')

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<style>
  .h {{ font: 700 26px 'Microsoft YaHei','PingFang SC',sans-serif; fill: #e8edf7; }}
  .n {{ font: 400 14px 'Microsoft YaHei',sans-serif; fill: #93a0b8; }}
  .t {{ font: 700 17px 'Microsoft YaHei',sans-serif; fill: #f1f5ff; }}
  .s {{ font: 400 13.5px 'Microsoft YaHei',sans-serif; fill: #b6c1d4; }}
</style>
<rect width="{W}" height="{H}" fill="#0b0f16"/>
{''.join(body)}
</svg>'''

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "shopmind-flow.svg").write_text(svg, encoding="utf-8")
print(OUT / "shopmind-flow.svg")
