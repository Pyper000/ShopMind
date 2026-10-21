import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decide, filterCatalog, searchCandidates } from './chat.mjs';
import catalog from './catalog.json' with { type: 'json' };
import taxonomy from './category-taxonomy.json' with { type: 'json' };

test('simulated catalog covers core categories and every mapped subcategory', () => {
  const required = ['旅行装备', '数码配件', '家居生活', '户外运动', '服饰鞋包', '食品饮料', '厨房与小家电', '美妆个护', '办公学习', '母婴用品', '宠物用品', '汽车用品', '家用电器', '玩具乐器', '珠宝配饰', '鲜花园艺'];
  const ids = new Set(catalog.map(product => product.id));
  assert.equal(ids.size, catalog.length);
  for (const category of required) {
    const products = catalog.filter(product => product.category === category);
    assert(products.length >= 3, `${category} should have at least three products`);
    assert(products.every(product => product.title && product.brand && product.price > 0 && product.currency && product.ships_to.length));
  }
  const assigned = new Set();
  for (const [category, subcategories] of Object.entries(taxonomy)) {
    for (const [subcategory, productIds] of Object.entries(subcategories)) {
      assert(productIds.length >= 5, `${category}/${subcategory} should have at least five products`);
      for (const id of productIds) {
        assert(ids.has(id), `${id} must exist in catalog`);
        assert(!assigned.has(id), `${id} must belong to only one subcategory`);
        assert.equal(catalog.find(product => product.id === id).category, category, `${id} category mismatch`);
        assigned.add(id);
      }
    }
  }
  assert.equal(assigned.size, catalog.length, 'every product must have a secondary category');
});

test('budget, destination and rejection are enforced before model selection', async () => {
  const plan = { goal: 'gift', stage: 'search', budget: 150, destination: 'CN', excludes: [], tasks: ['茶点礼盒'], must_buy: ['茶点礼盒'], directions: [] };
  const eligible = filterCatalog(plan);
  assert(eligible.length > 0);
  assert(eligible.every(p => p.price <= 150 && p.ships_to.includes('CN')));
  const responses = [plan, { text: '推荐', groups: [{ name: '错误', products: [{ id: 'invented' }, { id: 'P1006' }] }, { name: '选择', products: [{ id: 'P1072' }] }] }];
  const result = await decide([{ role: 'user', content: '礼物' }], async () => responses.shift());
  assert.equal(result.groups[0].products.length, 1);
  assert.equal(result.groups[0].products[0].id, 'P1072');
  assert.equal(result.groups[0].products[0].price, 128);
});

test('explicitly reusable items are not offered as products to buy again', () => {
  const eligible = filterCatalog({ budget: null, destination: null, excludes: [], reusable: ['双肩包'], not_buy: [] });
  assert(eligible.every(product => !`${product.title} ${product.description}`.includes('双肩包')));
});

test('clarification does not invent product cards or require a second model call', async () => {
  let calls = 0;
  const result = await decide([{ role: 'user', content: '改善通勤' }], async () => {
    calls++; return { stage: 'clarify', warmth: '每天通勤，舒服一点很重要。', question: '最困扰你的是噪音还是携带不便？' };
  });
  assert.equal(calls, 1);
  assert.equal(result.groups.length, 0);
  assert(result.text.length < 80);
});

test('scene planning returns a category checklist before matching any products', async () => {
  let calls = 0;
  const result = await decide([{ role: 'user', content: '周末去海边两天，不知道要准备什么' }], async () => {
    calls++;
    return { goal: '海边两天出行', stage: 'plan', warmth: '先把出行清单列清楚。', text: '必备：换洗衣物、洗漱用品。建议补充：防晒用品。按需或条件项：泳衣或速干衣物、手机防水袋、沙滩拖鞋、衣物收纳袋。你更想优先轻装，还是把下水用品准备齐？', tasks: ['换洗衣物', '洗漱用品'], optional: ['防晒用品', '泳衣或速干衣物', '手机防水袋', '沙滩拖鞋', '衣物收纳袋'], reusable: [], must_buy: [], unknowns: ['是否下水'] };
  });
  assert.equal(calls, 1);
  assert.equal(result.plan.stage, 'plan');
  assert.deepEqual(result.groups, []);
  assert.match(result.text, /建议补充/);
  assert.match(result.text, /轻装/);
  assert.deepEqual(result.plan.reusable, []);
});

test('a plan that still has a material question is presented as clarification', async () => {
  const result = await decide([{ role: 'user', content: '第一次租房，想布置卧室' }], async () => ({
    goal: '出租屋卧室布置', stage: 'plan', warmth: '先梳理卧室必需品。',
    question: '退租时是否需要恢复原状？', text: '先确认墙面是否允许打孔。',
    tasks: ['床垫', '窗帘'], optional: ['窗帘'],
  }));
  assert.equal(result.plan.stage, 'clarify');
  assert.match(result.text, /退租时是否需要恢复原状/);
  assert.deepEqual(result.plan.optional, []);
  assert.deepEqual(result.groups, []);
});

test('non-purchasable preparation reminders stay outside the product checklist', async () => {
  const result = await decide([{ role: 'user', content: '去意大利出差一周，帮我整理行前准备' }], async () => ({
    goal: '意大利出差', stage: 'plan', text: '必备：转换插头。行前提醒：确认护照有效期。',
    tasks: ['欧标转换插头'], prep: ['确认护照有效期', '核对机票信息'], optional: [],
  }));
  assert.deepEqual(result.plan.tasks, ['欧标转换插头']);
  assert.deepEqual(result.plan.prep, ['确认护照有效期', '核对机票信息']);
  assert.deepEqual(result.groups, []);
});

test('preference feedback after checklist proceeds to product matching without re-asking', async () => {
  const responses = [
    { goal: '海边两天出行', stage: 'search', warmth: '按轻装来整理。', text: '按轻装优先，给你看一件衣物收纳候选。', preferences: ['轻装少带'], tasks: ['衣物收纳'], must_buy: [], optional: [], reusable: [], unknowns: ['是否已有收纳用品'], directions: [] },
    { text: '這款收纳袋方便分装换洗衣物。', groups: [{ name: '衣物收纳', priority: 'candidate', products: [{ id: 'P1014', reason: '六件套可分装衣物', tradeoff: '已有袋子时可以不用买' }] }] },
  ];
  const result = await decide([
    { role: 'user', content: '周末去海边两天，主要拍照可能下水' },
    { role: 'assistant', content: '必备清单：换洗衣物、防晒用品。建议补充：泳衣、手机防水袋。按需准备：沙滩拖鞋。你优先轻装还是下水准备齐？' },
    { role: 'user', content: '优先轻装，可能不下水' },
  ], async () => responses.shift());
  assert.equal(result.plan.stage, 'search');
  assert.equal(result.groups[0].priority, 'candidate');
  assert.deepEqual(result.plan.must_buy, []);
  assert.deepEqual(result.plan.reusable, []);
});

test('product selection only receives candidates relevant to the purchase slots', async () => {
  const responses = [
    { goal: '海边出行', stage: 'search', preferences: ['轻便'], tasks: ['旅行毛巾'], must_buy: ['旅行毛巾'], optional: [], reusable: [], not_buy: [], unknowns: [] },
    { text: '先补旅行毛巾。', groups: [{ name: '旅行毛巾', priority: 'candidate', products: [{ id: 'P1007', reason: '适合近水拍照', tradeoff: '厚度未提供' }] }] },
  ];
  let selectionPrompt = '';
  const result = await decide([
    { role: 'user', content: '周末去海边，可能下水' },
    { role: 'assistant', content: '清单：旅行毛巾。优先轻便还是功能齐全？' },
    { role: 'user', content: '优先轻便，旅行毛巾我没有' },
  ], async messages => {
    const response = responses.shift();
    if (response.groups) selectionPrompt = messages[0].content;
    return response;
  });
  assert.match(selectionPrompt, /旅行毛巾/);
  assert(!selectionPrompt.includes('床垫'));
  assert.equal(result.groups[0].products[0].id, 'P1007');
  assert(result.trace.candidatePool < result.trace.eligible);
});

test('an explicitly confirmed checklist advances to product matching even if the planner repeats plan stage', async () => {
  const responses = [
    { goal: '海边出行', stage: 'plan', tasks: ['旅行毛巾'], must_buy: ['旅行毛巾'], preferences: ['轻便'], budget: 300 },
    { text: '给你两款旅行毛巾对比。', groups: [{ name: '旅行毛巾', priority: 'must_buy', products: [{ id: 'P1007', reason: '速干轻薄', tradeoff: '具体厚度未提供' }] }] },
  ];
  const result = await decide([
    { role: 'user', content: '海边旅行' },
    { role: 'assistant', content: '清单：旅行毛巾。' },
    { role: 'user', content: '清单已逐项确认。明确缺少、需要购买的必备项：旅行毛巾。预算300元。优先考虑轻便。' },
  ], async () => responses.shift());
  assert.equal(result.plan.stage, 'search');
  assert.equal(result.groups[0].products[0].id, 'P1007');
});

test('structured checklist skips repeated requirement parsing but keeps product explanation call', async () => {
  let calls = 0;
  const result = await decide([
    { role: 'user', content: '海边出行' },
    { role: 'assistant', content: '已列出旅行毛巾。' },
    { role: 'user', content: '清单已逐项确认。需要购买旅行毛巾。' },
  ], async () => {
    calls += 1;
    return { text: '按轻便选择。', groups: [{ name: '旅行毛巾', priority: 'must_buy', products: [{ id: 'P1007', reason: '速干', tradeoff: '三件装' }] }] };
  }, {
    goal: '海边出行', budget: 300, destination: 'CN', preference: '轻便少占空间',
    tasks: ['旅行毛巾'], must_buy: ['旅行毛巾'], optional: [], reusable: [], not_buy: [], unknowns: [], prep: [], excludes: [],
  });
  assert.equal(calls, 1);
  assert.equal(result.plan.stage, 'search');
  assert.equal(result.groups[0].products[0].id, 'P1007');
});

test('preference-only changes reuse the filtered candidate pool and rerank without recall', async () => {
  const responses = [{ text: '按轻便优先。', groups: [{ name: '旅行毛巾', products: [{ id: 'P1007', reason: '速干', tradeoff: '厚度未知' }] }] }];
  const result = await decide([{ role: 'user', content: '按轻便优先' }], async () => responses.shift(), {
    goal: '海边出行', budget: null, destination: null, preference: '轻便少占空间',
    tasks: ['旅行毛巾'], must_buy: ['旅行毛巾'], optional: [], reusable: [], not_buy: [], unknowns: [], prep: [], excludes: [],
  }, { signature: '{"slots":["旅行毛巾"],"destination":null,"budget":null,"excludes":[]}', candidate_ids: ['P1007', 'P1014'] });
  assert.equal(result.trace.retrieval_action, 'filter_and_rerank');
  assert.equal(result.trace.retrieval_strategy, 'cached_candidates_keyword_rerank');
  assert.deepEqual(new Set(result.trace.candidate_ids), new Set(['P1007', 'P1014']));
});

test('purchase-slot or hard-constraint changes trigger fresh candidate recall', async () => {
  const result = await decide([{ role: 'user', content: '只买手机防水袋，预算200' }], async () => ({
    text: '给你防水袋候选。', groups: [{ name: '手机防水袋', products: [{ id: 'P1023', reason: '适用近水场景', tradeoff: '防水等级未提供' }] }],
  }), {
    goal: '海边出行', budget: 200, destination: 'CN', preference: '轻便', tasks: ['手机防水袋'],
    must_buy: ['手机防水袋'], optional: [], reusable: ['双肩包'], not_buy: [], unknowns: [], prep: [], excludes: [],
  }, { signature: 'previous-state', candidate_ids: ['P1007'] });
  assert.equal(result.trace.retrieval_action, 'recall');
  assert.equal(result.trace.retrieval_strategy, 'keyword_fallback');
  assert(!result.trace.candidate_ids.includes('P1007'));
});

test('shopping plan exposes task slots, reusable items and unknowns', async () => {
  const responses = [
    { goal: '周末海边出行', stage: 'search', warmth: '先把行李控制住。', question: '需要带电脑吗？', budget: 500, tasks: ['旅行茶具', '随身收纳'], reusable: ['双肩包'], unknowns: ['是否带电脑'], directions: [] },
    { text: '先补齐旅行茶具，已有双肩包不用重复买。', groups: [{ name: '旅行茶具', priority: 'must_buy', products: [{ id: 'P1006', reason: '适合场景', tradeoff: '收纳体积未提供' }] }] },
  ];
  const result = await decide([{ role: 'user', content: '周末去海边两天，家里有双肩包，预算500' }], async () => responses.shift());
  assert.deepEqual(result.plan.tasks, ['旅行茶具', '随身收纳']);
  assert.deepEqual(result.plan.reusable, ['双肩包']);
  assert.deepEqual(result.plan.unknowns, ['是否带电脑']);
  assert.equal(result.groups[0].products[0].id, 'P1006');
});

test('drops optional purchase tasks first to keep the plan within the total budget', async () => {
  const responses = [
    { goal: '短途出行', stage: 'search', warmth: '先把任务列清楚。', question: '需要带电脑吗？', budget: 200, tasks: ['收纳', '轻便出行'], must_buy: ['收纳'], optional: ['轻便出行'], reusable: [], not_buy: ['新背包，已有双肩包'], unknowns: [], directions: [] },
    { text: '推荐两项', groups: [
      { name: '收纳', priority: 'must_buy', products: [{ id: 'P1001', reason: '可用', tradeoff: '容量有限' }] },
      { name: '轻便出行', priority: 'optional', products: [{ id: 'P1003', reason: '可用', tradeoff: '容量有限' }] },
    ] },
  ];
  const result = await decide([{ role: 'user', content: '短途出行预算200' }], async () => responses.shift());
  assert.deepEqual(result.groups.map(group => group.name), ['收纳']);
  assert.deepEqual(result.plan.must_buy, ['收纳']);
  assert.deepEqual(result.plan.not_buy, ['新背包，已有双肩包']);
  assert.equal(result.budget.status, 'within_budget');
  assert.equal(result.budget.minimum_total, 189);
  assert.equal(result.budget.optional_total, 0);
});

test('reports optional spend separately from the required plan total', async () => {
  const responses = [
    { goal: '露营', stage: 'search', warmth: '先满足基础照明。', budget: 300, tasks: ['主灯', '备用灯'], must_buy: ['主灯'], optional: ['备用灯'], reusable: [], not_buy: [], unknowns: [], directions: [] },
    { text: '主灯优先，备用灯按需。', groups: [
      { name: '主灯', priority: 'must_buy', products: [{ id: 'P1008', reason: '基础照明候选', tradeoff: '续航未核实' }] },
      { name: '备用灯', priority: 'optional', products: [{ id: 'P1038', reason: '夜间活动较多时备用', tradeoff: '不影响基础照明任务' }] },
    ] },
  ];
  const result = await decide([{ role: 'user', content: '露营灯预算300' }], async () => responses.shift());
  assert.equal(result.budget.minimum_total, 89);
  assert.equal(result.budget.optional_total, 45);
});

test('keeps relevant candidates for every confirmed purchase slot', async () => {
  const result = await decide([{ role: 'user', content: '按这两类预算内匹配' }], async () => ({
    text: '两类都保留候选。', groups: [
      { name: '行李收纳', priority: 'must_buy', products: [{ id: 'P1014', reason: '分装衣物', tradeoff: '容量未知' }] },
      { name: '手机防水袋', priority: 'must_buy', products: [{ id: 'P1116', reason: '近水防护', tradeoff: '防水等级未核实' }] },
    ],
  }), {
    goal: '海边出行', budget: 500, destination: 'CN', preference: '轻便',
    tasks: ['行李收纳', '手机防水袋'], must_buy: ['行李收纳', '手机防水袋'], optional: [],
    reusable: [], not_buy: [], unknowns: [], prep: [], excludes: [],
  });
  assert(result.trace.candidate_ids.includes('P1014'));
  assert(result.trace.candidate_ids.includes('P1116'));
  assert.equal(result.groups.length, 2);
});

test('incremental retrieval reranks the existing pool for budget and preference changes', async () => {
  const plan = { goal: '海边出行', tasks: ['旅行毛巾'], must_buy: ['旅行毛巾'], optional: [], unknowns: [], preferences: ['轻便'], budget: 300, destination: 'CN' };
  const pool = filterCatalog({ ...plan, preferences: [], budget: null });
  const result = await searchCandidates(filterCatalog(plan), { scheme: plan, mutation: {
    has_prior_state: true, added_slots: [], removed_slots: [], scene_changed: false, core_use_changed: false,
  } }, { signature: 'before', candidate_ids: pool.slice(0, 5).map(product => product.id) }, '轻便');
  assert.equal(result.action, 'filter_and_rerank');
  assert(result.candidates.every(product => product.price <= plan.budget));
});

test('incremental retrieval recalls only a newly added slot', async () => {
  const plan = { goal: '海边出行', tasks: ['旅行毛巾', '手机防水袋'], must_buy: ['旅行毛巾', '手机防水袋'], optional: [], unknowns: [], preferences: [], budget: null, destination: null };
  const prior = catalog.filter(product => /毛巾/.test(product.title)).slice(0, 3);
  const result = await searchCandidates(filterCatalog(plan), { scheme: plan, mutation: {
    has_prior_state: true, added_slots: ['手机防水袋'], removed_slots: [], scene_changed: false, core_use_changed: false,
  } }, { signature: 'before', candidate_ids: prior.map(product => product.id) }, '加一个手机防水袋');
  assert.equal(result.action, 'recall_local');
  assert(result.explanation.includes('手机防水袋'));
  assert(result.candidates.some(product => /防水袋/.test(product.title)));
});

test('a scene or core-use change triggers full candidate recall', async () => {
  const plan = { goal: '租房入住', tasks: ['台灯'], must_buy: ['台灯'], optional: [], unknowns: [], preferences: [], budget: null, destination: null };
  const result = await searchCandidates(filterCatalog(plan), { scheme: plan, mutation: {
    has_prior_state: true, added_slots: [], removed_slots: [], scene_changed: true, core_use_changed: true,
  } }, { signature: 'before', candidate_ids: ['P1007'] }, '租房台灯');
  assert.equal(result.action, 'recall_all');
  assert.notDeepEqual(result.candidates.map(product => product.id), ['P1007']);
});

test('MainAgent returns a versioned scheme and structured checklist mutation', async () => {
  const previous = { state_version: 7, goal: '海边出行', core_use: '拍照', people: 2, budget: 300,
    preferences: ['轻便'], tasks: ['旅行毛巾'], must_buy: ['旅行毛巾'], optional: [], reusable: [], not_buy: [], unknowns: [] };
  const result = await decide([{ role: 'user', content: '改成三个人，带一个手机防水袋，预算500' }], async () => ({
    goal: '海边出行', core_use: '多人拍照和下水', people: 3, stage: 'plan', budget: 500,
    preferences: ['轻便'], tasks: ['旅行毛巾', '手机防水袋'], must_buy: ['手机防水袋'], optional: [],
    reusable: ['旅行毛巾'], not_buy: [], unknowns: ['是否已有防水袋'],
  }), null, null, previous);
  assert.equal(result.scheme.state_version, 8);
  assert.deepEqual(result.mutation.added_slots, ['手机防水袋']);
  assert.deepEqual(result.mutation.removed_slots, ['旅行毛巾']);
  assert.deepEqual(result.mutation.existing_slots, ['旅行毛巾']);
  assert.deepEqual(result.mutation.pending_slots, ['是否已有防水袋']);
  assert.deepEqual(result.mutation.budget_change, { from: 300, to: 500 });
  assert.equal(result.mutation.core_use_changed, true);
  assert.deepEqual(result.search_request, { scheme: result.scheme, mutation: result.mutation });
});
