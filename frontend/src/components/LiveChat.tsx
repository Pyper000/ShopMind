import { useEffect, useState } from 'react';

type Message = { role: 'user' | 'assistant'; content: string };
type Product = { id: string; title: string; brand: string; category: string; subcategory: string; price: number; currency: string; highlights: string[]; reason: string; tradeoff: string };
type ChecklistItem = { name: string; quantity?: string; note?: string; condition?: string };
type ChecklistSection = { id: string; title: string; required: ChecklistItem[]; optional: ChecklistItem[]; reference?: { title: string; url?: string; note?: string } };
type Result = { text: string; preview?: boolean; scheme?: { state_version: number; goal: string; core_use?: string; people?: number | null; stage: string; budget: number | null; destination: string | null; excludes: string[]; tasks?: string[]; must_buy?: string[]; optional?: string[]; prep?: string[]; reusable?: string[]; not_buy?: string[]; unknowns?: string[]; preferences?: string[] }; mutation?: { state_version: number; target_slots: { name: string; status: string }[]; added_slots: string[]; removed_slots: string[]; existing_slots: string[]; deferred_slots: string[]; pending_slots: string[]; budget_change: unknown; preference_change: unknown; scene_changed: boolean; core_use_changed: boolean }; plan: { goal: string; template_id?: string; core_use?: string; people?: number | null; stage: string; budget: number | null; destination: string | null; excludes: string[]; tasks?: string[]; must_buy?: string[]; optional?: string[]; prep?: string[]; reusable?: string[]; not_buy?: string[]; unknowns?: string[]; preferences?: string[]; checklist_sections?: ChecklistSection[] }; groups: { name: string; priority: 'must_buy' | 'optional' | 'candidate'; products: Product[]; catalog_message?: string }[]; budget?: { limit: number | null; minimum_total: number; optional_total: number; status: string }; trace?: { candidate_ids?: string[]; retrieval_signature?: string; retrieval_action?: string; retrieval_strategy?: string; retrieval_explanation?: string } };
type SlotStatus = 'have' | 'buy' | 'unknown' | 'not_needed';
type CommerceEvent = { type: string; payload: Record<string, unknown> };
type CommerceResponse = { shopping_session_id: string; final_text: string; events: CommerceEvent[]; detail?: string };
type TestRun = {
  test_id: string; tester_id: string; scenario_id: string; version: string; device_type: string;
  start_time: string; end_time: string; duration_sec: number; user_turns: number; assistant_turns: number;
  first_feedback_sec: number | null; first_checklist_sec: number | null; first_committed_checklist_sec: number | null; first_products_sec: number | null; turn_end_sec: number | null;
  clarify_turns: number; plan_updates: number; compare_selection_actions: number; model_compare_requests: number;
  product_candidates_returned: number;
  retrieval_actions: string; retrieval_strategies: string; conversation_transcript: string;
  plan_task_slots: number; plan_must_buy_slots: number; plan_optional_slots: number; plan_reusable_items: number;
  plan_not_buy_items: number; plan_unknown_items: number; budget_limit_cny: number | null;
  minimum_required_total_cny: number | null; optional_total_cny: number | null; budget_status: string;
  task_completed: string; completion_step: string; abandon_reason: string; satisfaction_1to5: string;
  ease_1to5: string; trust_1to5: string; plan_completeness_1to5: string; omission_confidence_1to5: string;
  budget_understanding_1to5: string; tradeoff_understanding_1to5: string; decision_confidence_1to5: string;
  stuck_step: string; error_count: number; last_conditions: string;
};

function record(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? value as Record<string, unknown> : {};
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function eventLabel(event: CommerceEvent) {
  const payload = event.payload;
  if (event.type === 'plan.update') return 'MainAgent 已更新购物清单';
  if (event.type === 'plan.preview') return 'MainAgent 正在生成清单草稿';
    if (event.type === 'model.call') return `${String(payload.agent || 'Agent')} 模型调用 ${Number(payload.elapsed_ms || 0) / 1000} 秒`;
  if (event.type === 'agent.dispatch') return payload.agent === 'search_agent' ? 'SearchAgent 开始检索' : `已派发 ${String(payload.agent || '子 Agent')}`;
  if (event.type === 'tool.invoke') return `开始调用 ${String(payload.tool || '工具')}`;
  if (event.type === 'tool.result') return `${String(payload.tool || '工具')} 返回 ${String(payload.hit_count ?? 0)} 个候选`;
  if (event.type === 'agent.result') return `${String(payload.agent || '子 Agent')} 已返回结果`;
  if (event.type === 'final.result') return 'MainAgent 已整理最终方案';
  if (event.type === 'error') return String(payload.message || '处理遇到问题');
  return event.type;
}

function fromCommerceResponse(response: CommerceResponse): Result {
  const planEvent = [...response.events].reverse().find(event => (event.type === 'plan.update' || event.type === 'plan.preview') && event.payload.shopping_plan);
  const planData = record(planEvent?.payload.shopping_plan);
  const stage = String(planData.stage || 'clarify');
  const plan: Result['plan'] = {
    goal: String(planData.goal || ''), template_id: typeof planData.template_id === 'string' ? planData.template_id : undefined, core_use: typeof planData.core_use === 'string' ? planData.core_use : undefined,
    people: typeof planData.people === 'number' ? planData.people : null,
    stage: stage === 'explore' ? 'plan' : stage,
    budget: typeof planData.budget_major === 'number' ? planData.budget_major : null,
    destination: typeof planData.destination === 'string' ? planData.destination : null,
    excludes: stringList(planData.excludes), tasks: stringList(planData.tasks),
    must_buy: stringList(planData.must_buy), optional: stringList(planData.optional),
    prep: stringList(planData.prep), reusable: stringList(planData.reusable),
    not_buy: stringList(planData.not_buy), unknowns: stringList(planData.unknowns),
    preferences: stringList(planData.preferences),
    checklist_sections: Array.isArray(planData.checklist_sections) ? planData.checklist_sections as ChecklistSection[] : [],
  };
  const grouped = new Map<string, Result['groups'][number]>();
  const rationales = new Map<string, { reason: string; tradeoff: string }>();
  const rankedByTask = new Map<string, string[]>();
  const notes: string[] = [];
  for (const event of response.events) {
    if (event.type !== 'agent.result' || event.payload.agent !== 'search_agent') continue;
    try {
      const output = record(JSON.parse(String(event.payload.output || '{}')));
      if (typeof event.payload.shopping_task === 'string' && Array.isArray(output.hits)) {
        rankedByTask.set(event.payload.shopping_task, output.hits.map(value => String(record(value).product_id || '')).filter(Boolean));
      }
      if (typeof output.notes === 'string' && output.notes) notes.push(output.notes);
      if (!Array.isArray(output.hits)) continue;
      for (const value of output.hits) {
        const hit = record(value);
        const id = String(hit.product_id || hit.id || '');
        if (id) rationales.set(id, {
          reason: typeof hit.reason === 'string' ? hit.reason : '',
          tradeoff: typeof hit.tradeoff === 'string' ? hit.tradeoff : '',
        });
      }
    } catch {
      // SearchAgent text remains visible in the conversation even if it is not JSON.
    }
  }
  const searchEvents = response.events.filter(event =>
    event.type === 'tool.result' && event.payload.tool === 'product_search_tool',
  );
  for (const event of searchEvents) {
    const payload = event.payload;
    const task = String(payload.shopping_task || '商品候选');
    const priority = plan.must_buy?.includes(task) ? 'must_buy' : plan.optional?.includes(task) ? 'optional' : 'candidate';
    const hits = Array.isArray(payload.hits) ? payload.hits : [];
    const gap = record(payload.catalog_gap);
    const catalogMessage = typeof gap.message === 'string' ? gap.message : hits.length ? undefined
      : '当前没有严格匹配该清单项目的模拟商品；不相关类目已隐藏。';
    const group = grouped.get(task) ?? { name: task, priority, products: [], catalog_message: catalogMessage };
    if (catalogMessage) group.catalog_message = catalogMessage;
    for (const value of hits) {
      const hit = record(value);
      const id = String(hit.product_id || '');
      if (!id || group.products.some(product => product.id === id)) continue;
      const price = typeof hit.price_major === 'number' ? hit.price_major : Number.NaN;
      if (!Number.isFinite(price)) continue;
      const highlights = stringList(hit.highlights);
      const rationale = rationales.get(id);
      group.products.push({
        id, title: String(hit.title || id), brand: String(hit.brand || ''),
        category: String(hit.category || task), subcategory: String(hit.origin_country || ''),
        price, currency: String(hit.currency || 'CNY'),
        highlights, reason: rationale?.reason || '', tradeoff: rationale?.tradeoff || '',
      });
    }
    grouped.set(task, group);
  }
  const groups = [...grouped.values()].map(group => {
    const ids = rankedByTask.get(group.name);
    if (!ids) return group; // Raw candidates are explicitly provisional until SearchAgent finishes.
    return { ...group, products: ids.map(id => group.products.find(product => product.id === id)).filter((product): product is Product => Boolean(product)) };
  }).filter(group => group.products.length || group.catalog_message);
  const productGroups = groups.filter(group => group.products.length);
  const canSumBudget = productGroups.flatMap(group => group.products).every(product => product.currency === 'CNY');
  const totalFor = (priority: 'must_buy' | 'optional') => productGroups
    .filter(group => group.priority === priority)
    .reduce((sum, group) => sum + Math.min(...group.products.map(product => product.price)), 0);
  const retrieval = searchEvents.map(event => event.payload);
  const actions = [...new Set(retrieval.map(item => String(item.retrieval_action || '')).filter(Boolean))];
  const strategies = [...new Set(retrieval.map(item => String(item.recall_strategy || '')).filter(Boolean))];
  const explanations = [...new Set(retrieval.map(item => String(item.retrieval_explanation || '')).filter(Boolean))];
  const scheme = planEvent ? { ...planData, ...plan } as Result['scheme'] : undefined;
  const rawMutation = planEvent?.payload.mutation;
  const mutation = rawMutation && typeof rawMutation === 'object' ? rawMutation as Result['mutation'] : undefined;
  return {
    text: response.final_text, preview: planEvent?.type === 'plan.preview', plan, scheme, mutation, groups,
    budget: productGroups.length && canSumBudget ? {
      limit: plan.budget, minimum_total: totalFor('must_buy'), optional_total: totalFor('optional'),
      status: plan.budget === null || totalFor('must_buy') <= plan.budget ? 'within_budget' : 'required_items_over_budget',
    } : undefined,
    trace: {
      candidate_ids: groups.flatMap(group => group.products.map(product => product.id)),
      retrieval_action: actions.join(', '), retrieval_strategy: strategies.join(', '),
      retrieval_explanation: [...explanations, ...notes].join(' '),
    },
  };
}

function mergeIncrementalResult(previous: Result | undefined, current: Result, reset: boolean): Result {
  if (!previous || reset || current.mutation?.scene_changed || current.mutation?.core_use_changed) return current;
  const mustBuy = new Set(current.plan.must_buy ?? []);
  const optional = new Set(current.plan.optional ?? []);
  const groups = new Map<string, Result['groups'][number]>();
  for (const group of previous.groups) {
    if (!mustBuy.has(group.name) && !optional.has(group.name)) continue;
    groups.set(group.name, { ...group, priority: mustBuy.has(group.name) ? 'must_buy' : 'optional' });
  }
  for (const group of current.groups) {
    if (!mustBuy.has(group.name) && !optional.has(group.name)) continue;
    groups.set(group.name, group);
  }
  return { ...current, groups: [...groups.values()] };
}

function newId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function storedId(storage: Storage, key: string, prefix: string) {
  const existing = storage.getItem(key);
  if (existing) return existing;
  const created = newId(prefix);
  storage.setItem(key, created);
  return created;
}

const STORAGE_KEY = 'shopmind-usability-tests-v1';
const VERSION = 'live-chat-v8-sse-agentscope';
const columns: (keyof TestRun)[] = ['test_id', 'tester_id', 'scenario_id', 'version', 'device_type', 'start_time', 'end_time', 'duration_sec', 'first_feedback_sec', 'first_checklist_sec', 'first_committed_checklist_sec', 'first_products_sec', 'turn_end_sec', 'user_turns', 'assistant_turns', 'clarify_turns', 'plan_updates', 'plan_task_slots', 'plan_must_buy_slots', 'plan_optional_slots', 'plan_reusable_items', 'plan_not_buy_items', 'plan_unknown_items', 'budget_limit_cny', 'minimum_required_total_cny', 'optional_total_cny', 'budget_status', 'compare_selection_actions', 'model_compare_requests', 'product_candidates_returned', 'retrieval_actions', 'retrieval_strategies', 'conversation_transcript', 'task_completed', 'completion_step', 'abandon_reason', 'satisfaction_1to5', 'ease_1to5', 'trust_1to5', 'plan_completeness_1to5', 'omission_confidence_1to5', 'budget_understanding_1to5', 'tradeoff_understanding_1to5', 'decision_confidence_1to5', 'stuck_step', 'error_count'];

function readRuns(): TestRun[] {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'); } catch { return []; }
}

function csvCell(value: unknown) {
  let text = String(value ?? '');
  if (/^[=+@\-\t\r]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function downloadCsv(runs: TestRun[]) {
  const csv = [columns.join(','), ...runs.map(run => columns.map(key => csvCell(run[key])).join(','))].join('\r\n');
  const url = URL.createObjectURL(new Blob(['\ufeff', csv], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url; link.download = `shopmind-usability-${new Date().toISOString().slice(0, 10)}.csv`; link.click();
  URL.revokeObjectURL(url);
}

export default function LiveChat() {
  const [sessionId, setSessionId] = useState(() => storedId(sessionStorage, 'shopmind.session', 'session'));
  const [buyerId] = useState(() => storedId(localStorage, 'shopmind.buyer', 'buyer'));
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [checklistSubmitting, setChecklistSubmitting] = useState(false);
  const [busySeconds, setBusySeconds] = useState(0);
  const [streamEvents, setStreamEvents] = useState<CommerceEvent[]>([]);
  const [streamText, setStreamText] = useState('');
  const [error, setError] = useState('');
  const [result, setResult] = useState<Result>();
  const [planConfirmed, setPlanConfirmed] = useState(false);
  const [slotStatus, setSlotStatus] = useState<Record<string, SlotStatus>>({});
  const [planBudget, setPlanBudget] = useState('');
  const [planPriority, setPlanPriority] = useState('');
  // 每个槽位只允许选一件：group name -> product id。总价只按已选商品计算。
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [testerId, setTesterId] = useState('');
  const [scenarioId, setScenarioId] = useState('');
  const [runs, setRuns] = useState<TestRun[]>(readRuns);
  const [activeRun, setActiveRun] = useState<TestRun | null>(() => {
    try { return JSON.parse(localStorage.getItem(`${STORAGE_KEY}-active`) || 'null'); } catch { return null; }
  });
  const [testOpen, setTestOpen] = useState(false);
  const [survey, setSurvey] = useState({ task_completed: 'partial', completion_step: 'understood', abandon_reason: '', satisfaction_1to5: '', ease_1to5: '', trust_1to5: '', plan_completeness_1to5: '', omission_confidence_1to5: '', budget_understanding_1to5: '', tradeoff_understanding_1to5: '', decision_confidence_1to5: '', stuck_step: '' });
  const base = import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? '' : location.origin);
  function showResult(data: Result, initializeChecklist = false) {
    setResult(data);
    if (initializeChecklist && data.plan.stage === 'plan') {
      const statuses: Record<string, SlotStatus> = {};
      for (const [prefix, items] of [['required', data.plan.tasks ?? []], ['optional', data.plan.optional ?? []]] as const) {
        for (const item of items) statuses[`${prefix}:${item}`] = data.plan.reusable?.includes(item) ? 'have'
          : data.plan.not_buy?.includes(item) ? 'not_needed' : data.plan.must_buy?.includes(item) ? 'buy' : 'unknown';
      }
      setSlotStatus(statuses);
      setPlanBudget(data.plan.budget ? String(data.plan.budget) : '');
      const preferences = data.plan.preferences?.join(' ') || '';
      setPlanPriority(/轻装|轻便|少占空间/.test(preferences) ? 'lightweight' : /省钱|实惠|性价比|预算/.test(preferences) ? 'value' : /品质|耐用/.test(preferences) ? 'quality' : /周全|齐全/.test(preferences) ? 'complete' : /美观|颜值/.test(preferences) ? 'appearance' : '');
    }
  }
  useEffect(() => { sessionStorage.setItem('shopmind.session', sessionId); }, [sessionId]);
  useEffect(() => { localStorage.setItem(STORAGE_KEY, JSON.stringify(runs)); }, [runs]);
  useEffect(() => { if (activeRun) localStorage.setItem(`${STORAGE_KEY}-active`, JSON.stringify(activeRun)); else localStorage.removeItem(`${STORAGE_KEY}-active`); }, [activeRun]);
  useEffect(() => {
    if (!busy) { setBusySeconds(0); return; }
    const started = Date.now();
    const timer = window.setInterval(() => setBusySeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

  function patchRun(update: (run: TestRun) => TestRun) {
    setActiveRun(current => current ? update(current) : current);
  }

  function startTest() {
    if (!testerId.trim() || !scenarioId.trim()) return;
    setTestOpen(true);
    const start = new Date();
    setMessages([]); setResult(undefined); setError(''); setPicked({});
    setPlanConfirmed(false);
    setSessionId(newId('session')); setSlotStatus({});
    setActiveRun({ test_id: crypto.randomUUID(), tester_id: testerId.trim(), scenario_id: scenarioId.trim(), version: VERSION,
      device_type: matchMedia('(max-width: 760px)').matches ? 'mobile' : 'desktop', start_time: start.toISOString(), end_time: '', duration_sec: 0,
      user_turns: 0, assistant_turns: 0, clarify_turns: 0, plan_updates: 0, compare_selection_actions: 0, model_compare_requests: 0, product_candidates_returned: 0, retrieval_actions: '', retrieval_strategies: '', conversation_transcript: '',
      plan_task_slots: 0, plan_must_buy_slots: 0, plan_optional_slots: 0, plan_reusable_items: 0, plan_not_buy_items: 0, plan_unknown_items: 0,
      budget_limit_cny: null, minimum_required_total_cny: null, optional_total_cny: null, budget_status: '',
      task_completed: 'running', completion_step: '', abandon_reason: '', satisfaction_1to5: '', ease_1to5: '', trust_1to5: '',
      plan_completeness_1to5: '', omission_confidence_1to5: '', budget_understanding_1to5: '', tradeoff_understanding_1to5: '', decision_confidence_1to5: '',
      stuck_step: '', error_count: 0, last_conditions: '', first_feedback_sec: null, first_checklist_sec: null, first_committed_checklist_sec: null, first_products_sec: null, turn_end_sec: null });
  }

  function finishTest() {
    if (!activeRun) return;
    const end = new Date();
    setRuns(current => [...current, { ...activeRun, ...survey, end_time: end.toISOString(), duration_sec: Math.round((end.getTime() - new Date(activeRun.start_time).getTime()) / 1000) }]);
    setActiveRun(null);
  }

  async function send(query = input, retry = false, newSession = false, checklistSelection?: Record<string, unknown>) {
    if (!query.trim() || busy) return;
    const requestSessionId = newSession ? newId('session') : sessionId;
    if (newSession) setSessionId(requestSessionId);
    const history = retry ? messages : newSession ? [{ role: 'user' as const, content: query }] : [...messages, { role: 'user' as const, content: query }];
    if (activeRun && !retry) patchRun(run => ({ ...run, user_turns: run.user_turns + 1 }));
    setMessages(history); setInput(''); setBusy(true); setError(''); setStreamEvents([]); setStreamText('');
    const started = performance.now();
    const timing: Record<string, number | string | null> = { session: requestSessionId, query, first_feedback_sec: null, first_checklist_sec: null, first_committed_checklist_sec: null, first_products_sec: null, turn_end_sec: null };
    const mark = (key: string) => {
      if (timing[key] !== null) return;
      window.requestAnimationFrame(() => {
        if (timing[key] === null) timing[key] = Math.round((performance.now() - started) / 10) / 100;
      });
    };
    try {
      const response = await fetch(`${base}/commerce/intents/stream`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shopping_session_id: requestSessionId, buyer_id: buyerId, locale: 'zh-CN', currency: 'CNY', raw_query: query, ...(checklistSelection ? { checklist_selection: checklistSelection } : {}) }), signal: AbortSignal.timeout(240000) });
      if (!response.ok) {
        const payload = await response.json() as CommerceResponse;
        throw new Error(payload.detail || '服务暂时不可用');
      }
      const reader = response.body?.getReader();
      if (!reader) throw new Error('浏览器未能读取实时事件流');
      const decoder = new TextDecoder();
      const events: CommerceEvent[] = [];
      let buffer = '';
      let finalText = '';
      const consume = (frame: string) => {
        const data = frame.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n');
        if (!data) return;
        const event = JSON.parse(data) as CommerceEvent;
        events.push(event);
        setStreamEvents([...events]);
        if (event.type === 'token.delta' && typeof event.payload.token === 'string') {
          finalText += event.payload.token;
          setStreamText(finalText);
          if (finalText.trim()) mark('first_feedback_sec');
        }
        if (event.type === 'final.result' && typeof event.payload.text === 'string') finalText = event.payload.text;
        if (event.type === 'plan.update' && event.payload.shopping_plan) mark('first_committed_checklist_sec');
        if (((event.type === 'plan.update' || event.type === 'plan.preview') && event.payload.shopping_plan) || event.type === 'tool.result' || event.type === 'agent.result') {
          const partial = fromCommerceResponse({ shopping_session_id: requestSessionId, final_text: finalText, events });
          showResult(mergeIncrementalResult(result, partial, newSession), event.type === 'plan.update');
          if (event.type === 'plan.update' && partial.plan.stage === 'plan' && (partial.plan.checklist_sections?.length || partial.plan.tasks?.length || partial.plan.optional?.length)) mark('first_checklist_sec');
          if (partial.groups.some(group => group.products.length)) mark('first_products_sec');
        }
        // Useful text and the first structured checklist are separate milestones.
        if (event.type === 'final.result' && finalText) mark('first_feedback_sec');
        if (event.type === 'error' && !event.payload.retrying) setError(String(event.payload.message || '处理失败'));
      };
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() || '';
        frames.forEach(consume);
        if (done) break;
      }
      if (buffer.trim()) consume(buffer);
      const payload: CommerceResponse = { shopping_session_id: requestSessionId, final_text: finalText, events };
      const currentData = fromCommerceResponse(payload);
      const data = mergeIncrementalResult(result, currentData, newSession);
      // 只保留仍然存在的选择：改需求后卡片会重排，但用户已选的款不该凭空丢失。
      setPicked(current => {
        const alive = new Map<string, Set<string>>();
        for (const group of data.groups) alive.set(group.name, new Set(group.products.map(product => product.id)));
        return Object.fromEntries(Object.entries(current).filter(([group, id]) => alive.get(group)?.has(id)));
      });
      setPlanConfirmed(false);
      if (activeRun) patchRun(run => {
        const state = JSON.stringify([data.plan.budget, data.plan.destination, data.plan.excludes, data.plan.tasks, data.plan.must_buy, data.plan.optional, data.plan.prep, data.plan.reusable, data.plan.not_buy, data.plan.unknowns, data.groups.map(group => [group.name, group.priority, group.products.map(product => product.id)])]);
        const changed = run.last_conditions !== '' && run.last_conditions !== state;
          const cards = currentData.groups.reduce((count: number, group: Result['groups'][number]) => count + group.products.length, 0);
        return { ...run, assistant_turns: run.assistant_turns + 1, clarify_turns: run.clarify_turns + (data.plan.stage === 'clarify' ? 1 : 0),
          plan_updates: (run.plan_updates ?? 0) + Number(changed), product_candidates_returned: (run.product_candidates_returned ?? 0) + cards,
          plan_task_slots: data.plan.tasks?.length ?? 0, plan_must_buy_slots: data.plan.must_buy?.length ?? 0,
          plan_optional_slots: data.plan.optional?.length ?? 0, plan_reusable_items: data.plan.reusable?.length ?? 0,
          plan_not_buy_items: data.plan.not_buy?.length ?? 0, plan_unknown_items: data.plan.unknowns?.length ?? 0,
          budget_limit_cny: data.budget?.limit ?? data.plan.budget, minimum_required_total_cny: data.budget?.minimum_total ?? null,
          optional_total_cny: data.budget?.optional_total ?? null,
          budget_status: data.budget?.status ?? 'not_calculated',
          retrieval_actions: [run.retrieval_actions, data.trace?.retrieval_action].filter(Boolean).join('|'),
          retrieval_strategies: [run.retrieval_strategies, data.trace?.retrieval_strategy].filter(Boolean).join('|'),
          conversation_transcript: [run.conversation_transcript, JSON.stringify({ user: history[history.length - 1]?.content, assistant: data.text, plan: data.plan, groups: data.groups, trace: data.trace })].filter(Boolean).join('\n'),
          last_conditions: state };
      });
      setMessages([...history, { role: 'assistant', content: data.text + '\n' + data.groups.map((g: Result['groups'][number]) => `${g.name}: ${g.products.map(p => `${p.id} ${p.title}`).join('、')}`).join('\n') }]);
    } catch (e) { if (activeRun) patchRun(run => ({ ...run, error_count: run.error_count + 1 })); setError(e instanceof Error ? e.message : '请求失败，请重试'); }
    finally {
      timing.turn_end_sec = Math.round((performance.now() - started) / 10) / 100;
      const saved = JSON.parse(sessionStorage.getItem('shopmind.latency') || '[]');
      sessionStorage.setItem('shopmind.latency', JSON.stringify([...saved, timing]));
      if (activeRun) patchRun(run => ({ ...run, first_feedback_sec: timing.first_feedback_sec as number | null, first_checklist_sec: timing.first_checklist_sec as number | null, first_committed_checklist_sec: timing.first_committed_checklist_sec as number | null, first_products_sec: timing.first_products_sec as number | null, turn_end_sec: timing.turn_end_sec as number }));
      setBusy(false); setChecklistSubmitting(false);
    }
  }
  const requiredCount = result?.groups.filter(group => group.priority === 'must_buy').length ?? 0;
  const optionalCount = result?.groups.filter(group => group.priority === 'optional').length ?? 0;
  function checklistRow(item: ChecklistItem, sectionId: string, isOptional: boolean) {
    const prefix = isOptional ? 'optional' : 'required';
    const key = prefix + ':' + item.name;
    const options: [SlotStatus, string][] = isOptional
      ? [['have', '已有'], ['buy', '这次要买'], ['unknown', '还不确定'], ['not_needed', '不需要']]
      : [['have', '已有'], ['buy', '需要购买'], ['unknown', '还没确认']];
    return <div className={'checklist-item ' + (isOptional ? 'is-optional' : 'is-required')} key={sectionId + ':' + key}><div className="checklist-item-copy"><b>{item.name}</b>{(item.quantity || item.condition || item.note) && <small>{[item.quantity, item.condition, item.note].filter(Boolean).join(' · ')}</small>}</div><div role="group" aria-label={item.name + '状态'}>{options.map(([status, label]) => <button type="button" key={status} disabled={busy || Boolean(result?.preview)} className={slotStatus[key] === status ? 'selected' : ''} aria-pressed={slotStatus[key] === status} onClick={() => setSlotStatus(current => ({ ...current, [key]: current[key] === status ? 'unknown' : status }))}>{label}</button>)}</div></div>;
  }
  const productGroups = result?.groups.filter(group => group.products.length) ?? [];
  const pickedGroups = productGroups.filter(group => {
    const productId = picked[group.name];
    return productId && group.products.some(product => product.id === productId);
  });
  const pickedTotal = pickedGroups.reduce((sum, group) => {
    const product = group.products.find(item => item.id === picked[group.name]);
    return sum + (product?.price ?? 0);
  }, 0);
  const unpicked = productGroups.length - pickedGroups.length;
  const overBudget = Boolean(result?.plan.budget) && pickedTotal > Number(result?.plan.budget);
  const checklistReady = Object.values(slotStatus).some(status => status !== undefined);
  function confirmPlan() {
    if (!pickedGroups.length || planConfirmed) return;
    const lines = pickedGroups.map(group => {
      const product = group.products.find(item => item.id === picked[group.name]);
      return `${group.name}：${product?.title ?? picked[group.name]}（${product?.price ?? '-'} ${product?.currency ?? 'CNY'}）`;
    });
    const message = `采购方案已确认：\n${lines.join('\n')}\n商品价合计 ${pickedTotal} CNY${result?.plan.budget ? `（预算 ${result.plan.budget} CNY）` : ''}。本演示仅记录选择，不会加入真实购物车或创建订单。`;
    setPlanConfirmed(true);
    setMessages(current => [...current, { role: 'assistant', content: message }]);
    if (activeRun) patchRun(run => ({ ...run, completion_step: 'confirmed', minimum_required_total_cny: pickedTotal, budget_status: result?.plan.budget ? (pickedTotal <= result.plan.budget ? 'within_budget' : 'required_items_over_budget') : 'no_budget', conversation_transcript: [run.conversation_transcript, JSON.stringify({ assistant: message, event: 'plan_confirmed', picked })].filter(Boolean).join('\n') }));
  }
  function confirmChecklist() {
    if (!result || !checklistReady || busy) return;
    const required = (result.plan.tasks ?? []).filter(item => !result.plan.optional?.includes(item));
    const optional = result.plan.optional ?? [];
    const labels = (items: string[], prefix: string, status: SlotStatus) => items.filter(item => status === 'unknown'
      ? !slotStatus[`${prefix}:${item}`] || slotStatus[`${prefix}:${item}`] === 'unknown'
      : slotStatus[`${prefix}:${item}`] === status);
    const have = [...labels(required, 'required', 'have'), ...labels(optional, 'optional', 'have')];
    const missing = labels(required, 'required', 'buy');
    const addOptional = labels(optional, 'optional', 'buy');
    const notNeeded = labels(optional, 'optional', 'not_needed');
    const unknown = [...labels(required, 'required', 'unknown'), ...labels(optional, 'optional', 'unknown')];
    const priorityLabels: Record<string, string> = { lightweight: '轻便少占空间', value: '实惠/性价比', quality: '品质耐用', complete: '准备周全', appearance: '美观协调' };
    const summary = [
      `清单已逐项确认。已有可复用：${have.join('、') || '暂无'}`,
      `明确缺少、需要购买的必备项：${missing.join('、') || '暂无'}`,
      `按需项中希望准备：${addOptional.join('、') || '暂无'}`,
      `明确不需要的按需项：${notNeeded.join('、') || '暂无'}`,
      `尚未确认：${unknown.join('、') || '暂无'}`,
      planBudget ? `整套预算上限 ${planBudget} 元。` : '预算暂未提供。',
      planPriority ? `优先考虑${priorityLabels[planPriority]}。` : '请根据清单先匹配商品，并询问我最关键的取舍偏好。',
      '请只为明确缺少的必备项和我选择准备的按需项匹配商品；已有、不需要和还不确定的项目不要推荐。商品候选后说明选择与取舍，并汇总预算。',
    ].join(' ');
    setChecklistSubmitting(true);
    void send(summary, false, false, {
      have, must_buy: missing, optional_buy: addOptional, deferred: notNeeded, unknown,
      budget_major: planBudget ? Number(planBudget) : null, priority: planPriority,
    });
  }
  return <section className="live-section" id="demo">
    <div className="section-heading"><p className="eyebrow">SCENARIO DEMO / LIVE AGENT</p><h2>场景演示：与 ShopMind 实时对话</h2><p>本区通过本地 Commerce API 实际运行；已配置场景会优先生成清单模板，后续澄清与商品检索按实际链路调用。商品和价格来自模拟目录，不涉及真实交易。</p></div>
    <div className={`test-panel ${testOpen || activeRun ? 'open' : ''}`}>
      <button className="test-panel-toggle" type="button" onClick={() => setTestOpen(open => !open)} aria-expanded={testOpen || Boolean(activeRun)}><strong>前端可用性测试</strong><span>{activeRun ? `记录中 · ${activeRun.scenario_id}` : `本机已保存 ${runs.length} 条`}</span><b>{testOpen || activeRun ? '收起' : '展开测试模式'}</b></button>
      {(testOpen || activeRun) && <>
      {!activeRun ? <div className="test-start">
        <label>测试者编号<input value={testerId} onChange={e => setTesterId(e.target.value)} placeholder="例如 T01（无需填写姓名）" /></label>
        <label>场景编号<input value={scenarioId} onChange={e => setScenarioId(e.target.value)} placeholder="例如 gift-01" /></label>
        <button type="button" disabled={!testerId.trim() || !scenarioId.trim()} onClick={startTest}>开始记录</button>
      </div> : <div className="test-finish">
        <label>任务结果<select value={survey.task_completed} onChange={e => setSurvey(s => ({ ...s, task_completed: e.target.value }))}><option value="completed">完成</option><option value="partial">部分完成</option><option value="abandoned">放弃</option><option value="error">技术问题</option></select></label>
        <label>完成阶段<select value={survey.completion_step} onChange={e => setSurvey(s => ({ ...s, completion_step: e.target.value }))}><option value="understood">理解需求</option><option value="got_directions">获得场景方向</option><option value="got_products">获得商品</option><option value="compared">完成比较</option><option value="confirmed">确认选择</option><option value="none">未完成</option></select></label>
        <label>满意度<select value={survey.satisfaction_1to5} onChange={e => setSurvey(s => ({ ...s, satisfaction_1to5: e.target.value }))}><option value="" disabled>选择评分</option>{[1, 2, 3, 4, 5].map(n => <option key={n}>{n}</option>)}</select></label>
        <label>顺畅度<select value={survey.ease_1to5} onChange={e => setSurvey(s => ({ ...s, ease_1to5: e.target.value }))}><option value="" disabled>选择评分</option>{[1, 2, 3, 4, 5].map(n => <option key={n}>{n}</option>)}</select></label>
        <label>推荐可信度<select value={survey.trust_1to5} onChange={e => setSurvey(s => ({ ...s, trust_1to5: e.target.value }))}><option value="" disabled>选择评分</option>{[1, 2, 3, 4, 5].map(n => <option key={n}>{n}</option>)}</select></label>
        <label>清单完整度<select value={survey.plan_completeness_1to5} onChange={e => setSurvey(s => ({ ...s, plan_completeness_1to5: e.target.value }))}><option value="" disabled>选择评分</option>{[1, 2, 3, 4, 5].map(n => <option key={n}>{n}</option>)}</select></label>
        <label>遗漏顾虑缓解度<select value={survey.omission_confidence_1to5} onChange={e => setSurvey(s => ({ ...s, omission_confidence_1to5: e.target.value }))}><option value="" disabled>选择评分</option>{[1, 2, 3, 4, 5].map(n => <option key={n}>{n}</option>)}</select></label>
        <label>预算理解度<select value={survey.budget_understanding_1to5} onChange={e => setSurvey(s => ({ ...s, budget_understanding_1to5: e.target.value }))}><option value="" disabled>选择评分</option>{[1, 2, 3, 4, 5].map(n => <option key={n}>{n}</option>)}</select></label>
        <label>取舍理解度<select value={survey.tradeoff_understanding_1to5} onChange={e => setSurvey(s => ({ ...s, tradeoff_understanding_1to5: e.target.value }))}><option value="" disabled>选择评分</option>{[1, 2, 3, 4, 5].map(n => <option key={n}>{n}</option>)}</select></label>
        <label>决策信心<select value={survey.decision_confidence_1to5} onChange={e => setSurvey(s => ({ ...s, decision_confidence_1to5: e.target.value }))}><option value="" disabled>选择评分</option>{[1, 2, 3, 4, 5].map(n => <option key={n}>{n}</option>)}</select></label>
        <label>未完成原因<select value={survey.abandon_reason} onChange={e => setSurvey(s => ({ ...s, abandon_reason: e.target.value }))}><option value="">无/不适用</option><option value="too_many_questions">追问太多</option><option value="irrelevant_products">商品不相关</option><option value="slow_response">响应太慢</option><option value="interface_unclear">界面不清楚</option><option value="technical_error">技术错误</option><option value="other">其他</option></select></label>
        <label>卡点/未完成原因<input value={survey.stuck_step} onChange={e => setSurvey(s => ({ ...s, stuck_step: e.target.value }))} placeholder="可留空；不要填写个人隐私" /></label>
        <button type="button" disabled={busy} onClick={finishTest}>结束并保存</button>
      </div>}
      <div className="test-panel-actions"><button type="button" disabled={!runs.length && !activeRun} onClick={() => downloadCsv(activeRun ? [...runs, { ...activeRun, duration_sec: Math.round((Date.now() - new Date(activeRun.start_time).getTime()) / 1000) }] : runs)}>导出 CSV（{runs.length + (activeRun ? 1 : 0)}）</button><button type="button" disabled={!runs.length && !activeRun} onClick={() => { if (confirm('清除这台设备保存的所有测试记录？')) { setRuns([]); setActiveRun(null); } }}>清空本机记录</button><small>自动记录清单槽位、预算状态、检索路径和逐轮对话；只保存在本机浏览器，可随 CSV 导出。</small></div></>}
    </div>
    <div className="live-console" id="live">
    <div className="live-console-head"><strong>ShopMind Assistant</strong><span>{busy ? '正在整理最小采购方案' : '准备好帮你规划'}</span></div>
      <div className="live-actions"><button disabled={busy} onClick={() => { setMessages([]); setResult(undefined); setPlanConfirmed(false); setError(''); setPicked({}); setSlotStatus({}); setPlanBudget(''); setPlanPriority(''); const freshSession = newId('session'); setSessionId(freshSession); sessionStorage.setItem('shopmind.session', freshSession); }}>新对话</button>
      {['周末去海边两天，帮我准备购物清单', '想给妈妈买礼物，不知道买什么', '第一次租房入住，不知道要准备什么'].map(q => <button disabled={busy} key={q} onClick={() => { setMessages([]); setResult(undefined); setPlanConfirmed(false); setSlotStatus({}); setPicked({}); setError(''); void send(q, false, true); }}>{q}</button>)}</div>
    <div className="live-messages" aria-live="polite">{messages.map((m, i) => <div key={i} className={`live-message ${m.role}`}><small>{m.role === 'user' ? '你' : 'ShopMind'}</small><p>{m.role === 'assistant' ? <>{m.content.split('\n').filter(line => !/^.+: P\d/.test(line)).join('\n').replace(/\*\*/g, '').replace(/^\s*[-*]\s+/gm, '• ')}{result?.plan.stage === 'plan' && i === messages.length - 1 ? '\n\n请在下方逐项确认已有、需要补或暂不需要，再确认预算和优先级。' : ''}</> : m.content}</p></div>)}
      {!messages.length && <div className="live-message assistant"><small>ShopMind</small><p>还没想好商品名也没关系。说说你要完成的生活任务，我会先问清会改变清单的场景条件，再列准备品类。</p></div>}
      {busy && <div className="live-status" role="status"><span className="live-spinner" />{busySeconds >= 20 ? `Agent 仍在整理方案，已等待 ${busySeconds} 秒…` : 'MainAgent 正在理解需求，SearchAgent 将按方案检索…'}</div>}
      {busy && streamText && <div className="live-message assistant streaming-reply"><small>ShopMind · 实时回复</small><p>{streamText}</p></div>}
      {busy && streamEvents.length > 0 && <ol className="live-event-feed" aria-label="Agent 实时处理进度">{streamEvents.filter(event => event.type !== 'token.delta').slice(-6).map((event, index) => <li key={`${event.type}-${index}`}>{eventLabel(event)}</li>)}</ol>}
      {error && <div role="alert">{error} {messages[messages.length - 1]?.role === 'user' && <button onClick={() => void send(messages[messages.length - 1].content, true)}>重试</button>}</div>}
    </div>
    {result && <><div className="insight-chips"><span>目标：{result.plan.goal}</span><span>总预算：{result.plan.budget ?? '待确认'} {result.plan.budget ? 'CNY（商品价）' : ''}</span><span>配送地：{result.plan.destination ?? '待确认'}</span>{result.plan.excludes.map(x => <span key={x}>不买：{x}</span>)}</div>
      {result.plan.stage === 'plan' && !result.groups.length && <section className={'checklist-step' + (result.plan.template_id === 'maternity_hospital_bag' ? ' maternity-checklist' : '')} aria-label="逐项核对采购清单" aria-busy={busy || Boolean(result.preview)}>
        <header><div><strong>{result.preview ? '清单草稿 · 正在生成' : '逐项核对清单'}</strong><span>{result.preview ? '正在整理分组清单；草稿暂不作为正式核对项' : '点选状态即可，不用把已有物品逐条打字'}</span></div><small>未标记的项目保持“还不确定”</small></header>
        {result.preview && !result.plan.checklist_sections?.length && <div className="checklist-draft-wait" role="status"><span className="live-spinner" />正在整理详细物品、数量和分类，请稍候…</div>}
        {!!result.plan.checklist_sections?.length && result.plan.checklist_sections.map(section => <div className="checklist-section" key={section.id}>
          <h3>{section.title}</h3>
          {!!section.required.length && <div className="checklist-group"><h4>必备（完成任务通常需要）</h4>{section.required.map(item => checklistRow(item, section.id, false))}</div>}
          {!!section.optional.length && <div className="checklist-group optional-slots"><h4>按需（满足条件时再准备）</h4>{section.optional.map(item => checklistRow(item, section.id, true))}</div>}
          {section.reference && <details className="checklist-reference"><summary>参考依据：{section.reference.title}</summary><p>{section.reference.note}</p>{section.reference.url && <a href={section.reference.url} target="_blank" rel="noreferrer">查看来源 ↗</a>}</details>}
        </div>)}
        {!result.preview && !result.plan.checklist_sections?.length && !!result.plan.tasks?.length && <div className="checklist-group"><h3>必备物品（任务需要，不代表要购买）</h3>{result.plan.tasks.filter(item => !result.plan.optional?.includes(item)).map(item => {
          const key = `required:${item}`;
          return <div className="checklist-item is-required" key={key}><b>{item}</b><div role="group" aria-label={`${item}状态`}>
            {([['have', '已有'], ['buy', '需要购买'], ['unknown', '还没确认']] as [SlotStatus, string][]).map(([status, label]) => <button type="button" key={status} disabled={busy || Boolean(result.preview)} className={slotStatus[key] === status ? 'selected' : ''} aria-pressed={slotStatus[key] === status} onClick={() => setSlotStatus(current => ({ ...current, [key]: current[key] === status ? 'unknown' : status }))}>{label}</button>)}
          </div></div>;
        })}</div>}
        {!result.preview && !result.plan.checklist_sections?.length && !!result.plan.optional?.length && <div className="checklist-group optional-slots"><h3>按需准备</h3>{result.plan.optional.map(item => {
          const key = `optional:${item}`;
          return <div className="checklist-item is-optional" key={key}><b>{item}</b><div role="group" aria-label={`${item}状态`}>
            {([['have', '已有'], ['buy', '这次要买'], ['unknown', '还不确定'], ['not_needed', '不需要']] as [SlotStatus, string][]).map(([status, label]) => <button type="button" key={status} disabled={busy || Boolean(result.preview)} className={slotStatus[key] === status ? 'selected' : ''} aria-pressed={slotStatus[key] === status} onClick={() => setSlotStatus(current => ({ ...current, [key]: current[key] === status ? 'unknown' : status }))}>{label}</button>)}
          </div></div>;
        })}</div>}
        {!!result.plan.prep?.length && <div className="checklist-reminders"><h3>出发前提醒（不需要购买）</h3><p>{result.plan.prep.join(' · ')}</p></div>}
        <div className="checklist-preferences"><label>预算上限（商品价）<span><input type="number" min="1" disabled={busy} value={planBudget} onChange={event => setPlanBudget(event.target.value)} placeholder="可留空" /> 元</span></label><label>优先考虑<select disabled={busy} value={planPriority} onChange={event => setPlanPriority(event.target.value)}><option value="">暂不选，让 ShopMind 根据清单追问</option><option value="lightweight">轻便少占空间</option><option value="value">实惠 / 性价比</option><option value="quality">品质耐用</option><option value="complete">准备周全</option><option value="appearance">美观协调</option></select></label></div>
        <div className="checklist-next"><span>{result.preview ? '清单仍在生成，确认按钮暂不可用。' : checklistReady ? '只为“需要购买 / 这次要买”的项目找商品；已有、不需要和还不确定的项目不会推荐。' : '请先标记至少一项；其他项目可以保持还没确认。'}</span><button type="button" disabled={busy || Boolean(result.preview) || !checklistReady} aria-busy={checklistSubmitting} onClick={confirmChecklist}>{checklistSubmitting ? <><span className="live-spinner" aria-hidden="true" />正在处理清单… {busySeconds} 秒</> : '确认清单，匹配商品 →'}</button></div>
        {checklistSubmitting && <div className="checklist-progress" role="status">{busySeconds >= 30 ? '本轮耗时较长，仍在等待 Agent 返回；你的选择已保留，请勿重复提交。' : '正在提交物品状态，并为明确待购项匹配商品；无需重复点击。'}</div>}
      </section>}
      {result.plan.stage !== 'clarify' && result.plan.stage !== 'plan' && <div className="plan-readout">
        <div><b>必备任务槽位</b><span>{result.plan.tasks?.join(' · ') || '按当前场景整理'}</span></div>
        <div><b>已有 / 可复用</b><span>{result.plan.reusable?.join(' · ') || '已有物品尚未确认'}</span></div>
        <div><b>明确不需要</b><span>{result.plan.not_buy?.join(' · ') || '暂无'}</span></div>
        <div><b>按需 / 还不确定</b><span>{[...(result.plan.optional ?? []), ...(result.plan.unknowns ?? [])].join(' · ') || '暂无额外项'}</span></div>
      </div>}
      {productGroups.length > 0 && <p className={`budget-readout ${overBudget ? 'over' : pickedGroups.length ? 'ok' : ''}`} aria-live="polite">已选 {pickedGroups.length} / {productGroups.length} 个待购项（{requiredCount} 必买 · {optionalCount} 按需）。商品价合计 <b>{pickedTotal}</b> CNY{result?.plan.budget ? ` / 预算上限 ${result.plan.budget} CNY` : '（未设预算）'}，不含运费；每项最多选一件。{unpicked > 0 && ` 还有 ${unpicked} 项未选，不计入合计。`}{overBudget && ' 已超预算，换一个候选即可重新计算。'}</p>}
      {result.groups.map(g => <div key={g.name} className={`live-group ${g.priority}`}><h3>{g.name}<small>{g.priority === 'must_buy' ? '已确认待购' : g.priority === 'candidate' ? '待核对候选' : '按需准备'}</small></h3>{g.products.length ? <div className={`live-products count-${Math.min(g.products.length, 3)}`}>{g.products.map((p, index) => {
        const isPicked = picked[g.name] === p.id;
        return <article key={p.id} className={`live-product ${isPicked ? 'picked' : ''}`}><div className={`live-product-art tone-${index % 3}`}><span>{p.category}</span><small>{p.subcategory}</small></div><div className="live-product-copy"><div className="live-product-meta"><span>{p.brand}</span><strong>{p.price} {p.currency}</strong></div><h4>{p.title}</h4>{p.reason && <p>{p.reason}</p>}{p.tradeoff && <p className="live-tradeoff"><b>取舍</b>{p.tradeoff}</p>}<button type="button" className={`pick-btn ${isPicked ? 'picked' : ''}`} aria-pressed={isPicked} disabled={busy} onClick={() => { setPicked(current => isPicked ? Object.fromEntries(Object.entries(current).filter(([group]) => group !== g.name)) : { ...current, [g.name]: p.id }); setPlanConfirmed(false); if (activeRun) patchRun(run => ({ ...run, compare_selection_actions: (run.compare_selection_actions ?? 0) + 1 })); }}>{isPicked ? '✓ 已选这件（点按取消）' : '选这件'}</button></div></article>;
      })}</div> : <p className="catalog-gap-message">{g.catalog_message}</p>}</div>)}
      {productGroups.length > 0 && <button type="button" className="confirm-plan-button" disabled={busy || planConfirmed || !pickedGroups.length} onClick={confirmPlan}>{planConfirmed ? '方案已确认' : pickedGroups.length ? `确认这套采购方案 · ${pickedTotal} CNY` : '先选要买的商品'}</button>}
      </>}
    <form className="demo-composer" onSubmit={e => { e.preventDefault(); void send(); }}><input maxLength={1500} value={input} onChange={e => setInput(e.target.value)} placeholder="说说你要完成的生活任务，或已经卡住的选择…" aria-label="真实对话输入" /><button disabled={busy || !input.trim()} type="submit" aria-label="发送真实对话" title="发送">↑</button></form>
    </div>
    <p className="live-note">商品、品牌、价格、库存与配送范围均为虚拟演示数据；方案预算按商品价估算，未确认配送地时不能判断可购买性。</p>
  </section>;
}
