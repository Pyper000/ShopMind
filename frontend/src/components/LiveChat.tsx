import { useEffect, useState } from 'react';
import ChatAvatar from './ChatAvatar';

type Message = { role: 'user' | 'assistant'; content: string };
type Product = { id: string; title: string; brand: string; category: string; subcategory: string; price: number; currency: string; highlights: string[]; reason: string; tradeoff: string };
type ChecklistItem = { name: string; quantity?: string; note?: string; condition?: string; purchasable?: boolean };
type ChecklistSection = { id: string; title: string; required: ChecklistItem[]; optional: ChecklistItem[]; reference?: { title: string; url?: string; note?: string } };
type Result = { text: string; preview?: boolean; scheme?: { state_version: number; goal: string; core_use?: string; people?: number | null; stage: string; budget: number | null; destination: string | null; excludes: string[]; tasks?: string[]; must_buy?: string[]; optional?: string[]; prep?: string[]; reusable?: string[]; prepared?: string[]; not_buy?: string[]; unknowns?: string[]; preferences?: string[] }; mutation?: { state_version: number; target_slots: { name: string; status: string }[]; added_slots: string[]; removed_slots: string[]; existing_slots: string[]; deferred_slots: string[]; pending_slots: string[]; budget_change: unknown; preference_change: unknown; scene_changed: boolean; core_use_changed: boolean }; plan: { goal: string; template_id?: string; core_use?: string; people?: number | null; stage: string; budget: number | null; destination: string | null; excludes: string[]; tasks?: string[]; must_buy?: string[]; optional?: string[]; prep?: string[]; reusable?: string[]; prepared?: string[]; not_buy?: string[]; unknowns?: string[]; preferences?: string[]; checklist_sections?: ChecklistSection[] }; groups: { name: string; priority: 'must_buy' | 'optional' | 'candidate'; products: Product[]; catalog_message?: string }[]; budget?: { limit: number | null; minimum_total: number; optional_total: number; status: string }; trace?: { candidate_ids?: string[]; retrieval_signature?: string; retrieval_action?: string; retrieval_strategy?: string; retrieval_explanation?: string } };
type SlotStatus = 'have' | 'prepare' | 'unknown' | 'not_needed';
const CHECKLIST_STATUSES: [SlotStatus, string][] = [['have', '已有'], ['prepare', '需要准备'], ['unknown', '还没确认'], ['not_needed', '不需要']];

function isNonPurchasableName(name: string) {
  if (/(收纳包|收纳袋|证件包|证件夹|护照夹|证件套|卡套)/.test(name)) return false;
  return /(身份证|护照|签证|驾驶证|驾照|医保卡|社保卡|银行卡|出生证明|结婚证|产检资料|入院资料|入院凭证|病历|就诊资料|疫苗记录|健康记录|处方药)/.test(name);
}
type CommerceEvent = { type: string; payload: Record<string, unknown> };
type CommerceResponse = { shopping_session_id: string; final_text: string; events: CommerceEvent[]; detail?: string };
type MemoryPreference = { kind: 'like' | 'dislike'; statement: string; created_at?: string };
type InventoryItem = { item: string; quantity: string; condition: string; updated_at?: string };
type TemplateCandidate = { candidate_id: string; status: string; created_at: string; sections: ChecklistSection[]; evaluation_passed?: boolean | null; evaluation?: { checks?: Record<string, boolean>; module_count?: number; item_count?: number }; published_scene_id?: string; published_version?: number | null; reviewer?: string; active?: boolean };
type QuickFeedbackKind = 'checklist_usefulness' | 'purchase_outcome';
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

async function readApiJson<T>(response: Response): Promise<T> {
  if (!(response.headers.get('content-type') || '').includes('application/json')) {
    throw new Error('本地后端尚未加载 ShopMind MVP 接口；请重启隔离后端后重试。');
  }
  return await response.json() as T;
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
    must_buy: stringList(planData.must_buy), optional: stringList(planData.optional), prepared: stringList(planData.prepared),
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
const CHAT_SESSION_KEY = 'shopmind.session';
const VERSION = 'live-chat-v8-sse-agentscope';
const columns: (keyof TestRun)[] = ['test_id', 'tester_id', 'scenario_id', 'version', 'device_type', 'start_time', 'end_time', 'duration_sec', 'first_feedback_sec', 'first_checklist_sec', 'first_committed_checklist_sec', 'first_products_sec', 'turn_end_sec', 'user_turns', 'assistant_turns', 'clarify_turns', 'plan_updates', 'plan_task_slots', 'plan_must_buy_slots', 'plan_optional_slots', 'plan_reusable_items', 'plan_not_buy_items', 'plan_unknown_items', 'budget_limit_cny', 'minimum_required_total_cny', 'optional_total_cny', 'budget_status', 'compare_selection_actions', 'model_compare_requests', 'product_candidates_returned', 'retrieval_actions', 'retrieval_strategies', 'conversation_transcript', 'task_completed', 'completion_step', 'abandon_reason', 'satisfaction_1to5', 'ease_1to5', 'trust_1to5', 'plan_completeness_1to5', 'omission_confidence_1to5', 'budget_understanding_1to5', 'tradeoff_understanding_1to5', 'decision_confidence_1to5', 'stuck_step', 'error_count'];

function readRuns(): TestRun[] {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'); } catch { return []; }
}

function readSessionId() {
  const saved = localStorage.getItem(CHAT_SESSION_KEY) || sessionStorage.getItem(CHAT_SESSION_KEY);
  if (saved) { localStorage.setItem(CHAT_SESSION_KEY, saved); return saved; }
  const created = newId('session');
  localStorage.setItem(CHAT_SESSION_KEY, created);
  return created;
}

function readSavedChat(sessionId: string) {
  try {
    const value = JSON.parse(localStorage.getItem(`shopmind.chat:${sessionId}`) || '{}');
    return {
      messages: Array.isArray(value.messages) ? value.messages as Message[] : [],
      result: value.result as Result | undefined,
      slotStatus: value.slotStatus && typeof value.slotStatus === 'object' ? Object.fromEntries(Object.entries(value.slotStatus).map(([key, status]) => [key, status === 'buy' ? 'prepare' : status])) as Record<string, SlotStatus> : {},
      planBudget: typeof value.planBudget === 'string' ? value.planBudget : '',
      planPriority: typeof value.planPriority === 'string' ? value.planPriority : '',
      picked: value.picked && typeof value.picked === 'object' ? value.picked as Record<string, string> : {},
      planConfirmed: Boolean(value.planConfirmed),
    };
  } catch { return { messages: [] as Message[], result: undefined, slotStatus: {}, planBudget: '', planPriority: '', picked: {}, planConfirmed: false }; }
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
  const [sessionId, setSessionId] = useState(readSessionId);
  const [buyerId, setBuyerId] = useState(() => localStorage.getItem('shopmind.user_id') || storedId(localStorage, 'shopmind.buyer', 'buyer'));
  const [messages, setMessages] = useState<Message[]>(() => readSavedChat(readSessionId()).messages);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [checklistSubmitting, setChecklistSubmitting] = useState(false);
  const [busySeconds, setBusySeconds] = useState(0);
  const [streamEvents, setStreamEvents] = useState<CommerceEvent[]>([]);
  const [streamText, setStreamText] = useState('');
  const [error, setError] = useState('');
  const [result, setResult] = useState<Result | undefined>(() => readSavedChat(readSessionId()).result);
  const [planConfirmed, setPlanConfirmed] = useState(() => readSavedChat(readSessionId()).planConfirmed);
  const [slotStatus, setSlotStatus] = useState<Record<string, SlotStatus>>(() => readSavedChat(readSessionId()).slotStatus);
  const [planBudget, setPlanBudget] = useState(() => readSavedChat(readSessionId()).planBudget);
  const [planPriority, setPlanPriority] = useState(() => readSavedChat(readSessionId()).planPriority);
  // 每个槽位只允许选一件：group name -> product id。总价只按已选商品计算。
  const [picked, setPicked] = useState<Record<string, string>>(() => readSavedChat(readSessionId()).picked);
  const [testerId, setTesterId] = useState('');
  const [scenarioId, setScenarioId] = useState('');
  const [runs, setRuns] = useState<TestRun[]>(readRuns);
  const [activeRun, setActiveRun] = useState<TestRun | null>(() => {
    try { return JSON.parse(localStorage.getItem(`${STORAGE_KEY}-active`) || 'null'); } catch { return null; }
  });
  const [testOpen, setTestOpen] = useState(false);
  const [survey, setSurvey] = useState({ task_completed: 'partial', completion_step: 'understood', abandon_reason: '', satisfaction_1to5: '', ease_1to5: '', trust_1to5: '', plan_completeness_1to5: '', omission_confidence_1to5: '', budget_understanding_1to5: '', tradeoff_understanding_1to5: '', decision_confidence_1to5: '', stuck_step: '' });
  const [authToken, setAuthToken] = useState(() => localStorage.getItem('shopmind.auth_token') || '');
  const [accountName, setAccountName] = useState('');
  const [accountPassword, setAccountPassword] = useState('');
  const [authMessage, setAuthMessage] = useState('');
  const [memoryConsent, setMemoryConsent] = useState(false);
  const [memoryPreferences, setMemoryPreferences] = useState<MemoryPreference[]>([]);
  const [inventory, setInventory] = useState<InventoryItem[]>([]);
  const [newPreference, setNewPreference] = useState('');
  const [newPreferenceKind, setNewPreferenceKind] = useState<'like' | 'dislike'>('like');
  const [newInventoryItem, setNewInventoryItem] = useState('');
  const [newInventoryQuantity, setNewInventoryQuantity] = useState('');
  const [feedbackSaved, setFeedbackSaved] = useState<Record<string, string>>(() => {
    try { return JSON.parse(localStorage.getItem(`shopmind.feedback:${readSessionId()}`) || '{}'); } catch { return {}; }
  });
  const [candidateSubmitted, setCandidateSubmitted] = useState(() => localStorage.getItem(`shopmind.candidate:${readSessionId()}`) || '');
  const [reviewCandidates, setReviewCandidates] = useState<TemplateCandidate[]>([]);
  const [publishedCandidates, setPublishedCandidates] = useState<TemplateCandidate[]>([]);
  const [reviewMessage, setReviewMessage] = useState('');
  const [reviewDraft, setReviewDraft] = useState<Record<string, { scene_id: string; name: string; core_use: string; match_terms: string; reviewer: string }>>({});
  const base = import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? '' : location.origin);
  const authHeaders = (token = authToken): Record<string, string> => token ? { Authorization: `Bearer ${token}` } : {};

  async function loadMemory(token = authToken) {
    if (!token) { setMemoryConsent(false); setMemoryPreferences([]); setInventory([]); return; }
    try {
      const response = await fetch(`${base}/commerce/mvp/memory`, { headers: authHeaders(token) });
      if (!response.ok) throw new Error('读取授权记忆失败');
      const data = await readApiJson<{ consent: boolean; preferences: MemoryPreference[]; inventory: InventoryItem[] }>(response);
      setMemoryConsent(data.consent); setMemoryPreferences(data.preferences); setInventory(data.inventory);
    } catch (e) { setAuthMessage(e instanceof Error ? e.message : '读取记忆失败'); }
  }

  async function authenticate(register: boolean) {
    setAuthMessage('');
    try {
      const response = await fetch(`${base}/commerce/mvp/auth/${register ? 'register' : 'login'}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: accountName, password: accountPassword }),
      });
      const data = await readApiJson<{ detail?: string; user_id?: string; token?: string }>(response);
      if (!response.ok || !data.token || !data.user_id) throw new Error(data.detail || '登录失败');
      localStorage.setItem('shopmind.auth_token', data.token);
      localStorage.setItem('shopmind.user_id', data.user_id);
      setAuthToken(data.token); setBuyerId(data.user_id); setAccountPassword('');
      setAuthMessage(register ? '账号已创建；长期记忆默认关闭。' : '已登录；长期记忆默认关闭。');
      await loadMemory(data.token);
    } catch (e) { setAuthMessage(e instanceof Error ? e.message : '登录失败'); }
  }

  async function logout() {
    try { await fetch(`${base}/commerce/mvp/auth/logout`, { method: 'POST', headers: authHeaders() }); } catch { /* local sign-out still completes */ }
    localStorage.removeItem('shopmind.auth_token'); localStorage.removeItem('shopmind.user_id');
    setAuthToken(''); setBuyerId(storedId(localStorage, 'shopmind.buyer', 'buyer'));
    setMemoryConsent(false); setMemoryPreferences([]); setInventory([]); setAuthMessage('已退出登录。游客会话仍保存在此浏览器。');
  }

  async function toggleMemoryConsent(enabled: boolean) {
    if (!authToken) return;
    const response = await fetch(`${base}/commerce/mvp/memory/consent`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ enabled }),
    });
    const data = await readApiJson<{ detail?: string; consent?: boolean }>(response);
    if (!response.ok) throw new Error(data.detail || '授权设置失败');
    setMemoryConsent(Boolean(data.consent));
    await loadMemory();
  }

  async function addPreference() {
    if (!newPreference.trim()) return;
    const response = await fetch(`${base}/commerce/mvp/memory/preferences`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ kind: newPreferenceKind, statement: newPreference.trim() }),
    });
    const data = await readApiJson<{ detail?: string }>(response);
    if (!response.ok) throw new Error(data.detail || '偏好保存失败');
    setNewPreference(''); await loadMemory();
  }

  async function addInventory() {
    if (!newInventoryItem.trim()) return;
    const response = await fetch(`${base}/commerce/mvp/memory/inventory`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ item: newInventoryItem.trim(), quantity: newInventoryQuantity.trim() }),
    });
    const data = await readApiJson<{ detail?: string }>(response);
    if (!response.ok) throw new Error(data.detail || '库存保存失败');
    setNewInventoryItem(''); setNewInventoryQuantity(''); await loadMemory();
  }

  async function deleteInventory(item: string) {
    const response = await fetch(`${base}/commerce/mvp/memory/inventory/${encodeURIComponent(item)}`, { method: 'DELETE', headers: authHeaders() });
    if (!response.ok) throw new Error('删除库存记忆失败');
    await loadMemory();
  }

  async function forgetAllMemory() {
    const response = await fetch(`${base}/commerce/mvp/memory`, { method: 'DELETE', headers: authHeaders() });
    if (!response.ok) throw new Error('清除记忆失败');
    await loadMemory(); setAuthMessage('已清除库存与偏好记忆，授权已关闭。');
  }

  async function recordQuickFeedback(kind: QuickFeedbackKind, value: string) {
    const key = kind;
    try {
      const response = await fetch(`${base}/commerce/mvp/feedback`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, kind, value }),
      });
    const data = await readApiJson<{ detail?: string }>(response);
      if (!response.ok) throw new Error(data.detail || '反馈提交失败');
      setFeedbackSaved(current => ({ ...current, [key]: value }));
      setAuthMessage('反馈已记录，可随时更新；购买结果是用户自报，不代表真实订单。');
    } catch (e) { setError(e instanceof Error ? e.message : '反馈提交失败'); }
  }

  async function submitCandidate() {
    if (!result?.plan.checklist_sections?.length) return;
    try {
      const sections = result.plan.checklist_sections.map(section => ({
        title: section.title,
        required: section.required.map(({ name, quantity, condition, purchasable }) => ({ name, quantity, condition, purchasable })),
        optional: section.optional.map(({ name, quantity, condition, purchasable }) => ({ name, quantity, condition, purchasable })),
      }));
      const response = await fetch(`${base}/commerce/mvp/template-candidates`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ anonymized: true, sections }),
      });
      const data = await readApiJson<{ detail?: string; candidate_id?: string }>(response);
      if (!response.ok || !data.candidate_id) throw new Error(data.detail || '匿名候选提交失败');
      setCandidateSubmitted(data.candidate_id); setReviewMessage(`匿名候选 ${data.candidate_id} 已进入待人工审核池。`);
    } catch (e) { setError(e instanceof Error ? e.message : '候选提交失败'); }
  }

  async function loadReviewCandidates() {
    const [pendingResponse, approvedResponse] = await Promise.all([
      fetch(`${base}/commerce/mvp/template-candidates?status=pending`),
      fetch(`${base}/commerce/mvp/template-candidates?status=approved`),
    ]);
    const data = await readApiJson<{ items?: TemplateCandidate[]; detail?: string }>(pendingResponse);
    const approvedData = await readApiJson<{ items?: TemplateCandidate[]; detail?: string }>(approvedResponse);
    if (!pendingResponse.ok || !approvedResponse.ok) throw new Error(data.detail || approvedData.detail || '读取审核候选失败');
    const items = data.items || [];
    setPublishedCandidates(approvedData.items || []);
    setReviewCandidates(items);
    setReviewDraft(current => {
      const next = { ...current };
      for (const candidate of items) if (!next[candidate.candidate_id]) next[candidate.candidate_id] = {
        scene_id: `scene_${candidate.candidate_id.slice(-8)}`,
        name: candidate.sections[0]?.title || '新场景', core_use: '', match_terms: '', reviewer: 'local-reviewer',
      };
      return next;
    });
  }

  async function reviewCandidate(candidate: TemplateCandidate, action: 'approve' | 'reject') {
    const draft = reviewDraft[candidate.candidate_id];
    if (!draft) return;
    const response = await fetch(`${base}/commerce/mvp/template-candidates/${candidate.candidate_id}/review`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action, reviewer: draft.reviewer, scene_id: draft.scene_id, name: draft.name,
        core_use: draft.core_use, semantic_description: `${draft.name}；${draft.core_use}`,
        match_terms: draft.match_terms.split(/[，,]/).map(term => term.trim()).filter(Boolean),
        note: action === 'reject' ? '人工审核未通过' : '本地人工审核发布',
      }),
    });
    const data = await readApiJson<{ detail?: string; status?: string; published_version?: number }>(response);
    if (!response.ok) throw new Error(data.detail || '候选审核失败');
    setReviewMessage(action === 'approve' ? `已发布 ${draft.scene_id} v${data.published_version}；新会话可命中该模板。` : '候选已拒绝。');
    await loadReviewCandidates();
  }

  async function evaluateCandidate(candidate: TemplateCandidate) {
    const response = await fetch(`${base}/commerce/mvp/template-candidates/${candidate.candidate_id}/evaluate`, { method: 'POST' });
    const data = await readApiJson<{ detail?: string; evaluation_passed?: boolean; evaluation?: { checks?: Record<string, boolean> } }>(response);
    if (!response.ok) throw new Error(data.detail || '结构评测失败');
    const passed = Boolean(data.evaluation_passed);
    setReviewMessage(passed ? `结构评测通过：${candidate.candidate_id}，现在可进入人工审核。` : `结构评测未通过：${Object.entries(data.evaluation?.checks || {}).filter(([, ok]) => !ok).map(([name]) => name).join('、')}`);
    await loadReviewCandidates();
  }

  async function rollbackTemplate(candidate: TemplateCandidate) {
    if (!candidate.published_scene_id) return;
    const response = await fetch(`${base}/commerce/mvp/templates/${candidate.published_scene_id}/rollback`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reviewer: 'local-reviewer', note: `人工回退候选 ${candidate.candidate_id}` }),
    });
    const data = await readApiJson<{ detail?: string; restored_version?: number | null }>(response);
    if (!response.ok) throw new Error(data.detail || '模板回退失败');
    setReviewMessage(`模板 ${candidate.published_scene_id} 已回退，恢复版本 ${data.restored_version ?? '内置版本/无动态版本'}。`);
    await loadReviewCandidates();
  }

  function showResult(data: Result, initializeChecklist = false) {
    setResult(data);
    if (initializeChecklist && data.plan.stage === 'plan') {
      const statuses: Record<string, SlotStatus> = {};
      for (const [prefix, items] of [['required', data.plan.tasks ?? []], ['optional', data.plan.optional ?? []]] as const) {
        for (const item of items) statuses[`${prefix}:${item}`] = data.plan.reusable?.includes(item) ? 'have'
          : data.plan.prepared?.includes(item) || data.plan.must_buy?.includes(item) || data.plan.optional?.includes(item) ? 'prepare'
            : data.plan.not_buy?.includes(item) ? 'not_needed' : 'unknown';
      }
      setSlotStatus(statuses);
      setPlanBudget(data.plan.budget ? String(data.plan.budget) : '');
      const preferences = data.plan.preferences?.join(' ') || '';
      setPlanPriority(/轻装|轻便|少占空间/.test(preferences) ? 'lightweight' : /省钱|实惠|性价比|预算/.test(preferences) ? 'value' : /品质|耐用/.test(preferences) ? 'quality' : /周全|齐全/.test(preferences) ? 'complete' : /美观|颜值/.test(preferences) ? 'appearance' : '');
    }
  }
  useEffect(() => { localStorage.setItem(CHAT_SESSION_KEY, sessionId); sessionStorage.setItem(CHAT_SESSION_KEY, sessionId); }, [sessionId]);
  useEffect(() => { localStorage.setItem(`shopmind.chat:${sessionId}`, JSON.stringify({ messages, result, slotStatus, planBudget, planPriority, picked, planConfirmed })); }, [sessionId, messages, result, slotStatus, planBudget, planPriority, picked, planConfirmed]);
  useEffect(() => { localStorage.setItem(`shopmind.feedback:${sessionId}`, JSON.stringify(feedbackSaved)); }, [sessionId, feedbackSaved]);
  useEffect(() => { if (candidateSubmitted) localStorage.setItem(`shopmind.candidate:${sessionId}`, candidateSubmitted); else localStorage.removeItem(`shopmind.candidate:${sessionId}`); }, [sessionId, candidateSubmitted]);
  useEffect(() => { if (authToken) void loadMemory(authToken); else { setMemoryConsent(false); setMemoryPreferences([]); setInventory([]); } }, [authToken]);
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
    setFeedbackSaved({}); setCandidateSubmitted('');
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
      const response = await fetch(`${base}/commerce/intents/stream`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
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
        const state = JSON.stringify([data.plan.budget, data.plan.destination, data.plan.excludes, data.plan.tasks, data.plan.must_buy, data.plan.optional, data.plan.prep, data.plan.reusable, data.plan.prepared, data.plan.not_buy, data.plan.unknowns, data.groups.map(group => [group.name, group.priority, group.products.map(product => product.id)])]);
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
    const options = CHECKLIST_STATUSES;
    return <div className={'checklist-item ' + (isOptional ? 'is-optional' : 'is-required')} key={sectionId + ':' + key}><div className="checklist-item-copy"><b>{item.name}</b>{(item.quantity || item.condition || item.note) && <small>{[item.quantity, item.condition, item.note].filter(Boolean).join(' · ')}</small>}{(item.purchasable === false || isNonPurchasableName(item.name)) && <small>非商品：按要求携带或办理，不会搜索商品</small>}</div><div role="group" aria-label={item.name + '状态'}>{options.map(([status, label]) => <button type="button" key={status} disabled={busy || Boolean(result?.preview)} className={slotStatus[key] === status ? 'selected' : ''} aria-pressed={slotStatus[key] === status} onClick={() => setSlotStatus(current => ({ ...current, [key]: current[key] === status ? 'unknown' : status }))}>{label}</button>)}</div></div>;
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
    const prepare = [...labels(required, 'required', 'prepare'), ...labels(optional, 'optional', 'prepare')];
    const nonProductPrepare = prepare.filter(item => {
      const checklistItem = result.plan.checklist_sections?.flatMap(section => [...section.required, ...section.optional]).find(entry => entry.name === item);
      return checklistItem?.purchasable === false || isNonPurchasableName(item);
    });
    const productPrepare = prepare.filter(item => !nonProductPrepare.includes(item));
    const requiredBuy = productPrepare.filter(item => required.includes(item));
    const optionalBuy = productPrepare.filter(item => optional.includes(item));
    const notNeeded = [...labels(required, 'required', 'not_needed'), ...labels(optional, 'optional', 'not_needed')];
    const unknown = [...labels(required, 'required', 'unknown'), ...labels(optional, 'optional', 'unknown')];
    const priorityLabels: Record<string, string> = { lightweight: '轻便少占空间', value: '实惠/性价比', quality: '品质耐用', complete: '准备周全', appearance: '美观协调' };
    const summary = [
      `清单已逐项确认。已有可复用：${have.join('、') || '暂无'}`,
      `需要准备：${prepare.join('、') || '暂无'}`,
      `明确不需要：${notNeeded.join('、') || '暂无'}`,
      `还没确认：${unknown.join('、') || '暂无'}`,
      planBudget ? `整套预算上限 ${planBudget} 元。` : '预算暂未提供。',
      planPriority ? `优先考虑${priorityLabels[planPriority]}。` : '请根据清单先匹配商品，并询问我最关键的取舍偏好。',
      '证件、资料等非商品只记录为需要准备，不进入商品搜索；请只为可购买的待准备物品匹配商品，已有、不需要和还没确认的项目不要推荐。商品候选后说明选择与取舍，并汇总预算。',
    ].join(' ');
    setChecklistSubmitting(true);
    void send(summary, false, false, {
      have, prepare: nonProductPrepare, must_buy: requiredBuy, optional_buy: optionalBuy,
      deferred: notNeeded, unknown,
      budget_major: planBudget ? Number(planBudget) : null, priority: planPriority,
    });
  }
  return <section className="live-section" id="demo">
    <div className="section-heading"><p className="eyebrow">SCENARIO DEMO / LIVE AGENT</p><h2>场景演示：与 ShopMind 实时对话</h2><p>本区通过本地 Commerce API 实际运行；已配置场景会优先生成清单模板，后续澄清与商品检索按实际链路调用。商品和价格来自模拟目录，不涉及真实交易。</p></div>
    <details className="account-panel">
      <summary>{authToken ? `已登录 · ${buyerId.slice(0, 16)} · 管理授权记忆` : '游客模式 · 当前会话自动保存在此浏览器 · 可选登录'}</summary>
      {!authToken ? <div className="account-controls">
        <label>账号<input value={accountName} maxLength={48} onChange={event => setAccountName(event.target.value)} placeholder="3–48 位" autoComplete="username" /></label>
        <label>密码<input type="password" value={accountPassword} maxLength={256} onChange={event => setAccountPassword(event.target.value)} placeholder="注册至少 10 位" autoComplete="current-password" /></label>
        <button type="button" disabled={!accountName.trim() || !accountPassword} onClick={() => void authenticate(false)}>登录</button>
        <button type="button" disabled={!accountName.trim() || accountPassword.length < 10} onClick={() => void authenticate(true)}>创建本地账号</button>
      </div> : <>
        <div className="memory-consent-row"><label><input type="checkbox" checked={memoryConsent} onChange={event => void toggleMemoryConsent(event.target.checked).catch(error => setAuthMessage(error instanceof Error ? error.message : '授权更新失败'))} /> 我授权 ShopMind 跨会话保存并使用我的库存与偏好</label><button type="button" onClick={() => void logout()}>退出登录</button></div>
        {!memoryConsent ? <p className="account-note">长期记忆当前关闭；开启后可在此管理。对话游客记录仍保存在本机浏览器中。</p> : <div className="memory-manager">
          <form onSubmit={event => { event.preventDefault(); void addPreference().catch(error => setAuthMessage(error instanceof Error ? error.message : '偏好保存失败')); }}>
            <select value={newPreferenceKind} onChange={event => setNewPreferenceKind(event.target.value as 'like' | 'dislike')} aria-label="偏好类型"><option value="like">偏好</option><option value="dislike">避免</option></select>
            <input value={newPreference} maxLength={200} onChange={event => setNewPreference(event.target.value)} placeholder="例如：喜欢易清洁材质" />
            <button disabled={!newPreference.trim()}>记住偏好</button>
          </form>
          <form onSubmit={event => { event.preventDefault(); void addInventory().catch(error => setAuthMessage(error instanceof Error ? error.message : '库存保存失败')); }}>
            <input value={newInventoryItem} maxLength={100} onChange={event => setNewInventoryItem(event.target.value)} placeholder="已有物品，例如：猫砂盆" />
            <input value={newInventoryQuantity} maxLength={80} onChange={event => setNewInventoryQuantity(event.target.value)} placeholder="数量/备注（可选）" />
            <button disabled={!newInventoryItem.trim()}>记住库存</button>
          </form>
          <div className="memory-columns"><div><b>已授权偏好</b>{memoryPreferences.length ? memoryPreferences.map((item, index) => <p key={`${item.kind}-${index}`}>{item.kind === 'like' ? '偏好' : '避免'}：{item.statement}</p>) : <p>暂无</p>}</div><div><b>已授权库存</b>{inventory.length ? inventory.map(item => <p key={item.item}>{item.item}{item.quantity ? ` · ${item.quantity}` : ''}<button type="button" aria-label={`删除${item.item}`} onClick={() => void deleteInventory(item.item).catch(error => setAuthMessage(error instanceof Error ? error.message : '删除失败'))}>删除</button></p>) : <p>暂无</p>}</div></div>
          <button type="button" className="memory-clear" onClick={() => void forgetAllMemory().catch(error => setAuthMessage(error instanceof Error ? error.message : '清除失败'))}>清除库存与偏好并撤回授权</button>
        </div>}
      </>}
      {authMessage && <p className="account-note" role="status">{authMessage}</p>}
    </details>
    <div className={`test-panel ${testOpen || activeRun ? 'open' : ''}`}>
      <button className="test-panel-toggle" type="button" onClick={() => setTestOpen(open => !open)} aria-expanded={testOpen || Boolean(activeRun)}><strong>前端可用性测试</strong><span>{activeRun ? `记录中 · ${activeRun.scenario_id}` : `本机已保存 ${runs.length} 条`}</span><b>{testOpen || activeRun ? '收起' : '展开测试模式'}</b></button>
      {(testOpen || activeRun) && <>
      {!activeRun ? <div className="test-start">
        <label>测试者编号<input value={testerId} onChange={e => setTesterId(e.target.value)} placeholder="例如 T01（无需填写姓名）" /></label>
        <label>场景编号<input value={scenarioId} onChange={e => setScenarioId(e.target.value)} placeholder="例如 travel-01" /></label>
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
      {['周末去海边两天，帮我准备购物清单', '第一次租房入住，不知道要准备什么', '开学了，帮我准备一份采购清单', '我第一次生产，想准备待产包', '第一次养猫，我需要准备清单'].map(q => <button disabled={busy} key={q} onClick={() => { setMessages([]); setResult(undefined); setPlanConfirmed(false); setSlotStatus({}); setPicked({}); setError(''); void send(q, false, true); }}>{q}</button>)}</div>
    <div className="live-messages" aria-live="polite">{messages.map((m, i) => <div key={i} className={`live-message ${m.role}`}><small><ChatAvatar role={m.role} />{m.role === 'user' ? '你' : 'ShopMind'}</small><p>{m.role === 'assistant' ? <>{m.content.split('\n').filter(line => !/^.+: P\d/.test(line)).join('\n').replace(/\*\*/g, '').replace(/^\s*[-*]\s+/gm, '• ')}{result?.plan.stage === 'plan' && i === messages.length - 1 ? '\n\n请在下方逐项选择已有、需要准备、还没确认或不需要。' : ''}</> : m.content}</p></div>)}
      {!messages.length && <div className="live-message assistant"><small><ChatAvatar role="assistant" />ShopMind</small><p>还没想好商品名也没关系。说说你要完成的生活任务，我会先问清会改变清单的场景条件，再列准备品类。</p></div>}
      {busy && <div className="live-status" role="status"><span className="live-spinner" />{busySeconds >= 20 ? `Agent 仍在整理方案，已等待 ${busySeconds} 秒…` : 'MainAgent 正在理解需求，SearchAgent 将按方案检索…'}</div>}
      {busy && streamText && <div className="live-message assistant streaming-reply"><small><ChatAvatar role="assistant" />ShopMind · 实时回复</small><p>{streamText}</p></div>}
      {busy && streamEvents.length > 0 && <ol className="live-event-feed" aria-label="Agent 实时处理进度">{streamEvents.filter(event => event.type !== 'token.delta').slice(-6).map((event, index) => <li key={`${event.type}-${index}`}>{eventLabel(event)}</li>)}</ol>}
      {error && <div role="alert">{error} {messages[messages.length - 1]?.role === 'user' && <button onClick={() => void send(messages[messages.length - 1].content, true)}>重试</button>}</div>}
    </div>
    {result && <><div className="insight-chips"><span>目标：{result.plan.goal}</span><span>总预算：{result.plan.budget ?? '待确认'} {result.plan.budget ? 'CNY（商品价）' : ''}</span><span>配送地：{result.plan.destination ?? '待确认'}</span>{result.plan.excludes.map(x => <span key={x}>不买：{x}</span>)}</div>
      {(result.plan.stage === 'plan' || result.plan.stage === 'confirm') && !result.groups.length && <section className={'checklist-step' + (result.plan.template_id === 'maternity_hospital_bag' ? ' maternity-checklist' : '')} aria-label="逐项核对准备清单" aria-busy={busy || Boolean(result.preview)}>
        <header><div><strong>{result.preview ? '清单草稿 · 正在生成' : '逐项核对清单'}</strong><span>{result.preview ? '正在整理分组清单；草稿暂不作为正式核对项' : '点选状态即可，不用把已有物品逐条打字'}</span></div><small>未标记的项目保持“还没确认”</small></header>
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
            {CHECKLIST_STATUSES.map(([status, label]) => <button type="button" key={status} disabled={busy || Boolean(result.preview)} className={slotStatus[key] === status ? 'selected' : ''} aria-pressed={slotStatus[key] === status} onClick={() => setSlotStatus(current => ({ ...current, [key]: current[key] === status ? 'unknown' : status }))}>{label}</button>)}
          </div></div>;
        })}</div>}
        {!result.preview && !result.plan.checklist_sections?.length && !!result.plan.optional?.length && <div className="checklist-group optional-slots"><h3>按需准备</h3>{result.plan.optional.map(item => {
          const key = `optional:${item}`;
          return <div className="checklist-item is-optional" key={key}><b>{item}</b><div role="group" aria-label={`${item}状态`}>
            {CHECKLIST_STATUSES.map(([status, label]) => <button type="button" key={status} disabled={busy || Boolean(result.preview)} className={slotStatus[key] === status ? 'selected' : ''} aria-pressed={slotStatus[key] === status} onClick={() => setSlotStatus(current => ({ ...current, [key]: current[key] === status ? 'unknown' : status }))}>{label}</button>)}
          </div></div>;
        })}</div>}
        {!!result.plan.prep?.length && <div className="checklist-reminders"><h3>出发前提醒（不推荐商品）</h3><p>{result.plan.prep.join(' · ')}</p></div>}
        <div className="checklist-preferences"><label>预算上限（商品价）<span><input type="number" min="1" disabled={busy} value={planBudget} onChange={event => setPlanBudget(event.target.value)} placeholder="可留空" /> 元</span></label><label>优先考虑<select disabled={busy} value={planPriority} onChange={event => setPlanPriority(event.target.value)}><option value="">暂不选，让 ShopMind 根据清单追问</option><option value="lightweight">轻便少占空间</option><option value="value">实惠 / 性价比</option><option value="quality">品质耐用</option><option value="complete">准备周全</option><option value="appearance">美观协调</option></select></label></div>
        <div className="checklist-next"><span>{result.preview ? '清单仍在生成，确认按钮暂不可用。' : checklistReady ? '选择“需要准备”后，证件/资料等非商品只记录准备；可购买的物品才会进入商品匹配。' : '请先标记至少一项；其他项目可以保持还没确认。'}</span><button type="button" disabled={busy || Boolean(result.preview) || !checklistReady} aria-busy={checklistSubmitting} onClick={confirmChecklist}>{checklistSubmitting ? <><span className="live-spinner" aria-hidden="true" />正在处理清单… {busySeconds} 秒</> : '确认准备清单 →'}</button></div>
        {checklistSubmitting && <div className="checklist-progress" role="status">{busySeconds >= 30 ? '本轮耗时较长，仍在等待 Agent 返回；你的选择已保留，请勿重复提交。' : '正在提交准备状态；只为可购买物品匹配商品，无需重复点击。'}</div>}
      </section>}
      {(result.plan.checklist_sections?.length ?? 0) > 0 && result.plan.stage !== 'clarify' && <section className="quick-feedback" aria-label="清单效果反馈">
        <div className="quick-feedback-row"><div><strong>这份清单有用吗？</strong><small>一键记录，可在同一会话中更新</small></div><div className="feedback-options">
          <button type="button" aria-pressed={feedbackSaved.checklist_usefulness === 'helpful'} onClick={() => void recordQuickFeedback('checklist_usefulness', 'helpful')}>有帮助</button>
          <button type="button" aria-pressed={feedbackSaved.checklist_usefulness === 'needs_work'} onClick={() => void recordQuickFeedback('checklist_usefulness', 'needs_work')}>还不够准确</button>
        </div></div>
        <div className="quick-feedback-row"><div><strong>完成后，需要的商品买到了吗？</strong><small>可稍后回来更新；用户自报结果，不代表真实订单</small></div><div className="feedback-options">
          <button type="button" aria-pressed={feedbackSaved.purchase_outcome === 'bought'} onClick={() => void recordQuickFeedback('purchase_outcome', 'bought')}>买到了</button>
          <button type="button" aria-pressed={feedbackSaved.purchase_outcome === 'not_found'} onClick={() => void recordQuickFeedback('purchase_outcome', 'not_found')}>没找到</button>
          <button type="button" aria-pressed={feedbackSaved.purchase_outcome === 'chose_not_to_buy'} onClick={() => void recordQuickFeedback('purchase_outcome', 'chose_not_to_buy')}>决定不买</button>
          <button type="button" aria-pressed={feedbackSaved.purchase_outcome === 'not_yet'} onClick={() => void recordQuickFeedback('purchase_outcome', 'not_yet')}>还没买/未决定</button>
        </div></div>
        {!result.plan.template_id && (result.plan.checklist_sections?.length ?? 0) >= 2 && (result.plan.checklist_sections?.length ?? 0) <= 6 && <div className="candidate-share">
          <p>匿名贡献：仅提交模块标题、物品名、数量和条件，不提交原始对话、账号或会话编号。提交后需人工审核，不会自动成为模板。</p>
          <button type="button" disabled={Boolean(candidateSubmitted)} onClick={() => void submitCandidate()}>{candidateSubmitted ? '已匿名提交，等待人工审核' : '同意匿名分享这份清单作为模板候选'}</button>
        </div>}
      </section>}
      {result.plan.stage !== 'clarify' && result.plan.stage !== 'plan' && <div className="plan-readout">
        <div><b>必备任务槽位</b><span>{result.plan.tasks?.join(' · ') || '按当前场景整理'}</span></div>
        <div><b>已有 / 可复用</b><span>{result.plan.reusable?.join(' · ') || '已有物品尚未确认'}</span></div>
        <div><b>需要携带 / 办理（不搜商品）</b><span>{result.plan.prepared?.join(' · ') || '暂无'}</span></div>
        <div><b>明确不需要</b><span>{result.plan.not_buy?.join(' · ') || '暂无'}</span></div>
        <div><b>按需项目与未确认项</b><span>{[...(result.plan.optional ?? []), ...(result.plan.unknowns ?? [])].join(' · ') || '暂无额外项'}</span></div>
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
    <details className="review-panel">
      <summary>本地人工审核模板候选</summary>
      <div className="review-panel-body"><p>仅本机可访问。审核通过时需补齐模板名称、核心用途和匹配词；通过后生成新版本并供后续匹配。</p>
        <button type="button" onClick={() => void loadReviewCandidates().then(() => setReviewMessage('已载入待审核候选。')).catch(error => setReviewMessage(error instanceof Error ? error.message : '读取失败'))}>载入待审核候选</button>
        {reviewMessage && <p role="status">{reviewMessage}</p>}
        {!reviewCandidates.length ? <p>当前没有已载入的待审候选。</p> : reviewCandidates.map(candidate => {
          const draft = reviewDraft[candidate.candidate_id];
          if (!draft) return null;
          const update = (key: keyof typeof draft, value: string) => setReviewDraft(current => ({ ...current, [candidate.candidate_id]: { ...draft, [key]: value } }));
          return <article className="review-candidate" key={candidate.candidate_id}>
            <h3>{candidate.candidate_id} · 待审核</h3>
            {candidate.sections.map(section => <p key={section.id}><b>{section.title}</b>：必备 {section.required.map(item => item.name).join('、')}；按需 {section.optional.map(item => item.name).join('、') || '无'}</p>)}
            <p>结构评测：{candidate.evaluation_passed === true ? `通过（${candidate.evaluation?.module_count} 模块 / ${candidate.evaluation?.item_count} 项）` : candidate.evaluation_passed === false ? '未通过，需提交修订后的匿名候选' : '尚未运行'}</p>
            <div className="review-fields">
              <label>模板 ID<input value={draft.scene_id} onChange={event => update('scene_id', event.target.value)} /></label>
              <label>模板名称<input value={draft.name} onChange={event => update('name', event.target.value)} /></label>
              <label>核心用途<input value={draft.core_use} onChange={event => update('core_use', event.target.value)} /></label>
              <label>匹配词（逗号分隔）<input value={draft.match_terms} onChange={event => update('match_terms', event.target.value)} placeholder="至少两个" /></label>
              <label>审核人<input value={draft.reviewer} onChange={event => update('reviewer', event.target.value)} /></label>
            </div>
            <div className="feedback-options"><button type="button" onClick={() => void evaluateCandidate(candidate).catch(error => setReviewMessage(error instanceof Error ? error.message : '评测失败'))}>运行结构评测</button><button type="button" disabled={!candidate.evaluation_passed} onClick={() => void reviewCandidate(candidate, 'approve').catch(error => setReviewMessage(error instanceof Error ? error.message : '审核失败'))}>评测通过后人工发布</button><button type="button" onClick={() => void reviewCandidate(candidate, 'reject').catch(error => setReviewMessage(error instanceof Error ? error.message : '审核失败'))}>拒绝候选</button></div>
          </article>;
        })}
        {!!publishedCandidates.length && <div className="published-candidates"><h3>已发布版本</h3>{publishedCandidates.map(candidate => <article key={candidate.candidate_id}>
          <span>{candidate.published_scene_id} · v{candidate.published_version} · {candidate.active ? '当前生效' : '已停用'} · 审核人 {candidate.reviewer}</span>
          <button type="button" disabled={!candidate.active} onClick={() => void rollbackTemplate(candidate).catch(error => setReviewMessage(error instanceof Error ? error.message : '回退失败'))}>回退该模板</button>
        </article>)}</div>}
      </div>
    </details>
    <p className="live-note">商品、品牌、价格、库存与配送范围均为虚拟演示数据；方案预算按商品价估算，未确认配送地时不能判断可购买性。</p>
  </section>;
}
