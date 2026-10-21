# ShopMind AI

面向第一次执行生活任务、缺乏采购经验的用户，把模糊目标变成一份“够用、可解释、可调整”的采购方案。ShopMind 管理一次购买决策，而不只是推荐更多商品。

当前 MVP 聚焦海边出行、节日送礼、首次租房等模拟场景。目录和商品为模拟数据，不连接真实支付、订单或物流；10 个场景的自动评测是脚本化测试，不代表真人用户研究或线上业务效果。

## 产品流程

```text
描述生活任务 -> 澄清会改变清单的条件 -> 生成采购清单
             -> 核对已有物品 -> 搜索候选、解释取舍 -> 用户调整方案
```

清单区分已有、必买、按需和待确认。用户改预算、偏好或清单时，系统保留已确认的方案事实，并只重算受影响的商品候选。

## Agent 分工

- **MainAgent**：理解自然语言，维护会话阶段、清单状态、预算、偏好和状态版本；输出结构化方案变更，处理必要澄清，并在搜索完成后提交状态、组织最终回复。
- **SearchAgent**：接收完整方案与本轮变更，自主选择增量重排、受影响品类局部召回或整体重召回；返回候选、搜索轨迹和取舍解释，不修改清单事实。
- **TradeAgent**：保留扩展入口，但默认关闭，不进入 MVP 主链路。

通过 AgentScope 的 `MainAgentFactory`、`SearchAgentFactory` 和 `task_dispatch` 子 Agent 调度连接主搜索链路。商品预算等硬约束由规则和工具校验，不让模型编造商品事实。

## 技术栈

- 前端：React 18、TypeScript、Vite，沿用对话式购物工作台交互。
- 后端：Python 3.11+、FastAPI、Uvicorn，提供会话 API 与 SSE 事件流。
- Agent 编排：AgentScope 2.x；项目工厂封装 MainAgent/SearchAgent，`task_dispatch` 将 SearchAgent 作为子 Agent 调度。
- 检索：模拟商品目录、结构化过滤和候选重排；向量检索可使用 Qdrant，未配置外部服务时按本地设置运行。
- 模型：OpenAI 兼容接口；Render Blueprint 默认配置 SiliconFlow 与 DeepSeek V3.2，密钥仅由部署环境注入。
- 部署：Docker Web Service + Render Blueprint。前端也可单独部署，并用 `VITE_API_BASE` 指向后端。

MVP 默认使用文件会话存储，关闭 Redis 队列和语义缓存，以降低部署复杂度。它适合验证对话、清单状态和增量搜索链路，不等同于带真实商品、库存、交易与用户数据的生产电商系统。

## 本地运行

后端需要配置模型服务的 `LLM_API_KEY`：

```powershell
uv sync
$env:LLM_API_KEY = "你的模型服务密钥"
uv run uvicorn app.presentation.server:app --host 0.0.0.0 --port 8000
```

另开终端启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

## 验证

```powershell
uv run pytest
cd frontend
npm run build
node --test ../api/chat.test.mjs
```

针对可访问的 Python AgentScope API 运行 10 个脚本场景并导出对话及 CSV/JSON 指标：

```powershell
uv run python scripts/eval_scenarios.py --base-url https://你的后端地址
```

评测结果用于发现清单覆盖、已有物品误召回、请求成功率和延迟问题；模拟数据不能用于推断真实 ROI、转化率或用户满意度。

## 部署

仓库包含 `render.yaml` 与 `Dockerfile`。Render 服务需要配置 `LLM_API_KEY` 后才能调用模型；成功部署后可通过 `/health` 检查服务。不要把密钥写入代码或提交到 Git。
