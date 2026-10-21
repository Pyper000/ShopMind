import { useEffect, useRef, useState } from "react";
import "./landing.css";
import LiveChat from "./components/LiveChat";

type Product = {
  name: string;
  category: string;
  price: string;
  kind: "must" | "optional" | "alternative";
  reason: string;
  tradeoff: string;
  unknown: string;
  accent: string;
};

type Scenario = {
  id: string;
  category: string;
  prompt: string;
  chips: string[];
  reply: string;
  question: string;
  answer: string;
  planReply: string;
  preferenceAnswer: string;
  directions: string[];
  preparation: { label: string; items: string[] }[];
  reusable: string[];
  notBuy: string[];
  noBuyPlan: string;
  optional: string[];
  openQuestions: string[];
  summary: string;
  comparisonSummary: string;
  budgetLimit?: number;
  requiredPlanTotal: number;
  optionalTotal: number;
  products: Product[];
};

const scenarios: Scenario[] = [
  {
    id: "beach-trip",
    category: "旅游出行",
    prompt: "周末去海边两天，帮我准备购物清单",
    chips: ["场景: 海边短途出行", "人数: 1人", "偏好: 轻装少占空间"],
    question: "收到，我来帮你梳理一套刚好适配的采购方案。先确认两个会影响清单的问题：几个人同行？行李有数量或体积限制吗？",
    answer: "一个人，我想只带个随身包，家里有个双肩包。",
    planReply: "先按一个人、两天海边来列清单：防晒、换洗衣物和洗漱用品是基础准备；双肩包继续复用；衣物收纳袋是可选项；如果会下水，再考虑防水手机袋和沙滩拖鞋。你更在意轻装少占空间，还是功能齐全？",
    preferenceAnswer: "轻量少占空间，换洗和洗漱用品家里都有，防晒还没准备。",
    reply: "好，按轻量少占空间来选；双肩包、换洗衣物和洗漱用品都继续复用，本次只为未准备的防晒任务匹配商品。收纳袋保留为可选，下水用品先不计入。",
    directions: ["复用现有双肩包", "必备任务：防晒准备", "收纳袋与下水用品：按需"],
    preparation: [
      { label: "已有可复用", items: ["双肩包"] },
      { label: "必备清单", items: ["防晒用品", "换洗衣物", "洗漱用品"] },
      { label: "建议 / 按需", items: ["轻量衣物收纳袋", "下水时再准备防水手机袋、沙滩拖鞋"] },
    ],
    reusable: ["双肩包、换洗衣物、洗漱用品（用户明确）"],
    notBuy: ["不重复购买背包", "未确认下水前不买泳衣、防水手机袋和沙滩拖鞋"],
    noBuyPlan: "现有双肩包可装换洗衣物；如果不需要独立分装，收纳袋可以不买。",
    optional: ["压缩收纳袋：现有背包分装不便时再考虑"],
    openQuestions: ["是否下水未确认；相关商品暂不计入采购方案"],
    summary: "复用双肩包、换洗和洗漱用品；必买 1 件防晒候选，商品价 89 元。收纳袋可选，下水用品暂缓。",
    comparisonSummary: "防晒伞是当前必需候选（89元）；压缩收纳袋是可选项（89元），两者解决不同任务，不作为同类替代，也不叠加进必需总价。",
    requiredPlanTotal: 89,
    optionalTotal: 89,
    products: [
      { name: "MiniUmbrella 五折超轻晴雨伞", category: "防晒准备", price: "CNY 89", kind: "must", reason: "目录标注 180g、UPF50+，可作为轻装防晒候选。", tradeoff: "需要手持；不等同于防晒霜，具体遮挡效果受使用方式影响。", unknown: "演示目录未提供伞面尺寸和实际防晒测试报告。", accent: "sky" },
      { name: "CompressCube 压缩收纳方块", category: "衣物收纳", price: "CNY 89", kind: "optional", reason: "目录标注可用于行李分装，并称体积减少 40%。", tradeoff: "会多带一件收纳用品；背包内直接分装够用时不建议购买。", unknown: "目录未提供具体尺寸、重量和装入衣物后的实测体积。", accent: "blue" },
    ],
  },
  {
    id: "rental-home",
    category: "第一次租房",
    prompt: "第一次租房入住，不知道要准备什么",
    chips: ["场景: 首次租房入住", "约束: 不打孔", "偏好: 先满足日常使用"],
    question: "先确认两个会改变清单的问题：房间是空房还是已有基础家具？租期内能打孔或粘贴墙面吗？",
    answer: "基础家具都有，不能打孔，想先把卧室和日常收纳弄好。",
    planReply: "先列入住清单：基础准备是床品、照明和清洁用品；特别考虑不能破坏墙面，需要免打孔收纳和可移除布置；按需准备桌面收纳、氛围灯。你更在意先把日常用起来，还是优先改善视觉效果？",
    preferenceAnswer: "先解决日常使用，预算500元，家里有台灯和基础清洁用品。",
    reply: "按日常使用优先来选；台灯和清洁用品复用，不重复购买。先补床品和免打孔收纳，装饰类放到可选项。",
    directions: ["复用已有台灯和清洁用品", "必备任务：床品与基础收纳", "装饰布置：按需"],
    preparation: [{ label: "基础准备", items: ["床品", "照明", "清洁用品"] }, { label: "特别考虑", items: ["免打孔收纳", "可移除墙面布置"] }, { label: "按需准备", items: ["桌面收纳", "氛围灯"] }],
    reusable: ["台灯、基础清洁用品（用户明确）"],
    notBuy: ["不重复购买台灯和清洁用品", "不买需要打孔的固定式收纳"],
    noBuyPlan: "先用已有台灯和清洁用品完成入住；装饰和氛围灯不影响基本生活，可以暂缓。",
    optional: ["桌面收纳：桌面确实凌乱时再补", "氛围灯：不影响入住，可后置"],
    openQuestions: ["床垫和被褥尺寸尚未确认", "墙面材质未确认，免打孔产品需先核对"],
    summary: "复用已有台灯和清洁用品，先补床品与不伤墙的收纳；氛围灯和装饰不计入最低配置。",
    budgetLimit: 500,
    requiredPlanTotal: 268,
    optionalTotal: 79,
    products: [
      { name: "SoftNest 可机洗基础床品套装", category: "床品", price: "CNY 199", kind: "must", reason: "先满足入住后的睡眠使用，套装比零散购买更省决策。", tradeoff: "尺寸必须先确认；暂不追求高规格面料。", unknown: "模拟目录未提供具体尺寸、面料克重和洗后表现。", accent: "sky" },
      { name: "NoDrill 免打孔收纳架", category: "免打孔收纳", price: "CNY 69", kind: "must", reason: "补充日常收纳，同时符合租房不能打孔的约束。", tradeoff: "承重和适用墙面有限；重物仍需改用落地收纳。", unknown: "目录未提供墙面适配范围和实测承重。", accent: "violet" },
      { name: "DeskGrid 桌面分区收纳盒", category: "桌面收纳", price: "CNY 79", kind: "optional", reason: "桌面确实凌乱时再补，提升使用便利但不影响入住。", tradeoff: "会增加一件物品；桌面物品较少时可以不买。", unknown: "模拟目录未提供具体尺寸和分格数量。", accent: "blue" },
    ],
    comparisonSummary: "床品和免打孔收纳解决不同任务，不能为了比价互相替代；桌面收纳是可选项，不和最低配置一起强制购买。",
  },
  {
    id: "gift",
    category: "送礼",
    prompt: "想给妈妈买礼物，不知道买什么",
    chips: ["场景: 生日送礼", "对象: 妈妈", "预算: CNY 200"],
    question: "我先帮你把送礼方案收敛一下：这次是什么场合？预算大概希望控制在多少？",
    answer: "生日，预算200元，妈妈平时喜欢喝茶。",
    planReply: "先列送礼任务框架：核心是一份符合她日常兴趣的礼物；包装或贺卡按需增加；饮食限制和已有相似物品尚未确认。你更偏向能分享的茶点，还是日常喝的茶？",
    preferenceAnswer: "日常喝的茶，简单实用就好。",
    reply: "明白，我按日常饮茶、实用优先来选。先比较茶叶礼盒和茶点礼盒这两个不同方向；两者都在200元预算内，但只选一份，不叠加计算。",
    directions: ["一份主礼物", "预算上限 CNY 200", "包装与贺卡按需"],
    preparation: [{ label: "必备任务", items: ["一份适合收礼人的主礼物"] }, { label: "已有可复用", items: ["尚未确认，不假设已有"] }, { label: "按需准备", items: ["礼品包装、贺卡"] }],
    reusable: ["收礼人日常喜欢喝茶（用户明确）"],
    notBuy: ["不同时购买茶叶和茶点礼盒，避免超出送礼所需"],
    noBuyPlan: "在两个候选中选一份即可；包装或贺卡若已有合适的就不用额外购买。",
    optional: ["礼品包装或贺卡：按现有包装情况决定"],
    openQuestions: ["饮食限制、口味偏好和家中茶叶库存未确认"],
    summary: "推荐 1 件乌龙茶礼盒，商品价 168 元，预算内余 32 元；128 元茶点礼盒是更省钱的替代，不与主方案相加。",
    comparisonSummary: "两款是主礼物的互斥选择：茶叶礼盒168元，更贴近日常饮茶；茶点礼盒128元，便宜40元、适合分享但不是茶叶。预算200元时都可选，按“日常喝的茶”偏好选前者。",
    budgetLimit: 200,
    requiredPlanTotal: 168,
    optionalTotal: 0,
    products: [
      { name: "MountainTea 清香乌龙茶礼盒", category: "茶叶礼盒", price: "CNY 168", kind: "must", reason: "与妈妈日常饮茶习惯相关，价格在200元预算内。", tradeoff: "比茶点礼盒贵40元；需确认收礼人是否接受乌龙茶。", unknown: "模拟目录未提供产地、等级与保质期。", accent: "blue" },
      { name: "GoodDay 茶点分享礼盒", category: "茶点礼盒", price: "CNY 128", kind: "alternative", reason: "价格低40元，独立小份装适合家庭分享。", tradeoff: "更偏茶点，不是日常饮用的茶叶；饮食限制需先核对。", unknown: "模拟目录未提供具体口味、配料与保质期。", accent: "sky" },
    ],
  },
];
const demoScenarios = [scenarios[0], scenarios[2], scenarios[1]];

function getScenario(query: string): Scenario {
  const text = query.toLowerCase();
  if (/海边|沙滩|beach/.test(text)) return scenarios[0];
  if (/租房|入住|搬家|租屋|rental/.test(text)) return scenarios[1];
  if (/妈妈|礼物|送礼|gift/.test(text)) return scenarios[2];
  return { ...scenarios[1], prompt: query, chips: ["自然语言需求", "预算: 待确认", "已有物品: 待确认"], reply: "我先按你的生活任务整理采购品类；你补充预算、已有物品或优先级后，我会更新整套方案。", directions: ["先列必备任务", "已有物品待确认", "可选项不计入必需总价"], preparation: [{ label: "必备清单", items: ["按场景拆解，不默认用户库存"] }, { label: "已有可复用", items: ["待用户补充"] }, { label: "建议 / 按需", items: ["根据场景和回答增减"] }] };
}

function ProductCard({ product }: { product: Product }) {
  const kindLabel = product.kind === "must" ? "最低配置候选" : product.kind === "optional" ? "可选提升" : "备选，不叠加预算";
  return (
    <article className="product-card">
      <div className={`product-art ${product.accent}`}><span>{product.category}</span><i /><i /><i /></div>
      <div className="product-copy">
        <div className="product-meta"><span>{kindLabel}</span><span>{product.price}</span></div>
        <h4>{product.name}</h4>
        <p>{product.reason}</p><p className="demo-tradeoff"><b>取舍：</b>{product.tradeoff}</p><p className="demo-unknown"><b>未知：</b>{product.unknown}</p>
      </div>
    </article>
  );
}

export default function Landing() {
  const [menuOpen, setMenuOpen] = useState(false);
  const [scenario, setScenario] = useState(scenarios[0]);
  const [query, setQuery] = useState(scenarios[0].prompt);
  const [thinking, setThinking] = useState(true);
  const [typing, setTyping] = useState(false);
  const [typedReply, setTypedReply] = useState("");
  const timers = useRef<number[]>([]);

  const clearTimers = () => {
    timers.current.forEach((timer) => window.clearTimeout(timer));
    timers.current = [];
  };

  const runDemo = (nextQuery: string) => {
    const next = getScenario(nextQuery);
    clearTimers();
    setScenario(next);
    setQuery(nextQuery);
    setThinking(true);
    setTyping(false);
    setTypedReply("");
    timers.current.push(window.setTimeout(() => {
      setThinking(false);
      setTyping(true);
      let index = 0;
      const typeNext = () => {
        index += 1;
        setTypedReply(next.reply.slice(0, index));
        if (index < next.reply.length) timers.current.push(window.setTimeout(typeNext, 16));
        else setTyping(false);
      };
      typeNext();
    }, 720));
  };

  useEffect(() => {
    runDemo(scenarios[0].prompt);
    return clearTimers;
  }, []);

  return (
    <div className="site-shell">
      <header className="site-header">
        <a className="brand" href="#overview"><span className="brand-symbol">S</span><span>ShopMind <b>AI</b></span></a>
        <button className="menu-toggle" type="button" onClick={() => setMenuOpen((open) => !open)} aria-label="Open navigation"><span /><span /></button>
        <nav className={menuOpen ? "site-nav open" : "site-nav"}>
          {[["Overview", "overview"], ["Dual Modes", "modes"], ["Decision Flow", "orchestration"], ["Demo", "demo"], ["Product", "live"]].map(([item, anchor]) => <a key={item} href={`#${anchor}`} onClick={() => setMenuOpen(false)}>{item}</a>)}
        </nav>
        <div className="header-actions"><a className="case-link" href="#demo">View Case Study <span>↗</span></a><a className="primary-btn compact" href="#live">真实对话 <span>↗</span></a></div>
      </header>

      <main>
        <section className="hero" id="overview">
          <div className="hero-copy reveal">
            <p className="eyebrow">CONVERSATIONAL COMMERCE / 01</p>
            <h1>ShopMind AI</h1>
            <h2 className="hero-subtitle">大概知道买什么，却怕漏了关键？</h2>
            <p className="hero-text">ShopMind 把生活任务拆成完整采购方案，核对已有与缺项，再按预算比较候选，讲清该买、可不买和每项取舍。</p>
            <div className="hero-actions"><a className="primary-btn" href="#live">开始购物对话 <span>↗</span></a><a className="secondary-btn" href="#orchestration">查看能力 <span>↓</span></a></div>
            <div className="capability-tags"><span>Intent Understanding</span><span>Tool Calling</span><span>Explainable Recommendations</span></div>
          </div>
          <div className="hero-console reveal delayed">
            <div className="console-header"><div>ShopMind Assistant</div><span>场景示例 · 模拟商品</span></div>
            <div className="console-chat"><div className="mini-message user">周末去海边两天，帮我准备购物清单</div><div className="mini-message ai">几个人同行？行李有数量或体积限制吗？</div></div>
            <div className="console-chat"><div className="mini-message user">一个人，我想只带个随身包，家里有个双肩包。</div><div className="mini-message ai">复用双肩包；先列基础防晒、换洗和洗漱，再按需考虑收纳与下水用品。</div></div>
            <div className="mini-products">
              <article className="mini-product"><div className="mini-product-art blue" /><div><b>必需方案</b><span>CNY 89 · 1 个采购任务</span></div></article>
              <article className="mini-product"><div className="mini-product-art violet" /><div><b>收纳袋（可选）</b><span>CNY 89 · 不计入必需总价</span></div></article>
            </div>
            <div className="console-glow" />
          </div>
        </section>

        <LiveChat />

        <section className="value-section">
          <div className="section-heading"><p className="eyebrow">WHY SHOPMIND</p><h2>你缺的不是商品，<span>而是做决定的把握。</span></h2></div>
          <div className="value-grid">
            {[["01", "⌁", "怕漏买，也怕买多", "面对一次旅行、入住或送礼任务，用户往往不是找不到商品，而是不确定到底要准备哪些、哪些已有、哪些其实不用买。"], ["02", "⌘", "目标不是多推荐，而是刚好完成", "平台可以继续给出更多单品；ShopMind 先控制采购范围，优先生成完成任务所需的最小购买集合。"], ["03", "✦", "把选择依据交还给用户", "每个候选都说明为什么选、放弃了什么、还缺什么信息；用户调整预算或优先级后，方案跟着变化。"]].map(([index, icon, title, text]) => (
              <article className="value-card" key={title}><span className="value-index">{index}</span><div className="value-icon">{icon}</div><h3>{title}</h3><p>{text}</p></article>
            ))}
          </div>
        </section>

        <section className="modes-section" id="modes">
          <div className="section-heading split-heading"><div><p className="eyebrow">DUAL MODES</p><h2>同一套能力，<span>两种进入方式。</span></h2></div><p>知道商品词时直接搜索；只知道生活目标时，先规划任务再形成采购方案。</p></div>
          <div className="mode-grid">
            <article className="mode-card">
              <div className="mode-top"><span>01 / SEARCH</span><b>关键词搜索模式</b></div>
              <div className="search-input-demo"><span>⌕</span><p>防水 蓝牙耳机 200-300</p><i>↗</i></div>
              <div className="filter-chips"><span>防水</span><span>蓝牙</span><span>200-300</span></div>
              <p className="mode-copy">快、可控、适合已经知道商品词和筛选条件的用户。</p><div className="mode-line" /><strong>Structured Query → Search → Filter</strong>
            </article>
            <article className="mode-card">
              <div className="mode-top"><span>02 / CONVERSATION</span><b>问答对话模式</b></div>
              <div className="mode-dialog user">周末去海边，不知道要准备什么</div><div className="mode-dialog ai">先问活动场景，再区分已有、必需、可选与不建议购买。</div>
              <p className="mode-copy chat-copy">用户先看清任务清单，再表达偏好；商品卡片解释选择理由和取舍。</p><div className="mode-line" /><strong>Scene → List → Preference → Products</strong>
            </article>
          </div>
        </section>

        <section className="orchestration-section" id="orchestration">
          <div className="section-heading"><p className="eyebrow">DECISION FLOW</p><h2>把一次购买，<span>收敛成一套可确认的方案。</span></h2></div>
          <div className="flow-scroll"><div className="flow">
            {[["01", "场景澄清", "确认人数、时间、空间或特殊活动等边界"], ["02", "清单确认", "先列完整品类，再拆出已有、待补缺口和按需项"], ["03", "收集用户判断", "用户补充已有物品、预算和优先关注点"], ["04", "匹配待补候选", "只为缺少的任务槽位生成商品卡片，不重复推荐已有物品"], ["05", "取舍比较", "按预算和优先级比较候选，说明选择与放弃的代价"], ["06", "确认或调整", "用户确认方案；若有变化，重新计算清单、候选和总价"]].map(([number, title, text], index, items) => (
              <div className="flow-step" key={title}><div className="flow-node" tabIndex={0}><span>{number}</span><b>{title}</b><p>{text}</p></div>{index < items.length - 1 && <div className="flow-connector"><i /></div>}</div>
            ))}
          </div></div>
          <p className="orchestration-note">反馈可以让选择回到追问或检索环节；用户没说的已有物品保持待确认。当前比较基于模拟商品目录，不提供全网实时比价，也不执行真实交易。</p>
        </section>

        <section className="demo-section" id="demo">
          <div className="section-heading split-heading"><div><p className="eyebrow">SCENARIO DEMO</p><h2>场景演示</h2></div><p>预设示例对话 · 商品与价格均为模拟数据</p></div>
          <div className="demo-grid">
            <aside className="prompt-list">
              <p>CHOOSE A SCENARIO</p>
              {demoScenarios.map((item, index) => <button key={item.id} className={scenario.id === item.id ? "prompt-option active" : "prompt-option"} type="button" onClick={() => runDemo(item.prompt)}><span>0{index + 1}</span><b>{item.prompt}</b><small>{item.category}</small><i>↗</i></button>)}
              <div className="signal-card"><span className="online-dot" /><p>SIMULATED AGENT</p><strong>商品、价格与结果均为演示数据</strong></div>
            </aside>
            <div className="demo-console">
              <div className="demo-console-head"><div><span className="assistant-avatar">S</span><b>ShopMind Assistant</b></div><span className="case-disclaimer">预设案例输入 · 非默认库存</span><span className="processing-state">预设对话</span></div>
              <div className="demo-stream">
                <div className="demo-stage-label">01 · 承接任务与基础需求收敛</div>
                <div className="message-wrap user-wrap"><span>YOU</span><div className="demo-message user">{query}</div></div>
                <div className="message-wrap ai-wrap"><span>SHOPMIND · 只问关键条件</span><div className="demo-message ai">{scenario.question}</div></div>
                <div className="message-wrap user-wrap"><span>YOU · 场景补充</span><div className="demo-message user">{scenario.answer}</div></div>
                <div className="demo-stage-label">02 · 清单框架与最小购买集合</div>
                <div className="message-wrap ai-wrap"><span>SHOPMIND · 先列品类，不急着推商品</span><div className="demo-message ai">{scenario.planReply}</div></div>
                <div className="checklist-frame" aria-label="准备品类清单">{scenario.preparation.map((group) => <div key={group.label}><b>{group.label}</b><span>{group.items.join(" · ")}</span></div>)}</div>
                <div className="demo-stage-label">03 · 清单确认：已有 / 待补缺口 / 按需</div>
                <div className="message-wrap user-wrap"><span>YOU · 取舍偏好与已有物品</span><div className="demo-message user">{scenario.preferenceAnswer}</div></div>
                <div className="message-wrap ai-wrap"><span>SHOPMIND · 对齐选型标准</span><div className="demo-message ai">{thinking ? <span className="loading-dots"><i /><i /><i /></span> : typedReply}{typing && <span className="typing-caret">|</span>}</div></div>
                <div className="plan-status-frame" aria-label="清单状态">
                  <div><b>已有可复用</b><span>{scenario.reusable.join(" · ") || "尚未确认，不默认已有"}</span></div>
                  <div><b>待补缺口</b><span>{scenario.products.filter((product) => product.kind === "must").map((product) => product.category).join(" · ") || "暂无"}</span></div>
                  <div><b>按需准备</b><span>{scenario.optional.join(" · ") || "暂无"}</span></div>
                </div>
                {!thinking && typedReply && <>
                  <div className="demo-stage-label">04 · 只为待补缺口提供商品候选</div>
                  <div className="insight-chips">{scenario.chips.map((chip) => <span key={chip}>{chip}</span>)}</div>
                  <p className="candidate-note">以下商品只覆盖待补缺口；已有物品不重复生成商品卡片，可选项不计入最低配置。</p>
                  {scenario.products.length > 0 ? <div className="demo-product-grid">{scenario.products.map((product) => <ProductCard key={product.name} product={product} />)}</div> : <p className="demo-no-products">目前没有需要补充的商品；先完成任务条件确认。</p>}
                  <div className="demo-stage-label">05 · 取舍比较与方案确认</div>
                  <div className="recommendation-block"><div className="recommendation-title"><span>按预算与优先级完成取舍</span><b>确认方案 / 继续调整</b></div><div className="direction-list">{scenario.directions.map((direction) => <span key={direction}>{direction}</span>)}</div><p className="demo-plan-line"><b>本轮决策依据</b>{scenario.preferenceAnswer}</p><p className="demo-plan-line"><b>已有可复用</b>{scenario.reusable.join(" · ") || "尚未确认"}</p><p className="demo-plan-line"><b>不建议购买</b>{scenario.notBuy.join(" · ") || "暂无"}</p><p className="demo-plan-line optional"><b>可选提升项</b>{scenario.optional.join(" · ") || "暂无"}</p><p className="demo-comparison"><b>方案比选</b>{scenario.comparisonSummary}</p><p className="demo-no-buy"><b>不买也能完成：</b>{scenario.noBuyPlan}</p><p className="demo-unknown"><b>待确认：</b>{scenario.openQuestions.join(" · ") || "暂无"}</p><div className="demo-budget"><span>必需采购总价</span><strong>CNY {scenario.requiredPlanTotal}</strong><span>预算上限</span><strong>{scenario.budgetLimit ? `CNY ${scenario.budgetLimit}` : "未提供"}</strong><span>可选项金额</span><strong>CNY {scenario.optionalTotal}</strong></div><p className="demo-summary"><b>方案总结</b>{scenario.summary}</p></div>
                </>}
              </div>
              <a className="secondary-btn" href="#live">用自己的需求开始真实对话 ↗</a>
            </div>
          </div>
        </section>
      </main>

      <footer className="site-footer"><a className="brand" href="#overview"><span className="brand-symbol">S</span><span>ShopMind <b>AI</b></span></a><p>模拟商品与模拟账户上下文；Product 为真实模型对话，Demo 为预设场景。</p><div><a href="https://github.com/Pyper000/Pyper000.github.io">GitHub</a><a href="#overview">Portfolio</a></div></footer>
    </div>
  );
}
