import { useEffect, useMemo, useRef, useState } from "react";
import EventTimeline from "./components/EventTimeline";
import ProductCards from "./components/ProductCards";
import type { ShoppingPlan, TradeEvent } from "./types";

const localApiBase = `${window.location.protocol}//${window.location.hostname || "127.0.0.1"}:8000`;
const isLocalVite = window.location.port === "5173" || window.location.port === "5174";
const API_BASE =
  import.meta.env.VITE_API_BASE || (isLocalVite ? localApiBase : window.location.hostname.endsWith(".github.io") ? "" : window.location.origin);
const WS_BASE = API_BASE.replace(/^http/, "ws");

const UI = {
  zh: {
    brand: "Ecommerce",
    title: "跨境购物搜索工作台",
    eyebrow: "AI SEARCH COMPANION",
    connected: "事件流已连接",
    disconnected: "事件流断开",
    ready: "等待你的购物任务",
    working: "Agent 正在拆解需求",
    results: "召回结果",
    brief: "你的购物需求",
    briefHint: "把预算、品类、目的地和偏好说出来，Agent 会帮你整理成可检索的 Query。",
    emptyBrief: "你的问题会出现在这里，并逐步变成结构化搜索条件。",
    queryReadout: "Query 解析",
    originalQuery: "原始表达",
    normalizedQuery: "标准化检索词",
    category: "品类",
    destination: "目的地",
    budget: "预算上限",
    strategy: "召回策略",
    candidates: "候选数",
    notCaptured: "尚未捕捉",
    waiting: "等待搜索",
    noBudget: "未设置",
    noDestination: "未设置",
    noCategory: "待识别",
    welcomeTitle: "先告诉我，你想买什么？",
    welcomeText: "不用组织成标准关键词。把场景、预算和顾虑直接说出来，我会先理解，再检索和解释结果。",
    placeholder: "例如：我人在美国，250 美元预算买个降噪耳机寄美国，到手价多少？",
    send: "发送",
    processing: "处理中",
    simulated: "模拟商品目录 · 模拟账户上下文 · 不连接真实交易",
    language: "EN",
    productLabel: "PRODUCT STUDY / 01",
    projectTitle: "把模糊的购买表达，\n变成可解释的搜索。",
    projectText: "Ecommerce 是一个面向跨境购物场景的 Agent 搜索工作台。它把用户的自然语言需求拆成 Query、品类、目的地与预算，再召回商品并解释为什么这样推荐。",
    contextTitle: "SIMULATED CONTEXT",
    contextItems: ["自然语言意图理解", "商品召回与到手价估算", "事件流与决策可追溯"],
    desktop: "DESKTOP",
    mobile: "MOBILE",
    live: "LIVE SEARCH",
    conversation: "Agent conversation",
    customer: "Customer",
    queryInspector: "Query inspector",
    session: "SESSION",
    planTitle: "购物计划",
    planEmpty: "描述一个场景后，购物目标和任务会显示在这里。",
    planGoal: "目标",
    planConditions: "已识别条件",
    planTasks: "商品任务",
    planMissing: "待确认",
    examples: [
      "我要去美国露营，总预算 500 元，想准备轻便耐用的装备。",
      "给经常坐飞机的家人准备一套 400 元以内的旅行用品，寄到美国。",
      "第一次在家做手冲咖啡，预算 300 元，需要准备什么？",
    ],
  },
  en: {
    brand: "Ecommerce",
    title: "Cross-border shopping workbench",
    eyebrow: "AI SEARCH COMPANION",
    connected: "Event stream connected",
    disconnected: "Event stream offline",
    ready: "Ready for your shopping task",
    working: "Agent is structuring your request",
    results: "Retrieved candidates",
    brief: "Your shopping brief",
    briefHint: "Share your budget, category, destination, and preferences. The Agent will turn them into a searchable query.",
    emptyBrief: "Your question will appear here and gradually become structured search conditions.",
    queryReadout: "Query readout",
    originalQuery: "Original expression",
    normalizedQuery: "Normalized query",
    category: "Category",
    destination: "Destination",
    budget: "Budget cap",
    strategy: "Recall strategy",
    candidates: "Candidates",
    notCaptured: "Not captured",
    waiting: "Waiting for search",
    noBudget: "Not set",
    noDestination: "Not set",
    noCategory: "To be identified",
    welcomeTitle: "What are you looking for?",
    welcomeText: "Skip the perfect keywords. Tell me the situation, budget, and concerns in your own words. I will understand, search, and explain.",
    placeholder: "For example: I am in the US, find noise-cancelling headphones under 250 USD and estimate the landed price.",
    send: "Send",
    processing: "Working",
    simulated: "Simulated catalog · simulated account context · no real transactions",
    language: "中文",
    productLabel: "PRODUCT STUDY / 01",
    projectTitle: "Turn vague shopping intent\ninto explainable search.",
    projectText: "Ecommerce is an Agent workbench for cross-border shopping. It translates natural language into query, category, destination and budget, then retrieves and explains product candidates.",
    contextTitle: "SIMULATED CONTEXT",
    contextItems: ["Natural-language intent", "Product recall and landed price", "Traceable agent decisions"],
    desktop: "DESKTOP",
    mobile: "MOBILE",
    live: "LIVE SEARCH",
    conversation: "Agent conversation",
    customer: "Customer",
    queryInspector: "Query inspector",
    session: "SESSION",
    planTitle: "Shopping plan",
    planEmpty: "Describe a situation to reveal the goal and product tasks.",
    planGoal: "Goal",
    planConditions: "Conditions",
    planTasks: "Product tasks",
    planMissing: "To clarify",
    examples: [
      "I am camping in the US with a CNY 500 budget and need lightweight, durable gear.",
      "Prepare travel essentials under CNY 400 for a frequent flyer, shipped to the US.",
      "I am new to pour-over coffee and have a CNY 300 budget. What do I need?",
    ],
  },
} as const;

function loadOrCreate(key: string, prefix: string): string {
  const existing = localStorage.getItem(key);
  if (existing) return existing;
  const created = `${prefix}-${Math.random().toString(36).slice(2, 8)}`;
  localStorage.setItem(key, created);
  return created;
}

interface Turn {
  role: "buyer" | "agent";
  text: string;
}

function latestEvent(events: TradeEvent[], type: TradeEvent["type"], tool?: string) {
  return [...events]
    .reverse()
    .find((event) => event.type === type && (!tool || event.payload?.tool === tool));
}

export default function App() {
  const [sessionId, setSessionId] = useState(() => loadOrCreate("ecommerce.session", "web"));
  const [buyerId] = useState(() => loadOrCreate("ecommerce.buyer", "buyer"));
  const [events, setEvents] = useState<TradeEvent[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [language, setLanguage] = useState<"zh" | "en">("zh");
  const [previewMode, setPreviewMode] = useState<"desktop" | "mobile">("desktop");
  const wsRef = useRef<WebSocket | null>(null);
  const copy = UI[language];

  useEffect(() => {
    let closed = false;
    let retryTimer: number | undefined;

    const connect = () => {
      if (closed || !API_BASE) return;
      const ws = new WebSocket(`${WS_BASE}/commerce/events`);
      wsRef.current = ws;
      ws.onopen = () => {
        if (closed) {
          ws.close();
          return;
        }
        ws.send(JSON.stringify({ shopping_session_id: sessionId }));
        setConnected(true);
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retryTimer = window.setTimeout(connect, 1500);
      };
      ws.onmessage = (message) => {
        const event: TradeEvent = JSON.parse(message.data);
        if (event.type === "token.delta") {
          setStreaming((prev) => prev + (event.payload.token ?? ""));
          return;
        }
        setEvents((prev) => [...prev, event]);
      };
    };

    connect();
    return () => {
      closed = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      wsRef.current?.close();
    };
  }, [sessionId]);

  const submit = async (suggestedQuery?: string) => {
    const query = (suggestedQuery ?? input).trim();
    if (!query || busy) return;
    if (!API_BASE) {
      setTurns((prev) => [...prev, { role: "agent", text: "在线服务正在部署，当前无法调用大模型。请稍后重试；本页不会用预设回复替代真实回答。" }]);
      return;
    }
    setInput("");
    setBusy(true);
    setStreaming("");
    setEvents([]);
    setTurns((prev) => [...prev, { role: "buyer", text: query }]);
    try {
      const response = await fetch(`${API_BASE}/commerce/intents`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          shopping_session_id: sessionId,
          buyer_id: buyerId,
          locale: language === "en" ? "en-US" : "zh-CN",
          currency: "CNY",
          raw_query: query,
        }),
        signal: AbortSignal.timeout(180000),
      });
      if (!response.ok) throw new Error(`服务返回 ${response.status}，请稍后重试`);
      const result = await response.json();
      if (result.events) setEvents(result.events);
      setTurns((prev) => [...prev, { role: "agent", text: result.final_text }]);
    } catch (error) {
      setTurns((prev) => [...prev, { role: "agent", text: `[error] 请求失败：${error}` }]);
    } finally {
      setStreaming("");
      setBusy(false);
    }
  };

  const buyerTurns = turns.filter((turn) => turn.role === "buyer");
  const latestBuyerQuery = buyerTurns.length ? buyerTurns[buyerTurns.length - 1].text : "";
  const searchInvoke = latestEvent(events, "tool.invoke", "product_search_tool");
  const searchResult = latestEvent(events, "tool.result", "product_search_tool");
  const searchArgs = searchInvoke?.payload?.args ?? {};
  const searchPayload = searchResult?.payload ?? {};
  const planEvent = [...events]
    .reverse()
    .find((event) => event.type === "plan.update" && event.payload?.shopping_plan);
  const shoppingPlan = planEvent?.payload?.shopping_plan as ShoppingPlan | undefined;
  const queryRows = useMemo(
    () => [
      { label: copy.originalQuery, value: latestBuyerQuery || copy.notCaptured, tone: "plain" },
      {
        label: copy.normalizedQuery,
        value: searchArgs.normalized_query || copy.notCaptured,
        tone: "blue",
      },
      { label: copy.category, value: searchArgs.category || copy.noCategory, tone: "violet" },
      {
        label: copy.destination,
        value: searchArgs.ship_to || copy.noDestination,
        tone: "coral",
      },
      {
        label: copy.budget,
        value:
          searchArgs.price_max_major !== undefined && searchArgs.price_max_major !== null
            ? `${searchArgs.price_max_major} CNY`
            : copy.noBudget,
        tone: "lime",
      },
    ],
    [copy, latestBuyerQuery, searchArgs],
  );
  const statusLabel = busy ? copy.working : connected ? copy.ready : copy.disconnected;

  return (
    <div className={`app-frame ${previewMode === "mobile" ? "mobile-preview" : ""}`}>
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark">E</div>
          <div>
            <div className="eyebrow">{copy.eyebrow}</div>
            <h1>
              {copy.brand} <span>{copy.title}</span>
            </h1>
          </div>
        </div>
        <div className="topbar-actions">
          <button type="button" disabled={busy} onClick={() => {
            const id = `web-${crypto.randomUUID()}`;
            localStorage.setItem("ecommerce.session", id);
            setSessionId(id); setTurns([]); setEvents([]); setStreaming("");
          }}>新对话</button>
          <div className="view-switch" aria-label="Preview mode">
            <button
              className={previewMode === "desktop" ? "active" : ""}
              type="button"
              onClick={() => setPreviewMode("desktop")}
            >
              {copy.desktop}
            </button>
            <button
              className={previewMode === "mobile" ? "active" : ""}
              type="button"
              onClick={() => setPreviewMode("mobile")}
            >
              {copy.mobile}
            </button>
          </div>
          <div className="session-meta">
            <span>{sessionId}</span>
            <span className={connected ? "dot on" : "dot off"}>
              {connected ? copy.connected : copy.disconnected}
            </span>
          </div>
          <button
            className="language-switch"
            type="button"
            onClick={() => setLanguage((current) => (current === "zh" ? "en" : "zh"))}
            aria-label="Switch language"
          >
            {copy.language}
          </button>
        </div>
      </header>

      <div className="portfolio-layout">
        <aside className="project-rail">
          <div className="rail-topline">
            <span className="rail-index">E / 01</span>
            <span className="rail-status"><i /> {copy.live}</span>
          </div>
          <div className="rail-copy">
            <span className="rail-kicker">{copy.productLabel}</span>
            <h2>{copy.projectTitle}</h2>
            <p>{copy.projectText}</p>
          </div>
          <div className="rail-context">
            <div className="rail-context-title">{copy.contextTitle}</div>
            {copy.contextItems.map((item, index) => (
              <div className={`context-item ${index === 0 ? "selected" : ""}`} key={item}>
                <span className="context-number">0{index + 1}</span>
                <span>{item}</span>
                <span className="context-mark">{index === 0 ? "↗" : "·"}</span>
              </div>
            ))}
          </div>
          <div className="rail-footer">
            <span>{copy.session}</span>
            <strong>{sessionId}</strong>
            <span className="rail-disclaimer">{copy.simulated}</span>
          </div>
        </aside>

        <main className="conversation-shell">
          <div className="conversation-header">
            <div>
              <span className="section-kicker">02 / CONVERSATION</span>
              <h2>{copy.conversation}</h2>
            </div>
            <span className={`status-badge ${busy ? "is-busy" : ""}`}>
              <span className="status-pulse" />
              {statusLabel}
            </span>
          </div>

          <div className="conversation-body">
            <div className="conversation-feed">
              {turns.length === 0 && !streaming && (
                <div className="welcome-block">
                  <div className="welcome-icon">✦</div>
                  <div>
                    <h3>{copy.welcomeTitle}</h3>
                    <p>{copy.welcomeText}</p>
                    <div className="example-queries">
                      {copy.examples.map((example) => (
                        <button key={example} type="button" onClick={() => void submit(example)}>
                          {example}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              <section className={`shopping-plan ${shoppingPlan ? "has-plan" : ""}`}>
                <div className="plan-heading">
                  <span className="section-kicker">PLAN / LIVE</span>
                  <h3>{copy.planTitle}</h3>
                </div>
                {!shoppingPlan ? (
                  <p className="plan-empty">{copy.planEmpty}</p>
                ) : (
                  <div className="plan-grid">
                    <div className="plan-summary">
                      <span>{copy.planGoal}</span>
                      <strong>{shoppingPlan.goal}</strong>
                    </div>
                    <div className="plan-summary">
                      <span>{copy.planConditions}</span>
                      <strong>
                        {[
                          shoppingPlan.destination,
                          shoppingPlan.budget_major
                            ? `${shoppingPlan.budget_major} ${shoppingPlan.currency}`
                            : "",
                          ...shoppingPlan.preferences,
                        ]
                          .filter(Boolean)
                          .join(" · ") || "--"}
                      </strong>
                    </div>
                    <div className="plan-list">
                      <span>{copy.planTasks}</span>
                      <ol>
                        {shoppingPlan.tasks.map((task) => <li key={task}>{task}</li>)}
                      </ol>
                    </div>
                    {!!shoppingPlan.missing_slots.length && (
                      <div className="plan-list missing">
                        <span>{copy.planMissing}</span>
                        <p>{shoppingPlan.missing_slots.join(" · ")}</p>
                      </div>
                    )}
                  </div>
                )}
              </section>

              {turns.map((turn, index) =>
                turn.role === "buyer" ? (
                  <div className="chat-turn buyer-turn" key={index}>
                    <div className="bubble-label">
                      <span className="buyer-avatar">YOU</span>
                      <span>{copy.customer}</span>
                    </div>
                    <div className="bubble-text">{turn.text}</div>
                  </div>
                ) : (
                  <div className="chat-turn agent-turn" key={index}>
                    <div className="bubble-label">
                      <span className="agent-avatar">E</span>
                      <span>{copy.brand}</span>
                      <span className="bubble-marker">GUIDANCE</span>
                    </div>
                    <div className="bubble-text">{turn.text}</div>
                  </div>
                ),
              )}

              {streaming && (
                <div className="chat-turn agent-turn streaming">
                  <div className="bubble-label">
                    <span className="agent-avatar">E</span>
                    <span>{copy.brand}</span>
                    <span className="bubble-marker">LIVE</span>
                  </div>
                  <div className="bubble-text">{streaming}</div>
                </div>
              )}

              {busy && (
                <div className="thinking-line" role="status" aria-live="polite">
                  <span className="thinking-logo" aria-hidden="true">E</span>
                  <span>{streaming ? "正在生成" : copy.working}</span>
                  <span className="thinking-dots"><i /><i /><i /></span>
                </div>
              )}

              <div className="search-output">
                <div className="output-heading">
                  <div>
                    <span className="section-kicker">03 / OUTPUT</span>
                    <h3>{copy.results}</h3>
                  </div>
                  <span className="result-count">
                    {searchPayload.hit_count !== undefined ? `${searchPayload.hit_count}` : "--"}
                  </span>
                </div>
                <ProductCards events={events} onCompare={(query) => void submit(query)} busy={busy} />
              </div>

              <div className="insight-panel">
                <div className="output-heading">
                  <div>
                    <span className="section-kicker">04 / READOUT</span>
                    <h3>{copy.queryInspector}</h3>
                  </div>
                  <span className="query-symbol">⌁</span>
                </div>
                <p className="panel-intro">{copy.briefHint}</p>
                <div className="query-rows">
                  {queryRows.map((row) => (
                    <div className="query-row" key={row.label}>
                      <span className="row-label">{row.label}</span>
                      <span className={`row-value ${row.tone}`}>{row.value}</span>
                    </div>
                  ))}
                </div>
                <div className="query-stats">
                  <div>
                    <span>{copy.strategy}</span>
                    <strong>{searchPayload.recall_strategy || copy.waiting}</strong>
                  </div>
                  <div>
                    <span>{copy.candidates}</span>
                    <strong>{searchPayload.hit_count ?? "--"}</strong>
                  </div>
                </div>
              </div>

              <div className="activity-section">
                <EventTimeline events={events} />
              </div>
            </div>
          </div>

          <form
            className="composer-bar"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <div className="composer-icon">↗</div>
            <textarea
              value={input}
              placeholder={copy.placeholder}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void submit();
                }
              }}
            />
            <button type="submit" disabled={busy || !input.trim()}>
              <span>{busy ? copy.processing : copy.send}</span>
              <span className="send-arrow">↗</span>
            </button>
          </form>
        </main>
      </div>

      <div className="demo-note">
        <span className="note-dot" />
        {copy.simulated}
      </div>
    </div>
  );
}
