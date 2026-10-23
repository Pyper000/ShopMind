import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const outputName = process.env.SHOPMIND_ACCEPTANCE_DIR || 'local_acceptance_20261023';
assert.ok(!path.isAbsolute(outputName) && !outputName.split(/[\\/]/).includes('..'), 'acceptance output must stay under eval/results');
const outputDir = path.join(root, 'eval', 'results', outputName);
const baseUrl = process.env.SHOPMIND_LOCAL_URL || 'http://127.0.0.1:5183';
const apiTarget = process.env.SHOPMIND_API_TARGET || 'http://127.0.0.1:8000';
const edgePath = process.env.SHOPMIND_EDGE || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
await mkdir(outputDir, { recursive: true });

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => server.listen(0, '127.0.0.1', resolve).once('error', reject));
  const { port } = server.address();
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function waitFor(fn, label, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await fn();
    if (value) return value;
    await delay(100);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

async function capturePlan(query) {
  const sessionId = `browser-acceptance-${randomUUID()}`;
  const startedAt = performance.now();
  const response = await fetch(`${baseUrl}/commerce/intents/stream`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      shopping_session_id: sessionId,
      buyer_id: 'local-browser-acceptance',
      locale: 'zh-CN', currency: 'CNY', raw_query: query,
    }),
  });
  assert.equal(response.status, 200, 'frontend proxy accepts a fresh SSE session');
  let buffer = '';
  const events = [];
  const milestones = { firstEventSec: null, firstPlanSec: null };
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() || '';
    for (const frame of frames) {
      const line = frame.split(/\r?\n/).find(item => item.startsWith('data:'));
      if (!line) continue;
      const event = JSON.parse(line.slice(5).trim());
      events.push(event);
      const elapsed = Math.round((performance.now() - startedAt) * 10) / 10000;
      milestones.firstEventSec ??= elapsed;
      if ((event.type === 'plan.update' || event.type === 'plan.preview') && event.payload?.shopping_plan) {
        milestones.firstPlanSec ??= elapsed;
      }
    }
    if (done) break;
  }
  const planEvent = [...events].reverse().find(event => event.type === 'plan.update' && event.payload?.shopping_plan);
  const plan = planEvent?.payload.shopping_plan;
  assert.ok(plan, 'fresh session emitted a plan.update event');
  return { sessionId, events, plan, milestones, turnEndSec: Math.round((performance.now() - startedAt) * 10) / 10000 };
}

function assertMaternityPlan(plan) {
  assert.equal(plan.template_id, 'maternity_hospital_bag');
  assert.deepEqual(plan.checklist_sections.map(section => section.title), ['孕妇', '宝宝', '陪护人']);
  const allNames = plan.checklist_sections.flatMap(section => [...section.required, ...section.optional].map(item => item.name));
  assert.ok(allNames.includes('内裤'));
  assert.ok(allNames.includes('睡衣/睡袍'));
  assert.ok(allNames.includes('拖鞋/便鞋'));
  assert.ok(allNames.includes('新生儿连体衣/包屁衣'));
  assert.ok(allNames.includes('宝宝出院衣物'));
  assert.ok(allNames.includes('陪护人换洗衣物'));
  assert.ok(!allNames.some(name => ['待产包', '孕妇用品', '宝宝用品'].includes(name)));
  const mother = plan.checklist_sections[0];
  assert.ok(mother.required.some(item => item.name === '内裤' && item.quantity === '参考5-6条'));
  assert.ok(plan.checklist_sections.every(section => section.optional.some(item => item.condition)));
  assert.ok(plan.checklist_sections.every(section => section.reference?.title.includes('NHS')));
  assert.ok(mother.reference.note.includes('以本院清单'));
}

class DevTools {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 0;
    this.pending = new Map();
    this.listeners = new Map();
    socket.addEventListener('message', ({ data }) => {
      const message = JSON.parse(data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        message.error ? pending.reject(new Error(message.error.message)) : pending.resolve(message.result || {});
      } else {
        for (const listener of this.listeners.get(message.method) || []) listener(message.params);
      }
    });
  }
  static async connect(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true });
      socket.addEventListener('error', reject, { once: true });
    });
    return new DevTools(socket);
  }
  on(method, listener) {
    const listeners = this.listeners.get(method) || [];
    listeners.push(listener);
    this.listeners.set(method, listeners);
  }
  send(method, params = {}) {
    const id = ++this.nextId;
    this.socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  async evaluate(expression) {
    const response = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.text || 'Browser evaluation failed');
    return response.result?.value;
  }
}

const maternity = await capturePlan('我第一次生产，想准备待产包');
assertMaternityPlan(maternity.plan);
const health = await fetch(`${baseUrl}/health`);
assert.equal(health.status, 200, 'Vite /health proxy reaches a backend');

const port = await freePort();
const profileDir = path.join(os.tmpdir(), `shopmind-edge-${randomUUID()}`);
await mkdir(profileDir);
const edge = spawn(edgePath, [
  '--headless=new', '--no-first-run', '--no-default-browser-check', '--disable-gpu',
  `--remote-debugging-port=${port}`, `--user-data-dir=${profileDir}`,
  '--window-size=1440,1200', 'about:blank',
], { stdio: 'ignore', windowsHide: true });
let devtools;
const networkRequests = [];
try {
  const targetsUrl = `http://127.0.0.1:${port}/json/list`;
  const targets = await waitFor(async () => {
    try {
      const response = await fetch(targetsUrl);
      return response.ok ? response.json() : null;
    } catch { return null; }
  }, 'Edge DevTools');
  const page = targets.find(target => target.type === 'page');
  assert.ok(page, 'Edge created a page target');
  devtools = await DevTools.connect(page.webSocketDebuggerUrl);
  devtools.on('Network.requestWillBeSent', event => {
    if (event.request.url.includes('/commerce/intents/stream')) networkRequests.push({ url: event.request.url, method: event.request.method });
  });
  await devtools.send('Page.enable');
  await devtools.send('Runtime.enable');
  await devtools.send('Network.enable');
  await devtools.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1200, deviceScaleFactor: 1, mobile: false });
  await devtools.send('Page.navigate', { url: baseUrl });
  await waitFor(() => devtools.evaluate(`document.querySelector('input[aria-label="真实对话输入"]') !== null`), 'ShopMind composer');

  async function sendInBrowser(query) {
    await devtools.evaluate(`(() => {
      const input = document.querySelector('input[aria-label="真实对话输入"]');
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      setter.call(input, ${JSON.stringify(query)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    })()`);
    await waitFor(() => devtools.evaluate(`!document.querySelector('button[aria-label="发送真实对话"]').disabled`), 'enabled send button');
    await devtools.evaluate(`document.querySelector('button[aria-label="发送真实对话"]').click()`);
  }

  async function saveChecklistScreenshot(filename) {
    await devtools.evaluate(`(() => { const root = document.querySelector('.checklist-step'); document.documentElement.style.scrollBehavior = 'auto'; window.scrollTo(0, root.getBoundingClientRect().top + window.scrollY); })()`);
    await delay(250);
    const clip = await devtools.evaluate(`(() => { const rect = document.querySelector('.checklist-step').getBoundingClientRect(); return { x: rect.left + window.scrollX, y: rect.top + window.scrollY, width: rect.width, height: rect.height }; })()`);
    const screenshot = await devtools.send('Page.captureScreenshot', {
      format: 'png', fromSurface: true, captureBeyondViewport: true,
      clip: { ...clip, scale: 1 },
    });
    await writeFile(path.join(outputDir, filename), Buffer.from(screenshot.data, 'base64'));
  }

  async function saveDemoScreenshot(filename) {
    await devtools.evaluate(`document.querySelector('#demo').scrollIntoView({ block: 'start', behavior: 'instant' })`);
    await delay(250);
    const screenshot = await devtools.send('Page.captureScreenshot', { format: 'png', fromSurface: true });
    await writeFile(path.join(outputDir, filename), Buffer.from(screenshot.data, 'base64'));
  }

  await sendInBrowser('我第一次生产，想准备待产包');
  await waitFor(() => devtools.evaluate(`(() => {
    const sections = [...document.querySelectorAll('.checklist-step .checklist-section')];
    return sections.length === 3 && sections.map(s => s.querySelector('h3')?.textContent.trim()).join('|') === '孕妇|宝宝|陪护人' && !document.querySelector('.live-status');
  })()`), 'maternity checklist rendered in the browser');
  const maternityUi = await devtools.evaluate(`(() => {
    const root = document.querySelector('.checklist-step');
    const sections = [...root.querySelectorAll('.checklist-section')];
    const rows = [...root.querySelectorAll('.checklist-item')];
    const required = root.querySelector('.checklist-item.is-required');
    const optional = root.querySelector('.checklist-item.is-optional');
    return {
      sections: sections.map(section => ({ title: section.querySelector('h3')?.textContent.trim(),
        items: [...section.querySelectorAll('.checklist-item')].map(row => row.textContent.trim()),
        reference: section.querySelector('.checklist-reference')?.textContent.trim() || '' })),
      grid: getComputedStyle(root).gridTemplateColumns,
      colors: { required: getComputedStyle(required).borderLeftColor, optional: getComputedStyle(optional).borderLeftColor },
      requiredStates: [...required.querySelectorAll('button')].map(button => button.textContent.trim()),
      optionalStates: [...optional.querySelectorAll('button')].map(button => button.textContent.trim()),
      optionalLayout: (() => {
        const card = optional.getBoundingClientRect();
        const group = optional.querySelector('[role="group"]');
        const groupRect = group.getBoundingClientRect();
        const buttons = [...group.querySelectorAll('button')].map(button => {
          const rect = button.getBoundingClientRect();
          return { label: button.textContent.trim(), top: Math.round(rect.top), left: Math.round(rect.left), right: Math.round(rect.right) };
        });
        return { columns: getComputedStyle(group).gridTemplateColumns.split(' ').length,
          card: { left: Math.round(card.left), right: Math.round(card.right) },
          group: { left: Math.round(groupRect.left), right: Math.round(groupRect.right) }, buttons };
      })(),
      request: [...performance.getEntriesByType('resource')].map(entry => entry.name).filter(name => name.includes('/commerce/intents/stream')),
      latency: JSON.parse(sessionStorage.getItem('shopmind.latency') || '[]').at(-1) || null,
    };
  })()`);
  assert.deepEqual(maternityUi.sections.map(section => section.title), ['孕妇', '宝宝', '陪护人']);
  assert.ok(maternityUi.sections.flatMap(section => section.items).some(item => item.includes('内裤') && item.includes('参考5-6条')));
  const maternityRows = maternityUi.sections.flatMap(section => section.items).join('\n');
  assert.ok(maternityRows.includes('睡衣/睡袍') && maternityRows.includes('拖鞋/便鞋'));
  assert.ok(maternityRows.includes('新生儿连体衣/包屁衣') && maternityRows.includes('陪护人证件/入院凭证'));
  assert.ok(maternityUi.sections.every(section => section.reference.includes('NHS')));
  assert.equal(maternityUi.colors.required, 'rgb(237, 201, 73)');
  assert.equal(maternityUi.colors.optional, 'rgb(89, 203, 179)');
  assert.ok(maternityUi.optionalStates.includes('不需要') && maternityUi.optionalStates.includes('还不确定'));
  assert.deepEqual(maternityUi.optionalStates, ['已有', '这次要买', '还不确定', '不需要']);
  assert.equal(maternityUi.optionalLayout.columns, 2);
  assert.equal(new Set(maternityUi.optionalLayout.buttons.map(button => button.top)).size, 2);
  assert.equal(maternityUi.optionalLayout.buttons[2].top, maternityUi.optionalLayout.buttons[3].top);
  assert.ok(maternityUi.optionalLayout.buttons[0].top < maternityUi.optionalLayout.buttons[2].top);
  assert.ok(maternityUi.optionalLayout.group.left >= maternityUi.optionalLayout.card.left);
  assert.ok(maternityUi.optionalLayout.group.right <= maternityUi.optionalLayout.card.right);
  assert.ok(maternityUi.optionalLayout.buttons.every(button => button.left >= maternityUi.optionalLayout.card.left && button.right <= maternityUi.optionalLayout.card.right));
  assert.ok(!maternityUi.optionalStates.includes('之后再决定'));
  assert.equal(maternityUi.request.length, 1, 'browser made one SSE request through the local frontend');
  assert.ok(maternityUi.request[0].startsWith(`${baseUrl}/commerce/intents/stream`));

  const layouts = {};
  for (const [name, width, height] of [['desktop', 1440, 1200], ['tablet', 820, 1100], ['mobile', 390, 844]]) {
    await devtools.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
    await delay(120);
    layouts[name] = await devtools.evaluate(`getComputedStyle(document.querySelector('.checklist-step')).gridTemplateColumns.split(' ').length`);
  }
  assert.deepEqual(layouts, { desktop: 3, tablet: 2, mobile: 1 });
  await devtools.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1200, deviceScaleFactor: 1, mobile: false });
  await saveChecklistScreenshot('maternity-local-desktop.png');

  await devtools.evaluate(`[...document.querySelectorAll('.live-actions button')].find(button => button.textContent.includes('新对话')).click()`);
  await delay(250);
  await devtools.evaluate(`[...document.querySelectorAll('.live-actions button')].find(button => button.textContent.includes('第一次租房入住')).click()`);
  await waitFor(() => devtools.evaluate(`(() => {
    const root = document.querySelector('#demo .checklist-step');
    return root?.querySelectorAll('.checklist-section').length === 3 && !document.querySelector('#demo .live-status');
  })()`), 'rental checklist rendered in the live scenario demo');
  const rentalUi = await devtools.evaluate(`(() => {
    const root = document.querySelector('#demo .checklist-step');
    const sections = [...root.querySelectorAll('.checklist-section')];
    const required = root.querySelector('.checklist-item.is-required');
    const optional = root.querySelector('.checklist-item.is-optional');
    return {
      heading: document.querySelector('#demo .eyebrow')?.textContent.trim() || '',
      sections: sections.map(section => ({ title: section.querySelector('h3')?.textContent.trim(),
        items: [...section.querySelectorAll('.checklist-item')].map(row => row.textContent.replace(/\\s+/g, ' ').trim()) })),
      grid: getComputedStyle(root).gridTemplateColumns.split(' ').length,
      colors: { required: getComputedStyle(required).borderLeftColor, optional: getComputedStyle(optional).borderLeftColor },
      requests: [...performance.getEntriesByType('resource')].map(entry => entry.name).filter(name => name.includes('/commerce/intents/stream')),
    };
  })()`);
  assert.equal(rentalUi.heading, 'SCENARIO DEMO / LIVE AGENT');
  assert.deepEqual(rentalUi.sections.map(section => section.title), ['睡眠与照明', '入住清洁', '收纳与日常']);
  const rentalRows = rentalUi.sections.flatMap(section => section.items).join('\n');
  assert.ok(rentalRows.includes('床品套件（床单/被套/枕套）') && rentalRows.includes('先量床垫尺寸'));
  assert.ok(rentalRows.includes('地面清洁工具（扫把/拖把）') && rentalRows.includes('按房间面积备1套'));
  assert.ok(rentalRows.includes('清洁布') && rentalRows.includes('2-3块'));
  assert.ok(rentalRows.includes('衣架') && rentalRows.includes('先备5-10个'));
  assert.ok(!rentalRows.includes('基础清洁用品') && !rentalRows.includes('收纳用品'));
  assert.equal(rentalUi.grid, 3);
  assert.equal(rentalUi.colors.required, 'rgb(237, 201, 73)');
  assert.equal(rentalUi.colors.optional, 'rgb(89, 203, 179)');
  assert.equal(rentalUi.requests.length, 2, 'rental demo sent a fresh SSE request through the local frontend');
  assert.ok(rentalUi.requests.every(url => url.startsWith(`${baseUrl}/commerce/intents/stream`)));
  await saveChecklistScreenshot('rental-local-desktop.png');
  await saveDemoScreenshot('rental-live-demo-desktop.png');

  await devtools.evaluate(`[...document.querySelectorAll('.live-actions button')].find(button => button.textContent.includes('新对话')).click()`);
  await delay(250);
  await sendInBrowser('第一次养猫，我需要准备清单');
  await waitFor(() => devtools.evaluate(`(() => {
    const sections = [...document.querySelectorAll('.checklist-step .checklist-section')];
    return sections.length === 5 && sections.some(s => s.querySelector('h3')?.textContent.trim() === '如厕') && !document.querySelector('.live-status');
  })()`), 'first-cat checklist rendered in the browser');
  const catUi = await devtools.evaluate(`(() => {
    const root = document.querySelector('.checklist-step');
    return { sections: [...root.querySelectorAll('.checklist-section')].map(section => ({
      title: section.querySelector('h3')?.textContent.trim(),
      count: section.querySelectorAll('.checklist-item').length,
      reference: section.querySelector('.checklist-reference')?.textContent.trim() || '',
    })), grid: getComputedStyle(root).gridTemplateColumns.split(' ').length };
  })()`);
  assert.deepEqual(catUi.sections.map(section => section.title), ['吃喝', '如厕', '安全与外出', '休息与抓挠', '护理与健康记录']);
  assert.equal(catUi.grid, 3);
  await saveChecklistScreenshot('first-cat-local-desktop.png');

  const report = {
    baseUrl, apiTarget, browser: 'Microsoft Edge headless (Chromium)',
    backendHealth: await health.json(),
    maternity: { sessionId: maternity.sessionId, templateId: maternity.plan.template_id, sections: maternity.plan.checklist_sections, milestones: maternity.milestones, turnEndSec: maternity.turnEndSec, browser: maternityUi, layouts },
    rentalDemo: { sections: rentalUi.sections, columns: rentalUi.grid, colors: rentalUi.colors, requests: rentalUi.requests },
    firstCat: { sections: catUi.sections, columns: catUi.grid },
    browserRequests: networkRequests,
    screenshots: ['maternity-local-desktop.png', 'rental-local-desktop.png', 'rental-live-demo-desktop.png', 'first-cat-local-desktop.png'],
    standardRecallAtK: null,
  };
  await writeFile(path.join(outputDir, 'local-browser-acceptance.json'), JSON.stringify(report, null, 2), 'utf8');
  console.log(JSON.stringify({ pass: true, baseUrl, apiTarget, maternityTemplate: maternity.plan.template_id, maternitySections: maternity.plan.checklist_sections.map(section => section.title), layouts, firstCatSections: catUi.sections.map(section => section.title), browserRequests: networkRequests.length, screenshots: report.screenshots }, null, 2));
} finally {
  try { await devtools?.send('Browser.close'); } catch {}
  try { devtools?.socket.close(); } catch {}
  const exited = await Promise.race([once(edge, 'exit'), delay(3000).then(() => null)]);
  if (exited === null) {
    edge.kill();
    await Promise.race([once(edge, 'exit'), delay(2000)]);
  }
  const tempRootPath = path.resolve(os.tmpdir());
  const profilePath = path.resolve(profileDir);
  if (path.dirname(profilePath) === tempRootPath && path.basename(profilePath).startsWith('shopmind-edge-')) {
    try { await rm(profilePath, { recursive: true, force: true }); }
    catch (error) {
      if (!['EBUSY', 'EACCES', 'EPERM'].includes(error.code)) throw error;
      await delay(1000);
      try { await rm(profilePath, { recursive: true, force: true }); }
      catch (retryError) {
        if (!['EBUSY', 'EACCES', 'EPERM'].includes(retryError.code)) throw retryError;
        console.warn(`Temporary Edge profile could not be removed: ${profilePath}`);
      }
    }
  }
}
