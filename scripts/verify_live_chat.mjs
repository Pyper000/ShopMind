import { decide } from '../api/chat.mjs';
import { writeFileSync, mkdirSync } from 'node:fs';
const history = [];
const records = [];
for (const query of ['想给妈妈买礼物，不知道买什么', '她喜欢喝茶，预算300元，寄中国', '已经有茶具，不要茶具，换个方向']) {
  history.push({ role: 'user', content: query });
  const start = Date.now();
  const result = await decide(history);
  records.push({ query, seconds: (Date.now()-start)/1000, ...result });
  history.push({ role: 'assistant', content: result.text + JSON.stringify(result.groups) });
  mkdirSync('data', { recursive: true });
  writeFileSync('data/serverless-live-observed.json', JSON.stringify(records, null, 2));
  console.log(JSON.stringify({query,seconds:records.at(-1).seconds,text:result.text,groups:result.groups.map(g=>({name:g.name,ids:g.products.map(p=>p.id)}))}));
}
