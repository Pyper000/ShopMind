import catalogData from './catalog.json' with { type: 'json' };
import taxonomy from './category-taxonomy.json' with { type: 'json' };

const active = new Set();
const traffic = new Map();
const subcategories = new Map(Object.entries(taxonomy).flatMap(([category, groups]) =>
  Object.entries(groups).flatMap(([subcategory, ids]) => ids.map(id => [id, { category, subcategory }]))));
const catalog = catalogData.map(product => ({ ...product, ...subcategories.get(product.id) }));
const destinations = new Set(catalog.flatMap(p => p.ships_to));
let catalogVectorsPromise;
let qdrantReadyPromise;

export function filterCatalog(plan) {
  const excluded = [...(plan.excludes || []), ...(plan.reusable || []), ...(plan.not_buy || [])].filter(Boolean);
  return catalog.filter(p => p.stock > 0 &&
    (!plan.destination || p.ships_to.includes(plan.destination)) &&
    (plan.budget == null || (p.currency === 'CNY' && p.price <= plan.budget)) &&
    !excluded.some(term => `${p.title} ${p.description}`.includes(term)));
}

function parseJson(text) {
  return JSON.parse(text.replace(/^\s*```(?:json)?\s*/i, '').replace(/\s*```\s*$/, ''));
}

function normalizeSchemeState(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const strings = key => Array.isArray(value[key]) ? value[key].filter(item => typeof item === 'string' && item.trim()).slice(0, 12) : [];
  return {
    state_version: Number.isInteger(value.state_version) && value.state_version >= 0 ? value.state_version : 0,
    goal: typeof value.goal === 'string' ? value.goal.slice(0, 120) : '',
    core_use: typeof value.core_use === 'string' ? value.core_use.slice(0, 120) : '',
    people: Number.isInteger(value.people) && value.people > 0 ? value.people : null,
    budget: Number.isFinite(value.budget) && value.budget > 0 ? value.budget : null,
    destination: destinations.has(value.destination) ? value.destination : null,
    tasks: strings('tasks'), must_buy: strings('must_buy'), optional: strings('optional'),
    reusable: strings('reusable'), not_buy: strings('not_buy'), unknowns: strings('unknowns'),
    excludes: strings('excludes'), preferences: strings('preferences'),
  };
}

function slotTokens(text) {
  const tokens = new Set();
  for (const part of String(text).toLowerCase().match(/[a-z0-9]+|[\u3400-\u9fff]+/g) || []) {
    if (/^[\u3400-\u9fff]+$/.test(part)) {
      for (let index = 0; index < part.length - 1; index += 1) tokens.add(part.slice(index, index + 2));
      if (part.length === 1) tokens.add(part);
    } else tokens.add(part);
  }
  return tokens;
}

function relevantProducts(products, plan, fallbackQuery, onlySlots = null) {
  const purchaseSlots = [...new Set([...(plan.must_buy || []), ...(plan.optional || [])])];
  const slots = onlySlots || (purchaseSlots.length ? purchaseSlots : [...new Set(plan.tasks || [])]);
  const aliases = {
    '衣物收纳': ['旅行收纳袋', '压缩收纳', '衣物分装'],
    '随身收纳': ['收纳袋', '收纳包', '证件收纳'],
    '收纳': ['收纳袋', '收纳包', '衣物分装'],
    '轻量防晒': ['防晒衣', '防晒霜', '遮阳帽'],
    '主灯': ['露营灯', '便携灯'],
    '备用灯': ['手电', '钥匙扣手电'],
  };
  const queries = slots.length ? slots.flatMap(slot => [slot, ...(aliases[slot] || [])]) : [fallbackQuery];
  const picked = new Map();
  for (const query of queries) {
    const normalizedQuery = String(query).toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
    const queryTokens = slotTokens(query);
    const ranked = products.map(product => {
      const text = `${product.title} ${product.brand} ${product.category} ${product.subcategory} ${product.description} ${(product.highlights || []).join(' ')}`;
      const normalizedText = text.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
      const tokens = slotTokens(text);
      const overlap = [...queryTokens].filter(token => tokens.has(token)).length;
      return { product, score: overlap + (normalizedQuery && normalizedText.includes(normalizedQuery) ? 100 : 0) };
    }).filter(item => item.score > 0).sort((left, right) => right.score - left.score || left.product.price - right.product.price);
    for (const { product } of ranked.slice(0, 6)) picked.set(product.id, product);
  }
  return [...picked.values()];
}

function balancedCandidates(products, plan) {
  const slots = [...new Set([...(plan.must_buy || []), ...(plan.optional || [])])];
  if (!slots.length) return products;
  const aliases = {
    '衣物收纳': ['旅行收纳袋', '压缩收纳', '衣物分装'],
    '随身收纳': ['收纳袋', '收纳包', '证件收纳'],
    '收纳': ['收纳袋', '收纳包', '衣物分装'],
    '轻量防晒': ['防晒衣', '防晒霜', '遮阳帽'],
    '主灯': ['露营灯', '便携灯'],
    '备用灯': ['手电', '钥匙扣手电'],
  };
  const bySlot = new Map();
  for (const slot of slots) {
    const queries = [slot, ...(aliases[slot] || [])];
    const ranked = new Map();
    for (const query of queries) {
      const normalizedQuery = String(query).toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
      const queryTokens = slotTokens(query);
      products.forEach((product, index) => {
        const text = `${product.title} ${product.brand} ${product.category} ${product.subcategory} ${product.description} ${(product.highlights || []).join(' ')}`;
        const normalizedText = text.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
        const productTokens = slotTokens(text);
        const overlap = [...queryTokens].filter(token => productTokens.has(token)).length;
        const exact = normalizedQuery && normalizedText.includes(normalizedQuery) ? 100 : 0;
        const score = overlap + exact;
        const previous = ranked.get(product.id);
        if (!previous || score > previous.score) ranked.set(product.id, { product, index, score });
      });
    }
    bySlot.set(slot, [...ranked.values()].sort((left, right) => right.score - left.score || left.index - right.index)
      .slice(0, 5).map(item => item.product));
  }
  const picked = new Map();
  for (let rank = 0; rank < 5; rank += 1) {
    for (const productsForSlot of bySlot.values()) {
      const product = productsForSlot[rank];
      if (product) picked.set(product.id, product);
    }
  }
  return [...picked.values()];
}

function retrievalSignature(plan) {
  return JSON.stringify({
    slots: [...new Set([...(plan.must_buy || []), ...(plan.optional || [])])].sort(),
    destination: plan.destination || null, budget: plan.budget ?? null,
    excludes: [...(plan.excludes || []), ...(plan.reusable || []), ...(plan.not_buy || [])].sort(),
  });
}

async function embedding(inputs) {
  const key = process.env.EMBEDDING_API_KEY || process.env.LLM_API_KEY;
  const base = (process.env.EMBEDDING_BASE_URL || process.env.LLM_BASE_URL || 'https://api.siliconflow.cn/v1').replace(/\/$/, '');
  const response = await fetch(`${base}/embeddings`, {
    method: 'POST', headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: process.env.EMBEDDING_MODEL || 'BAAI/bge-m3', input: inputs }),
    signal: AbortSignal.timeout(12000),
  });
  if (!response.ok) throw new Error(`EMBEDDING_PROVIDER_${response.status}`);
  const body = await response.json();
  if (!Array.isArray(body.data) || body.data.length !== inputs.length) throw new Error('EMBEDDING_RESPONSE_INVALID');
  return body.data.sort((a, b) => a.index - b.index).map(item => item.embedding);
}

async function catalogVectors() {
  if (!catalogVectorsPromise) catalogVectorsPromise = (async () => {
    const batches = [];
    for (let start = 0; start < catalog.length; start += 8) batches.push(catalog.slice(start, start + 8));
    const embedded = [];
    for (let start = 0; start < batches.length; start += 4) {
      const group = batches.slice(start, start + 4);
      embedded.push(...await Promise.all(group.map(batch => embedding(batch.map(p => `${p.title} ${p.brand} ${p.category} ${p.subcategory} ${p.description} ${(p.highlights || []).join(' ')}`)))));
    }
    const vectors = embedded.flat();
    const byId = new Map(catalog.map((product, index) => [product.id, vectors[index]]));
    if (process.env.QDRANT_URL) await ensureQdrantIndex(vectors);
    return byId;
  })().catch(error => { catalogVectorsPromise = undefined; throw error; });
  return catalogVectorsPromise;
}

async function qdrant(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(process.env.QDRANT_API_KEY ? { 'api-key': process.env.QDRANT_API_KEY } : {}) };
  return fetch(`${process.env.QDRANT_URL.replace(/\/$/, '')}${path}`, { ...options, headers: { ...headers, ...options.headers }, signal: AbortSignal.timeout(8000) });
}

async function ensureQdrantIndex(vectors) {
  if (!qdrantReadyPromise) qdrantReadyPromise = (async () => {
    const name = encodeURIComponent(process.env.QDRANT_COLLECTION || 'shopmind_products');
    let response = await qdrant(`/collections/${name}`);
    if (response.status === 404) {
      response = await qdrant(`/collections/${name}`, { method: 'PUT', body: JSON.stringify({ vectors: { size: vectors[0].length, distance: 'Cosine' } }) });
      if (!response.ok) throw new Error(`QDRANT_CREATE_${response.status}`);
    } else if (!response.ok) throw new Error(`QDRANT_COLLECTION_${response.status}`);
    for (let start = 0; start < catalog.length; start += 64) {
      const points = catalog.slice(start, start + 64).map((product, offset) => ({ id: start + offset + 1, vector: vectors[start + offset], payload: { product_id: product.id } }));
      response = await qdrant(`/collections/${name}/points?wait=true`, { method: 'PUT', body: JSON.stringify({ points }) });
      if (!response.ok) throw new Error(`QDRANT_UPSERT_${response.status}`);
    }
    return name;
  })().catch(error => { qdrantReadyPromise = undefined; throw error; });
  return qdrantReadyPromise;
}

function cosine(left, right) {
  let dot = 0, a = 0, b = 0;
  for (let i = 0; i < Math.min(left.length, right.length); i += 1) { dot += left[i] * right[i]; a += left[i] ** 2; b += right[i] ** 2; }
  return a && b ? dot / Math.sqrt(a * b) : 0;
}

async function semanticRecall(products, plan, onlySlots = null) {
  const vectors = await catalogVectors();
  const slots = [...new Set([...(plan.must_buy || []), ...(plan.optional || [])])];
  const queries = onlySlots || (slots.length ? slots : [plan.goal || '']);
  const queryVectors = await embedding(queries.map(query => `${query} ${plan.goal || ''}`));
  const recalled = new Map();
  if (process.env.QDRANT_URL) {
    try {
      const name = await qdrantReadyPromise;
      for (const queryVector of queryVectors) {
        const response = await qdrant(`/collections/${name}/points/query`, { method: 'POST', body: JSON.stringify({ query: queryVector, limit: catalog.length, with_payload: true }) });
        if (!response.ok) throw new Error(`QDRANT_QUERY_${response.status}`);
        const body = await response.json();
        const allowed = new Set(products.map(product => product.id));
        const byId = new Map(catalog.map(product => [product.id, product]));
        (body.result?.points || []).filter(point => allowed.has(point.payload?.product_id)).slice(0, 5)
          .forEach(point => recalled.set(point.payload.product_id, byId.get(point.payload.product_id)));
      }
      if (recalled.size) return { candidates: [...recalled.values()], strategy: 'qdrant_embedding_recall' };
    } catch { /* Qdrant unavailable: preserve semantic retrieval with the in-memory vector cache. */ }
  }
  for (const queryVector of queryVectors) {
    products.map(product => ({ product, score: cosine(queryVector, vectors.get(product.id) || []) }))
      .sort((a, b) => b.score - a.score || a.product.price - b.product.price).slice(0, 5)
      .forEach(({ product }) => recalled.set(product.id, product));
  }
  return { candidates: [...recalled.values()], strategy: process.env.QDRANT_URL ? 'embedding_cosine_fallback' : 'embedding_cosine_recall' };
}

async function rerankCandidates(products, plan) {
  if (!process.env.RERANKER_BASE_URL || !products.length) return products;
  const response = await fetch(`${process.env.RERANKER_BASE_URL.replace(/\/$/, '')}/rerank`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...(process.env.RERANKER_API_KEY ? { Authorization: `Bearer ${process.env.RERANKER_API_KEY}` } : {}) },
    body: JSON.stringify({ model: process.env.RERANKER_MODEL || '',
      query: `${[...(plan.must_buy || []), ...(plan.optional || [])].join(' ')} ${plan.preferences?.join(' ') || ''}`,
      documents: products.map(p => `${p.title} ${p.description} ${(p.highlights || []).join(' ')}`) }),
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`RERANKER_PROVIDER_${response.status}`);
  const body = await response.json();
  if (!Array.isArray(body.results) || body.results.length !== products.length) throw new Error('RERANKER_RESPONSE_INVALID');
  const scores = new Array(products.length);
  for (const result of body.results) scores[result.index] = Number(result.relevance_score ?? result.score ?? 0);
  return products.map((product, index) => ({ product, score: scores[index] ?? 0 }))
    .sort((a, b) => b.score - a.score || a.product.price - b.product.price).map(item => item.product);
}

function keywordRerank(products, plan) {
  const preferences = slotTokens((plan.preferences || []).join(' '));
  return products.map(product => {
    const text = `${product.title} ${product.description} ${(product.highlights || []).join(' ')}`;
    const tokens = slotTokens(text);
    const preferenceMatch = [...preferences].filter(token => tokens.has(token)).length;
    return { product, score: preferenceMatch };
  }).sort((a, b) => b.score - a.score || a.product.price - b.product.price).map(item => item.product);
}

async function searchAgentSearch(eligible, request, context, fallbackQuery) {
  const { scheme: plan, mutation } = request;
  const signature = retrievalSignature(plan);
  const byId = new Map(eligible.map(product => [product.id, product]));
  const reusablePool = Array.isArray(context?.candidate_ids)
    ? context.candidate_ids.map(id => byId.get(id)).filter(Boolean) : null;
  const recall = async (products, searchPlan, slots, query) => {
    try { return await semanticRecall(products, searchPlan, slots); }
    catch { return { candidates: relevantProducts(products, searchPlan, query, slots), strategy: 'keyword_fallback' }; }
  };
  const removed = new Set(mutation.removed_slots || []);
  const added = new Set(mutation.added_slots || []);
  const activeSlots = [...new Set([...(plan.must_buy || []), ...(plan.optional || []), ...(plan.unknowns || []), ...(plan.tasks || [])])]
    .filter(slot => !removed.has(slot) && !(plan.reusable || []).includes(slot) && !(plan.not_buy || []).includes(slot));
  const pureFilter = !mutation.scene_changed && !mutation.core_use_changed && added.size === 0;
  const hasCandidateSnapshot = Array.isArray(context?.candidate_ids);
  const staleContext = mutation.has_prior_state === false && context?.signature && context.signature !== signature;
  const rankingOnly = Boolean(mutation.budget_change || mutation.preference_change) && removed.size === 0 && !mutation.scene_changed;
  const shortSlots = hasCandidateSnapshot && reusablePool?.length && !rankingOnly
    ? activeSlots.filter(slot => relevantProducts(reusablePool, plan, '', [slot]).length < 2) : [];
  const localSlots = [...new Set([...added, ...shortSlots])];
  let candidates = reusablePool || [], strategy = 'cached_candidates', action = 'filter_and_rerank';
  if (mutation.scene_changed || mutation.core_use_changed) {
    const recalled = await recall(eligible, plan, null, fallbackQuery);
    candidates = recalled.candidates; strategy = recalled.strategy; action = 'recall_all';
  } else if (localSlots.length || !hasCandidateSnapshot || staleContext) {
    const recalled = await recall(eligible, plan, localSlots.length ? localSlots : activeSlots, fallbackQuery);
    const merged = new Map([...(staleContext ? [] : candidates), ...recalled.candidates].map(product => [product.id, product]));
    candidates = [...merged.values()]; strategy = recalled.strategy;
    action = localSlots.length && mutation.has_prior_state ? 'recall_local' : 'recall';
  } else if (pureFilter) {
    candidates = reusablePool;
  } else {
    candidates = reusablePool || [];
  }
  if (process.env.RERANKER_BASE_URL && candidates.length) {
    try { candidates = await rerankCandidates(candidates, plan); strategy += '_rerank'; }
    catch { strategy += '_rerank_fallback'; }
  } else if (candidates.length && action === 'filter_and_rerank') {
    candidates = keywordRerank(candidates, plan);
    strategy = 'cached_candidates_keyword_rerank';
  }
  return { candidates: balancedCandidates(candidates, plan), signature, action, strategy,
    explanation: action === 'filter_and_rerank' ? '复用既有候选，仅按预算与偏好重新筛选排序。' :
      action === 'recall_local' ? `只为候选不足的槽位局部召回：${localSlots.join('、')}。` :
        action === 'recall_all' ? '场景或核心用途变化，按完整方案重新召回。' : '候选池缺失或不匹配，按当前待购槽位召回。' };
}

export const searchCandidates = searchAgentSearch;

function schemeMutation(previous, next) {
  const purchaseSlots = scheme => [...new Set([...(scheme.must_buy || []), ...(scheme.optional || [])])];
  const oldSlots = purchaseSlots(previous || {}), newSlots = purchaseSlots(next);
  const oldSet = new Set(oldSlots), newSet = new Set(newSlots);
  const removedSlots = oldSlots.filter(slot => !newSet.has(slot));
  const addedSlots = previous ? newSlots.filter(slot => !oldSet.has(slot)) : [];
  const preferenceChanged = JSON.stringify(previous?.preferences || []) !== JSON.stringify(next.preferences || []);
  const budgetChanged = (previous?.budget ?? null) !== (next.budget ?? null);
  const goalChanged = Boolean(previous?.goal && previous.goal !== next.goal);
  const sceneChanged = goalChanged || Boolean(previous?.core_use && previous.core_use !== next.core_use) ||
    (previous?.people != null && previous.people !== next.people);
  const oldState = previous || {};
  const statuses = new Map();
  const allSlots = [...new Set([...(next.tasks || []), ...newSlots, ...(next.reusable || []), ...(next.not_buy || []), ...(next.unknowns || [])])];
  for (const slot of allSlots) {
    const status = (next.reusable || []).includes(slot) ? 'have' :
      (next.must_buy || []).includes(slot) ? 'must_buy' :
      (next.optional || []).includes(slot) ? 'optional' :
      (next.not_buy || []).includes(slot) ? 'deferred' : 'unknown';
    statuses.set(slot, status);
  }
  return {
    state_version: Number(oldState.state_version || 0) + 1,
    has_prior_state: Boolean(previous),
    target_slots: [
      ...[...statuses].map(([name, status]) => ({ name, status })),
      ...removedSlots.map(name => ({ name, status: 'removed' })),
    ],
    added_slots: addedSlots,
    removed_slots: removedSlots,
    existing_slots: next.reusable || [],
    deferred_slots: next.not_buy || [],
    pending_slots: next.unknowns || [],
    budget_change: budgetChanged ? { from: oldState.budget ?? null, to: next.budget ?? null } : null,
    preference_change: preferenceChanged ? { from: oldState.preferences || [], to: next.preferences || [] } : null,
    scene_changed: sceneChanged,
    core_use_changed: sceneChanged,
  };
}

async function model(messages) {
  const key = process.env.LLM_API_KEY;
  if (!key) throw new Error('MODEL_NOT_CONFIGURED');
  const response = await fetch(`${(process.env.LLM_BASE_URL || 'https://api.siliconflow.cn/v1').replace(/\/$/, '')}/chat/completions`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: process.env.LLM_MODEL || 'deepseek-ai/DeepSeek-V3.2',
      messages, response_format: { type: 'json_object' }, temperature: 0.3,
      max_tokens: 3200, enable_thinking: false }),
    signal: AbortSignal.timeout(42000),
  });
  if (!response.ok) throw new Error(`PROVIDER_${response.status}`);
  const body = await response.json();
  return parseJson(body.choices[0].message.content);
}

async function mainAgentDecide(history, callModel = model, confirmedChecklist = null, retrievalContext = null, schemeState = null) {
  const planRaw = confirmedChecklist ? {
    goal: confirmedChecklist.goal,
    core_use: confirmedChecklist.core_use,
    people: confirmedChecklist.people,
    stage: 'search',
    budget: confirmedChecklist.budget,
    destination: confirmedChecklist.destination,
    preferences: confirmedChecklist.preference ? [confirmedChecklist.preference] : [],
    tasks: confirmedChecklist.tasks,
    must_buy: confirmedChecklist.must_buy,
    optional: confirmedChecklist.optional,
    reusable: confirmedChecklist.reusable,
    not_buy: confirmedChecklist.not_buy,
    unknowns: confirmedChecklist.unknowns,
    prep: confirmedChecklist.prep,
    excludes: confirmedChecklist.excludes,
    directions: [],
  } : await callModel([{ role: 'system', content:
    `你是购物需求规划器。只输出JSON。根据对话理解生活目标，不限送礼，也包括通勤、出租屋、旅行。
区分用户事实与假设；不推断年龄、健康、收入，不把收礼人偏好当买家偏好。
记录场景核心用途与明确人数；人数、活动或核心用途变化时重算方案，未提供人数保持null。
同一目标保留已确认预算和目的地，明确改条件则更新，换目标不继承临时条件。
excludes记录用户明确拒绝的商品词；已拥有物品放入reusable和not_buy，不作为用户长期偏好。
这是购物决策，不是闲聊。模糊生活任务按“边界澄清→品类清单→用户取舍→商品方案”推进；不要把生活任务直接跳成商品推荐。
stage=clarify：尚不知道会改变清单品类或数量的场景条件时，最多问2个关键问题。根据场景选择问题，例如海边出行问同行人数及是否下水、玩沙或主要拍照；出租屋改造问范围及能否复原。澄清问题必须写进text，不能只填question字段。
stage=plan：场景边界足够后，先输出清单框架。tasks必须是可逐项核对、可购买的具体物品或明确品类，最多6项；禁止用“电子设备”“衣物与鞋履”“日常用品”等大类代替物品。例：手机防水袋、舒适步行鞋、换洗上衣、充电器。optional最多6项，必须与tasks不重复，并标出触发条件。护照、签证、车票、离线地图/翻译App等不可通过商品目录购买的内容放进prep提醒，不得当商品推荐或必买采购项。text展示必备、建议补充、按需条件项；优先级由前端控件收集，不要在text里重复追问。已有物品只按用户明确表达记录；未提及保持待确认，不得假设没有。用户已说过偏好时不重复问。若还需要一个会改变品类或尺寸的关键答案，应返回stage=clarify并只问这个问题；stage=plan时question必须为空。
stage=search：仅在清单框架已展示且用户已反馈偏好/调整，或用户明确要买某个具体品类时生成商品方案。若用户未提已有物品，不得假定没有；将相关商品标记为待核对候选，并在text中简洁说明已有物品尚未确认。不得把候选商品都称为必买。用户偏好已在前文说明时不可重复追问。
已展示清单后，用户可能只回答取舍偏好，也可能同时补充已有物品或改变活动；按反馈更新清单，不重复问已知信息。若用户改变活动（如从可能下水改为不下水），移除因此不再需要的品类。简单明确的单品需求可跳过生活任务清单。
用户已经给出爱好、使用困扰或明确品类时，可先形成可调整方案；不必把预算、目的地逐项问齐才推荐。库存无对应品类时诚实说明。
例如爸爸喜欢养蜂和钓鱼：可先形成户外便携、钓鱼照明等候选方向，不再只问“选哪个爱好”；库存无对应品类时诚实说明。
避免重复追问、泛泛问“还有什么兴趣”，不把尚未确认的生活习惯当事实。预算和配送地未知时可展示候选，但明确未核实，不声称可购买。
warmth一句自然回应，最多25字，不复述整段需求，不说“为了给你更有针对性的建议”等套话。
澄清回复由warmth加一个question构成，总计80字内；清单阶段text简洁列出具体采购物品、非购买提醒和一个偏好问题，不写长开场。
JSON格式：{goal,core_use,people,stage,warmth,question,text,budget:null或人民币总预算上限,destination:null或国家码,excludes:[],preferences:[],tasks:[],must_buy:[],optional:[],prep:[],reusable:[],not_buy:[],unknowns:[],directions:[{name,query}]}。
预算口径为整个采购方案的模拟商品总价上限，不含运费，不能称为到手价。tasks是具体采购检查项，不代表都要购买；prep是非商品的行前提醒，永不进入商品搜索；must_buy只记录用户明确确认缺少且完成任务需要购买的项；optional是提升体验但可暂缓的项；reusable只记录用户明确已有的物品，未提及视为未知而非没有；not_buy是明确不建议重复购买的项；unknowns记录尚未确认且影响是否购买的内容。不要为了证明“少买”而省略真实必要物品。
目的地码可用：${[...destinations].join(',')}；未知目的地保持null并说明待核实，不能假设能配送。
方向最多3个，搜索词具体，不生成订单，不给医疗建议。` }, ...history]);
  const plan = {
    goal: String(planRaw.goal || '').slice(0, 120),
    core_use: String(planRaw.core_use ?? schemeState?.core_use ?? '').slice(0, 120),
    people: Number.isInteger(planRaw.people) && planRaw.people > 0 ? planRaw.people :
      Number.isInteger(schemeState?.people) ? schemeState.people : null,
    stage: ['clarify', 'plan', 'search', 'compare'].includes(planRaw.stage) ? planRaw.stage : 'clarify',
    warmth: String(planRaw.warmth || '').slice(0, 40),
    question: String(planRaw.question || '').slice(0, 100),
    budget: Number.isFinite(planRaw.budget) && planRaw.budget > 0 ? planRaw.budget : null,
    destination: destinations.has(planRaw.destination) ? planRaw.destination : null,
    excludes: Array.isArray(planRaw.excludes) ? planRaw.excludes.filter(x => typeof x === 'string' && x.trim()).slice(0, 10) : [],
    preferences: Array.isArray(planRaw.preferences) ? planRaw.preferences.slice(0, 6).map(String) : [],
    tasks: Array.isArray(planRaw.tasks) ? planRaw.tasks.slice(0, 6).map(String) : [],
    prep: Array.isArray(planRaw.prep) ? planRaw.prep.slice(0, 8).map(String) : [],
    must_buy: Array.isArray(planRaw.must_buy) ? planRaw.must_buy.slice(0, 5).map(String) : [],
    optional: Array.isArray(planRaw.optional) ? planRaw.optional.slice(0, 5).map(String) : [],
    reusable: Array.isArray(planRaw.reusable) ? planRaw.reusable.slice(0, 8).map(String) : [],
    not_buy: Array.isArray(planRaw.not_buy) ? planRaw.not_buy.slice(0, 8).map(String) : [],
    unknowns: Array.isArray(planRaw.unknowns) ? planRaw.unknowns.slice(0, 4).map(String) : [],
    directions: Array.isArray(planRaw.directions) ? planRaw.directions.slice(0, 3) : [],
  };
  const normalizeSlot = value => value.toLowerCase().replace(/[\s，,、/]+/g, '');
  plan.tasks = [...new Map(plan.tasks.map(item => [normalizeSlot(item), item])).values()];
  const taskSlots = new Set(plan.tasks.map(normalizeSlot));
  const optionalItems = plan.stage === 'plan' ? plan.optional.filter(item => !taskSlots.has(normalizeSlot(item))) : plan.optional;
  plan.optional = [...new Map(optionalItems.map(item => [normalizeSlot(item), item])).values()];
  if (plan.stage === 'plan' && plan.question) plan.stage = 'clarify';
  const checklistConfirmed = /清单已逐项确认/.test(history.at(-1)?.content || '');
  if (checklistConfirmed) plan.stage = 'search';
  if (!confirmedChecklist && plan.stage === 'search' && history.length > 1) {
    const hasPlanTurn = history.slice(0, -1).some(message => message.role === 'assistant' && /基础准备|必备(?:清单|品类)?|建议补充|按需(?:准备|项)|清单框架|准备清单/.test(message.content));
    const hasPreference = plan.preferences.length > 0;
    if (!hasPlanTurn && !hasPreference && !['clarify', 'plan'].includes(planRaw.stage)) plan.stage = 'plan';
  }
  const mutation = schemeMutation(schemeState, plan);
  plan.state_version = mutation.state_version;
  const search_request = { scheme: plan, mutation };
  if (plan.stage === 'clarify') return { plan, scheme: plan, mutation, search_request, text: `${plan.warmth}\n${plan.question || '你最想改善哪个方面？'}`, groups: [], mode: 'live' };
  if (plan.stage === 'plan') return { plan, scheme: plan, mutation, search_request, text: String(planRaw.text || `${plan.warmth}\n先按当前场景整理准备清单：${plan.tasks.join('、')}。告诉我你更看重哪方面；已有的物品也可以顺便说，我会从采购项里扣除。`).slice(0, 600), groups: [], mode: 'live' };
  const eligible = filterCatalog(plan);
  if (!eligible.length) return { plan, scheme: plan, mutation, search_request, text: `${plan.warmth}\n模拟目录中没有同时满足预算、配送地及排除条件的商品。你愿意调整哪个条件？`, groups: [], mode: 'live' };
  const retrieval = await searchAgentSearch(eligible, search_request, retrievalContext, history.at(-1)?.content || '');
  const candidates = retrieval.candidates;
  if (!candidates.length) return { plan, scheme: plan, mutation, search_request, text: '模拟商品目录里没有找到与这些待购项目相关的候选，我不会用不相干商品补位。可以调整待购项目或跳过商品推荐。', groups: [], mode: 'live' };
  const result = await callModel([{ role: 'system', content:
    `你是购物方案助手，只输出JSON。以下是规则层已过滤的模拟商品目录。
请把结果组织成“最小够用的采购方案”，不是一批互斥的商品推荐。每个group对应一个已确认待购品类，products是该品类内可替代商品；目录有多少个相关候选就返回多少个，最多5个，便于对比。不要因只展示少数卡片而遗漏已经确认待购的品类，也绝不拿不相关商品凑数。priority只能是must_buy、optional或candidate。
优先完成用户任务，不以少买为目的删掉必要准备项；明确已有物品不再推荐，并在方案里说明复用。仅当用户明确说缺少某必需品类时才标must_buy；已有物品未确认时标candidate，体验提升项标optional。方案最多6个已确认待购品类，每个品类最多5件候选。
每个明确待购品类尽量对应一个group；最多6个group，每组最多5件候选。只能选择目录内id；明确品类或比较任务可只给一个槽位。已确认偏好时直接筛选，不再追加宽泛问题。
不相关时不要硬凑，groups可以为空。不能声称看过浏览记录、买家评价、真实销量或真实物流。
理由和不足必须依据目录，未知属性说未提供；不是医疗功效建议。不能虚构价格、配送和材质。
text最多180字：一句温度回应、当前方案一句总结、一个下一步问题。不要长开场、总结重复、寒暄或emoji。
每个商品reason/tradeoff各最多45字。JSON：{text,groups:[{name,priority,products:[{id,reason,tradeoff}]}]}。
计划：${JSON.stringify(plan)}
商品候选（已按待购槽位缩小范围）：${JSON.stringify(candidates.map(({id,title,brand,category,subcategory,description,price,currency,highlights}) => ({id,title,brand,category,subcategory,description,price,currency,highlights})))}` }, ...history]);
  const byId = new Map(candidates.map(p => [p.id, p]));
  const seen = new Set();
  let groups = (Array.isArray(result.groups) ? result.groups : []).slice(0, 6).map(group => ({
    name: String(group.name || '推荐方向').slice(0, 50),
    priority: ['optional', 'candidate'].includes(group.priority) ? group.priority : 'must_buy',
    products: (Array.isArray(group.products) ? group.products : []).slice(0, 5).flatMap(item => {
      const p = byId.get(item.id);
      if (!p || seen.has(p.id)) return [];
      seen.add(p.id);
      return [{ ...p, reason: String(item.reason || '').slice(0, 80), tradeoff: String(item.tradeoff || '').slice(0, 80) }];
    }),
  })).filter(group => group.products.length);
  const sumMinimum = selectedGroups => selectedGroups.reduce((total, group) => total + Math.min(...group.products.map(product => product.price)), 0);
  if (plan.budget != null && sumMinimum(groups.filter(group => group.priority !== 'candidate')) > plan.budget) {
    groups = groups.filter(group => group.priority !== 'optional');
  }
  const requiredTotal = sumMinimum(groups.filter(group => group.priority === 'must_buy'));
  const optionalTotal = sumMinimum(groups.filter(group => group.priority === 'optional'));
  const overBudget = plan.budget != null && requiredTotal > plan.budget;
  const fallbackText = groups.length ? String(result.text || plan.warmth).slice(0, 450) :
    '当前模拟目录里没有足够相关的选择，我不想给你凑一份清单。你愿意尝试哪个其他方向？';
  return { plan, scheme: plan, mutation, search_request, groups, mode: 'live', budget: { limit: plan.budget, minimum_total: requiredTotal, optional_total: optionalTotal, status: overBudget ? 'required_items_over_budget' : 'within_budget' },
    text: overBudget ? `${fallbackText.slice(0, 300)} 必需项按当前目录最低约 ${requiredTotal} CNY，超过预算 ${plan.budget} CNY；可以调整预算或告诉我哪项能替代。` : fallbackText,
    trace: { catalogSize: catalog.length, eligible: eligible.length, candidatePool: candidates.length, candidate_ids: candidates.map(p => p.id),
      retrieval_signature: retrieval.signature, retrieval_action: retrieval.action, retrieval_strategy: retrieval.strategy,
      retrieval_explanation: retrieval.explanation, mutation,
      selected: seen.size, minimumTotal: requiredTotal } };
}

export const decide = mainAgentDecide;

export default async function handler(req, res) {
  const origin = req.headers.origin;
  if (['https://pyper000.github.io', 'https://pyper000-github-io.vercel.app'].includes(origin) || origin?.startsWith('http://127.0.0.1:')) res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  res.setHeader('Cache-Control', 'no-store');
  if (req.method === 'OPTIONS') return res.status(204).end();
  if (req.method !== 'POST') return res.status(405).json({ error: '请使用POST' });
  const body = req.body;
  if (!Array.isArray(body?.history) || body.history.length > 12 || !body.history.length ||
      body.history.some(m => !['user', 'assistant'].includes(m.role) || typeof m.content !== 'string' || m.content.length > 3000) ||
      body.history.at(-1).role !== 'user') return res.status(400).json({ error: '对话格式无效或过长，请新建对话' });
  const ip = String(req.headers['x-forwarded-for'] || 'unknown').split(',')[0];
  const now = Date.now();
  for (const [id, times] of traffic) if (!times.some(t => now - t < 3600000)) traffic.delete(id);
  const times = (traffic.get(ip) || []).filter(t => now - t < 3600000);
  // ponytail: per-instance limits; use a shared quota store for multi-instance production traffic.
  if (times.length >= 20 || active.size >= 2 || active.has(ip)) return res.status(429).json({ error: '演示服务繁忙或达到调用上限，请稍后重试' });
  traffic.set(ip, [...times, now]); active.add(ip);
  const checklist = body.checklist && typeof body.checklist === 'object' ? body.checklist : null;
  const retrievalContext = body.retrieval_context && typeof body.retrieval_context === 'object' ? body.retrieval_context : null;
  try { return res.status(200).json(await mainAgentDecide(body.history, model, checklist, retrievalContext, normalizeSchemeState(body.scheme))); }
  catch (error) {
    const message = error.message === 'MODEL_NOT_CONFIGURED' ? '在线模型尚未配置' :
      error.message === 'PROVIDER_401' ? '模型认证失败，请联系作品作者' : '模型暂时不可用或处理超时，请重试';
    return res.status(503).json({ error: message });
  } finally { active.delete(ip); }
}
