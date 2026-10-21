---
title: ShopMind AI
emoji: 🛍️
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# Cloud MVP Setup

This project uses simulated products and simulated account/order contexts.
Model conversations and catalog retrieval are real API calls, not prerecorded answers.

## Hosting

Create a Docker Space, then use this YAML header at the top of its README.md.
Upload the repository source excluding .git, .env, data, .venv, node_modules and dist.
The root Dockerfile builds the frontend and runs FastAPI on port 7860.

## Server Secrets

- LLM_API_KEY: the working SiliconFlow key, stored as a Space Secret only.
- EMBEDDING_API_KEY: the same working key, stored as a Space Secret only.

## Server Variables

- LLM_BASE_URL=https://api.siliconflow.cn/v1
- LLM_MODEL=deepseek-ai/DeepSeek-V3.2
- LLM_FALLBACK_MODEL=deepseek-ai/DeepSeek-V3.2
- EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
- EMBEDDING_MODEL=BAAI/bge-m3
- EMBEDDING_DIM=1024
- CORS_ORIGINS=https://pyper000.github.io
- PORT=7860
- REDIS_URL=
- QUEUE_ENABLED=false
- SEMANTIC_CACHE_ENABLED=false

Do not put API keys in a VITE variable or frontend code.
Free hosting availability and signup requirements must be verified in the account UI.
Space local data may be lost on rebuild; durable memory is not guaranteed.
This is a single-instance portfolio demo with request limits, not production hosting.

## Connect GitHub Pages

Set the repository Actions variable VITE_API_BASE to the Space public runtime URL.
Run the Deploy Portfolio workflow again. Open ?workbench to use real conversations.
When VITE_API_BASE is absent, the public workbench shows an explicit unavailable message.

## Verification

Run scripts/verify_scene_loop.py against the runtime URL.
Observed responses are saved to data/scene-loop-observed.json.
HTTP 200 is not a product-quality pass; manually inspect clarification, exclusions,
budget, delivery, evidence grounding and decision convergence.
