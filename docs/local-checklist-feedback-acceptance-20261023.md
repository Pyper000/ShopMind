# ShopMind 本地验收增量记录（2026-10-23）

## 断点与既有工作区

- 已先读取 `docs/local-acceptance-handoff-20261023.md` 与 `docs/worktree-inventory-20261023.md`。既有改动涉及编排、搜索、模板、DTO/SSE、前端、评测和测试；之前记录了完整测试 352 passed、三场景 API 回归 3/3，以及尚无本轮反馈颜色/状态验收的旧浏览器产物。
- 本轮开始前统计：36 个已跟踪文件有修改、276 个未跟踪路径、0 个暂存、0 个删除。未 reset、clean、覆盖既有报告、停止既有服务或推送/部署。
- 旧交接中的端口已不再完整可用；本轮使用独立 API `8012` 和前端 `5185`。旧 `8000`、`8010`、`5183`、`5184` 进程未停止或改配置。`5184` 已占用，因此没有复用。

## 本轮修改

- 按需项保留“还不确定”，把“之后再决定”改为“不需要”；明确不需要状态仍经兼容字段 `deferred` 提交，后端已有路径会写入 `not_buy`，不会进入商品搜索。
- 必备项改为显眼黄色，按需项改为绿色；必备和按需的选中状态也分别使用对应颜色。计划结果将“已有”和“明确不需要”分开显示。
- 修改位于 `frontend/src/components/LiveChat.tsx`、`frontend/src/landing.css`；补强 `scripts/check_checklist_ui.cjs` 与 `scripts/acceptance_local_browser.mjs` 的浏览器断言。

## 可复核验收

- 前端构建：`npm run build -- --outDir ..\eval\results\local_acceptance_20261023_feedback\frontend-build` 成功；产物写到新目录，没有清空现有 `frontend/dist`。
- 相关后端测试：场景模板、计划批量派发、增量搜索共 22 passed。全量 `pytest` 的 352 passed 是此前已有记录，本轮后端逻辑未改。
- Mocked SSE + Playwright UI 回归通过：确认只显示“不需要”和“还不确定”、两者对应状态不会混淆；必备黄、按需绿；提交时“不需要”进入 `deferred`，未知项仍留在 `unknown`。结果：`eval/results/checklist_ui_20261023-feedback/ui-result.json`。
- Edge 实际浏览器验收通过，浏览器请求为 `http://127.0.0.1:5185/commerce/intents/stream`，该前端代理明确配置到 `http://127.0.0.1:8012`。待产清单显示“孕妇 / 宝宝 / 陪护人”三模块，分别 17 / 8 / 5 项（必备与按需合计）；数量、按需条件、NHS 英国来源及“以本院清单为准”说明均已渲染。计算样式为桌面 3 栏、平板 2 栏、手机 1 栏；必备边框 `rgb(237, 201, 73)`，按需边框 `rgb(89, 203, 179)`。
- 同一真实浏览器用全新 session 验收“第一次养猫”，实际渲染 5 个模块，桌面 3 栏。
- 真实浏览器截图与完整事件/页面报告：`eval/results/local_acceptance_20261023_feedback/maternity-local-desktop.png`、`first-cat-local-desktop.png`、`local-browser-acceptance.json`。清单按钮专项截图与 JSON 在 `eval/results/checklist_ui_20261023-feedback/`。
- 本轮待产浏览器 session 的首条反馈和首份可见清单均为 1.38 秒，整轮结束 2.34 秒；未确认购买，所以该轮首批商品为 null。另一个独立的 API 新 session 首个 SSE 计划事件为 3.11 秒，整轮 3.62 秒；它与浏览器会话分开测量，不能混算。
- 既有三场景脚本化 API 结果 `eval/results/local_acceptance_20261023/mvp-core-three-20261023-174957.json` 为 3/3；清单首计划 1.18–1.265 秒，确认后首个商品 SSE 事件 0.042–0.056 秒，返回卡片标题/亮点的槽位匹配率为 1.0。该指标不是标准 Recall@K；报告中的 `standard_recall_at_k` 保持 null，且这组商品时间不是浏览器绘制时间。
- `git diff --check` 通过；仅显示既有文件的 LF/CRLF 转换提醒。第一次误用全局 Python 启动时因缺 `sqlalchemy` 失败，已用仓库 `.venv` 成功重试；失败日志单独保留在新验收目录，没有改动原服务。

## 当前本地入口

- 验收页面：`http://127.0.0.1:5185/`
- Vite 代理目标：`http://127.0.0.1:8012`
- 两个本轮服务保持运行，数据仅写入 `eval/results/local_acceptance_20261023_feedback/data/`；没有推 Git 或部署公网。
