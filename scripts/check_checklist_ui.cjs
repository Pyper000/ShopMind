// Run with PLAYWRIGHT_MODULE pointing at an installed Playwright module; no new dependency.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const output = path.resolve(process.env.SHOPMIND_TEST_OUTPUT || path.resolve(__dirname, '../eval/results/checklist_ui_20261021'));
fs.mkdirSync(output, { recursive: true });
const frame = (type, payload) => `data: ${JSON.stringify({ type, payload })}\n\n`;
const live = process.argv.includes('--live');
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    let calls = 0, release;
    const pending = new Promise(resolve => { release = resolve; });
    let submitted, selectionSubmitted;
    if (!live) await page.route('**/commerce/intents/stream', async route => {
      calls++;
      if (calls === 1) return route.fulfill({ contentType: 'text/event-stream', body:
        frame('plan.update', { shopping_plan: { goal: '海边游泳', stage: 'explore', tasks: ['泳衣', '防晒霜', '毛巾', '拖鞋', '换洗上衣'], optional: ['泳镜', '创可贴'], reusable: ['泳镜'], unknowns: ['毛巾'], prep: ['自行核对个人常用药'], budget_major: 200 } }) + frame('final.result', { text: '请分别核对每件物品。' }) });
      const request = route.request().postDataJSON();
      submitted = request.raw_query;
      selectionSubmitted = request.checklist_selection;
      if (calls === 2) {
        await pending;
        return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '测试用上游失败，请重试' }) });
      }
      return route.fulfill({ contentType: 'text/event-stream', body: frame('plan.update', { shopping_plan: { goal: '海边游泳', stage: 'search', tasks: ['泳衣'], must_buy: ['泳衣'], reusable: ['泳镜'] } }) + frame('final.result', { text: '清单已处理。' }) });
    });
    await page.goto((process.env.SHOPMIND_TEST_URL || 'http://localhost:5180') + '/#live');
    await page.getByRole('textbox', { name: '真实对话输入', exact: true }).fill(live
      ? '一个人周末去海边两天，会游泳，预算200元。我有泳镜但没有泳衣，其他物品有无还没确认。请先列出可分别核对的具体物品清单，包括按需的应急准备，不要搜索商品。'
      : '海边游泳清单');
    await page.getByRole('button', { name: '发送真实对话', exact: true }).click();
    const checklist = page.getByRole('region', { name: '逐项核对采购清单' });
    await checklist.waitFor({ timeout: 180000 });
    await page.locator('.live-status').waitFor({ state: 'hidden', timeout: 180000 });
    const labels = await checklist.locator('.checklist-item > b').allTextContents();
    assert(labels.includes('泳衣') && labels.includes('泳镜'), JSON.stringify(labels));
    assert(labels.length > 4, 'Checklist must not be limited to four umbrella categories');
    assert(!labels.some(item => ['游泳装备', '应急药品', '应急物品', '防晒用品', '换洗衣物'].includes(item)));
    const item = name => checklist.locator('.checklist-item').filter({ has: page.locator('b', { hasText: new RegExp(`^${name}$`) }) });
    assert.equal(await item('泳镜').getByRole('button', { name: '已有', exact: true }).getAttribute('aria-pressed'), 'true');
    assert.equal(await item('泳衣').getByRole('button', { name: '已有', exact: true }).getAttribute('aria-pressed'), 'false');
    assert.equal(await item('创可贴').getByRole('button', { name: '不需要', exact: true }).count(), 1);
    assert.equal(await item('创可贴').getByRole('button', { name: '之后再决定', exact: true }).count(), 0);
    assert.equal(await item('创可贴').getByRole('button', { name: '还不确定', exact: true }).count(), 1);
    assert.equal(await item('泳衣').evaluate(el => getComputedStyle(el).borderLeftColor), 'rgb(237, 201, 73)');
    assert.equal(await item('创可贴').evaluate(el => getComputedStyle(el).borderLeftColor), 'rgb(89, 203, 179)');
    await checklist.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, live ? 'live-atomic-items.png' : 'atomic-items.png'), fullPage: true });
    let timing = [];
    if (live) {
      const buySwimwear = item('泳衣').getByRole('button', { name: '需要购买', exact: true });
      if (await buySwimwear.getAttribute('aria-pressed') !== 'true') await buySwimwear.click();
      await checklist.getByRole('button', { name: '确认清单，匹配商品 →', exact: true }).click();
      await page.locator('.live-status').waitFor({ state: 'hidden', timeout: 240000 });
      await page.locator('.live-product').first().waitFor({ timeout: 1000 });
      await page.screenshot({ path: path.join(output, 'live-first-products.png'), fullPage: true });
      timing = await page.evaluate(() => JSON.parse(sessionStorage.getItem('shopmind.latency') || '[]'));
      assert.equal(timing.length, 2, 'Expected separate timing records for plan and search turns');
      assert(timing[0].first_checklist_sec !== null && timing[0].turn_end_sec !== null, JSON.stringify(timing));
      assert(timing[1].first_products_sec !== null && timing[1].turn_end_sec !== null, JSON.stringify(timing));
    } else {
      const noNeed = item('创可贴').getByRole('button', { name: '不需要', exact: true });
      await noNeed.click();
      assert.equal(await noNeed.getAttribute('aria-pressed'), 'true');
      assert.equal(await noNeed.evaluate(el => getComputedStyle(el).borderColor), 'rgb(89, 203, 179)');
      await item('泳衣').getByRole('button', { name: '需要购买', exact: true }).click();
      assert.equal(await item('泳衣').getByRole('button', { name: '需要购买', exact: true }).evaluate(el => getComputedStyle(el).borderColor), 'rgb(237, 201, 73)');
      await checklist.getByRole('button', { name: '确认清单，匹配商品 →', exact: true }).click();
      const loading = checklist.getByRole('button', { name: /正在处理清单/ });
      await loading.waitFor();
      assert(await loading.isDisabled());
      assert.equal(await loading.locator('.live-spinner').evaluate(el => getComputedStyle(el).animationName), 'live-spin');
      assert(await checklist.getByRole('status').isVisible());
      assert(await item('泳衣').getByRole('button', { name: '需要购买', exact: true }).isDisabled());
      assert(await checklist.locator('input[type=number]').isDisabled());
      await page.waitForFunction(() => document.querySelector('.checklist-next button').textContent.includes('1 秒'));
      await page.screenshot({ path: path.join(output, 'confirm-loading.png'), fullPage: true });
      assert(submitted.includes('已有可复用：泳镜'));
      assert(submitted.includes('需要购买的必备项：泳衣'));
      assert.deepEqual(selectionSubmitted.deferred, ['创可贴']);
      assert(selectionSubmitted.unknown.includes('防晒霜'));
      assert(submitted.includes('尚未确认：防晒霜、毛巾、拖鞋、换洗上衣'));
      release();
      await page.getByRole('alert').filter({ hasText: '测试用上游失败' }).waitFor();
      assert.equal(await item('泳衣').getByRole('button', { name: '需要购买', exact: true }).getAttribute('aria-pressed'), 'true');
      assert(await checklist.getByRole('button', { name: '确认清单，匹配商品 →', exact: true }).isEnabled());
      await checklist.getByRole('button', { name: '确认清单，匹配商品 →', exact: true }).click();
      await checklist.waitFor({ state: 'hidden' });
      assert.equal(calls, 3);
    }
    assert.deepEqual(errors, []);
    const result = { test: live ? 'real LLM checklist and search smoke' : 'mocked SSE UI regression (not LLM evaluation)', labels, submitted, selectionSubmitted, timing, passed: true };
    fs.writeFileSync(path.join(output, live ? 'live-result.json' : 'ui-result.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
