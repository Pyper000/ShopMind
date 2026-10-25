# ShopMind MVP：端到端细化流程

> 历史材料说明（2026-10-25）：本文保留早期方向/时点，当前产品定义、实现状态与验收边界请看[作品集总目录](portfolio/README.md)与[最新PRD](shopmind-task-to-plan-prd.md)。其中旧流程、旧指标及订单描述不能直接作为当前ShopMind能力结论。

状态：按 2026-10-22 已确认的架构实现；此文描述本地代码目标，未代表 GitHub / 公网部署状态。MainAgent 持有方案和用户状态，SearchAgent 只召回、过滤、排序；TradeAgent 仍关闭。MVP 终点是用户确认“这套采购方案”，不是创建真实订单。

## 1. 从输入到商品方案：前端和 MainAgent 的分支

```mermaid
flowchart TD
  U[用户在聊天框输入一段话] --> FE[前端发送 session_id + raw_query]
  FE --> API[POST /commerce/intents/stream]
  API --> STATE{MainAgent 是否已有该会话方案?}

  STATE -->|没有| FIRST{这一句是哪种请求?}
  FIRST -->|场景/清单表达| SCENE[先查持久化场景模板]
  SCENE --> HIT{模板命中?}
  HIT -->|命中| BUILD[模板逐项展开必备与按需项<br/>所有物品初始状态=还没确认]
  HIT -->|未命中| LLM[MainAgent 理解整体场景<br/>提交结构化任务槽位]
  LLM --> BLOCK{缺少会改变清单的关键信息?}
  BLOCK -->|是| ASK[只问一个关键问题<br/>暂不猜购买状态]
  BLOCK -->|否| BUILD
  BUILD --> PLAN[SchemeStateRegistry 保存 plan v1]
  PLAN --> EVENT1[发布 plan.update / plan.preview]
  EVENT1 --> UI1[前端显示逐项清单<br/>必备与按需分组]

  FIRST -->|明确的单一商品品类| ITEM[词表/别名确定性识别<br/>如“我想买旅行背包”]
  ITEM --> ONE[创建只有该品类的单槽位方案<br/>不生成整场景清单]
  ONE --> PLAN2[MainAgent 保存单槽位方案]
  PLAN2 --> EVENT2[plan.update，route=direct_item]
  EVENT2 --> DISPATCH1[只将该品类交给 SearchAgent]

  STATE -->|有| INPUT{本轮是清单控件提交还是文字补充?}
  INPUT -->|清单控件提交| SELECT[按 have / must_buy / optional_buy / deferred / unknown 写状态]
  SELECT --> PLAN3[MainAgent 更新方案版本<br/>不让模型重解按钮]
  PLAN3 --> BUY{有哪些明确待购槽位?}
  BUY -->|没有| NOSEARCH[保留状态并结束本轮<br/>不启动搜索]
  BUY -->|有| DISPATCH2[只派发明确待购槽位<br/>多个槽位并行]

  INPUT -->|文字补充| ROUTE[MainAgent 对照当前方案做轻量路由]
  ROUTE --> R1{已有品类状态更改?}
  R1 -->|我有了 / 不买| OWN[已有或暂缓状态更新<br/>删该槽位候选与已选商品]
  OWN --> ENDOWN[不调用 SearchAgent<br/>保留其他商品卡]
  R1 -->|需要买 / 想看这个品类| R2{品类是否已在当前清单?}
  R2 -->|是| PROMOTE[只把该槽位转成待购]
  R2 -->|否| ADD[只追加一个新待购槽位]
  PROMOTE --> PLAN4[MainAgent 更新状态版本<br/>保留其他槽位和用户确认]
  ADD --> PLAN4
  PLAN4 --> DISPATCH3[SearchAgent 只检索被更改/新增槽位]
  R1 -->|预算或偏好微调| SMALL[保留方案，不重开清单]
  SMALL --> CACHED[优先复用当前候选过滤/重排]
  CACHED --> ENOUGH{候选不足?}
  ENOUGH -->|是| LOCAL1[只为不足的槽位局部补召回]
  ENOUGH -->|否| NORECALL[不重复召回]
  R1 -->|场景 / 人数 / 核心用途变化| BIG[MainAgent 重新规划受影响任务]
  BIG --> SCOPE{变化影响范围?}
  SCOPE -->|局部槽位变化| LOCAL2[只清理并重召回受影响槽位]
  SCOPE -->|整场景或核心用途整体改变| FULL[清空旧候选池<br/>对新方案待购项全量重召回]
  R1 -->|含糊或无法可靠识别| CLARIFY[MainAgent 只追问一个关键点<br/>不让 SearchAgent 猜业务意图]

  DISPATCH1 --> SEARCH[进入 SearchAgent 检索子流程]
  DISPATCH2 --> SEARCH
  DISPATCH3 --> SEARCH
  LOCAL1 --> SEARCH
  LOCAL2 --> SEARCH
  FULL --> SEARCH
  NOSEARCH --> FINAL[发布 final.result]
  ENDOWN --> FINAL
  NORECALL --> FINAL
  ASK --> FINAL
  CLARIFY --> FINAL
```

## 2. SearchAgent 内部：任务槽位如何变成候选卡

```mermaid
flowchart TD
  IN[MainAgent 交付结构化方案 + 本轮状态变化] --> SCOPE[SearchAgent 取得本轮允许搜索的槽位]
  SCOPE --> FANOUT{槽位数大于 1?}
  FANOUT -->|是| PAR[每个独立槽位并行派发]
  FANOUT -->|否| ONE[派发单槽位任务]
  PAR --> ACTION
  ONE --> ACTION

  ACTION{检索动作选择} -->|场景/核心用途整体变更| ALL[清掉旧候选，recall_all]
  ACTION -->|新增品类、无缓存、缓存候选不足| LOCAL[仅该槽位 recall_local]
  ACTION -->|预算/偏好改变且缓存足够| RERANK[复用 candidate_ids<br/>按硬约束过滤并重排]
  ACTION -->|槽位未变且缓存足够| REUSE[复用候选并重排]

  ALL --> TOOL[SearchAgent 调 ProductSearchTool]
  LOCAL --> TOOL
  RERANK --> TOOL
  REUSE --> TOOL
  TOOL --> RETRIEVE{召回数据源}
  RETRIEVE -->|Qdrant / embedding 可用| VECTOR[向量召回候选]
  RETRIEVE -->|未配置或异常| KEYWORD[关键词 2-gram 降级召回<br/>Trace 标记实际路径]
  VECTOR --> FILTER
  KEYWORD --> FILTER
  FILTER[严格槽位一致性校验<br/>目标品类需出现在标题或亮点] --> HARD[应用层硬过滤<br/>价格上限 / 目的地可配送]
  HARD --> N{有符合条件的同品类商品?}
  N -->|有| RANK[候选排序，最多保留 3 个]
  RANK --> CARDS[返回 product_id、价格、标题、亮点<br/>及检索路径事件]
  N -->|目录里没有该品类| GAP[返回“模拟目录暂未收录 / 待入驻”<br/>不拿相邻类目凑数]
  N -->|有品类但受预算/配送限制| FILTERED[说明候选被硬条件过滤<br/>不伪称目录没有]
  CARDS --> EVENTS[tool.result / agent.result]
  GAP --> EVENTS
  FILTERED --> EVENTS
  EVENTS --> UI[前端按槽位合并新卡<br/>保留未受影响的旧卡]
```

## 3. 清单状态和候选的增量变化规则

```mermaid
flowchart LR
  subgraph STATE[MainAgent 管理的单个槽位状态]
    UNKNOWN[还没确认] --> HAVE[已有]
    UNKNOWN --> BUY[必备且需要买]
    UNKNOWN --> OPTIONAL[按需项，本次选择买]
    UNKNOWN --> DEFER[暂缓/不买]
    HAVE --> BUY
    DEFER --> BUY
    BUY --> HAVE
    BUY --> DEFER
  end
  BUY -->|只对明确待购项| SEARCH[SearchAgent 召回候选]
  OPTIONAL -->|必须由用户主动选“这次要买”| SEARCH
  HAVE --> REMOVE[该槽位候选与所选商品移除]
  DEFER --> REMOVE
  UNKNOWN -->|未确认| NOSHOW[不推荐，不计入总价]
  REMOVE --> PRESERVE[其他槽位候选/选择保留]
```

`ShoppingPlan` 是用户任务事实的唯一状态来源；`CandidatePool` 只存每个槽位的候选 ID 和命中数，不反向推断用户“已有/要买/不买”。一次文字补充只更新对应槽位和 `state_version`。清单控件的结构化选择优先级最高，避免模型把点选状态重新解释错。

## 4. 选商品、合计和 MVP 终点

```mermaid
flowchart TD
  CARDS[每个待购槽位展示 0 到 3 个同品类卡片] --> PICK[用户为某槽位选择 0 或 1 件商品]
  PICK --> OPT{这是按需槽位?}
  OPT -->|是| INCLUDE[用户先主动将其标为“这次要买”]
  OPT -->|否| SELECTED[保存 picked[slot] = product_id]
  INCLUDE --> SELECTED
  SELECTED --> TOTAL[前端应用层确定性计算<br/>Σ 已选商品价格 × 默认数量 1]
  TOTAL --> BUDGET[显示商品价合计、预算上限和是否超预算<br/>不含运费；不调用 LLM / TradeAgent]
  BUDGET --> CONFIRM[用户确认这套采购方案]
  CONFIRM --> END[MVP 完成：仅记录模拟选择<br/>不加购物车、不创建真实订单]
  CONFIRM -. 未来用户明确要求下单并二次确认后 .-> TRADE[TradeAgent 才可被启用]
  TRADE --> CHECK[服务端校验商品/SKU、库存、数量、地址和最终价格]
  CHECK --> ORDER[创建订单；真实支付/物流仍需单独接入]
```

因此 UI 上的“加入比较”不再作为本轮采购总价的门槛：比较功能不参与合计；每个待购槽位直接选中一个候选，合计只看实际选中的商品。现阶段不接真实下单，TradeAgent 继续休眠。

## 5. 三场景验收口径

脚本路径：`scripts/acceptance_mvp_core_three.py`。每个核心场景各跑“场景清单 → 只确认一个待购槽位并召回 → 明确品类补充”三步。

| 场景 | 第一轮模板 | 清单确认验收槽位 | 明确补充验收槽位 |
|---|---|---|---|
| 出行 | `general_travel` | `旅行背包` | `腰包与防水包` |
| 开学 | `school_start` | `学习文具` | `笔记本支架` |
| 租房 | `rental_move_in` | `床品套件` | `收纳箱` |

逐项输出首条有效事件、首份清单、首张商品卡、整轮完成延迟；同时记录模型调用数、SearchAgent 派发数、搜索槽位、卡片数和返回卡片与目标槽位的一致率。卡片一致率是基于返回标题/亮点的严格品类校验，**不是标准召回率**；没有人工标注的完整相关 SKU 集时不报告 Recall@K。

本地修改状态：尚未 commit / push / 部署。
