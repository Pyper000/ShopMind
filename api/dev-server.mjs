import { createServer } from 'node:http';
import chat from './chat.mjs';

createServer(async (req, res) => {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  try { req.body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : {}; }
  catch { req.body = null; }
  res.status = code => { res.statusCode = code; return res; };
  res.json = body => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(body)); return res; };
  await chat(req, res);
}).listen(8787, '127.0.0.1', () => console.log('ShopMind local API: http://127.0.0.1:8787'));
